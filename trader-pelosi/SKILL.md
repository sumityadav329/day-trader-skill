---
name: trader-pelosi
description: >-
  The Pelosi Tracker. Watches stock transactions disclosed by insiders and large
  institutions across US and Indian markets: reads the House Clerk's public Periodic
  Transaction Reports (PTRs) filed under the STOCK Act by members of Congress, as
  well as Indian regulatory filings including SEBI PIT (Prohibition of Insider
  Trading) Reg 7(2) insider disclosures, SEBI SAST (Substantial Acquisition of Shares
  and Takeovers) Reg 29 filings, and NSE/BSE Bulk & Block Deals. Alerts once per new
  filing, turns disclosures into clean rows (who, category, stock, ticker, buy or
  sell, shares/contracts, price, trade date, days to disclose), and flags trades in
  stocks you hold. Use when the user says "what is Pelosi buying", "track Nancy Pelosi",
  "Congress trades", "politician stock trades", "check bulk deals", "SEBI insider trading",
  "promoter buying", "block deals today", or "run the Pelosi skill".
argument-hint: "[member name | --market IN | deals | insider] [--read]"
---

# trader-pelosi

Insiders and lawmakers must report their transactions to regulatory bodies:
- In the US, members of Congress report trades by themselves, their spouse,
  and dependent children under the STOCK Act (Periodic Transaction Reports, PTRs).
- In India, company promoters, directors, and designated persons disclose
  transactions over Rs. 10 Lakhs to stock exchanges under SEBI (Prohibition of
  Insider Trading) Regulations, 2015, and institutional investors disclose large
  ownership shifts under SEBI (Substantial Acquisition of Shares and Takeovers)
  Regulations, 2011, alongside daily NSE/BSE Bulk and Block Deals.

This skill reads those filings, notices the moment a new report appears, and
formats the disclosures cleanly.

It is fast. It is not early. Regulatory disclosures arrive after the trade
has been executed (up to 45 days for US PTRs, within 2 trading days for SEBI
PIT, after market close for Bulk Deals). There is no inside information here:
only public filings, read quickly. Say that every time.

Tool in this folder (standard library Python; `pdftotext` from poppler is
optional for reading US House PDFs locally):

```bash
# US Congress filings:
python3 "${CLAUDE_SKILL_DIR}/ptr.py" index --member pelosi
python3 "${CLAUDE_SKILL_DIR}/ptr.py" index --since 2026-10-01
python3 "${CLAUDE_SKILL_DIR}/ptr.py" new --member pelosi --read

# Indian Market (SEBI PIT, SAST, Bulk/Block Deals):
python3 "${CLAUDE_SKILL_DIR}/ptr.py" index --market IN
python3 "${CLAUDE_SKILL_DIR}/ptr.py" index --market IN --symbol RELIANCE
python3 "${CLAUDE_SKILL_DIR}/ptr.py" deals --symbol HDFCBANK
python3 "${CLAUDE_SKILL_DIR}/ptr.py" insider --symbol TCS
python3 "${CLAUDE_SKILL_DIR}/ptr.py" new --market IN
```

## What it reads and writes

- US Filings: Reads `disclosures-clerk.house.gov` yearly index ZIP and each
  PTR PDF at `.../public_disc/ptr-pdfs/<YEAR>/<DocID>.pdf`.
- Indian Disclosures: Reads SEBI PIT continual disclosures (Regulation 7(2)),
  SEBI SAST substantial acquisition reports (Regulation 29), and NSE/BSE
  Bulk & Block Deal reports.
- Writes `trader/state/pelosi.json` (filing IDs already shown, so each trade
  alerts once) and keeps downloaded PDFs in `trader/state/ptr-pdfs/`.
- Reads `trader/portfolio.json` (if it exists) to flag trades in what you hold.

## The rules

- **Quote the filing, not a tracker.** Every row comes from the regulatory filing
  or exchange disclosure, with its ID and source.
- **Amounts and values.** US PTR amounts are ranges ("$1,000,001 - $5,000,000").
  Indian deals and disclosures show exact share counts, trade prices, and total
  rupee values (in ₹ Crores).
- **Owner and category.** US reports show SP (spouse), JT (joint), DC (dependent
  child). Indian reports show Promoter, Promoter Group, Director, KMP, or
  institutional fund name.
- **Say the delay.** Each row shows days between the trade and the filing.
  US STOCK Act limit is 45 days. SEBI PIT limit is 2 trading days (flagged as
  late if disclosed after).
- **Options are options.** A call option with strike and expiry is not a share
  purchase.

## Steps for Indian Disclosures

1. Check recent deals and insider buying:
   `python3 ptr.py deals --symbol RELIANCE`
   `python3 ptr.py insider --symbol TCS`
2. Check new disclosures across the market:
   `python3 ptr.py new --market IN`
3. If an insider or institutional trade touches a stock on your watchlist,
   note it in `/trader-news` and check the chart with `/trader-scanner`.
   A disclosure is a reason to inspect a chart, not an automated buy trigger.

## The Senate: the manual route

Senate reports live at `https://efdsearch.senate.gov`. Before searching, the
site asks you to tick a box agreeing to legal limits. That is an agreement
for the person to accept, so this skill does not automate it. See US instructions.

## What it never does

- Never says or implies an insider or member has inside information, or that
  copying a trade guarantees profit.
- Never places an order, and never copies options trades.
- Never automates user agreements or logins.

## Safety

- Education and tooling, not financial advice. Nothing in this pack is a
  recommendation to buy or sell anything.
- Paper trading by default (`"paper": true` in `trader/config.json`).
- Nothing places an order except `/trader-broker`, and only after
  `/trader-risk` passes AND the user types an explicit yes for that order in
  the chat, AND the brokerage tool is set to "Needs approval" in Claude.
- No options, margin, shorting or leverage unless config allows it and the user confirms.
- Congressional trades and SEBI disclosures are PUBLIC filings made after the
  trade. This skill reads them fast; it has no inside information.
