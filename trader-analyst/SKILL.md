---
name: trader-analyst
description: >-
  The Earnings Analyst. Reads reported financial numbers straight from regulatory
  filings (SEC EDGAR XBRL company facts for US filers, and NSE/BSE corporate
  filings under SEBI LODR Regulation 33 for Indian companies) and sums up what
  changed: revenue, gross, operating and net margins, net income and diluted EPS,
  quarter by quarter, each against the same quarter a year earlier, with trailing
  twelve months (TTM) in USD or INR Crores. Reads earnings call transcripts when
  pasted. This skill should be used when the user says "how did Tesla's earnings look", "analyze Reliance
  results", "what changed this quarter", "is revenue growing", "check the 10-Q",
  "check SEBI results", "compare margins", or before trading a stock around earnings.
argument-hint: "[tickers] [--market US|IN]"
---

# trader-analyst

Earnings move stocks more than anything else on the calendar, and the press
release is written to put the best number first. This skill reads the numbers
the company actually filed with regulators, lines them up quarter by quarter,
and says plainly what changed. For a day trader it answers two questions before
a trade: what did the last report say, and is the next one inside this trade?

Supports US companies (SEC EDGAR) and Indian companies (NSE/BSE).

Tool in this folder (standard library Python, no keys):

```bash
# US companies:
python3 "${CLAUDE_SKILL_DIR}/facts.py" AAPL
python3 "${CLAUDE_SKILL_DIR}/facts.py" TSLA NVDA AMD --metrics trader/metrics.json

# Indian companies (NSE / BSE):
python3 "${CLAUDE_SKILL_DIR}/facts.py" RELIANCE.NS TCS.NS
python3 "${CLAUDE_SKILL_DIR}/facts.py" RELIANCE INFY --market IN
```

For US filers, it uses the SEC User-Agent (`sec_user_agent` in `trader/config.json`,
or `SEC_USER_AGENT`): "Your Name you@example.com". Indian corporate filings do
not require SEC credentials.

## What it reads and writes

- US filers: Reads `data.sec.gov/api/xbrl/companyfacts/CIK##########.json`, the
  company's submissions list (for newest 10-Q or 10-K and 8-K item 2.02) and
  finds the earnings press release (EX-99.1 exhibit).
- Indian filers: Reads quarterly financial results filed with NSE/BSE under
  Regulation 33 of SEBI (Listing Obligations and Disclosure Requirements)
  Regulations, 2015, and exchange financial data.
- Writes `trader/metrics.json` with `--metrics`: one row per company (TTM
  revenue, net income and diluted EPS, latest margins and growth in USD or INR),
  for notes or spreadsheets.
- Earnings calls: The tool does not fetch audio. If the user pastes a transcript,
  read it and summarize management commentary: guidance, demand, margins.

## The rules

- **Filed numbers first.** The table comes from official regulatory filings.
  Use press releases for context, and say where numbers came from.
- **Compare like with like.** Growth is against the same quarter a year
  earlier. Quarter-on-quarter changes are shown, but seasonal factors apply.
- **Reporting units.** US results are in US$ millions except EPS. Indian
  company results are in INR Crores (₹ Cr, 1 Cr = 10,000,000 INR) except EPS.
- **Say what is derived.** For US companies, Q4 is derived from the 10-K minus
  three 10-Qs.
- **Flag one-offs.** When net income is above operating income, gains outside
  the core business (investments, exceptional tax items) are inside it.
  Banks and insurance companies have no gross margin: blank, not zero.

## Steps

1. Run it for the tickers in question:

   ```bash
   python3 "${CLAUDE_SKILL_DIR}/facts.py" TSLA
   python3 "${CLAUDE_SKILL_DIR}/facts.py" RELIANCE.NS TCS.NS
   ```

   `--quarters 8` shows two full years, `--json` gives machine-readable output.
2. Open the latest earnings release and regulatory filing when numbers need
   context, and read them.
3. Write the summary: one line on the quarter (revenue, EPS, YoY growth), one
   on margins, one on anything unusual (exceptional items, margin compression).
4. If the next earnings report falls inside the planned trade, say so before
   `/trader-exit` sets the stop: a stop cannot protect against an overnight gap.

## What it never does

- Never says a stock is cheap, expensive, a buy or a sell.
- Never invents a number the filings do not contain.
- Never calls SEC without the User-Agent.

## Safety

Education and tooling, not financial advice. This skill only reads filings.
Nothing places an order except `/trader-broker`, after `/trader-risk` passes
and the user types an explicit yes for that order.
