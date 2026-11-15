---
name: trader-scanner
description: >-
  Chart Scanner. Scans thousands of charts for setups: opening-range
  breakouts, flags, oversold bounces across US (NYSE/Nasdaq) and Indian
  (NSE/BSE) markets. Builds symbol universes from Nasdaq Trader directories or
  Indian market presets (Nifty 50, Nifty Bank, Nifty Next 50, Nifty 100, Nifty
  500, liquid F&O), runs pre-market scans of daily closes (20-day breakout, bull
  flag, 20-day MA reclaim, oversold, bounce) and intraday opening-range breakout
  scans on 5-minute bars (09:30-09:45 ET for US, 09:15-09:30 IST for India), then
  writes trader/watchlist.json for trader-news and trader-entry. Free Yahoo data.
  This skill should be used when the user says "scan the market", "find me setups", "what is breaking out",
  "opening range breakouts", "scan Nifty", "scan Indian market", "pre-market scan",
  "build my watchlist", or at the start of a trading day before /trader-news.
argument-hint: "[universe | daily | orb] [--preset PRESET] [--market US|IN]"
---

# trader-scanner

The Chart Scanner is the first step of a day trade. It reads thousands of
free charts and lists the ones that match a rule. That is all it does. A scan
is a list of charts that match a rule, not a list of stocks to buy, and a
setup on a chart is not a prediction. Most people who day trade lose money;
the scanner does not change that. It only saves you from flipping through
thousands of charts by hand.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
# US Market:
python3 "${CLAUDE_SKILL_DIR}/scan.py" universe                       # -> trader/universe.txt
python3 "${CLAUDE_SKILL_DIR}/scan.py" daily                          # before the open -> trader/watchlist.json
python3 "${CLAUDE_SKILL_DIR}/scan.py" daily --symbols TSLA,NVDA,AMD  # or --watchlist FILE, --limit 200
python3 "${CLAUDE_SKILL_DIR}/scan.py" orb                            # after 9:50 AM ET -> trader/watchlist.json
python3 "${CLAUDE_SKILL_DIR}/scan.py" orb --at 10:15                 # cut the clock at 10:15 AM ET

# Indian Market (NSE / BSE):
python3 "${CLAUDE_SKILL_DIR}/scan.py" universe --preset nifty50      # Nifty 50 -> trader/universe.txt
python3 "${CLAUDE_SKILL_DIR}/scan.py" daily --preset nifty50         # Nifty 50 pre-market scan
python3 "${CLAUDE_SKILL_DIR}/scan.py" daily --symbols RELIANCE,TCS,INFY # Indian tickers (.NS auto-resolved)
python3 "${CLAUDE_SKILL_DIR}/scan.py" orb --preset niftybank         # Nifty Bank ORB (09:15-09:30 IST range)
python3 "${CLAUDE_SKILL_DIR}/scan.py" orb --at 10:00 --market IN     # 10:00 AM IST cutoff
```

Flags: `--min-price` and `--min-volume` (US: $5 and 1,000,000; India: Rs. 5 and 100,000),
`--preset` (`nifty50`, `niftybank`, `niftynext50`, `nifty100`, `nifty500`, `fno`),
`--market` (`US` or `IN`, auto-detected when symbols or presets are given),
`--or-minutes 15`, `--max-confirm 200` (orb stage 2),
`--now "2026-10-19 10:15"` to pin the clock, `--offline PATH`
to read saved Yahoo files, `--no-write`, `--show N`, `--json`. Exit code 0
when the scan found something, 1 when it found nothing or could not run.

## What it reads and writes

| file | what |
| --- | --- |
| `trader/universe.txt` | written by `universe`: one `SYMBOL\|Name` a line, read by `daily` and `orb` |
| `trader/watchlist.json` | written by `daily` and `orb`: `mode`, `as_of`, `universe`, `scanned`, `failed`, `hits[]`. trader-news and trader-entry read `hits[].symbol` |
| `trader/config.json` | read by `orb`: `market` (US or IN), `no_entries_after` (default 15:30 US, 15:15 IN) |

## The universe

For US markets, `universe` downloads `nasdaqlisted.txt` and `otherlisted.txt` from
nasdaqtrader.com/dynamic/SymDir and filters common stocks and ADRs.

For Indian markets, `universe --preset PRESET` builds clean universes:
- `nifty50`: Top 50 blue chips of the National Stock Exchange of India (NSE).
- `niftybank`: 12 banking institutions driving Indian financial markets.
- `niftynext50`: 50 next large-cap companies.
- `nifty100`: Nifty 50 and Nifty Next 50 combined.
- `nifty50`, `niftybank`, `niftynext50`, `nifty100`, `nifty500`: read live from NSE's public index CSVs (cached a day, no login). If NSE can't be reached the bundled lists are used, and `nifty500` then warns it is only Nifty 100.
- `fno`: every stock with futures today (about 218), read live from Upstox's public instrument master; falls back to an incomplete bundled list with a warning.

Indian symbols are normalized with `.NS` for NSE (e.g. `RELIANCE.NS`, `TCS.NS`,
`HDFCBANK.NS`) and `.BO` for BSE. Index symbols like `NIFTY50` map to `^NSEI` and
`BANKNIFTY` to `^NSEBANK` on Yahoo Finance.

## The daily scan (before the open)

Yahoo spark endpoint returns closes for 20 symbols a request. Liquidity comes
from the spark meta: last price at least `--min-price` and volume at least
`--min-volume`. Run it before the open or after the close:
- US session: closes through 16:00 ET.
- Indian session: closes through 15:30 IST.

The setups are computed from daily closes:

| setup | the rule on closes |
| --- | --- |
| `breakout_20d` | the last close is above every close of the 20 sessions before it |
| `bull_flag` | up more than 10 % over the 10 sessions before the last 5, then the last 5 closes inside a 5 % range |
| `reclaim_ma20` | the close before was under its 20-day average, the last close is above it |
| `oversold` | RSI 14 (Wilder) under 30 |
| `oversold_bounce` | RSI 14 was under 30 yesterday and today closed up |

Hits are listed most traded first (price times volume).

## The opening-range breakout scan (during the session)

- US: The opening range is the first `--or-minutes` (15) of the session (09:30 to 09:45 ET).
- India: The opening range is 09:15 to 09:30 IST.
A hit is the first completed 5-minute bar to CLOSE above the range high, at that bar's close time.

1. Stage 1, every symbol: spark closes of today's 5-minute bars, 20 a request.
   A chart goes on only if a completed bar after the range closed at or above
   the highest close of the range.
2. Stage 2, each candidate: the v8 chart with real highs and lows.
   Checks at most `--max-confirm` (200) candidates, most traded first.

A chart is usable only when every 5-minute bar from market open to the cutoff is
present. Before the range ends, on a weekend or holiday, after the close, or after
`no_entries_after` (default 15:30 US, 15:15 IST India for intraday MIS square-off),
it prints one plain line and writes nothing.

## Steps

1. Build or select the universe:
   `scan.py universe` for US, or `scan.py universe --preset nifty50` for India.
2. Before the open: `scan.py daily`. Tell the user the counts and top hits.
3. During the session (after 09:50 ET for US, or after 09:35 IST for India): `scan.py orb`.
4. The user picks one chart. Hand it on: `/trader-news TICKER`, then `/trader-entry TICKER`.

## Rules

- Say what was scanned: how many symbols, how many charts came back, how many
  were usable, and the time of the data.
- Say "matched a rule", never "a buy", "a winner" or "a good trade".
- Never print a P&L, a return or a profit figure.
- A hit is stale once the price has moved on. Re-run before acting on it.

## Safety

- Education and tooling, not financial advice. A PASS is a rules check, not a recommendation.
- Paper trading by default (`"paper": true`). Going live is the user's own decision, in their own config.
- Nothing in this pack places an order except trader-broker, and only when all three hold: trader-risk
  passed that exact order today, the user typed an explicit yes for that one order in the chat, and the
  user clicked Allow once on Claude's own card because the order tool is set to "Needs approval"
  (Customize > Connectors > tool permissions). Never set an order tool to "Always allow".
- No options, margin, short selling or leverage unless the config allows it and the user confirms.
- Broker keys live in environment variables or the Claude Desktop config, never in the chat.
