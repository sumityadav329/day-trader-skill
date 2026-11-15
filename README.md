# The Day Trader skill

11 Claude skills for day trading: scan thousands of charts, track breaking news, time every entry and exit, and replay every outcome before you risk a dollar.

Free, MIT, no signup, no API key. The tools are plain Python that read free
public data: Yahoo Finance charts, Nasdaq Trader's symbol lists, SEC EDGAR
and the House Clerk.

One scans thousands of charts for setups. Another tracks breaking news on
every stock you watch. One watches a setup bar by bar and tells you the moment
to get in, and another sets your stop and your target before you do. And the
craziest one is the Time Machine: before you risk a dollar, it replays the
rest of the session 10,000 times from the stock's own recent bars, plus every
past day the same setup fired, and tells you the honest odds.

And no trade goes through until you say yes. Nothing in this pack can place
an order on its own. Paper trading is the default.

> **Looking for the original Trader skill?** Copy an investor's 13F, the
> stock screener, tax lots and rebalancing live in
> [github.com/Jakeschincariol/trader-skill](https://github.com/Jakeschincariol/trader-skill).
> This repo is its day-trading sequel: the Trader skill v2.

## Install

Paste this into Claude:

```
https://github.com/sumityadav329/day-trader-skill

install skill
```

Or as a plugin, in Claude Code:

```
/plugin marketplace add sumityadav329/day-trader-skill
/plugin install day-trader-skill@day-trader-skill
```

Claude Code namespaces plugin skills, so installed as a plugin they show up as
`/day-trader-skill:trader-scanner` and so on. Copy the folders instead if you want
plain `/trader-scanner`:

```bash
git clone https://github.com/sumityadav329/day-trader-skill.git
cp -r day-trader-skill/trader-* ~/.claude/skills/
```

Project-local instead of global: copy the same folders into your repo's
`.claude/skills/`. No Claude Code at all? Paste any single `SKILL.md` at the
top of a chat and it runs as a mode. You lose the Python tools, but the method
works.

The tools need Python 3.8 or newer. Nothing to pip install. The SEC tools
(news filings, the analyst) ask you to identify yourself: put your name and
email in `trader/config.json` (`"sec_user_agent": "Your Name you@example.com"`)
or in the `SEC_USER_AGENT` environment variable.

## The eleven

| command | job | what it does |
| --- | --- | --- |
| `/trader-scanner` | Chart Scanner | Scans thousands of charts for setups: opening-range breakouts, flags, oversold bounces. |
| `/trader-news` | News Monitor | Tracks breaking news on every stock you watch and flags what moves it. |
| `/trader-entry` | Entry Timer | Watches a setup bar by bar and tells you the moment to get in. |
| `/trader-exit` | Exit Planner | Sets your stop and your target before you enter, so you know when to get out. |
| `/trader-timemachine` | Time Machine | Replays every possible outcome of a trade before you risk a dollar: 10,000 simulated sessions plus every past time the setup fired. |
| `/trader-risk` | Risk Manager | Checks every trade against your limits and stops anything over them. |
| `/trader-budget` | Budget Manager | Sizes every position to your budget and your max loss per trade. |
| `/trader-broker` | Order Executor | Sends approved orders to your broker, paper trading by default, never without your yes. |
| `/trader-alerts` | Price Alerts | Rings when a price, a stop or the opening bell hits. |
| `/trader-analyst` | Earnings Analyst | Reads earnings reports and sums up what changed. |
| `/trader-pelosi` | Pelosi Tracker | Reads Congress's stock-trade disclosures the minute they are filed. |

## How to use it

Everything lands in a `trader/` folder in your project, and each skill reads
what the last one wrote:

```
scanner -> news -> entry -> exit -> timemachine -> budget -> risk -> broker   (one trade)
alerts: stop, target, flat by the close                                       (while it is open)
news / analyst / pelosi                                                        (on a schedule)
```

An example, the trade in the reel: Tesla, Monday October 19 2026, real data.

1. **`/trader-scanner`** at 10:15 AM ET: of the 231 charts in the reel's
   scan, 42 had closed above their 15-minute opening range. TSLA is one: range 354.47 to 362.18,
   the 10:10 candle closed at 364.18, 0.55 % over the top.
2. **`/trader-news`**: the morning's TSLA headlines, in New York time
   ("Tesla-SpaceX Merger Mania Strikes Wall Street Ahead of Earnings", 8:04,
   tagged earnings and deal). Tesla reports this week: a day trade is out
   before the close, so it is not exposed to the report.
3. **`/trader-entry`** watched TSLA bar by bar. At 10:12 it said waiting (the
   10:10 bar was still forming). At 10:15 it said triggered: the first
   5-minute close above the range, 364.18.
4. **`/trader-exit`** set the plan before the entry: stop at the range's
   midpoint, 358.32, so 5.86 a share at risk; target one times that risk,
   370.04; flat by 3:55 PM either way.
5. **`/trader-timemachine`** replayed the rest of the day 10,000 times, each
   5-minute candle drawn from TSLA's own candles at that time of day over the
   previous 59 sessions (seed 19, so anyone can repeat it): target first
   29.4 %, stop first 25.5 %, still open at the close 45.1 %. It also re-ran
   the same rule on those 59 real sessions: it fired 33 times, 13 hit the
   target, 15 the stop, 5 neither. Its verdict: close to a coin flip. That is
   the point of looking before you risk a dollar.
6. **`/trader-budget`** with a $5,000 account and a $50 max loss per trade:
   50 / 5.86 = 8 shares ($2,913.44). If the stop fills at 358.32, the loss is
   $46.88. It writes one bracket order: buy 8 TSLA, stop-loss 358.32,
   take-profit 370.04.
7. **`/trader-risk`** checks it against your limits: the max loss, the daily
   loss limit, trades per day, reward to risk, the entry window, position
   size and cash. PASS or BLOCK, with the reason.
8. **`/trader-broker`** shows you the ticket in plain words. You type yes for
   that exact order. Claude calls your brokerage connector (Alpaca's MCP
   server, paper account by default), and Claude's own "Allow once / Deny"
   card asks you one more time.
9. **`/trader-alerts`** rings on the stop, the target and at 3:55 PM to
   flatten whatever is still open.

## The tools

All standard-library Python. The data tools read public endpoints and are
polite about it.

```bash
python3 trader-scanner/scan.py universe                                # ~5,900 US stocks, Nasdaq Trader
python3 trader-scanner/scan.py daily                                   # pre-market setups, whole universe
python3 trader-scanner/scan.py orb --at 10:15                          # opening-range breakouts so far
python3 trader-news/news.py --tickers TSLA --hours 12                  # headlines, 8-Ks, today's move
python3 trader-entry/entry.py check TSLA --setup orb                   # waiting / triggered / expired
python3 trader-entry/entry.py watch TSLA --every 30                    # bar by bar until it triggers
python3 trader-exit/exit.py plan --stop or-mid --target 1R --write     # -> trader/plan.json
python3 trader-timemachine/timemachine.py run --write                  # 10,000 replays + the history
python3 trader-budget/size.py --write                                  # -> trader/orders.json
python3 trader-risk/check.py                                           # PASS / BLOCK per order
python3 trader-broker/ticket.py show                                   # the tickets, READY or not
python3 trader-alerts/alerts.py add --from-plan                        # stop, target, flat by close
python3 trader-alerts/alerts.py check                                  # rings what hit, once
python3 trader-analyst/facts.py TSLA                                   # quarters, growth, EPS
python3 trader-pelosi/ptr.py index --member pelosi                     # her PTRs this year
```

## Indian market support (NSE / BSE & Upstox)

All 11 skills support the Indian stock market (NSE and BSE) and Indian brokers such as Upstox:

```bash
# Universe and scanning (Nifty 50, Nifty Bank, Nifty Next 50, Nifty 100, Nifty 500, F&O)
python3 trader-scanner/scan.py universe --preset nifty50
python3 trader-scanner/scan.py orb --preset nifty50 --market IN

# News and corporate announcements (Moneycontrol, Economic Times, Livemint, NSE/BSE)
python3 trader-news/news.py --tickers RELIANCE.NS,TCS.NS --market IN

# Bar-by-bar entry and exit planning (IST session 09:15 to 15:30, 15:15 MIS cutoff)
python3 trader-entry/entry.py check RELIANCE.NS --setup orb --market IN
python3 trader-exit/exit.py plan --stop or-mid --target 1R --market IN --write

# Indian session Time Machine (75 five-minute bars / 375 minutes per trading day)
python3 trader-timemachine/timemachine.py run --market IN --write

# Position sizing in INR (tick size 0.05, Indian Rupee ₹)
python3 trader-budget/size.py --config trader-budget/config.upstox.example.json --write

# Risk management with daily circuit bands (5%, 10%, 20%) and SEBI peak margin rules
python3 trader-risk/check.py --market IN

# Order execution ticket for Upstox API v2 and Indian brokers (MIS/CNC product types)
python3 trader-broker/ticket.py show

# Indian market alerts: 09:15 opening bell, 15:15 MIS auto-square-off alert, 15:30 close
python3 trader-alerts/alerts.py clock --market IN
python3 trader-alerts/alerts.py add --from-plan

# Quarterly financial results under SEBI LODR Regulation 33 (in ₹ Crores)
python3 trader-analyst/facts.py RELIANCE.NS --market IN

# SEBI SAST, PIT Insider Trading disclosures, and NSE/BSE Bulk & Block Deals
python3 trader-pelosi/ptr.py deals --symbol RELIANCE.NS
python3 trader-pelosi/ptr.py insider --symbol RELIANCE.NS
```

```bash
python3 -m unittest discover -s tests -v
```

The tests run offline, on fixtures cut from real data: TSLA's 5-minute bars
for the 60 days to October 19 2026, the 10:15 AM opening-range scan, Yahoo's
TSLA headlines that morning, the House Clerk's 2026 index, real PTR text and
real XBRL facts. They reproduce every number in the example above.

## Fine print

**Not financial advice.** This is software that reads public data and does
arithmetic. It does not know your situation and it cannot see the future.
Most people who day trade lose money. You can lose money. Decide for
yourself, and talk to a licensed professional if you need advice.

**No trade goes through until you say yes.** Only `/trader-broker` can lead to
an order, and only after `/trader-risk` passes that exact order and you type
yes for it in the chat. Set your brokerage tool to "Needs approval" in Claude
(Customize, Connectors, tool permissions), never "Always allow", so Claude's
own "Allow once / Deny" card is a second check. No options, margin, short
selling or leverage unless you turn them on.

**Paper first.** `"paper": true` is the default in `config.example.json`, and
Alpaca's MCP server defaults to its paper account. Keys live in environment
variables, never in the chat.

**The Time Machine is a simulation, not a prediction.** It is built from the
stock's own last 60 days of 5-minute bars. The future can do things those 60
days never did: an earnings report, a halt, a headline. Treat its odds as a
reality check, never as a promise.

**A stop is a plan, not a guarantee.** In a fast market or across a gap, a
stop order can fill well past its price. The max loss shown is the loss at
the stop price.

**The data is free and unofficial.** Yahoo Finance's chart endpoint is not an
official API: it can lag, change or rate-limit you. The scanner and the entry
timer read 1-minute or 5-minute bars, not every tick. Check anything that
matters against your broker before you act on it.

**Day-trading rules.** FINRA removed the $25,000 pattern-day-trader minimum
on June 4, 2026 (Regulatory Notice 26-10), and brokers may phase the change
in until October 20, 2027, so yours may still apply the old rule. If it does,
set `"pdt_guard": true` and `/trader-risk` counts your day trades.

**Congress's trades are public, not inside information.** Members must disclose
trades over $1,000 within 45 days (the STOCK Act). The Pelosi skill reads those
public filings the minute they post.

**Taxes.** Day-trading gains are short-term gains, and frequent trading makes
wash sales likely. Nothing here is tax advice.

## Files

```
trader-scanner/     SKILL.md, scan.py
trader-news/        SKILL.md, news.py
trader-entry/       SKILL.md, entry.py
trader-exit/        SKILL.md, exit.py
trader-timemachine/ SKILL.md, timemachine.py
trader-risk/        SKILL.md, check.py
trader-budget/      SKILL.md, size.py, config.example.json, targets.example.json
trader-broker/      SKILL.md, ticket.py, claude_desktop_config.example.json
trader-alerts/      SKILL.md, alerts.py, alerts.example.json
trader-analyst/     SKILL.md, facts.py, metrics.example.json
trader-pelosi/      SKILL.md, ptr.py
tests/              unittest suite + offline fixtures
```

MIT. Made by [Jake Schincariol](https://opusjake.ai).

## Indian market: data sources and known limits

Forked from [Jakeschincariol/day-trader-skill](https://github.com/Jakeschincariol/day-trader-skill) (MIT) and extended for NSE/BSE and Upstox.

- **Instruments:** ticket and sizing resolve ISIN keys, tick size, lot and freeze quantity from Upstox's public instrument master (cached in `trader/`). `NSE_EQ|SYMBOL` keys are rejected by Upstox and never emitted.
- **Public by default, Upstox optional.** Everything works on public data with no login: Yahoo candles, Upstox's public instrument list, NSE's public data. Set `UPSTOX_ACCESS_TOKEN` in the environment to upgrade the entry timer to live Upstox candles and to read each stock's circuit band; any failure falls back to Yahoo. Don't trust Yahoo NSE candles for opening-range breakouts: on 2026-11-13 RELIANCE's 15-minute range high was ₹1333.40 on Yahoo and ₹1336.60 on Upstox, and the signal fired a bar apart.
- **Candles:** Yahoo 5-minute bars drop a few NSE bars every day; the time machine forward-fills up to 4 holes. Replays end at the 15:15 MIS square-off.
- **Costs:** sizing shows brokerage, STT, GST and fees with default rates (override `charges` in config; verify against Upstox's calculator), and refuses a trade whose reward:risk falls under your minimum *after* costs.
- **NSE MCP** (`mcp.nseindia.in`): daily history, live snapshot, corporate actions. No intraday candles, no orders; NSE says informational use only. Config is in `.mcp.json` one folder up (the Upstox project root), not in this repo; restart Claude Code to load it.
- **Circuit band:** read per stock when a token is set (RELIANCE is ±10 %, not 20 %); without one it is reported as unchecked, never assumed.
- **Index lists:** Nifty 50/Bank/Next 50/100/500 come live from NSE's public CSVs; F&O from the Upstox master.
- **Holidays:** `trader-entry` says when NSE is shut (calendar covers 2026–2027, from `trader-alerts`).
- **Not available:** live NSE bulk/block deals and SEBI insider feeds (NSE blocks scripts; download NSE's deals CSV by hand and pass `--offline FILE`, `trader-pelosi` says so otherwise), Indian financials in `trader-analyst` (Yahoo returns 401; it now says where to read results).

## First day on the real market (paper only)

1. Log in to Upstox and export the fresh token (it expires about 03:30 IST daily): `set -a; . ../.env; set +a`. Without it everything still runs on public Yahoo data, with the weaker opening-range candles.
2. 09:00: `python3 trader-scanner/scan.py daily --preset nifty100 --market IN` for the watchlist, then `trader-news` on the picks.
3. After 09:30: `python3 trader-entry/entry.py check SYMBOL --market IN`. The first line must say `Upstox (live)`. Run once more with the token unset and compare the opening range: that is the Yahoo-versus-Upstox gap measured on a live session.
4. On a trigger: `trader-exit`, `trader-timemachine`, `trader-budget` (check the net-of-costs line and the circuit band), `trader-risk`, then `trader-broker show`. Keep `paper: true` and `UPSTOX_ALLOW_LIVE_ORDERS` off. Read the entry and stop payloads; place nothing live.
5. Note anything that disagrees with the Upstox app (price, tick, band, costs) and fix it before any real order.
