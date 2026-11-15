"""Gaps found by running the Indian-market paths against real NSE/Upstox data (2026-11-15)."""
import unittest

from _load import load

ticket = load("trader-broker", "ticket")


class InstrumentKey(unittest.TestCase):
    def test_ticket_uses_isin_key_not_symbol(self):
        out = ticket.upstox_tool_input({"symbol": "RELIANCE.NS", "qty": 10, "side": "buy"}, {})
        self.assertEqual(out["instrument_token"], "NSE_EQ|INE002A01018")

    def test_unknown_symbol_refuses(self):
        with self.assertRaises(ticket.TicketError):
            ticket.upstox_tool_input({"symbol": "NOSUCHCO.NS", "qty": 1}, {})

    def test_over_freeze_quantity_refuses(self):
        with self.assertRaises(ticket.TicketError):
            ticket.upstox_tool_input({"symbol": "TCS", "qty": 200000}, {})

    def test_explicit_token_passes_through(self):
        out = ticket.upstox_tool_input({"symbol": "TCS", "qty": 1, "instrument_token": "NSE_EQ|INE467B01029"}, {})
        self.assertEqual(out["instrument_token"], "NSE_EQ|INE467B01029")

    def test_symbol_style_token_is_replaced(self):
        out = ticket.upstox_tool_input({"symbol": "TCS", "qty": 1, "instrument_token": "NSE_EQ|TCS"}, {})
        self.assertEqual(out["instrument_token"], "NSE_EQ|INE467B01029")


if __name__ == "__main__":
    unittest.main()


tm = load("trader-timemachine", "timemachine")


class NseGaps(unittest.TestCase):
    def _day(self, n=75, missing=()):
        return {i: {"date": "2026-11-12", "slot": i, "start": "x", "o": 100.0, "h": 101.0, "l": 99.0, "c": 100.0, "v": 5}
                for i in range(n) if i not in missing}

    def test_yahoo_nse_day_missing_1520_and_1525_counts_as_full(self):
        # real Yahoo pattern: slots 73 (15:20) and 74 (15:25) empty
        full, short = tm.full_sessions({"2026-11-12": self._day(missing=(73, 74))}, "2026-11-13",
                                       session_bars=75, max_gap=tm.IN_MAX_GAP)
        self.assertEqual(len(full), 1)
        self.assertTrue(full[0][1][74]["filled"])

    def test_us_behaviour_unchanged(self):
        full, short = tm.full_sessions({"d": self._day(n=78, missing=(77,))}, "e", session_bars=78)
        self.assertEqual((len(full), len(short)), (0, 1))

    def test_too_many_holes_still_rejected(self):
        full, short = tm.full_sessions({"2026-11-12": self._day(missing=(10, 11, 12, 13, 14))}, "2026-11-13",
                                       session_bars=75, max_gap=tm.IN_MAX_GAP)
        self.assertEqual(len(full), 0)


size = load("trader-budget", "size")


class RealTick(unittest.TestCase):
    def test_reliance_tick_is_ten_paise(self):
        row = size.instrument_row("RELIANCE.NS")
        self.assertEqual(float(row["tick_size"]) / 100, 0.10)

    def test_unknown_symbol_returns_none(self):
        self.assertIsNone(size.instrument_row("NOSUCHCO"))


class Costs(unittest.TestCase):
    def test_small_trade_costs_are_material(self):
        # 20 sh RELIANCE 1340 -> 1360: gross win 400; costs must be a visible slice, never zero
        c = size.india_intraday_costs(1340, 1360, 20)
        self.assertTrue(40 < c < 80, c)  # ~57: 2 x Rs20 brokerage + STT + GST + fees

    def test_brokerage_capped_at_flat_fee(self):
        big = size.india_intraday_costs(1000, 1000, 1000, {"stt_sell_pct": 0, "txn_pct": 0, "sebi_pct": 0, "stamp_buy_pct": 0})
        self.assertAlmostEqual(big, round(40 * 1.18, 2))


import io
import contextlib

ptr = load("trader-pelosi", "ptr")


class PelosiNoSilentEmpty(unittest.TestCase):
    def test_live_deals_with_no_feed_says_so_and_exits_2(self):
        err, out = io.StringIO(), io.StringIO()
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(out):
            rc = ptr.main(["deals"])
        self.assertEqual(rc, 2)
        self.assertIn("not 'no deals'", err.getvalue())


scan = load("trader-scanner", "scan")


class Presets(unittest.TestCase):
    def test_fno_uses_live_list_when_available(self):
        orig = scan.live_fno_symbols
        scan.live_fno_symbols = lambda: [("ABB.NS", "ABB")]
        try:
            self.assertEqual(scan.preset_list("fno"), [("ABB.NS", "ABB")])
        finally:
            scan.live_fno_symbols = orig

    def test_fno_falls_back_loudly(self):
        orig, err = scan.live_fno_symbols, io.StringIO()
        scan.live_fno_symbols = lambda: None
        try:
            with contextlib.redirect_stderr(err):
                self.assertTrue(scan.preset_list("fno"))
        finally:
            scan.live_fno_symbols = orig
        self.assertIn("incomplete", err.getvalue())

    def test_nifty500_warns_it_is_only_100(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(len(scan.preset_list("nifty500")), 100)
        self.assertIn("not the full Nifty 500", err.getvalue())


class StopAndProtection(unittest.TestCase):
    ORDER = {"symbol": "RELIANCE.NS", "qty": 56, "side": "buy", "type": "market", "stop_loss_stop_price": "1328.90"}

    def test_market_order_carries_protection(self):
        self.assertEqual(ticket.upstox_tool_input(self.ORDER, {})["market_protection"], 2.0)

    def test_limit_order_has_no_protection_field(self):
        out = ticket.upstox_tool_input(dict(self.ORDER, type="limit", limit_price="1334.00"), {})
        self.assertNotIn("market_protection", out)

    def test_stop_is_a_separate_sell_sl_m(self):
        st = ticket.upstox_stop_input(self.ORDER, {})
        self.assertEqual((st["transaction_type"], st["order_type"], st["trigger_price"]), ("SELL", "SL-M", 1328.90))
        self.assertEqual(st["instrument_token"], "NSE_EQ|INE002A01018")

    def test_no_stop_no_second_order(self):
        self.assertIsNone(ticket.upstox_stop_input({"symbol": "TCS", "qty": 1}, {}))
