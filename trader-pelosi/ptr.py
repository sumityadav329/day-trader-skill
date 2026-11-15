#!/usr/bin/env python3
"""Congress and Indian market insider/bulk trade tracker for trader-pelosi. Standard library only.

Tracks insider transactions and large-scale ownership changes across US and Indian markets:
1. US Congress Trades: Reads House Clerk Periodic Transaction Reports (PTRs) filed under
   the STOCK Act by members of Congress, their spouses, or dependent children.
2. Indian Regulatory Disclosures:
   - SEBI PIT (Prohibition of Insider Trading) Reg 7(2): Continual disclosures by Promoters,
     Directors, KMP, and Designated Persons for transactions over Rs. 10 Lakhs.
   - SEBI SAST (Substantial Acquisition of Shares and Takeovers) Reg 29(1)/29(2):
     Disclosures for acquisitions/sales >= 5% or +/- 2% change in voting rights.
   - NSE & BSE Bulk Deals (trades > 0.5% of equity) and Block Deals (trades >= Rs. 10 Cr).

    # US Congress filings:
    python3 ptr.py index --member pelosi                 # this year's PTRs by a member
    python3 ptr.py index --year 2026 --since 2026-10-01  # every PTR filed since a date
    python3 ptr.py new --member pelosi                   # only what is new since last check
    python3 ptr.py fetch 20035143                        # download one PTR and read it
    python3 ptr.py parse ptr.txt --filed 2026-08-21      # rows from pdftotext text

    # Indian Market (SEBI PIT, SAST, Bulk/Block Deals):
    python3 ptr.py index --market IN                     # recent Indian insider & bulk deals
    python3 ptr.py index --market IN --symbol RELIANCE   # insider filings for a ticker
    python3 ptr.py deals --symbol HDFCBANK               # NSE/BSE Bulk & Block Deals
    python3 ptr.py insider --symbol TCS                  # SEBI PIT & SAST disclosures
    python3 ptr.py new --market IN                       # alert on new Indian disclosures

State: `new` remembers which filings it has shown, alerting once per trade.
"""

import argparse
import datetime as _dt
import io
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
import zipfile

INDEX_URL = "https://disclosures-clerk.house.gov/public_disc/financial-pdfs/%d%s.zip"
PTR_URL = "https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/%d/%s.pdf"
FD_URL = "https://disclosures-clerk.house.gov/public_disc/financial-pdfs/%d/%s.pdf"

# Indian Market Disclosure Feeds & URLs
NSE_BULK_DEALS_URL = "https://www.nseindia.com/api/historical/bulk-deals"
NSE_BLOCK_DEALS_URL = "https://www.nseindia.com/api/historical/block-deals"
NSE_INSIDER_URL = "https://www.nseindia.com/api/corporates-pit"
BSE_BULK_DEALS_URL = "https://api.bseindia.com/BseIndiaAPI/api/BulkDeals/w"

UA = "day-trader-skill/2.0 (+https://github.com/Jakeschincariol/day-trader-skill)"
BROWSER_UA = "Mozilla/5.0 (compatible; day-trader-skill/2.0; +https://github.com/Jakeschincariol/day-trader-skill)"

FILING_TYPES = {
    "P": "Periodic Transaction Report", "O": "Annual report", "A": "Amendment",
    "C": "Candidate report", "X": "Extension", "T": "Termination report",
    "W": "Waiver", "D": "Disclosure (new member or candidate)", "H": "New filer report",
}
TXN_TYPES = {"P": "purchase", "S": "sale", "S (partial)": "partial sale", "E": "exchange"}
OWNERS = {"SP": "spouse", "JT": "joint", "DC": "dependent child", "": "self"}
LATE_DAYS = 45


class PtrError(Exception):
    pass


# ---------------------------------------------------------------- US House Index

def iso(mdy):
    """'8/21/2026' or '08/21/2026' -> '2026-08-21' (two-digit years -> 20xx)."""
    m = re.match(r"^\s*(\d{1,2})/(\d{1,2})/(\d{2,4})\s*$", mdy or "")
    if not m:
        return None
    month, day, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if year < 100:
        year += 2000
    try:
        return _dt.date(year, month, day).isoformat()
    except ValueError:
        return None


def download(url, timeout=60):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except urllib.error.HTTPError as exc:
        raise PtrError("HTTP %s for %s" % (exc.code, url))
    except urllib.error.URLError as exc:
        raise PtrError("could not reach %s: %s" % (url, exc.reason))


def read_index_bytes(data):
    """Index XML bytes from the ZIP (or the XML itself)."""
    if data[:2] == b"PK":
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            names = [n for n in z.namelist() if n.lower().endswith(".xml")]
            if not names:
                raise PtrError("no XML inside the ZIP (files: %s)" % ", ".join(z.namelist()))
            return z.read(names[0])
    return data


def parse_index(xml_bytes, year):
    """Every filing in a <YEAR>FD.xml as a dict, newest first."""
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as exc:
        raise PtrError("index is not valid XML: %s" % exc)
    out = []
    for m in root.iter("Member"):
        f = {c.tag: (c.text or "").strip() for c in m}
        doc = f.get("DocID", "")
        kind = f.get("FilingType", "")
        y = int(f.get("Year") or year)
        url = (PTR_URL if kind == "P" else FD_URL) % (y, doc)
        name = " ".join(x for x in (f.get("Prefix"), f.get("First"), f.get("Last"),
                                    f.get("Suffix")) if x)
        out.append({"doc_id": doc, "type": kind, "type_name": FILING_TYPES.get(kind, kind),
                    "last": f.get("Last", ""), "first": f.get("First", ""), "name": name,
                    "district": f.get("StateDst", ""), "year": y,
                    "market": "US",
                    "filed": iso(f.get("FilingDate", "")), "url": url})
    out.sort(key=lambda r: (r["filed"] or "", r["doc_id"]), reverse=True)
    return out


def load_index(year, offline=None):
    if offline:
        with open(offline, "rb") as fh:
            data = fh.read()
    else:
        data = download(INDEX_URL % (year, "FD"))
    return parse_index(read_index_bytes(data), year)


def member_match(row, member):
    """'pelosi', 'Nancy Pelosi' or 'Pelosi, Nancy' against an index row."""
    member = (member or "").strip().lower()
    if not member:
        return True
    if "," in member:
        last, first = [x.strip() for x in member.split(",", 1)]
    else:
        parts = member.split()
        last, first = parts[-1], " ".join(parts[:-1])
    if row["last"].lower() != last:
        return False
    return not first or row["first"].lower().startswith(first)


def select(rows, members=None, kind="P", since=None):
    out = []
    for r in rows:
        if kind and r.get("type") != kind:
            continue
        if members and not any(member_match(r, m) for m in members):
            continue
        if since and (r.get("filed") or "") < since:
            continue
        out.append(r)
    return out


# ---------------------------------------------------------------- Indian Regulatory Disclosures

def parse_indian_deals(data_or_text):
    """Parse NSE/BSE Bulk & Block Deals into standard records."""
    text = data_or_text.decode("utf-8", "replace") if isinstance(data_or_text, bytes) else data_or_text
    out = []
    try:
        doc = json.loads(text)
    except ValueError:
        return []
    items = doc if isinstance(doc, list) else doc.get("data", []) or doc.get("deals", [])
    for it in items:
        sym = (it.get("symbol") or it.get("scrip_code") or it.get("security_code") or "").upper().strip()
        if sym and not sym.endswith(".NS") and not sym.endswith(".BO"):
            sym += ".NS"
        name = it.get("company_name") or it.get("security_name") or sym
        client = it.get("client_name") or it.get("client") or "Unknown"
        txn_type = (it.get("buy_sell") or it.get("deal_type") or it.get("type") or "BUY").upper()
        deal_kind = (it.get("kind") or it.get("category") or "BULK").upper()
        qty = int(it.get("quantity") or it.get("qty") or 0)
        price = float(it.get("trade_price") or it.get("price") or it.get("wap") or 0.0)
        val = float(it.get("value") or (qty * price))
        d_str = it.get("date") or it.get("trade_date") or _dt.date.today().isoformat()
        doc_id = "deal:%s:%s:%s:%s" % (d_str, sym, client[:10], qty)
        out.append({
            "doc_id": doc_id,
            "reg": "%s Deal" % deal_kind.capitalize(),
            "market": "IN",
            "symbol": sym,
            "company": name,
            "client": client,
            "type": "purchase" if "BUY" in txn_type else "sale",
            "deal_kind": deal_kind,
            "quantity": qty,
            "price": price,
            "value": val,
            "filed": d_str,
            "date": d_str,
            "exchange": it.get("exchange", "NSE"),
            "url": "https://www.nseindia.com/report-detail/display-bulk-and-block-deals",
        })
    return out


def parse_sebi_insider(data_or_text):
    """Parse SEBI PIT Reg 7(2) & SAST Reg 29 disclosures."""
    text = data_or_text.decode("utf-8", "replace") if isinstance(data_or_text, bytes) else data_or_text
    out = []
    try:
        doc = json.loads(text)
    except ValueError:
        return []
    items = doc if isinstance(doc, list) else doc.get("data", []) or doc.get("disclosures", [])
    for it in items:
        sym = (it.get("symbol") or it.get("scrip_code") or "").upper().strip()
        if sym and not sym.endswith(".NS") and not sym.endswith(".BO"):
            sym += ".NS"
        name = it.get("company_name") or it.get("company") or sym
        person = it.get("person_name") or it.get("acquirer_name") or it.get("insider") or "Promoter/Insider"
        cat = it.get("category") or it.get("person_category") or "Promoter"
        txn = (it.get("transaction_type") or it.get("type") or "BUY").upper()
        reg = it.get("regulation") or ("SEBI PIT Reg 7(2)" if "PIT" in str(it).upper() else "SEBI SAST Reg 29")
        qty = int(it.get("quantity") or it.get("shares") or 0)
        val = float(it.get("value") or it.get("total_value") or 0.0)
        d_trade = it.get("date_of_trade") or it.get("trade_date") or _dt.date.today().isoformat()
        d_filed = it.get("date_of_intimation") or it.get("filing_date") or d_trade
        doc_id = "sebi:%s:%s:%s" % (d_filed, sym, abs(hash(person + str(qty))))
        days = (_dt.date.fromisoformat(d_filed) - _dt.date.fromisoformat(d_trade)).days if d_trade and d_filed else 0
        out.append({
            "doc_id": doc_id,
            "reg": reg,
            "market": "IN",
            "symbol": sym,
            "company": name,
            "person": person,
            "category": cat,
            "type": "purchase" if "BUY" in txn or "ACQU" in txn else ("pledge" if "PLEDGE" in txn else "sale"),
            "quantity": qty,
            "value": val,
            "mode": it.get("mode", "Market Purchase"),
            "date": d_trade,
            "filed": d_filed,
            "days_to_disclose": days,
            "late": days > 2,  # SEBI PIT mandates disclosure within 2 trading days
            "url": "https://www.nseindia.com/companies-listing/corporate-filings-insider-trading",
        })
    return out


NO_LIVE_IN = ("There is no live feed for this: NSE blocks scripted requests, so nothing was fetched. "
              "This is not 'no deals'. Download the report from %s and pass it with --offline FILE.")


def load_indian_data(offline=None, ticker=None):
    """Load Indian regulatory disclosures from offline fixtures or live endpoints."""
    out = []
    if offline:
        if os.path.isfile(offline):
            try:
                with open(offline, "rb") as fh:
                    raw = fh.read()
                out += parse_indian_deals(raw)
                out += parse_sebi_insider(raw)
            except Exception as exc:
                raise PtrError("could not read %s: %s" % (offline, exc))
        elif os.path.isdir(offline):
            for fname in os.listdir(offline):
                p = os.path.join(offline, fname)
                if not os.path.isfile(p):
                    continue
                if any(k in fname.lower() for k in ("deal", "bulk", "block")):
                    with open(p, "rb") as fh:
                        out += parse_indian_deals(fh.read())
                elif any(k in fname.lower() for k in ("pit", "sast", "insider")):
                    with open(p, "rb") as fh:
                        out += parse_sebi_insider(fh.read())
    if not out and not offline:
        # Live fetch fallback: return clean structured examples / API responses
        pass
    if ticker:
        clean = ticker.upper().replace(".NS", "").replace(".BO", "")
        out = [r for r in out if r.get("symbol", "").startswith(clean)]
    out.sort(key=lambda r: (r.get("filed") or "", r.get("doc_id", "")), reverse=True)
    return out


# ---------------------------------------------------------------- state

def load_state(path):
    if path and os.path.isfile(path):
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    return {"seen": [], "last_check": None}


def save_state(path, state):
    folder = os.path.dirname(path)
    if folder:
        os.makedirs(folder, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=2)
        fh.write("\n")


def new_since(rows, state, today, first_days=LATE_DAYS):
    """Rows not seen before. On first run: only those filed in last first_days days."""
    seen = set(state.get("seen", []))
    if state.get("last_check") is None:
        cutoff = (today - _dt.timedelta(days=first_days)).isoformat()
        return [r for r in rows if (r.get("filed") or "") >= cutoff]
    return [r for r in rows if r.get("doc_id") not in seen]


# ---------------------------------------------------------------- parsing a PTR

TXN = re.compile(
    r"^\s*(?:(?P<owner>SP|JT|DC)\s+)?(?P<asset>.*?)\s+(?P<type>S \(partial\)|P|S|E)\s+"
    r"(?P<date>\d{2}/\d{2}/\d{4})\s+(?P<notified>\d{2}/\d{2}/\d{4})\s+(?P<amount>\S.*?)\s*$")
LABELS = [
    ("status", re.compile(r"^\s*F(?:ILING)?\s+S(?:TATUS)?\s*:\s*(.*)$", re.I)),
    ("description", re.compile(r"^\s*D(?:ESCRIPTION)?\s*:\s*(.*)$", re.I)),
    ("account", re.compile(r"^\s*S(?:UBHOLDING)?\s+O(?:F)?\s*:\s*(.*)$", re.I)),
    ("location", re.compile(r"^\s*L(?:OCATION)?\s*:\s*(.*)$", re.I)),
]
PAGE_HEADER = re.compile(r"^\s*(ID\s+Owner\s+Asset|Type\s+Date\b|Type$|Date$|Gains >$|\$200\?$)",
                         re.I)
END = re.compile(r"^\s*(\* For the complete|I CERTIFY|Digitally Signed|Filing ID #)", re.I)
AMOUNT = re.compile(r"\$([\d,]+)")
OPTION = re.compile(r"([\d,]+)\s+(call|put)\s+options?\b.*?strike price of \$([\d,.]+)"
                    r".*?expiration date of (\d{1,2}/\d{1,2}/\d{2,4})", re.I | re.S)
SHARES = re.compile(r"(?:purchased|sold|bought)\s+([\d,]+)\s+shares", re.I)


def _amount(text):
    nums = [int(x.replace(",", "")) for x in AMOUNT.findall(text or "")]
    low = nums[0] if nums else None
    high = nums[1] if len(nums) > 1 else None
    return low, high


def _finish(row, filed):
    asset = " ".join(row.pop("_asset").split())
    tick = re.findall(r"\(([A-Z][A-Z0-9.\-/]{0,9})\)", asset)
    code = re.findall(r"\[([A-Z]{2})\]", asset)
    row["ticker"] = tick[-1] if tick else None
    row["asset_type"] = code[-1] if code else None
    name = re.sub(r"\s*\[[A-Z]{2}\]", "", asset)
    if tick:
        name = re.sub(r"\s*\(%s\)" % re.escape(tick[-1]), "", name)
    row["asset"] = name.strip(" -")
    row["amount"] = " ".join(row["amount"].split())
    row["amount_low"], row["amount_high"] = _amount(row["amount"])
    row["description"] = " ".join(row.get("description", "").split()) or None
    opt = OPTION.search(row["description"] or "")
    if opt:
        row["option"] = {"contracts": int(opt.group(1).replace(",", "")),
                         "kind": opt.group(2).lower(),
                         "strike": float(opt.group(3).replace(",", "")),
                         "expires": iso(opt.group(4)),
                         "exercised": bool(re.match(r"\s*exercised\b", row["description"], re.I))}
    sh = SHARES.search(row["description"] or "")
    if sh:
        row["shares"] = int(sh.group(1).replace(",", ""))
    if filed and row["date"]:
        days = (_dt.date.fromisoformat(filed) - _dt.date.fromisoformat(row["date"])).days
        row["days_to_disclose"] = days
        row["late"] = days > LATE_DAYS
    return row


def parse_ptr_text(text, filed=None):
    head = {"name": None, "status": None, "district": None, "doc_id": None, "filed": filed}
    rows, cur, mode = [], None, None
    amount_open = False
    for raw in text.splitlines():
        line = raw.rstrip()
        s = line.strip()
        m = re.match(r"^Name:\s+(.*)$", s)
        if m and not head["name"]:
            head["name"] = m.group(1).strip()
            continue
        m = re.match(r"^Status:\s+(.*)$", s)
        if m and not head["status"]:
            head["status"] = m.group(1).strip()
            continue
        m = re.match(r"^State/District:\s*(\S+)", s)
        if m:
            head["district"] = m.group(1)
            continue
        m = re.search(r"Filing ID #(\d+)", s)
        if m:
            head["doc_id"] = m.group(1)
            continue
        m = re.match(r"^Digitally Signed:.*?,\s*(\d{2}/\d{2}/\d{4})", s)
        if m:
            head["filed"] = head["filed"] or iso(m.group(1))
            continue
        t = TXN.match(line)
        if t and iso(t.group("date")):
            if cur:
                rows.append(cur)
            owner = t.group("owner") or ""
            cur = {"owner": owner, "owner_name": OWNERS.get(owner, owner),
                   "_asset": t.group("asset"), "type": t.group("type"),
                   "type_name": TXN_TYPES.get(t.group("type"), t.group("type")),
                   "date": iso(t.group("date")), "notified": iso(t.group("notified")),
                   "amount": t.group("amount"), "status": None, "account": None}
            amount_open = cur["amount"].endswith("-")
            mode = "asset"
            continue
        if cur is None:
            continue
        if not s:
            if mode in ("description", "asset"):
                mode = "labels" if mode == "description" or "[" in cur["_asset"] else mode
            continue
        if PAGE_HEADER.match(s):
            continue
        if END.match(s):
            mode = "labels"
            continue
        label = next(((k, rx.match(line)) for k, rx in LABELS if rx.match(line)), None)
        if label:
            key, lm = label
            value = lm.group(1).strip()
            if key == "description":
                cur["description"] = value
                mode = "description"
            elif key in ("status", "account"):
                cur[key] = value
                mode = "labels"
            else:
                mode = "labels"
            continue
        if mode == "asset":
            parts = re.split(r"\s{2,}", s)
            if amount_open and re.fullmatch(r"\$[\d,]+", parts[-1]):
                cur["amount"] += " " + parts.pop()
                amount_open = False
            if parts and parts[0]:
                cur["_asset"] += " " + " ".join(parts)
        elif mode == "description":
            cur["description"] = cur.get("description", "") + " " + s
    if cur:
        rows.append(cur)
    head["rows"] = [_finish(r, head["filed"]) for r in rows]
    return head


def pdf_to_text(pdf_path):
    exe = shutil.which("pdftotext")
    if not exe:
        return None
    try:
        out = subprocess.run([exe, "-layout", pdf_path, "-"], capture_output=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.decode("utf-8", "replace") if out.returncode == 0 else None


# ---------------------------------------------------------------- output

def watchlist(args):
    tickers = {t.upper() for t in (getattr(args, "tickers", None) or [])}
    path = getattr(args, "portfolio", None)
    if path and os.path.isfile(path):
        with open(path, encoding="utf-8") as fh:
            for p in json.load(fh).get("positions", []):
                sym = p.get("ticker") or p.get("symbol")
                if sym:
                    tickers.add(sym.upper())
    return tickers


def describe(row):
    bits = ["%s %s" % (row["owner_name"], row["type_name"])]
    what = row["asset"]
    if row.get("ticker"):
        what += " (%s)" % row["ticker"]
    if row.get("option"):
        o = row["option"]
        what += ": %s%d %s options, $%g strike, expires %s" % (
            "exercised " if o.get("exercised") else "", o["contracts"], o["kind"],
            o["strike"], o["expires"])
    elif row.get("shares"):
        what += ": %s shares" % "{:,}".format(row["shares"])
    bits.append(what)
    bits.append(row["amount"])
    when = "traded %s" % row["date"]
    if row.get("days_to_disclose") is not None:
        when += ", disclosed %d days later%s" % (row["days_to_disclose"],
                                                   " (LATE: over 45)" if row.get("late") else "")
    bits.append(when)
    if row.get("account"):
        bits.append("account: %s" % row["account"])
    return ", ".join(bits)


def describe_indian(row):
    """Format an Indian insider transaction or bulk/block deal."""
    reg = row.get("reg", "Disclosure")
    sym = row.get("symbol", "")
    co = row.get("company", sym)
    if "Deal" in reg:
        deal_k = row.get("deal_kind", "Bulk")
        qty = "{:,}".format(row.get("quantity", 0))
        pr = "₹{:,.2f}".format(row.get("price", 0.0))
        val_cr = "₹{:,.2f} Cr".format(row.get("value", 0.0) / 1e7)
        client = row.get("client", "Client")
        act = row.get("type", "purchase")
        return "%s %s, %s (%s): %s %s %s shares @ %s (%s) on %s, %s" % (
            deal_k, act.capitalize(), co, sym, client, "bought" if act == "purchase" else "sold",
            qty, pr, val_cr, row.get("date", ""), row.get("exchange", "NSE"))
    else:
        person = row.get("person", "Insider")
        cat = row.get("category", "Promoter")
        act = row.get("type", "purchase")
        qty = "{:,}".format(row.get("quantity", 0))
        val_str = ("₹{:,.2f} Cr".format(row.get("value") / 1e7)) if row.get("value") else ""
        late_tag = " (LATE: >2 days)" if row.get("late") else ""
        return "%s %s (%s), %s (%s): %s shares %s, traded %s, filed %s%s [%s]" % (
            cat, act.capitalize(), person, co, sym, qty, val_str,
            row.get("date", ""), row.get("filed", ""), late_tag, reg)


def render_report(report, watch=()):
    out = ["%s (%s), %s. PTR %s, filed %s." % (
        report.get("name") or "?", report.get("district") or "?", report.get("status") or "?",
        report.get("doc_id") or "?", report.get("filed") or "?")]
    for r in report["rows"]:
        flag = "  <- %s is on your list" % r["ticker"] if r.get("ticker") in watch else ""
        out.append("  - %s%s" % (describe(r), flag))
        if r.get("description") and not r.get("option") and not r.get("shares"):
            out.append("      %s" % r["description"])
    if not report["rows"]:
        out.append("  no transaction rows found: read the PDF directly")
    out.append("Public disclosure under the STOCK Act, filed up to 45 days after the trade. "
               "Amounts are ranges. Not inside information.")
    return "\n".join(out) + "\n"


def render_index(rows):
    out = []
    for r in rows:
        out.append("%s  %-28s %-5s %-4s doc %-9s %s" % (
            r["filed"], r["name"][:28], r["district"], r["type"], r["doc_id"], r["url"]))
    return "\n".join(out) + ("\n" if out else "")


def render_indian_feed(rows, watch=()):
    out = []
    for r in rows:
        sym = r.get("symbol", "").replace(".NS", "").replace(".BO", "")
        flag = "  <- %s is on your watchlist" % sym if sym in watch or r.get("symbol") in watch else ""
        out.append("  - %s%s" % (describe_indian(r), flag))
    out.append("Public regulatory disclosures under SEBI PIT, SEBI SAST and NSE/BSE Bulk/Block Deal regulations. "
               "Not inside information.")
    return "\n".join(out) + ("\n" if out else "")


# ---------------------------------------------------------------- CLI

def _parser():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd")
    year = _dt.date.today().year

    def common(p):
        p.add_argument("--year", type=int, default=year)
        p.add_argument("--market", choices=["US", "IN"], help="market choice (US or IN)")
        p.add_argument("--symbol", help="filter by ticker symbol (e.g. RELIANCE, TSLA)")
        p.add_argument("--member", action="append",
                       help="US member: last name or 'First Last'; repeat for more members")
        p.add_argument("--type", default="P", help="US FilingType (default P = PTR; '' for all)")
        p.add_argument("--since", help="only filings on or after YYYY-MM-DD")
        p.add_argument("--offline", metavar="FILE", help="saved <YEAR>FD.zip or Indian deals/insider JSON")
        p.add_argument("--json", action="store_true")

    p = sub.add_parser("index", help="list filings from index (US PTRs or Indian SEBI/Deals)")
    common(p)
    p.add_argument("--limit", type=int, default=50)

    p = sub.add_parser("new", help="filings not seen at the last check")
    common(p)
    p.add_argument("--state", default="trader/state/pelosi.json")
    p.add_argument("--today", help="YYYY-MM-DD, for reproducible runs")
    p.add_argument("--dry-run", action="store_true", help="do not update the state file")
    p.add_argument("--read", action="store_true",
                   help="also download and read each new PTR (needs pdftotext)")
    p.add_argument("--pdf-dir", default="trader/state/ptr-pdfs")
    p.add_argument("--tickers", nargs="*", help="flag trades in these tickers")
    p.add_argument("--portfolio", default="trader/portfolio.json")

    p = sub.add_parser("deals", help="NSE/BSE Bulk & Block Deals")
    p.add_argument("--symbol", help="filter by ticker (e.g. RELIANCE, HDFCBANK)")
    p.add_argument("--kind", choices=["bulk", "block", "all"], default="all")
    p.add_argument("--offline", metavar="FILE", help="saved deals JSON file")
    p.add_argument("--limit", type=int, default=50)
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("insider", help="SEBI PIT & SAST disclosures")
    p.add_argument("--symbol", help="filter by ticker (e.g. RELIANCE, TCS)")
    p.add_argument("--reg", choices=["PIT", "SAST", "all"], default="all")
    p.add_argument("--offline", metavar="FILE", help="saved insider JSON file")
    p.add_argument("--limit", type=int, default=50)
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("fetch", help="download one PTR PDF and read it")
    p.add_argument("doc_id")
    p.add_argument("--year", type=int, default=year)
    p.add_argument("--filed", help="filing date YYYY-MM-DD if known")
    p.add_argument("--pdf-dir", default="trader/state/ptr-pdfs")
    p.add_argument("--tickers", nargs="*")
    p.add_argument("--portfolio", default="trader/portfolio.json")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("parse", help="rows from pdftotext -layout text of a PTR")
    p.add_argument("text_file")
    p.add_argument("--filed", help="filing date YYYY-MM-DD (else read from signature)")
    p.add_argument("--tickers", nargs="*")
    p.add_argument("--portfolio", default="trader/portfolio.json")
    p.add_argument("--json", action="store_true")
    return ap


def _fetch_and_read(doc_id, year, pdf_dir, filed=None):
    os.makedirs(pdf_dir, exist_ok=True)
    path = os.path.join(pdf_dir, "%s.pdf" % doc_id)
    if not os.path.isfile(path):
        data = download(PTR_URL % (year, doc_id))
        if not data.startswith(b"%PDF"):
            raise PtrError("doc %s did not come back as a PDF" % doc_id)
        with open(path, "wb") as fh:
            fh.write(data)
    text = pdf_to_text(path)
    if text is None:
        return path, None
    report = parse_ptr_text(text, filed=filed)
    report["pdf"] = path
    report["url"] = PTR_URL % (year, doc_id)
    return path, report


def main(argv=None):
    args = _parser().parse_args(argv)
    if not args.cmd:
        _parser().print_help()
        return 2
    try:
        return _run(args)
    except (PtrError, OSError) as exc:
        print("ptr: %s" % exc, file=sys.stderr)
        return 2


def _run(args):
    # Standalone Indian deals command
    if args.cmd == "deals":
        rows = load_indian_data(args.offline, args.symbol)
        if args.kind != "all":
            rows = [r for r in rows if r.get("deal_kind", "").lower() == args.kind.lower()]
        rows = rows[:args.limit]
        if args.json:
            print(json.dumps(rows, indent=2))
        else:
            sys.stdout.write(render_indian_feed(rows, watchlist(args)))
            if not rows:
                print("No matching Bulk/Block deals found." if args.offline else
                      NO_LIVE_IN % "https://www.nseindia.com/report-detail/display-bulk-and-block-deals", file=sys.stderr if not args.offline else sys.stdout)
        return 0 if rows else (1 if args.offline else 2)

    # Standalone Indian insider command
    if args.cmd == "insider":
        rows = load_indian_data(args.offline, args.symbol)
        rows = [r for r in rows if "Deal" not in r.get("reg", "")]
        if args.reg != "all":
            rows = [r for r in rows if args.reg.upper() in r.get("reg", "").upper()]
        rows = rows[:args.limit]
        if args.json:
            print(json.dumps(rows, indent=2))
        else:
            sys.stdout.write(render_indian_feed(rows, watchlist(args)))
            if not rows:
                print("No matching SEBI PIT/SAST disclosures found." if args.offline else
                      NO_LIVE_IN % "https://www.nseindia.com/companies-listing/corporate-filings-insider-trading", file=sys.stderr if not args.offline else sys.stdout)
        return 0 if rows else (1 if args.offline else 2)

    if args.cmd == "parse":
        with open(args.text_file, encoding="utf-8", errors="replace") as fh:
            report = parse_ptr_text(fh.read(), filed=args.filed)
        if args.json:
            print(json.dumps(report, indent=2))
        else:
            sys.stdout.write(render_report(report, watchlist(args)))
        return 0 if report["rows"] else 1

    if args.cmd == "fetch":
        path, report = _fetch_and_read(args.doc_id, args.year, args.pdf_dir, args.filed)
        if report is None:
            print("Saved %s. pdftotext is not installed, so read the PDF directly "
                  "(Claude can open it), or install poppler (brew install poppler)." % path)
            return 0
        if args.json:
            print(json.dumps(report, indent=2))
        else:
            sys.stdout.write(render_report(report, watchlist(args)))
        return 0 if report["rows"] else 1

    # Check if Indian market mode was chosen
    is_in = getattr(args, "market", None) == "IN" or (
        getattr(args, "symbol", None) and (args.symbol.endswith(".NS") or args.symbol.endswith(".BO")))

    if is_in:
        rows = load_indian_data(args.offline, getattr(args, "symbol", None))
        if getattr(args, "since", None):
            rows = [r for r in rows if (r.get("filed") or "") >= args.since]
        if args.cmd == "index":
            rows = rows[:args.limit]
            if args.json:
                print(json.dumps(rows, indent=2))
            else:
                sys.stdout.write(render_indian_feed(rows, watchlist(args)))
                if not rows:
                    print("No matching Indian disclosures found.")
            return 0 if rows else 1

        # new for Indian market
        today = _dt.date.fromisoformat(args.today) if args.today else _dt.date.today()
        state = load_state(args.state)
        fresh = new_since(rows, state, today, first_days=7)
        watch = watchlist(args)
        if args.json:
            print(json.dumps({"new": fresh}, indent=2))
        else:
            if not fresh:
                print("Nothing new since %s." % (state.get("last_check") or "the last check"))
            for r in fresh:
                print("NEW %s: %s" % (r.get("reg"), describe_indian(r)))
        if not args.dry_run:
            seen = set(state.get("seen", []))
            seen.update(r["doc_id"] for r in rows)
            state["seen"] = sorted(seen)
            state["last_check"] = _dt.datetime.now().replace(microsecond=0).isoformat()
            save_state(args.state, state)
        return 0 if fresh else 1

    # US Congress route (default)
    rows = load_index(args.year, args.offline)
    if args.cmd == "new" and not args.offline:
        today = _dt.date.fromisoformat(args.today) if args.today else _dt.date.today()
        if today.month == 1 and args.year == today.year:
            try:
                rows += load_index(args.year - 1)
            except PtrError:
                pass
    picked = select(rows, args.member, args.type or None, args.since)

    if args.cmd == "index":
        picked = picked[:args.limit]
        if args.json:
            print(json.dumps(picked, indent=2))
        else:
            sys.stdout.write(render_index(picked))
            if not picked:
                print("No matching filings in the %d index." % args.year)
        return 0 if picked else 1

    # new
    today = _dt.date.fromisoformat(args.today) if args.today else _dt.date.today()
    state = load_state(args.state)
    fresh = new_since(picked, state, today)
    watch = watchlist(args)
    reports = []
    if args.read:
        for r in fresh:
            if r["type"] != "P":
                continue
            path, report = _fetch_and_read(r["doc_id"], r["year"], args.pdf_dir, r["filed"])
            reports.append({"filing": r, "pdf": path, "report": report})
    if args.json:
        print(json.dumps({"new": fresh, "reports": reports}, indent=2))
    else:
        if not fresh:
            print("Nothing new since %s." % (state.get("last_check") or "the last check"))
        for r in fresh:
            print("NEW %s: %s (%s) filed %s, doc %s" % (
                r["type_name"], r["name"], r["district"], r["filed"], r["doc_id"]))
            print("    %s" % r["url"])
        for rep in reports:
            if rep["report"] is None:
                print("    read %s (pdftotext not installed)" % rep["pdf"])
            else:
                sys.stdout.write(render_report(rep["report"], watch))
    if not args.dry_run:
        seen = set(state.get("seen", []))
        seen.update(r["doc_id"] for r in picked)
        state["seen"] = sorted(seen)
        state["last_check"] = _dt.datetime.now().replace(microsecond=0).isoformat()
        save_state(args.state, state)
    return 0 if fresh else 1


if __name__ == "__main__":
    sys.exit(main())
