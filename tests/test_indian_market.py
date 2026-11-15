"""Comprehensive unit tests for Indian stock market (NSE & BSE) integration.

Covers:
1. Scanner: NSE/BSE ticker resolution, Indian presets, detect_market, ORB window, calendar.
2. Timings: Entry ORB in IST, exit flat_by MIS cutoff, session timings.
3. Timemachine: 75 5-minute bars in Indian trading session (375 min), session filtering.
4. Alerts: Indian session clock, 09:15 opening bell, 15:15 MIS auto-square-off alert, INR pricing.
5. Budget & Sizing: INR currency handling, tick size (0.05 NSE), bracket orders with product/exchange.
6. Risk: Daily price bands / circuit limits, locked circuits, NSE tick size, SEBI peak margin, PDT bypass.
7. Broker: Upstox API v2 and Zerodha Kite tool inputs, explicit yes parsing, plain tickets, flattening.
8. News & Analyst: Indian RSS/announcements, tagging, SEBI LODR Reg 33 quarterly analysis.
9. Pelosi / Disclosures: NSE/BSE bulk and block deals, SEBI PIT Reg 7(2) & SAST Reg 29 disclosures.
"""

import datetime
import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout

from _load import load

# Load modules under test
scan = load("trader-scanner", "scan")
entry = load("trader-entry", "entry")
exit_mod = load("trader-exit", "exit")
tm = load("trader-timemachine", "timemachine")
alerts = load("trader-alerts", "alerts")
size = load("trader-budget", "size")
check = load("trader-risk", "check")
ticket = load("trader-broker", "ticket")
news = load("trader-news", "news")
facts = load("trader-analyst", "facts")
ptr = load("trader-pelosi", "ptr")


class TestIndianScannerAndUniverse(unittest.TestCase):
    def test_indian_symbol_normalization(self):
        self.assertTrue(scan.is_indian_symbol("RELIANCE.NS"))
        self.assertTrue(scan.is_indian_symbol("TCS.BO"))
        self.assertTrue(scan.is_indian_symbol("INFY.NS"))
        self.assertTrue(scan.is_indian_symbol("NIFTY50"))
        self.assertTrue(scan.is_indian_symbol("BANKNIFTY"))
        self.assertTrue(scan.is_indian_symbol("NIFTY"))
        self.assertTrue(scan.is_indian_symbol("SENSEX"))
        self.assertFalse(scan.is_indian_symbol("AAPL"))
        self.assertFalse(scan.is_indian_symbol("TSLA"))
        self.assertFalse(scan.is_indian_symbol("BRK.B"))

    def test_to_yahoo_mapping(self):
        # Indian equities retain .NS / .BO
        self.assertEqual(scan.to_yahoo("RELIANCE.NS"), "RELIANCE.NS")
        self.assertEqual(scan.to_yahoo("TCS.BO"), "TCS.BO")
        # Indices map to Yahoo index tickers
        self.assertEqual(scan.to_yahoo("NIFTY50"), "^NSEI")
        self.assertEqual(scan.to_yahoo("NIFTY"), "^NSEI")
        self.assertEqual(scan.to_yahoo("BANKNIFTY"), "^NSEBANK")
        self.assertEqual(scan.to_yahoo("NIFTYBANK"), "^NSEBANK")
        self.assertEqual(scan.to_yahoo("SENSEX"), "^BSESN")
        self.assertEqual(scan.to_yahoo("NIFTYNEXT50"), "^NSMIDCP")
        # US backward compatibility: dots to hyphens
        self.assertEqual(scan.to_yahoo("BRK.B"), "BRK-B")
        self.assertEqual(scan.to_yahoo("AAPL"), "AAPL")

    def test_indian_presets_exist_and_populated(self):
        for preset_name in ("nifty50", "niftybank", "niftynext50", "nifty100", "nifty500", "fno"):
            self.assertIn(preset_name, scan.INDIAN_PRESETS)
            symbols = scan.INDIAN_PRESETS[preset_name]
            self.assertGreater(len(symbols), 0)
            self.assertTrue(all(s[0].endswith(".NS") for s in symbols), preset_name)

        self.assertGreaterEqual(len(scan.INDIAN_NIFTY_50), 50)
        self.assertGreaterEqual(len(scan.INDIAN_NIFTY_BANK), 12)
        n50_tickers = [s[0] for s in scan.INDIAN_NIFTY_50]
        self.assertIn("RELIANCE.NS", n50_tickers)
        self.assertIn("HDFCBANK.NS", n50_tickers)

    def test_indian_market_detection(self):
        self.assertEqual(scan.detect_market(symbols=["RELIANCE.NS"]), "IN")
        self.assertEqual(scan.detect_market(symbols=["AAPL"]), "US")
        self.assertEqual(scan.detect_market(preset="nifty50"), "IN")
        self.assertEqual(scan.detect_market(preset="sp500"), "US")
        self.assertEqual(scan.detect_market(explicit_market="IN"), "IN")
        self.assertEqual(scan.detect_market(explicit_market="US"), "US")
        self.assertEqual(scan.detect_market(config={"market": "IN"}), "IN")

    def test_indian_money_formatting(self):
        self.assertEqual(scan.money(1234.50, market="IN"), "₹1,234.50")
        self.assertEqual(scan.money(1234.50, market="US"), "$1,234.50")

    def test_trading_day_and_session(self):
        # Republic Day 2026 is a holiday
        is_open, reason = scan.trading_day(datetime.date(2026, 1, 26), market="IN")
        self.assertFalse(is_open)
        self.assertIn("Republic Day", reason)

        # Regular Monday Oct 19 2026
        is_open, _ = scan.trading_day(datetime.date(2026, 10, 19), market="IN")
        self.assertTrue(is_open)

        # Indian session is 09:15 to 15:30 IST
        op, cl = scan.session(datetime.date(2026, 10, 19), market="IN")
        self.assertEqual((op.hour, op.minute), (9, 15))
        self.assertEqual((cl.hour, cl.minute), (15, 30))

    def test_orb_window_indian_session(self):
        now_utc = scan.ist_to_utc(datetime.datetime(2026, 10, 19, 11, 0))
        day, cutoff = scan.orb_window(
            now_utc, datetime.time(10, 30), 15, datetime.time(15, 15),
            explicit_day=datetime.date(2026, 10, 19), market="IN"
        )
        self.assertEqual(day, datetime.date(2026, 10, 19))
        self.assertEqual(cutoff.time(), datetime.time(10, 30))


class TestIndianTimingsAndSession(unittest.TestCase):
    def test_entry_symbol_and_trading_day(self):
        self.assertTrue(entry.is_indian_symbol("INFY.NS"))
        self.assertTrue(entry.is_indian_symbol("HDFCBANK.NS"))
        self.assertTrue(entry.is_indian_symbol("^NSEI"))
        self.assertFalse(entry.is_indian_symbol("MSFT"))

        # Dussehra 2026 (Tuesday Oct 20 2026)
        is_open, why = entry.trading_day(datetime.date(2026, 10, 20), market="IN")
        self.assertFalse(is_open)
        self.assertIn("Dussehra", why)

        # Indian session hours
        op, cl = entry.session(datetime.date(2026, 10, 19), market="IN")
        self.assertEqual((op.hour, op.minute), (9, 15))
        self.assertEqual((cl.hour, cl.minute), (15, 30))

    def test_exit_flat_by_mis_cutoff(self):
        # Default flat_by for Indian intraday is 15:15 IST (ahead of broker auto-square-off)
        flat_in, _ = exit_mod.resolve_flat_by(datetime.date(2026, 10, 19), None, {}, datetime.time(9, 35), market="IN")
        self.assertEqual(flat_in, "15:15")

        # US default flat_by remains 15:55 ET
        flat_us, _ = exit_mod.resolve_flat_by(datetime.date(2026, 10, 19), None, {}, datetime.time(10, 15), market="US")
        self.assertEqual(flat_us, "15:55")

        # Currency formatting in exit module
        self.assertEqual(exit_mod.money(2500.5, market="IN"), "₹2,500.50")
        self.assertEqual(exit_mod.money(2500.5, market="US"), "$2,500.50")


class TestIndianTimemachineBars(unittest.TestCase):
    def test_session_bar_count_and_timing_math(self):
        # Indian session: 09:15 to 15:30 IST = 375 minutes = 75 bars of 5-min
        self.assertEqual(tm.IN_SESSION_BARS, 75)
        self.assertEqual(tm.IN_OPEN_MIN, 9 * 60 + 15)   # 555
        self.assertEqual(tm.IN_CLOSE_MIN, 15 * 60 + 30)  # 930
        self.assertEqual(tm.IN_CLOSE_MIN - tm.IN_OPEN_MIN, 375)

    def test_full_sessions_requires_75_bars_for_indian_market(self):
        # Create a dictionary of mock session days:
        # Day 1 has 75 bars (full Indian session)
        # Day 2 has 74 bars (incomplete / gap)
        # Day 3 has 78 bars (US length, invalid for Indian market)
        sessions = {
            "2026-10-14": [{"c": 100.0} for _ in range(75)],
            "2026-10-15": [{"c": 101.0} for _ in range(74)],
            "2026-10-16": [{"c": 102.0} for _ in range(78)],
        }
        full, short = tm.full_sessions(sessions, "2026-10-19", session_bars=tm.IN_SESSION_BARS)
        self.assertEqual(len(full), 1)
        self.assertEqual(full[0][0], "2026-10-14")
        self.assertEqual(len(full[0][1]), 75)
        self.assertEqual(len(short), 2)

    def test_is_indian_symbol_detection(self):
        self.assertTrue(tm.is_indian_symbol("RELIANCE.NS"))
        self.assertTrue(tm.is_indian_symbol("TCS.BO"))
        self.assertTrue(tm.is_indian_symbol("NIFTY50"))
        self.assertFalse(tm.is_indian_symbol("SPY"))


class TestIndianAlertsAndClock(unittest.TestCase):
    def test_market_status_indian_market(self):
        # During regular session: 10:30 IST on Oct 19 2026
        now_utc = alerts.ist_to_utc(datetime.datetime(2026, 10, 19, 10, 30))
        st = alerts.market_status(now_utc, market="IN")
        self.assertEqual(st["market"], "IN")
        self.assertTrue(st["open"])
        self.assertEqual(st["square_off"], "15:15")
        self.assertEqual(st["opens"], "09:15")
        self.assertEqual(st["closes"], "15:30")

    def test_rules_from_plan_generates_mis_square_off_alert(self):
        indian_plan = {
            "symbol": "RELIANCE.NS",
            "entry": 2500.0,
            "stop": 2470.0,
            "target": 2550.0,
            "flat_by": "15:15",
            "market": "IN",
        }
        rules = alerts.rules_from_plan(indian_plan)
        flat_rule = next(r for r in rules if r["type"] == "time")
        self.assertEqual(flat_rule["at"], "15:15")
        self.assertEqual(flat_rule["label"], "MIS square-off / flat by close")
        self.assertEqual(flat_rule["market"], "IN")

    def test_square_off_cli_flag_and_clock(self):
        # Test clock command output via CLI
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = alerts.main(["clock", "--market", "IN", "--now", "2026-10-19 10:30"])
        self.assertEqual(code, 0)
        val = out.getvalue()
        self.assertIn("NSE/BSE OPEN", val)
        self.assertIn("09:15 to 15:30 IST", val)
        self.assertIn("Intraday MIS square-off cutoff: 15:15 IST", val)


class TestIndianBudgetAndSizing(unittest.TestCase):
    def setUp(self):
        self.cfg = {
            "paper": True,
            "broker": "upstox",
            "market": "IN",
            "budget": 100000.0,
            "max_loss_per_trade_inr": 2000.0,
            "max_daily_loss_inr": 5000.0,
            "max_order_inr": 50000.0,
            "min_order_inr": 1000.0,
        }
        self.plan = {
            "symbol": "INFY.NS",
            "side": "buy",
            "entry": 1500.0,
            "stop": 1480.0,
            "target": 1540.0,
            "risk_per_share": 20.0,
            "reward_risk": 2.0,
        }

    def test_size_trade_inr(self):
        s = size.size_trade(self.plan, self.cfg, today="2026-10-19")
        self.assertEqual(s["currency"], "INR")
        self.assertTrue(s["is_indian"])
        # max order is 50,000 INR -> 50000 / 1500 = 33 shares (cost = 49,500)
        # max loss is 2,000 INR -> 2000 / 20 = 100 shares
        # bound by max order
        self.assertEqual(s["qty"], 33)
        self.assertEqual(s["cost"], 49500.0)
        self.assertEqual(s["max_loss"], 660.0)

    def test_bracket_order_with_indian_fields(self):
        s = size.size_trade(self.plan, self.cfg, today="2026-10-19")
        order = size.bracket_order(s)
        self.assertEqual(order["symbol"], "INFY.NS")
        self.assertEqual(order["exchange"], "NSE_EQ")
        self.assertEqual(order["product"], "I")
        self.assertEqual(order["currency"], "INR")
        self.assertEqual(order["instrument_token"], "NSE_EQ|INFY")
        self.assertIn("₹", order["why"])

    def test_round_tick_nse_equity(self):
        # NSE tick size is 0.05
        self.assertEqual(size.round_tick(1500.02, tick=0.05), 1500.00)
        self.assertEqual(size.round_tick(1500.03, tick=0.05), 1500.05)
        self.assertEqual(size.round_tick(1500.07, tick=0.05), 1500.05)
        self.assertEqual(size.round_tick(1500.08, tick=0.05), 1500.10)

    def test_is_indian_market_helpers(self):
        self.assertTrue(size.is_indian_symbol("NSE:TCS"))
        self.assertTrue(size.is_indian_symbol("BSE_EQ|INFY"))
        self.assertTrue(size.is_indian_market(cfg={"broker": "upstox"}))
        self.assertTrue(size.is_indian_market(cfg={"broker": "kite"}))
        self.assertTrue(size.is_indian_market(symbol="RELIANCE.NS"))
        self.assertFalse(size.is_indian_market(cfg={"broker": "alpaca"}, symbol="AAPL"))


class TestIndianRiskAndCircuitLimits(unittest.TestCase):
    def setUp(self):
        self.cfg = {
            "paper": True,
            "broker": "upstox",
            "market": "IN",
            "budget": 200000.0,
            "max_loss_per_trade_inr": 5000.0,
            "max_daily_loss_inr": 10000.0,
            "max_order_inr": 100000.0,
            "pdt_guard": True,  # should be skipped for Indian market!
        }
        self.now_ist = check.parse_now("2026-10-19 10:30", is_in=True)

    def _make_order(self, **overrides):
        base = {
            "symbol": "TCS.NS",
            "side": "buy",
            "qty": "10",
            "currency": "INR",
            "exchange": "NSE_EQ",
            "product": "I",
            "price": "3500.00",
            "type": "limit",
            "time_in_force": "day",
            "order_class": "bracket",
            "stop_loss_stop_price": "3450.00",
            "take_profit_limit_price": "3600.00",
            "upper_circuit": 3800.0,
            "lower_circuit": 3200.0,
            "plan": {"entry": 3500.0, "stop": 3450.0, "target": 3600.0},
        }
        base.update(overrides)
        return base

    def test_valid_indian_order_passes(self):
        order = self._make_order()
        res = check.check([order], self.cfg, now=self.now_ist)
        r = res["results"][0]
        self.assertEqual(r["verdict"], "PASS")
        self.assertEqual(r["reasons"], [])

    def test_upper_circuit_limit_breach_blocks(self):
        # Entry price 3850 above upper circuit 3800
        order = self._make_order(price="3850.00", plan={"entry": 3850.0, "stop": 3450.0, "target": 3950.0})
        res = check.check([order], self.cfg, now=self.now_ist)
        r = res["results"][0]
        self.assertEqual(r["verdict"], "BLOCK")
        self.assertTrue(any("circuit: entry" in reason and "upper circuit limit" in reason for reason in r["reasons"]))

    def test_target_above_upper_circuit_blocks(self):
        # Target 3900 above upper circuit 3800
        order = self._make_order(take_profit_limit_price="3900.00",
                                 plan={"entry": 3500.0, "stop": 3450.0, "target": 3900.0})
        res = check.check([order], self.cfg, now=self.now_ist)
        r = res["results"][0]
        self.assertEqual(r["verdict"], "BLOCK")
        self.assertTrue(any("circuit: target" in reason and "upper circuit limit" in reason for reason in r["reasons"]))

    def test_stop_below_lower_circuit_blocks(self):
        # Stop 3150 below lower circuit 3200
        order = self._make_order(stop_loss_stop_price="3150.00",
                                 plan={"entry": 3500.0, "stop": 3150.0, "target": 3600.0})
        res = check.check([order], self.cfg, now=self.now_ist)
        r = res["results"][0]
        self.assertEqual(r["verdict"], "BLOCK")
        self.assertTrue(any("circuit: stop" in reason and "lower circuit limit" in reason for reason in r["reasons"]))

    def test_locked_at_upper_circuit_blocks_buy(self):
        # Live quote equals upper circuit 3800 -> stock locked at circuit limit
        order = self._make_order()
        res = check.check([order], self.cfg, prices={"TCS.NS": 3800.0}, now=self.now_ist)
        r = res["results"][0]
        self.assertEqual(r["verdict"], "BLOCK")
        self.assertTrue(any("locked at upper circuit" in reason for reason in r["reasons"]))

    def test_nse_tick_size_validation(self):
        # 3500.03 is not a multiple of 0.05
        order = self._make_order(price="3500.03")
        res = check.check([order], self.cfg, now=self.now_ist)
        r = res["results"][0]
        self.assertEqual(r["verdict"], "BLOCK")
        self.assertTrue(any("multiple of the NSE tick size 0.05 INR" in reason for reason in r["reasons"]))

    def test_pdt_guard_bypassed_for_indian_market(self):
        # Even with 4 day trades in state, Indian market orders must not be blocked by US PDT rule
        state = {
            "fills": [
                {"date": "2026-10-14", "symbol": "INFY.NS", "side": "buy"},
                {"date": "2026-10-14", "symbol": "INFY.NS", "side": "sell"},
                {"date": "2026-10-15", "symbol": "TCS.NS", "side": "buy"},
                {"date": "2026-10-15", "symbol": "TCS.NS", "side": "sell"},
                {"date": "2026-10-16", "symbol": "WIPRO.NS", "side": "buy"},
                {"date": "2026-10-16", "symbol": "WIPRO.NS", "side": "sell"},
                {"date": "2026-10-19", "symbol": "HDFCBANK.NS", "side": "buy"},
                {"date": "2026-10-19", "symbol": "HDFCBANK.NS", "side": "sell"},
            ]
        }
        order = self._make_order()
        res = check.check([order], self.cfg, state=state, now=self.now_ist)
        r = res["results"][0]
        self.assertEqual(r["verdict"], "PASS")
        self.assertFalse(any("PDT" in s or "pattern day trader" in s for s in r["reasons"]))


class TestIndianBrokerTickets(unittest.TestCase):
    def test_upstox_tool_input_format(self):
        order = {
            "symbol": "RELIANCE.NS",
            "side": "buy",
            "qty": "25",
            "type": "limit",
            "price": "2500.00",
            "product": "I",
            "exchange": "NSE_EQ",
        }
        inp = ticket.upstox_tool_input(order)
        self.assertEqual(inp["quantity"], 25)
        self.assertEqual(inp["product"], "I")
        self.assertEqual(inp["validity"], "DAY")
        self.assertEqual(inp["price"], 2500.00)
        self.assertEqual(inp["order_type"], "LIMIT")
        self.assertEqual(inp["transaction_type"], "BUY")
        self.assertEqual(inp["instrument_token"], "NSE_EQ|RELIANCE")
        self.assertFalse(inp["is_amo"])

    def test_kite_tool_input_format(self):
        order = {
            "symbol": "TCS.BO",
            "side": "sell",
            "qty": "10",
            "type": "market",
            "product": "CNC",
            "exchange": "BSE_EQ",
        }
        inp = ticket.kite_tool_input(order)
        self.assertEqual(inp["tradingsymbol"], "TCS")
        self.assertEqual(inp["exchange"], "BSE")
        self.assertEqual(inp["quantity"], 10)
        self.assertEqual(inp["product"], "CNC")
        self.assertEqual(inp["order_type"], "MARKET")
        self.assertEqual(inp["transaction_type"], "SELL")

    def test_parse_yes_indian_stock(self):
        order = {
            "symbol": "RELIANCE.NS",
            "side": "buy",
            "qty": "15",
            "currency": "INR",
            "exchange": "NSE_EQ",
        }
        ok, msg = ticket.parse_yes("yes RELIANCE.NS", order)
        self.assertTrue(ok)
        self.assertIn("RELIANCE.NS", msg)

        ok, msg = ticket.parse_yes("yes 15 shares", order)
        self.assertTrue(ok)

        ok, msg = ticket.parse_yes("yes", order)
        self.assertTrue(ok)

        ok, _ = ticket.parse_yes("no wait", order)
        self.assertFalse(ok)

    def test_plain_ticket_rendering_inr(self):
        order = {
            "symbol": "INFY.NS",
            "side": "buy",
            "qty": "20",
            "type": "limit",
            "price": "1450.00",
            "currency": "INR",
            "exchange": "NSE_EQ",
            "product": "I",
            "order_class": "bracket",
            "stop_loss_stop_price": "1430.00",
            "take_profit_limit_price": "1490.00",
        }
        t_str = ticket.plain(order)
        self.assertIn("BUY 20 INFY.NS", t_str)
        self.assertIn("stop-loss 1430.00", t_str)
        self.assertIn("take-profit 1490.00", t_str)
        self.assertIn("₹", t_str)
        self.assertIn("NSE_EQ", t_str)


class TestIndianNewsAndAnalyst(unittest.TestCase):
    def test_news_indian_ticker_support(self):
        self.assertTrue(news.is_indian_ticker("RELIANCE.NS"))
        self.assertTrue(news.is_indian_ticker("TCS.BO"))
        self.assertFalse(news.is_indian_ticker("AAPL"))
        self.assertEqual(news.yahoo_symbol("RELIANCE.NS"), "RELIANCE.NS")
        self.assertEqual(news.yahoo_symbol("BRK.B"), "BRK-B")

    def test_parse_corporate_announcements(self):
        sample_announcements = {
            "data": [
                {
                    "symbol": "INFY.NS",
                    "company_name": "Infosys Limited",
                    "category": "Board Meeting",
                    "subject": "Consideration of Financial Results and Interim Dividend",
                    "broadcast_date_time": "2026-10-14 17:30:00",
                    "attachment_url": "https://www.nseindia.com/announcements/12345.pdf",
                },
                {
                    "symbol": "TCS.NS",
                    "company_name": "Tata Consultancy Services Limited",
                    "category": "Regulatory / SEBI",
                    "subject": "SEBI LODR Regulation 30 disclosure regarding contract win",
                    "broadcast_date_time": "2026-10-14 18:00:00",
                    "attachment_url": "https://www.nseindia.com/announcements/12346.pdf",
                },
            ]
        }
        items = news.parse_corporate_announcements(json.dumps(sample_announcements), "INFY.NS")
        self.assertEqual(len(items), 2)
        first = items[0]
        self.assertEqual(first["ticker"], "INFY.NS")
        self.assertEqual(first["form"], "Announcement")
        self.assertEqual(first["title"], "Consideration of Financial Results and Interim Dividend")

        second = items[1]
        self.assertEqual(second["ticker"], "INFY.NS")
        self.assertIn("contract win", second["title"])

    def test_facts_indian_fiscal_labels(self):
        # Indian fiscal year ends March 31:
        # April - June: Q1 FY (year + 1)
        # July - Sept: Q2 FY (year + 1)
        # Oct - Dec: Q3 FY (year + 1)
        # Jan - March: Q4 FY (year)
        self.assertEqual(facts._indian_fiscal("2026-06-30"), "Q1 FY27")
        self.assertEqual(facts._indian_fiscal("2026-09-30"), "Q2 FY27")
        self.assertEqual(facts._indian_fiscal("2026-12-31"), "Q3 FY27")
        self.assertEqual(facts._indian_fiscal("2027-03-31"), "Q4 FY27")

    def test_facts_analyse_indian(self):
        mock_doc = {
            "financials": [
                {"endDate": "2026-06-30", "totalRevenue": 40000.0, "grossProfit": 14000.0,
                 "operatingIncome": 9500.0, "netIncome": 6500.0, "dilutedEPS": 15.5},
                {"endDate": "2026-03-31", "totalRevenue": 38000.0, "grossProfit": 13300.0,
                 "operatingIncome": 9000.0, "netIncome": 6100.0, "dilutedEPS": 14.5},
                {"endDate": "2025-12-31", "totalRevenue": 39000.0, "grossProfit": 13650.0,
                 "operatingIncome": 9200.0, "netIncome": 6300.0, "dilutedEPS": 15.0},
                {"endDate": "2025-09-30", "totalRevenue": 37000.0, "grossProfit": 12950.0,
                 "operatingIncome": 8800.0, "netIncome": 6000.0, "dilutedEPS": 14.2},
                {"endDate": "2025-06-30", "totalRevenue": 35000.0, "grossProfit": 12250.0,
                 "operatingIncome": 8200.0, "netIncome": 5600.0, "dilutedEPS": 13.3},
            ]
        }
        res = facts.analyse_indian(mock_doc, quarters=4, ticker="INFY.NS", name="Infosys Ltd")
        self.assertEqual(res["market"], "IN")
        self.assertEqual(res["currency"], "INR")
        self.assertEqual(res["entity"], "Infosys Ltd")
        self.assertEqual(len(res["rows"]), 4)

        q1 = res["rows"][0]
        self.assertEqual(q1["end"], "2026-06-30")
        self.assertEqual(q1["fiscal"], "Q1 FY27")
        self.assertEqual(q1["revenue"], 40000.0)
        self.assertEqual(q1["gross_margin"], 35.0)      # 14000 / 40000
        self.assertEqual(q1["operating_margin"], 23.8)  # 9500 / 40000
        self.assertEqual(q1["net_margin"], 16.2)        # 6500 / 40000
        # YoY vs 2025-06-30: (40000 - 35000) / 35000 = 14.3%
        self.assertEqual(q1["revenue_yoy"], 14.3)

        # TTM check (sum of latest 4 quarters: 40000 + 38000 + 39000 + 37000 = 154000)
        self.assertEqual(res["ttm"]["revenue"], 154000.0)
        self.assertEqual(res["ttm"]["net_income"], 24900.0)


class TestIndianInsiderAndDeals(unittest.TestCase):
    def test_parse_indian_deals(self):
        sample_deals = {
            "data": [
                {
                    "symbol": "RELIANCE",
                    "company_name": "Reliance Industries Limited",
                    "client_name": "Morgan Stanley Asia Singapore",
                    "buy_sell": "BUY",
                    "kind": "BULK",
                    "quantity": 1000000,
                    "trade_price": 2550.0,
                    "date": "2026-10-14",
                    "exchange": "NSE",
                }
            ]
        }
        deals = ptr.parse_indian_deals(json.dumps(sample_deals))
        self.assertEqual(len(deals), 1)
        d = deals[0]
        self.assertEqual(d["symbol"], "RELIANCE.NS")
        self.assertEqual(d["reg"], "Bulk Deal")
        self.assertEqual(d["type"], "purchase")
        self.assertEqual(d["quantity"], 1000000)
        self.assertEqual(d["price"], 2550.0)
        self.assertEqual(d["market"], "IN")

    def test_parse_sebi_insider_timely_and_late(self):
        sample_insider = {
            "disclosures": [
                {
                    "symbol": "TCS.NS",
                    "company": "Tata Consultancy Services Limited",
                    "person_name": "Tata Sons Private Limited",
                    "category": "Promoter",
                    "type": "BUY",
                    "quantity": 50000,
                    "trade_date": "2026-10-12",
                    "filing_date": "2026-10-13",  # filed in 1 day: timely!
                    "mode": "Market Purchase",
                },
                {
                    "symbol": "INFY.NS",
                    "company": "Infosys Limited",
                    "person_name": "Executive Director",
                    "category": "Director",
                    "type": "SELL",
                    "quantity": 20000,
                    "trade_date": "2026-10-05",
                    "filing_date": "2026-10-12",  # filed in 7 days: late!
                    "mode": "ESOP Sale",
                },
            ]
        }
        records = ptr.parse_sebi_insider(json.dumps(sample_insider))
        self.assertEqual(len(records), 2)

        timely = records[0]
        self.assertEqual(timely["type"], "purchase")
        self.assertFalse(timely["late"])
        self.assertEqual(timely["days_to_disclose"], 1)

        late = records[1]
        self.assertEqual(late["type"], "sale")
        self.assertTrue(late["late"])
        self.assertEqual(late["days_to_disclose"], 7)

    def test_describe_indian_deal_and_insider(self):
        deal = {
            "market": "IN",
            "reg": "Bulk Deal",
            "symbol": "RELIANCE.NS",
            "client": "Morgan Stanley",
            "type": "purchase",
            "quantity": 1000000,
            "price": 2550.0,
            "value": 2550000000.0,
            "date": "2026-10-14",
        }
        desc = ptr.describe_indian(deal)
        self.assertIn("RELIANCE.NS", desc)
        self.assertIn("Morgan Stanley", desc)
        self.assertIn("₹", desc)
        self.assertIn("Bulk", desc)


if __name__ == "__main__":
    unittest.main()
