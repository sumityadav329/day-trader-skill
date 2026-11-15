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

    def test_nifty500_fallback_warns_it_is_only_100(self):
        orig, err = scan.live_index_symbols, io.StringIO()
        scan.live_index_symbols = lambda n: None
        try:
            with contextlib.redirect_stderr(err):
                self.assertEqual(len(scan.preset_list("nifty500")), 100)
        finally:
            scan.live_index_symbols = orig
        self.assertIn("NOT the full", err.getvalue())

    def test_index_csv_parsed_from_nse_format(self):
        import tempfile
        # first rows of NSE's real ind_nifty500list.csv
        csv_text = ("Company Name,Industry,Symbol,Series,ISIN Code\n"
                    "360 ONE WAM Ltd.,Financial Services,360ONE,EQ,INE466L01038\n"
                    "ABB India Ltd.,Capital Goods,ABB,EQ,INE117A01022\n")
        with tempfile.TemporaryDirectory() as d:
            with open(_os.path.join(d, "ind_nifty500list.csv"), "w", encoding="utf-8") as fh:
                fh.write(csv_text)
            self.assertEqual(scan.live_index_symbols("nifty500", cache_dir=d),
                             [("360ONE.NS", "360 ONE WAM Ltd."), ("ABB.NS", "ABB India Ltd.")])


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


class StopSide(unittest.TestCase):
    def test_stop_is_opposite_of_entry_side(self):
        for entry, stop_side in (("buy", "SELL"), ("sell", "BUY")):
            o = {"symbol": "TCS", "qty": 1, "side": entry, "stop_loss_stop_price": "100"}
            self.assertEqual(ticket.upstox_stop_input(o, {})["transaction_type"], stop_side)


import datetime
import json as _json
import os as _os
from unittest import mock

entry = load("trader-entry", "entry")

# real Upstox v3 candles for RELIANCE, 2026-11-13 (newest first, as the API sends them)
REAL_CANDLES = [["2026-11-13T15:25:00+05:30", 1340.7, 1340.7, 1339.7, 1339.7, 512357, 0],
                ["2026-11-13T09:15:00+05:30", 1336.6, 1336.6, 1325.2, 1327.0, 559540, 0]]


class _Resp:
    def __init__(self, body): self.body = body
    def read(self): return self.body
    def __enter__(self): return self
    def __exit__(self, *a): return False


class UpstoxCandles(unittest.TestCase):
    def _run(self, token="t", candles=REAL_CANDLES, day=datetime.date(2026, 11, 13), today=datetime.date(2026, 11, 15)):
        env = {"UPSTOX_ACCESS_TOKEN": token} if token else {}
        body = _json.dumps({"status": "success", "data": {"candles": candles}}).encode()
        with mock.patch.dict(_os.environ, env, clear=False), \
             mock.patch.object(entry.urllib.request, "urlopen", return_value=_Resp(body)) as op:
            if not token:
                _os.environ.pop("UPSTOX_ACCESS_TOKEN", None); _os.environ.pop("UPSTOX_ANALYTICS_TOKEN", None)
            return entry.upstox_chart_text("RELIANCE.NS", "5m", day, today), op

    def test_no_token_means_public_source(self):
        text, op = self._run(token=None)
        self.assertIsNone(text)
        op.assert_not_called()

    def test_converts_to_yahoo_shape_oldest_first(self):
        text, op = self._run()
        ch = entry.parse_chart(text, "RELIANCE.NS")
        self.assertEqual(ch["symbol"], "RELIANCE.NS")
        self.assertEqual([b["o"] for b in ch["bars"]], [1336.6, 1340.7])
        self.assertLess(ch["bars"][0]["t"], ch["bars"][1]["t"])
        self.assertIn("INE002A01018", op.call_args[0][0].full_url.replace("%7C", "|"))

    def test_past_day_uses_history_and_today_uses_intraday(self):
        _, op = self._run()
        self.assertIn("/historical-candle/", op.call_args[0][0].full_url)
        self.assertNotIn("/intraday/", op.call_args[0][0].full_url)
        _, op = self._run(day=datetime.date(2026, 11, 15))
        self.assertIn("/intraday/", op.call_args[0][0].full_url)


class NetOfCostsAndBand(unittest.TestCase):
    CFG = {"paper": True, "broker": "upstox", "market": "IN", "budget": 100000.0, "max_loss_per_trade_inr": 1000.0,
           "max_order_inr": 75000.0, "min_order_inr": 100.0, "min_reward_risk": 1.0}

    def _plan(self, stop, target):
        return {"symbol": "RELIANCE.NS", "side": "buy", "entry": 1334.0, "stop": stop, "target": target}

    def test_two_r_plan_passes_net_of_costs(self):
        s = size.size_trade(self._plan(1328.9, 1344.3), self.CFG, today="2026-11-13")
        self.assertIsNone(s["refusal"])          # live-checked: gross 2.02, net 1.40

    def test_thin_target_refused_after_costs_though_gross_passes(self):
        # gross reward:risk 1.04 clears min 1.0, but costs push it under
        s = size.size_trade(self._plan(1328.9, 1339.3), self.CFG, today="2026-11-13")
        self.assertGreaterEqual(s["reward_risk"], 1.0)
        self.assertIn("after costs", s["refusal"] or "")

    def test_no_token_means_band_unknown_not_assumed(self):
        with mock.patch.dict(_os.environ, {}, clear=False):
            _os.environ.pop("UPSTOX_ACCESS_TOKEN", None)
            s = size.size_trade(self._plan(1328.9, 1344.3), self.CFG, today="2026-11-13")
        self.assertIsNone(s["circuit_upper"])
        self.assertNotIn("upper_circuit", size.bracket_order(s))

    def test_band_from_quote_rides_on_the_order(self):
        body = _json.dumps({"status": "success", "data": {"NSE_EQ:RELIANCE": {
            "upper_circuit_limit": 1473.6, "lower_circuit_limit": 1205.8}}}).encode()
        with mock.patch.dict(_os.environ, {"UPSTOX_ACCESS_TOKEN": "t"}), \
             mock.patch("urllib.request.urlopen", return_value=_Resp(body)):
            s = size.size_trade(self._plan(1328.9, 1344.3), self.CFG, today="2026-11-13")
        o = size.bracket_order(s)
        self.assertEqual((o["lower_circuit"], o["upper_circuit"]), (1205.8, 1473.6))


facts = load("trader-analyst", "facts")


class RemainingGaps(unittest.TestCase):
    def test_nse_deals_csv_is_parsed(self):
        # NSE's download header; values are synthetic placeholders
        csv_text = ("Date,Symbol,Security Name,Client Name,Buy/Sell,Quantity Traded,Trade Price / Wght. Avg. Price,Remarks\n"
                    "13-NOV-2026,ABC,ABC Ltd,Some Fund,BUY,\"1,50,000\",250.50,-\n")
        rows = ptr.parse_indian_deals(csv_text)
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["symbol"], rows[0]["quantity"], rows[0]["price"], rows[0]["date"], rows[0]["type"]),
                         ("ABC.NS", 150000, 250.5, "2026-11-13", "purchase"))

    def test_garbage_is_not_parsed_as_deals(self):
        self.assertEqual(ptr.parse_indian_deals("not a deals file"), [])

    def test_entry_notes_nse_holiday_and_weekend(self):
        self.assertIn("Diwali", entry.closed_note("2026-11-10", "IN"))
        self.assertIn("weekend", entry.closed_note("2026-11-14", "IN"))
        self.assertEqual(entry.closed_note("2026-11-13", "IN"), "")
        self.assertEqual(entry.closed_note("2026-11-10", "US"), "")
