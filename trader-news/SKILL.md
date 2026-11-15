---
name: trader-news
description: >-
  News Monitor. Tracks breaking news on every stock you watch and flags what
  moves it: Yahoo Finance headlines (US and India), Indian financial news
  (Economic Times, Moneycontrol, Livemint), BSE and NSE corporate announcements,
  and SEC EDGAR 8-K company filings (earnings, CEO changes, deals, restatements)
  with price moves vs yesterday's close. Shows only what is new since the last
  check, newest first, in local time (ET for US, IST for India), tags each item
  (earnings, analyst, deal, halt, regulatory, macro, insider) and never repeats one.
  Use when the user says "any news on TSLA", "why is Reliance moving", "what's the news
  on my watchlist", "morning briefing", "pre-market news", "check BSE announcements",
  "monitor the news", or after /trader-scanner finds setups.
argument-hint: "[tickers] [--market US|IN]"
---

# trader-news

A chart can look perfect and still be about to gap on an earnings date, a
halt, a board meeting or a regulatory action. Before a setup becomes a trade,
this skill reads the feeds that carry those first, for every stock on the
watchlist, and tells you only what is new. It also prints how far each stock
has moved today, so a headline and a move sit side by side.

Supports US stocks (NYSE/Nasdaq) and Indian stocks (NSE/BSE).

Tool in this folder (standard library Python, no keys):

```bash
# Watchlist + plan + portfolio:
python3 "${CLAUDE_SKILL_DIR}/news.py"

# US tickers:
python3 "${CLAUDE_SKILL_DIR}/news.py" --tickers TSLA NVDA --hours 12
python3 "${CLAUDE_SKILL_DIR}/news.py" --no-sec --json

# Indian tickers (NSE / BSE):
python3 "${CLAUDE_SKILL_DIR}/news.py" --tickers RELIANCE.NS TCS.NS HDFCBANK.NS
python3 "${CLAUDE_SKILL_DIR}/news.py" --tickers RELIANCE INFY --market IN
```

## What it reads and writes

| file / feed | what |
| --- | --- |
| `--tickers`, else `trader/watchlist.json` | scanner hits (trader-scanner) |
| `trader/plan.json` | trade you are planning (trader-exit) |
| `trader/portfolio.json` | what you hold (trader-broker records fills there) |
| Yahoo Finance headline RSS | US (`region=US`) or India (`region=IN`, `.NS`/`.BO`) |
| Indian Financial Feeds | Economic Times Markets, Moneycontrol, Livemint |
| Exchange Corporate Announcements | BSE and NSE corporate filings and board meeting disclosures |
| SEC EDGAR company Atom feed | 8-K by default (`--forms 8-K,10-Q` for US filers) |
| `trader/state/news.json` | what has been shown (kept 30 days) and state |

## The rules

- **Headlines are leads, not facts.** Before telling the user something
  happened, open the story or filing and read it. Quote the source and link it.
  Never summarise a paywalled story you could not read: say so.
- **Filings beat headlines.** If an exchange filing (SEC 8-K or BSE/NSE corporate
  announcement) and a headline disagree, the regulatory filing is official.
- **Say what it touches.** Every item lists tickers it touches and tags:
  earnings, analyst, legal, deal, capital, people, risk, halt, regulatory,
  macro, insider.
- **MOVING** marks a stock 3 % or more from yesterday's close (`--move-pct`).
  A move with no headline is worth saying out loud too.
- **No predictions.** Report what was published and how far the price is from
  yesterday's close. Never say what the stock will do next.

## Steps

1. Run the check: `python3 "${CLAUDE_SKILL_DIR}/news.py"`. Exit code 1 means
   nothing new. `--all` shows items already seen, `--json` gives machine-readable
   output, `--dry-run` leaves state unchanged.
2. Read the items that matter: every important filing, anything tagged earnings,
   halt, legal, regulatory or risk, and every MOVING ticker first.
3. Write a short brief, per ticker: what happened, source and link, price move,
   and one line on why it matters to the setup.
4. If a story changes the picture, say so before `/trader-entry` and
   `/trader-exit`: an earnings date inside the trade, a board meeting, a halt.
   Never act on a headline alone.

## What it never does

- Never invents or embellishes a story, a quote or a number.
- Never trades on news, and never tells the user to buy or sell because of a
  headline.
- Never hammers a server: polite pauses between requests.

## Safety

Education and tooling, not financial advice. This skill only reads and
reports. Nothing places an order except `/trader-broker`, after
`/trader-risk` passes and the user types an explicit yes for that order.
