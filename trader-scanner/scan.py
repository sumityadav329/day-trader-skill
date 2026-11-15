#!/usr/bin/env python3
"""Chart Scanner for trader-scanner: scan thousands of charts for setups. Standard library only.

    python3 scan.py universe                          # Nasdaq Trader lists -> trader/universe.txt
    python3 scan.py universe --preset nifty50         # Nifty 50 constituents -> trader/universe.txt
    python3 scan.py daily                             # before the open: daily setups -> trader/watchlist.json
    python3 scan.py daily --preset nifty50            # pre-market scan of Nifty 50
    python3 scan.py daily --symbols RELIANCE,TCS,INFY # Indian or US symbols
    python3 scan.py orb                               # opening-range breakouts so far
    python3 scan.py orb --preset niftybank            # Nifty Bank ORB scan (09:15-09:30 IST)
    python3 scan.py orb --at 10:15 --or-minutes 15    # cut the clock at 10:15
    python3 scan.py orb --offline wall5m.json.gz --now "2026-10-19 10:15" --min-price 0

universe  Downloads Nasdaq Trader symbol directories (nasdaqlisted.txt and
          otherlisted.txt) or generates Indian market presets (Nifty 50,
          Nifty Bank, Nifty Next 50, Nifty 100, Nifty 500, liquid F&O).
          For US: keeps US-listed common stocks, ordinary shares and ADRs.
          For India: outputs NSE symbols (.NS) with company names.

daily     Pre-market scan of daily CLOSES (Yahoo spark: 20 symbols a request,
          3 months of 1-day bars, closes only). Liquidity from the spark meta:
          regularMarketPrice >= --min-price and regularMarketVolume >= --min-volume.
          Setups, from closes only:
            breakout_20d     the last close is above every close of the 20 before it
            bull_flag        up more than 10 % over the 10 sessions before the
                             last 5, then the last 5 closes inside a 5 % range
            reclaim_ma20     closed under the 20-day average, then closed above it
            oversold         RSI 14 (Wilder) under 30
            oversold_bounce  RSI 14 was under 30 yesterday and today closed up
          Today's daily bar is dropped until the session's close.

orb       Opening-range breakouts, 5-minute bars.
          US: 15-minute range 09:30 to 09:45 ET.
          India: 15-minute range 09:15 to 09:30 IST.
          Stage 1: spark closes for every symbol (1 day of 5-minute bars, 20 a request).
          Stage 2: each candidate's v8 chart (real highs and lows): range high = the highest
          high of the range bars, a hit = the first completed bar to CLOSE above it.

Bars: null closes are dropped, a bar off the 5-minute grid is dropped,
and a bar counts only once it is complete (start + 5 minutes <= clock).
Times are New York (ET) for US or Kolkata (IST) for India.

A scan is a list of charts that match a rule, not a list of stocks to buy.
Exit code 0 when the scan found something, 1 when it did not or could not run.
"""

import argparse
import datetime
import gzip
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

UA = "Mozilla/5.0 (compatible; day-trader-skill/2.0; +https://github.com/Jakeschincariol/day-trader-skill)"
YAHOO_HOSTS = ("https://query2.finance.yahoo.com", "https://query1.finance.yahoo.com")
NASDAQ_FILES = ("nasdaqlisted.txt", "otherlisted.txt")
NASDAQ_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/"
SPARK_MAX = 20      # Yahoo's spark answers 400 above 20 symbols
PAUSE = 0.3         # seconds between requests: be polite to free endpoints
BACKOFF = 5.0       # seconds to wait before the one retry after a 429
SLEEP = time.sleep  # tests swap this out
BAR_MIN = 5
MAX_CONFIRM = 200   # stage-2 v8 charts per orb scan, most traded first
MIN_DAILY_BARS = 30
DEFAULT_UNIVERSE = "trader/universe.txt"
NOTE = "A scan is a list of charts that match a rule, not a list of stocks to buy."

# US Market Calendar & Times
NYSE_HOLIDAYS = {
    "2026-01-01": "New Year's Day",
    "2026-01-19": "Martin Luther King, Jr. Day",
    "2026-02-16": "Washington's Birthday",
    "2026-04-03": "Good Friday",
    "2026-05-25": "Memorial Day",
    "2026-06-19": "Juneteenth National Independence Day",
    "2026-07-03": "Independence Day (observed)",
    "2026-09-07": "Labor Day",
    "2026-11-26": "Thanksgiving Day",
    "2026-12-25": "Christmas Day",
    "2027-01-01": "New Year's Day",
    "2027-01-18": "Martin Luther King, Jr. Day",
    "2027-02-15": "Washington's Birthday",
    "2027-03-26": "Good Friday",
    "2027-05-31": "Memorial Day",
    "2027-06-18": "Juneteenth National Independence Day (observed)",
    "2027-07-05": "Independence Day (observed)",
    "2027-09-06": "Labor Day",
    "2027-11-25": "Thanksgiving Day",
    "2027-12-24": "Christmas Day (observed)",
}
NYSE_EARLY_CLOSE = {  # 1:00 p.m. ET
    "2026-11-27": "day after Thanksgiving",
    "2026-12-24": "Christmas Eve",
    "2027-11-26": "day after Thanksgiving",
}
CALENDAR_YEARS = (2026, 2027)
OPEN = datetime.time(9, 30)
CLOSE = datetime.time(16, 0)
EARLY = datetime.time(13, 0)

# Indian Market (NSE / BSE) Calendar & Times
NSE_OPEN = datetime.time(9, 15)
NSE_CLOSE = datetime.time(15, 30)
NSE_EARLY_CLOSE = {}
IST_OFFSET = datetime.timedelta(hours=5, minutes=30)
NSE_HOLIDAYS = {
    "2026-01-26": "Republic Day",
    "2026-03-03": "Holi",
    "2026-03-20": "Id-Ul-Fitr (Ramzan Id)",
    "2026-04-03": "Good Friday",
    "2026-04-14": "Dr. Baba Saheb Ambedkar Jayanti",
    "2026-05-01": "Maharashtra Day",
    "2026-05-28": "Bakri Id",
    "2026-08-15": "Independence Day",
    "2026-09-04": "Milad-un-Nabi",
    "2026-10-02": "Mahatma Gandhi Jayanti",
    "2026-10-20": "Dussehra",
    "2026-11-08": "Diwali Laxmi Pujan",
    "2026-11-10": "Diwali Balipratipada",
    "2026-11-24": "Gurunanak Jayanti",
    "2026-12-25": "Christmas",
    "2027-01-26": "Republic Day",
    "2027-03-22": "Holi",
    "2027-03-26": "Good Friday",
    "2027-04-14": "Dr. Baba Saheb Ambedkar Jayanti",
    "2027-05-01": "Maharashtra Day",
    "2027-08-15": "Independence Day",
    "2027-10-02": "Mahatma Gandhi Jayanti",
    "2027-10-11": "Dussehra",
    "2027-10-29": "Diwali Laxmi Pujan",
    "2027-12-25": "Christmas",
}

# Indian Index Mapping to Yahoo Finance Tickers
INDEX_MAP = {
    "NIFTY50": "^NSEI",
    "NIFTY": "^NSEI",
    "NIFTY 50": "^NSEI",
    "NIFTY_50": "^NSEI",
    "^NSEI": "^NSEI",
    "BANKNIFTY": "^NSEBANK",
    "NIFTYBANK": "^NSEBANK",
    "NIFTY BANK": "^NSEBANK",
    "NIFTY_BANK": "^NSEBANK",
    "^NSEBANK": "^NSEBANK",
    "SENSEX": "^BSESN",
    "BSESENSEX": "^BSESN",
    "BSE SENSEX": "^BSESN",
    "^BSESN": "^BSESN",
    "NIFTYNEXT50": "^NSMIDCP",
    "NIFTY NEXT 50": "^NSMIDCP",
    "NIFTY_NEXT_50": "^NSMIDCP",
}

SETUPS = [
    ("breakout_20d", "20-day closing-high breakout"),
    ("bull_flag", "bull flag"),
    ("reclaim_ma20", "reclaimed the 20-day average"),
    ("oversold", "oversold (RSI under 30)"),
    ("oversold_bounce", "oversold bounce"),
]
LABEL = dict(SETUPS)


class ScanError(Exception):
    pass


# ---------------------------------------------------------------- time

def _nth_sunday(year, month, n):
    d = datetime.date(year, month, 1)
    d += datetime.timedelta(days=(6 - d.weekday()) % 7)
    return d + datetime.timedelta(weeks=n - 1)


def eastern_offset(utc):
    """Hours from UTC for New York: -4 in daylight time, -5 otherwise.
    DST runs from 2:00 a.m. on the second Sunday of March to 2:00 a.m. on the
    first Sunday of November (US rule since 2007)."""
    y = utc.year
    start = datetime.datetime.combine(_nth_sunday(y, 3, 2), datetime.time(7, 0))   # 2:00 EST
    end = datetime.datetime.combine(_nth_sunday(y, 11, 1), datetime.time(6, 0))    # 2:00 EDT
    naive = utc.replace(tzinfo=None)
    return -4 if start <= naive < end else -5


def to_eastern(utc):
    return utc.replace(tzinfo=None) + datetime.timedelta(hours=eastern_offset(utc))


def eastern_to_utc(local):
    for off in (-4, -5):
        utc = local - datetime.timedelta(hours=off)
        if eastern_offset(utc) == off:
            return utc
    return local + datetime.timedelta(hours=5)


def ts_to_eastern(ts):
    """Unix seconds -> naive New York time. Never uses the machine's time zone."""
    return to_eastern(datetime.datetime(1970, 1, 1) + datetime.timedelta(seconds=int(ts)))


def to_ist(utc):
    """Aware or naive UTC -> naive Indian Standard Time (IST, UTC+05:30)."""
    if utc.tzinfo is not None:
        utc = (utc - utc.utcoffset()).replace(tzinfo=None)
    else:
        utc = utc.replace(tzinfo=None)
    return utc + IST_OFFSET


def ist_to_utc(local):
    """Naive IST -> naive UTC."""
    return local.replace(tzinfo=None) - IST_OFFSET


def ts_to_ist(ts):
    """Unix seconds -> naive IST (fixed UTC+05:30, no DST)."""
    return datetime.datetime(1970, 1, 1) + datetime.timedelta(seconds=int(ts)) + IST_OFFSET


def parse_now(text):
    """ISO time -> naive UTC. A time without an offset is taken as New York time."""
    if not text:
        return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0, tzinfo=None)
    t = text.strip().replace("Z", "+00:00")
    try:
        dt = datetime.datetime.fromisoformat(t)
    except ValueError:
        raise ScanError('--now wants a time like "2026-10-19 10:15" (New York) or ...Z (UTC)')
    if dt.tzinfo is None:
        return eastern_to_utc(dt)
    return (dt - dt.utcoffset()).replace(tzinfo=None)


def parse_hhmm(text, flag="--at"):
    m = re.match(r"^\s*(\d{1,2}):(\d{2})\s*$", text or "")
    if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
        raise ScanError("%s wants a time like 10:15" % flag)
    return datetime.time(int(m.group(1)), int(m.group(2)))


def trading_day(d, market="US"):
    """(is_trading_day, reason). Weekends and exchange holiday list."""
    if d.weekday() >= 5:
        return False, "weekend"
    if market == "IN":
        name = NSE_HOLIDAYS.get(d.isoformat())
        if name:
            return False, "NSE holiday: " + name
        return True, ""
    name = NYSE_HOLIDAYS.get(d.isoformat())
    if name:
        return False, "NYSE holiday: " + name
    return True, ""


def session(d, market="US"):
    """(open_time, close_time) in market local time, or None if closed all day."""
    ok, _ = trading_day(d, market)
    if not ok:
        return None
    if market == "IN":
        return NSE_OPEN, NSE_CLOSE
    return OPEN, (EARLY if d.isoformat() in NYSE_EARLY_CLOSE else CLOSE)


def clock_label(t):
    """datetime or time -> '10:15 AM'."""
    h = t.hour % 12 or 12
    return "%d:%02d %s" % (h, t.minute, "AM" if t.hour < 12 else "PM")


def day_label(d):
    return "%s %s" % (d.strftime("%a"), d.isoformat())


# ---------------------------------------------------------------- symbols & presets

INDIAN_NIFTY_50 = [
    ("RELIANCE.NS", "Reliance Industries Ltd"),
    ("TCS.NS", "Tata Consultancy Services Ltd"),
    ("HDFCBANK.NS", "HDFC Bank Ltd"),
    ("INFY.NS", "Infosys Ltd"),
    ("ICICIBANK.NS", "ICICI Bank Ltd"),
    ("BHARTIARTL.NS", "Bharti Airtel Ltd"),
    ("SBIN.NS", "State Bank of India"),
    ("ITC.NS", "ITC Ltd"),
    ("LT.NS", "Larsen & Toubro Ltd"),
    ("HINDUNILVR.NS", "Hindustan Unilever Ltd"),
    ("KOTAKBANK.NS", "Kotak Mahindra Bank Ltd"),
    ("AXISBANK.NS", "Axis Bank Ltd"),
    ("MARUTI.NS", "Maruti Suzuki India Ltd"),
    ("SUNPHARMA.NS", "Sun Pharmaceutical Industries Ltd"),
    ("TITAN.NS", "Titan Company Ltd"),
    ("BAJFINANCE.NS", "Bajaj Finance Ltd"),
    ("ULTRACEMCO.NS", "UltraTech Cement Ltd"),
    ("ASIANPAINT.NS", "Asian Paints Ltd"),
    ("NTPC.NS", "NTPC Ltd"),
    ("TATASTEEL.NS", "Tata Steel Ltd"),
    ("M&M.NS", "Mahindra & Mahindra Ltd"),
    ("POWERGRID.NS", "Power Grid Corporation of India Ltd"),
    ("TATAMOTORS.NS", "Tata Motors Ltd"),
    ("BAJAJFINSV.NS", "Bajaj Finserv Ltd"),
    ("HCLTECH.NS", "HCL Technologies Ltd"),
    ("ONGC.NS", "Oil & Natural Gas Corporation Ltd"),
    ("ADANIENT.NS", "Adani Enterprises Ltd"),
    ("ADANIPORTS.NS", "Adani Ports and Special Economic Zone Ltd"),
    ("COALINDIA.NS", "Coal India Ltd"),
    ("JSWSTEEL.NS", "JSW Steel Ltd"),
    ("GRASIM.NS", "Grasim Industries Ltd"),
    ("NESTLEIND.NS", "Nestle India Ltd"),
    ("TECHM.NS", "Tech Mahindra Ltd"),
    ("CIPLA.NS", "Cipla Ltd"),
    ("DRREDDY.NS", "Dr. Reddy's Laboratories Ltd"),
    ("BPCL.NS", "Bharat Petroleum Corporation Ltd"),
    ("BRITANNIA.NS", "Britannia Industries Ltd"),
    ("HINDALCO.NS", "Hindalco Industries Ltd"),
    ("EICHERMOT.NS", "Eicher Motors Ltd"),
    ("DIVISLAB.NS", "Divi's Laboratories Ltd"),
    ("APOLLOHOSP.NS", "Apollo Hospitals Enterprise Ltd"),
    ("HEROMOTOCO.NS", "Hero MotoCorp Ltd"),
    ("TATACONSUM.NS", "Tata Consumer Products Ltd"),
    ("SBILIFE.NS", "SBI Life Insurance Company Ltd"),
    ("HDFCLIFE.NS", "HDFC Life Insurance Company Ltd"),
    ("WIPRO.NS", "Wipro Ltd"),
    ("LTIM.NS", "LTIMindtree Ltd"),
    ("BEL.NS", "Bharat Electronics Ltd"),
    ("SHRIRAMFIN.NS", "Shriram Finance Ltd"),
    ("TRENT.NS", "Trent Ltd"),
]

INDIAN_NIFTY_BANK = [
    ("HDFCBANK.NS", "HDFC Bank Ltd"),
    ("ICICIBANK.NS", "ICICI Bank Ltd"),
    ("SBIN.NS", "State Bank of India"),
    ("KOTAKBANK.NS", "Kotak Mahindra Bank Ltd"),
    ("AXISBANK.NS", "Axis Bank Ltd"),
    ("INDUSINDBK.NS", "IndusInd Bank Ltd"),
    ("BANKBARODA.NS", "Bank of Baroda"),
    ("PNB.NS", "Punjab National Bank"),
    ("AUBANK.NS", "AU Small Finance Bank Ltd"),
    ("FEDERALBNK.NS", "Federal Bank Ltd"),
    ("IDFCFIRSTB.NS", "IDFC First Bank Ltd"),
    ("BANDHANBNK.NS", "Bandhan Bank Ltd"),
]

INDIAN_NIFTY_NEXT_50 = [
    ("ABB.NS", "ABB India Ltd"),
    ("AMBUJACEM.NS", "Ambuja Cements Ltd"),
    ("BAJAJHLDNG.NS", "Bajaj Holdings & Investment Ltd"),
    ("BOSCHLTD.NS", "Bosch Ltd"),
    ("CANBK.NS", "Canara Bank"),
    ("CHOLAFIN.NS", "Cholamandalam Investment and Finance Co Ltd"),
    ("COLPAL.NS", "Colgate-Palmolive (India) Ltd"),
    ("DABUR.NS", "Dabur India Ltd"),
    ("DLF.NS", "DLF Ltd"),
    ("GAIL.NS", "GAIL (India) Ltd"),
    ("GODREJCP.NS", "Godrej Consumer Products Ltd"),
    ("HAL.NS", "Hindustan Aeronautics Ltd"),
    ("HAVELLS.NS", "Havells India Ltd"),
    ("ICICIGI.NS", "ICICI Lombard General Insurance Co Ltd"),
    ("ICICIPRULI.NS", "ICICI Prudential Life Insurance Co Ltd"),
    ("INDIGO.NS", "InterGlobe Aviation Ltd"),
    ("IOC.NS", "Indian Oil Corporation Ltd"),
    ("IRCTC.NS", "Indian Railway Catering and Tourism Corp Ltd"),
    ("JINDALSTEL.NS", "Jindal Steel & Power Ltd"),
    ("JIOFIN.NS", "Jio Financial Services Ltd"),
    ("LODHA.NS", "Macrotech Developers Ltd"),
    ("MOTHERSON.NS", "Samvardhana Motherson International Ltd"),
    ("NAUKRI.NS", "Info Edge (India) Ltd"),
    ("PIDILITIND.NS", "Pidilite Industries Ltd"),
    ("PFC.NS", "Power Finance Corporation Ltd"),
    ("RECLTD.NS", "REC Ltd"),
    ("SIEMENS.NS", "Siemens Ltd"),
    ("SRF.NS", "SRF Ltd"),
    ("TVSMOTOR.NS", "TVS Motor Company Ltd"),
    ("UNITDSPR.NS", "United Spirits Ltd"),
    ("VBL.NS", "Varun Beverages Ltd"),
    ("VEDL.NS", "Vedanta Ltd"),
    ("ZYDUSLIFE.NS", "Zydus Lifesciences Ltd"),
    ("BHEL.NS", "Bharat Heavy Electricals Ltd"),
    ("CUMMINSIND.NS", "Cummins India Ltd"),
    ("MUTHOOTFIN.NS", "Muthoot Finance Ltd"),
    ("PERSISTENT.NS", "Persistent Systems Ltd"),
    ("POLYCAB.NS", "Polycab India Ltd"),
    ("TORNTPHARM.NS", "Torrent Pharmaceuticals Ltd"),
    ("AUROPHARMA.NS", "Aurobindo Pharma Ltd"),
    ("LUPIN.NS", "Lupin Ltd"),
    ("MARICO.NS", "Marico Ltd"),
    ("MAXHEALTH.NS", "Max Healthcare Institute Ltd"),
    ("OBEROIRLTY.NS", "Oberoi Realty Ltd"),
    ("PIIND.NS", "PI Industries Ltd"),
    ("PAGEIND.NS", "Page Industries Ltd"),
    ("PETRONET.NS", "Petronet LNG Ltd"),
    ("PGHH.NS", "Procter & Gamble Hygiene and Health Care Ltd"),
    ("SUPREMEIND.NS", "Supreme Industries Ltd"),
    ("TIINDIA.NS", "Tube Investments of India Ltd"),
]

INDIAN_PRESETS = {
    "nifty50": INDIAN_NIFTY_50,
    "nifty_50": INDIAN_NIFTY_50,
    "nifty-50": INDIAN_NIFTY_50,
    "niftybank": INDIAN_NIFTY_BANK,
    "nifty_bank": INDIAN_NIFTY_BANK,
    "banknifty": INDIAN_NIFTY_BANK,
    "niftynext50": INDIAN_NIFTY_NEXT_50,
    "nifty_next_50": INDIAN_NIFTY_NEXT_50,
    "nifty100": INDIAN_NIFTY_50 + INDIAN_NIFTY_NEXT_50,
    "nifty_100": INDIAN_NIFTY_50 + INDIAN_NIFTY_NEXT_50,
    "nifty500": INDIAN_NIFTY_50 + INDIAN_NIFTY_NEXT_50,
    "fno": INDIAN_NIFTY_50 + INDIAN_NIFTY_BANK + INDIAN_NIFTY_NEXT_50[:20],
}


def is_indian_symbol(sym):
    """Determine whether a symbol is an Indian equity or index."""
    s = (sym or "").upper().strip()
    if s.endswith(".NS") or s.endswith(".BO"):
        return True
    if s in INDEX_MAP:
        return True
    if s.startswith("^NSE") or s.startswith("^BSE"):
        return True
    return False



def live_fno_symbols(cache_dir="trader"):
    """Stocks with futures right now, from Upstox's public instrument master (the same cached file
    trader-broker uses). None if it can't be read."""
    import gzip
    import time
    import urllib.request
    cache = os.path.join(cache_dir, "instruments-NSE.json.gz")
    try:
        if not (os.path.isfile(cache) and time.time() - os.path.getmtime(cache) < 86400):
            url = "https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz"
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "trader-skill"}), timeout=60) as r:
                blob = r.read()
            os.makedirs(cache_dir, exist_ok=True)
            with open(cache, "wb") as fh:
                fh.write(blob)
        with gzip.open(cache, "rt", encoding="utf-8") as fh:
            rows = json.load(fh)
    except Exception:
        return None
    names = {s: n for s, n in INDIAN_NIFTY_50 + INDIAN_NIFTY_NEXT_50 + INDIAN_NIFTY_BANK}
    und = sorted({x["underlying_symbol"] for x in rows
                  if x.get("segment") == "NSE_FO" and x.get("instrument_type") == "FUT" and x.get("underlying_type") == "EQUITY"})
    return [(u + ".NS", names.get(u + ".NS", u)) for u in und] or None


INDEX_CSV = {"nifty50": "ind_nifty50list", "niftybank": "ind_niftybanklist", "niftynext50": "ind_niftynext50list",
             "nifty100": "ind_nifty100list", "nifty500": "ind_nifty500list"}


def live_index_symbols(p_name, cache_dir="trader"):
    """Constituents from NSE's public index archive (plain CSV, no login), cached a day.
    [(SYMBOL.NS, company)] or None if it can't be read."""
    import csv
    import io
    stem = INDEX_CSV.get(p_name)
    if not stem:
        return None
    cache = os.path.join(cache_dir, stem + ".csv")
    try:
        if not (os.path.isfile(cache) and time.time() - os.path.getmtime(cache) < 86400):
            req = urllib.request.Request("https://nsearchives.nseindia.com/content/indices/%s.csv" % stem,
                                         headers={"User-Agent": "trader-skill/1.0"})
            with urllib.request.urlopen(req, timeout=30) as r:
                blob = r.read()
            os.makedirs(cache_dir, exist_ok=True)
            with open(cache, "wb") as fh:
                fh.write(blob)
        with open(cache, encoding="utf-8-sig", newline="") as fh:
            rows = list(csv.DictReader(fh))
    except Exception:
        return None
    out = [(r["Symbol"].strip().upper() + ".NS", r.get("Company Name", "").strip()) for r in rows if r.get("Symbol")]
    return out or None


def preset_list(p_name):
    """Preset -> [(symbol, name)]. Index presets come live from NSE's public CSVs, fno from the Upstox master; the
    bundled lists are the fallback (and nifty500's says it is only Nifty 100)."""
    if p_name == "fno":
        live = live_fno_symbols()
        if live:
            return live
        print("scan: could not read the live F&O list; using the bundled approximation (incomplete).", file=sys.stderr)
    live = live_index_symbols(p_name)
    if live:
        return live
    if p_name == "nifty500":
        print("scan: could not read NSE's Nifty 500 list; using the bundled Nifty 100 (100 names), NOT the full "
              "Nifty 500. Use --universe FILE for the full list.", file=sys.stderr)
    return INDIAN_PRESETS.get(p_name)


def detect_market(symbols=None, preset=None, config=None, explicit_market=None):
    """Detect whether scanning US or Indian market."""
    if explicit_market:
        return explicit_market.upper()
    if preset:
        p = preset.lower().replace("-", "").replace("_", "")
        if any(p.startswith(k) for k in ("nifty", "banknifty", "fno", "bse", "sensex")):
            return "IN"
    if symbols:
        for s in symbols:
            if is_indian_symbol(s):
                return "IN"
    if config:
        cfg_market = str(config.get("market") or "").upper()
        if cfg_market in ("IN", "INDIA", "NSE", "BSE"):
            return "IN"
        if config.get("currency") in ("INR", "₹"):
            return "IN"
    return "US"


# ---------------------------------------------------------------- network

def http_get(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def yahoo_get(path):
    """GET a Yahoo Finance path. query2 first, query1 on a 429 or 5xx, and one
    more round after BACKOFF seconds if a 429 was seen. 4xx other than 429 is final."""
    last = "no answer"
    for attempt in (0, 1):
        saw_429 = False
        for host in YAHOO_HOSTS:
            try:
                return http_get(host + path)
            except urllib.error.HTTPError as e:
                last = "HTTP %d" % e.code
                try:
                    body = e.read().decode("utf-8", "replace") if 400 <= e.code < 500 and e.code != 429 else ""
                except Exception:
                    body = ""
                finally:
                    e.close()
                if e.code == 429:
                    saw_429 = True
                    continue
                if e.code >= 500:
                    continue
                if body.strip().startswith("{"):
                    return body  # Yahoo's JSON error ("No data found"), parsed by the caller
                raise ScanError("Yahoo answered %s for %s" % (last, path.split("?")[0]))
            except (urllib.error.URLError, OSError) as e:
                last = str(getattr(e, "reason", e))
                continue
        if attempt == 0 and saw_429:
            SLEEP(BACKOFF)
            continue
        break
    raise ScanError("Yahoo answered %s for %s" % (last, path.split("?")[0]))


def to_yahoo(sym, market=None):
    """Normalize symbol to Yahoo Finance ticker.
    US tickers with dots like BRK.B become BRK-B.
    Indian tickers keep .NS or .BO extension, index symbols mapped to Yahoo symbols."""
    s = sym.upper().strip()
    if s in INDEX_MAP:
        return INDEX_MAP[s]
    if s.endswith(".NS") or s.endswith(".BO") or s.startswith("^"):
        return s
    if market in ("IN", "INDIA", "NSE"):
        return s + ".NS"
    if market == "BSE":
        return s + ".BO"
    return s.replace(".", "-")


def spark_path(symbols, rng, interval, market=None):
    return "/v7/finance/spark?symbols=%s&range=%s&interval=%s" % (
        ",".join(to_yahoo(s, market) for s in symbols), rng, interval)


def chart_path(sym, rng, interval, market=None):
    return "/v8/finance/chart/%s?range=%s&interval=%s" % (to_yahoo(sym, market), rng, interval)


# ---------------------------------------------------------------- parsing

def series_from_result(res):
    """A Yahoo chart result (v8 or one spark response) -> plain lists."""
    q = ((res.get("indicators") or {}).get("quote") or [{}])[0] or {}
    t = res.get("timestamp") or []
    n = len(t)

    def col(k):
        v = q.get(k)
        return list(v) + [None] * (n - len(v)) if isinstance(v, list) else None
    return {"t": list(t), "c": col("close") or [None] * n, "o": col("open"), "h": col("high"),
            "l": col("low"), "v": col("volume"), "meta": res.get("meta") or {}}


def parse_spark(text_or_doc, wanted=None):
    """Spark JSON -> {SYM: series}. Symbols Yahoo does not know are simply missing
    from the answer. `wanted` maps Yahoo symbols back to ours (BRK-B -> BRK.B)."""
    doc = json.loads(text_or_doc) if isinstance(text_or_doc, str) else text_or_doc
    sp = (doc or {}).get("spark") if isinstance(doc, dict) else None
    if not isinstance(sp, dict):
        raise ScanError("Yahoo's spark answer is not the shape this tool knows")
    err = sp.get("error")
    out = {}
    for item in sp.get("result") or []:
        resp = item.get("response") or []
        if not resp:
            continue
        ysym = item.get("symbol") or (resp[0].get("meta") or {}).get("symbol") or ""
        sym = (wanted or {}).get(ysym.upper(), ysym.upper().replace("-", "."))
        out[sym] = series_from_result(resp[0])
    if err and not out:
        raise ScanError("Yahoo spark: %s" % (err.get("description") or err.get("code") or err))
    return out


def parse_chart(text_or_doc, sym=""):
    """v8 chart JSON -> series. Raises ScanError for Yahoo's 'no data' answers."""
    try:
        doc = json.loads(text_or_doc) if isinstance(text_or_doc, str) else text_or_doc
        chart = doc["chart"]
    except (ValueError, KeyError, TypeError):
        raise ScanError("Yahoo returned something unexpected for %s" % sym)
    res = chart.get("result")
    if not res:
        err = chart.get("error") or {}
        raise ScanError("no data for %s: %s" % (sym, err.get("description", "unknown ticker")))
    return series_from_result(res[0])


def _read_maybe_gz(path):
    if path.endswith(".gz"):
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            return fh.read()
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _stem(name):
    for ext in (".json.gz", ".json"):
        if name.endswith(ext):
            return name[:-len(ext)]
    return name


def _absorb(doc, name_hint, data, errors):
    """One parsed offline JSON doc -> data / errors."""
    if isinstance(doc, dict) and "spark" in doc:
        data.update(parse_spark(doc))
    elif isinstance(doc, dict) and "chart" in doc:
        sym = name_hint
        if not sym:
            try:
                sym = (doc["chart"].get("result") or [{}])[0]["meta"]["symbol"]
            except (AttributeError, KeyError, TypeError, IndexError):
                raise ScanError("an offline v8 chart without a symbol: name the file <TICKER>.json")
        sym = sym.upper().replace("-", ".")
        try:
            data[sym] = parse_chart(doc, sym)
        except ScanError as e:
            errors[sym] = str(e)
    elif isinstance(doc, dict):
        for k, v in doc.items():
            _absorb(v, k, data, errors)
    else:
        raise ScanError("offline file %s is not a spark answer, a v8 chart or a bundle" % name_hint)


def load_offline(path):
    """A folder of <T>.json(.gz) files or one file -> ({SYM: series}, {SYM: error})."""
    data, errors = {}, {}
    if os.path.isdir(path):
        names = sorted(f for f in os.listdir(path) if f.endswith((".json", ".json.gz")))
        if not names:
            raise ScanError("no .json files in %s" % path)
        for f in names:
            try:
                doc = json.loads(_read_maybe_gz(os.path.join(path, f)))
            except ValueError as e:
                raise ScanError("%s is not valid JSON: %s" % (f, e))
            if isinstance(doc, dict) and ("chart" in doc or "spark" in doc):
                _absorb(doc, "" if "spark" in doc else _stem(f), data, errors)
            else:
                _absorb(doc, "", data, errors)
    elif os.path.isfile(path):
        try:
            doc = json.loads(_read_maybe_gz(path))
        except ValueError as e:
            raise ScanError("%s is not valid JSON: %s" % (path, e))
        _absorb(doc, "", data, errors)
    else:
        raise ScanError("no offline file or folder at %s" % path)
    return data, errors


# ---------------------------------------------------------------- universe

TICKER = re.compile(r"^[A-Z]{1,5}$|^[A-Z]{1,4}\.[A-Z]$")
ALWAYS_OUT = [
    ("warrant", re.compile(r"\bwarrants?\b")),
    ("unit or right", re.compile(r"\b(units?|rights?),? (each|that|entitling)\b|(?<!the )\brights? to\b")),
    ("note", re.compile(r"\b(notes?|etns?|debentures?)\b")),
    ("coupon", re.compile(r"%")),
    ("depositary", re.compile(r"(?<!american )(?<!global )\bdepositary shares?\b")),
]
COMMON = re.compile(
    r"\bcommon (stock|shares?|units?)\b|\bord(inary)? shares?\b|\bshares? of beneficial interest\b"
    r"|\b(american|global) deposit[ao]ry\b|\bads[s]?\b|\badrs?\b"
    r"|\b(limited partnership|partnership|trust) units?\b|\bl\.?p\.? units?\b"
    r"|\bunits? (of|representing)\b.*\b(limited partner|partnership|beneficial interest|limited liability)")
SOFT_OUT = [
    ("unit", re.compile(r"\bunits?\b")),
    ("right", re.compile(r"\brights?\b")),
    ("bond", re.compile(r"\bbonds?\b")),
    ("preferred", re.compile(r"\bpreferred\b|\bpreference\b")),
]


def classify(row):
    """One symbol-directory row -> 'keep' or the rule that drops it."""
    sym = (row.get("Symbol") or row.get("ACT Symbol") or "").strip()
    if (row.get("Test Issue") or "").strip() != "N":
        return "test issue"
    if (row.get("ETF") or "").strip() != "N":
        return "ETF"
    if (row.get("NextShares") or "N").strip() != "N":
        return "NextShares"
    if not TICKER.match(sym):
        return "not a plain ticker"
    if re.search(r"\.[UWR]$", sym):
        return "unit / warrant / right suffix"
    name = " ".join((row.get("Security Name") or "").lower().split())
    for tag, rx in ALWAYS_OUT:
        if rx.search(name):
            return tag
    if COMMON.search(name):
        return "keep"
    for tag, rx in SOFT_OUT:
        if rx.search(name):
            return tag
    return "keep"


def parse_symdir(text):
    """Nasdaq Trader pipe file -> (rows, file_created). The last line is 'File Creation Time: ...'."""
    lines = [ln for ln in text.replace("\r", "").split("\n") if ln.strip()]
    if not lines or "|" not in lines[0]:
        raise ScanError("not a Nasdaq Trader symbol directory")
    hdr = [h.strip() for h in lines[0].split("|")]
    if "Security Name" not in hdr or not ({"Symbol", "ACT Symbol"} & set(hdr)):
        raise ScanError("not a Nasdaq Trader symbol directory (header %s)" % lines[0][:60])
    rows, created = [], None
    for ln in lines[1:]:
        if ln.startswith("File Creation Time"):
            m = re.search(r"(\d{2})(\d{2})(\d{4})(\d{2}):(\d{2})", ln)
            if m:
                created = "%s-%s-%s %s:%s" % (m.group(3), m.group(1), m.group(2), m.group(4), m.group(5))
            continue
        rows.append(dict(zip(hdr, ln.split("|"))))
    return rows, created


def build_universe(texts):
    """{filename: text} -> (kept [(SYM, name)], counts, created)."""
    kept, counts, created = {}, {}, None
    for fname in NASDAQ_FILES:
        if fname not in texts:
            raise ScanError("missing %s" % fname)
        rows, c = parse_symdir(texts[fname])
        created = created or c
        counts["rows"] = counts.get("rows", 0) + len(rows)
        for r in rows:
            why = classify(r)
            counts[why] = counts.get(why, 0) + 1
            if why == "keep":
                sym = (r.get("Symbol") or r.get("ACT Symbol")).strip()
                kept[sym] = " ".join((r.get("Security Name") or "").split())
    return sorted(kept.items()), counts, created


# ---------------------------------------------------------------- daily setups

def rsi(closes, n=14):
    """Wilder's RSI on closes (the same math as the reel's refs/scan.py)."""
    if len(closes) < n + 1:
        return None
    g = [max(0.0, closes[i] - closes[i - 1]) for i in range(1, len(closes))]
    lo = [max(0.0, closes[i - 1] - closes[i]) for i in range(1, len(closes))]
    ag, al = sum(g[:n]) / n, sum(lo[:n]) / n
    for i in range(n, len(g)):
        ag = (ag * (n - 1) + g[i]) / n
        al = (al * (n - 1) + lo[i]) / n
    return 100 - 100 / (1 + ag / al) if al else 100.0


def daily_closes(series, now_utc, market="US"):
    """[(date, close)] of COMPLETE daily bars: null closes dropped, today's bar dropped
    until the session closes, anything dated after the clock dropped."""
    if market == "IN":
        now = to_ist(now_utc)
        today = now.date()
        s = session(today, market="IN")
        today_done = s is None or now.time() >= s[1]
        out = {}
        for ts, c in zip(series["t"], series["c"]):
            if c is None:
                continue
            d = ts_to_ist(ts).date()
            if d > today or (d == today and not today_done):
                continue
            out[d] = float(c)
        return sorted(out.items())

    now = to_eastern(now_utc)
    today = now.date()
    s = session(today, market="US")
    today_done = s is None or now.time() >= s[1]
    out = {}
    for ts, c in zip(series["t"], series["c"]):
        if c is None:
            continue
        d = ts_to_eastern(ts).date()
        if d > today or (d == today and not today_done):
            continue
        out[d] = float(c)  # one bar a day: a late duplicate (the live tick) wins
    return sorted(out.items())


def find_setups(closes):
    """closes (oldest first, >= 30) -> (setup keys, numbers)."""
    c = closes
    last = c[-1]
    hi20 = max(c[-21:-1])
    ma20 = sum(c[-20:]) / 20.0
    ma20_prev = sum(c[-21:-1]) / 20.0
    run = c[-6] / c[-16] - 1
    rng5 = (max(c[-5:]) - min(c[-5:])) / last
    r = rsi(c)
    r_prev = rsi(c[:-1])
    tags = []
    if last > hi20:
        tags.append("breakout_20d")
    if run > 0.10 and rng5 < 0.05:
        tags.append("bull_flag")
    if c[-2] < ma20_prev and last > ma20:
        tags.append("reclaim_ma20")
    if r is not None and r < 30:
        tags.append("oversold")
    if r_prev is not None and r_prev < 30 and last > c[-2]:
        tags.append("oversold_bounce")
    return tags, {"rsi": r, "rsi_prev": r_prev, "high20": hi20, "ma20": ma20,
                  "run_pct": run * 100, "range5_pct": rng5 * 100}


def scan_daily(data, symbols, now_utc, min_price, min_volume, market="US"):
    """Pure. data = {SYM: series}. Returns (hits, counts, last_date)."""
    hits, dates = [], {}
    counts = {"requested": len(symbols), "fetched": 0, "illiquid": 0, "short": 0, "scanned": 0}
    for sym in symbols:
        s = data.get(sym)
        if not s:
            continue
        counts["fetched"] += 1
        sym_market = "IN" if is_indian_symbol(sym) or market == "IN" else "US"
        bars = daily_closes(s, now_utc, sym_market)
        meta = s.get("meta") or {}
        price = meta.get("regularMarketPrice")
        if price is None and bars:
            price = bars[-1][1]
        vol = meta.get("regularMarketVolume")
        if price is None or price < min_price or (min_volume > 0 and (vol is None or vol < min_volume)):
            counts["illiquid"] += 1
            continue
        if len(bars) < MIN_DAILY_BARS:
            counts["short"] += 1
            continue
        counts["scanned"] += 1
        d_last = bars[-1][0]
        dates[d_last] = dates.get(d_last, 0) + 1
        closes = [c for _, c in bars]
        tags, nums = find_setups(closes)
        if tags:
            hits.append({"symbol": sym, "setups": tags, "price": round(closes[-1], 2),
                         "close_date": d_last.isoformat(), "rsi": round(nums["rsi"], 1),
                         "volume": int(vol) if vol is not None else None})
    hits.sort(key=lambda h: (-(h["price"] * (h["volume"] or 0)), h["symbol"]))  # most traded first
    last_date = max(dates, key=lambda d: (dates[d], d)) if dates else None
    counts["hits"] = len(hits)
    return hits, counts, last_date


# ---------------------------------------------------------------- opening-range breakout

def slot(day, k, market="US"):
    base_time = NSE_OPEN if market == "IN" else OPEN
    return datetime.datetime.combine(day, base_time) + datetime.timedelta(minutes=BAR_MIN * k)


def session_bars(series, day, cutoff, market="US"):
    """5-minute bars of `day` that are complete at `cutoff` (naive local time),
    on the grid, with a close. Sorted, one per slot."""
    s = session(day, market)
    open_t = NSE_OPEN if market == "IN" else OPEN
    close_t = s[1] if s else (NSE_CLOSE if market == "IN" else CLOSE)
    to_local = ts_to_ist if market == "IN" else ts_to_eastern
    out = {}
    hs, ls = series.get("h"), series.get("l")
    for i, ts in enumerate(series["t"]):
        c = series["c"][i]
        if c is None:
            continue
        lt = to_local(ts)
        if lt.date() != day or lt.second or lt.minute % BAR_MIN:
            continue  # another day, or live tick off the grid
        if lt.time() < open_t or lt.time() >= close_t:
            continue  # pre-market, after hours, closing print
        if lt + datetime.timedelta(minutes=BAR_MIN) > cutoff:
            continue  # still in progress at cutoff
        v = series.get("v")
        out[lt] = {"start": lt, "c": float(c),
                   "h": None if not hs or hs[i] is None else float(hs[i]),
                   "l": None if not ls or ls[i] is None else float(ls[i]),
                   "v": (v[i] or 0) if v else 0}
    return [out[k] for k in sorted(out)]


def usable(bars, day, cutoff, n_range, market="US"):
    """Every slot from market open to the cutoff is there, and at least the range."""
    s0 = slot(day, 0, market)
    want = int((cutoff - s0).total_seconds() // (BAR_MIN * 60))
    s = session(day, market)
    if s:
        want = min(want, int((datetime.datetime.combine(day, s[1]) - s0).total_seconds()
                             // (BAR_MIN * 60)))
    if want < n_range or len(bars) != want:
        return False
    return all(b["start"] == slot(day, k, market) for k, b in enumerate(bars))


def orb_candidate(bars, n_range):
    """Stage 1, closes only: some completed bar after the range closed at or above
    the highest close of the range. Necessary for a breakout."""
    top = max(b["c"] for b in bars[:n_range])
    return any(b["c"] >= top for b in bars[n_range:])


def orb_hit(bars, n_range):
    """Stage 2, real highs and lows: the first completed bar to CLOSE above range high."""
    rng = bars[:n_range]
    if any(b["h"] is None or b["l"] is None for b in rng):
        return None
    oh = max(b["h"] for b in rng)
    ol = min(b["l"] for b in rng)
    for k in range(n_range, len(bars)):
        b = bars[k]
        if b["c"] > oh:
            closes_at = b["start"] + datetime.timedelta(minutes=BAR_MIN)
            return {"price": round(b["c"], 2), "at": clock_label(closes_at),
                    "bar_start": b["start"].strftime("%H:%M"), "bar_index": k,
                    "or_high": round(oh, 2), "or_low": round(ol, 2),
                    "pct_over": round((b["c"] / oh - 1) * 100, 2), "_t": closes_at}
    return None


def orb_window(now_utc, at, or_minutes, until, explicit_day=None, market="US"):
    """-> (day, cutoff naive local time) or raise ScanError with plain message."""
    now = to_ist(now_utc) if market == "IN" else to_eastern(now_utc)
    day = explicit_day or now.date()
    tz_label = "IST" if market == "IN" else "ET"
    open_str = "09:15" if market == "IN" else "9:30"
    if or_minutes < BAR_MIN or or_minutes % BAR_MIN or or_minutes > 120:
        raise ScanError("--or-minutes wants a multiple of 5 from 5 to 120 (15 is the classic)")
    ok, why = trading_day(day, market)
    if not ok:
        raise ScanError("No session on %s (%s): the market is closed. Run orb on a trading day "
                        "after the opening range, or replay a past day with --offline." % (day_label(day), why))
    s = session(day, market)
    range_end = slot(day, or_minutes // BAR_MIN, market)
    first_close = range_end + datetime.timedelta(minutes=BAR_MIN)
    close_dt = datetime.datetime.combine(day, s[1])
    if at is not None:
        cutoff = datetime.datetime.combine(day, at)
        if explicit_day is None and cutoff > now + datetime.timedelta(seconds=59):
            raise ScanError("--at %s is later than now (%s %s)." % (at.strftime("%H:%M"), clock_label(now), tz_label))
        if cutoff > close_dt:
            raise ScanError("--at %s is after the %s close on %s." % (at.strftime("%H:%M"), clock_label(close_dt),
                                                                       day_label(day)))
    else:
        cutoff = now.replace(second=0, microsecond=0)
        if cutoff >= close_dt:
            raise ScanError("The market closed at %s %s on %s. Replay the session with --at 10:15."
                            % (clock_label(close_dt), tz_label, day_label(day)))
    if cutoff < first_close:
        raise ScanError("It is %s %s: the %d-minute opening range runs %s to %s and the first bar after it "
                        "closes at %s. Run orb again after %s."
                        % (clock_label(cutoff), tz_label, or_minutes, open_str, clock_label(range_end),
                           clock_label(first_close), clock_label(first_close)))
    if until is not None and cutoff.time() > until:
        raise ScanError("It is %s %s, after %s, your last time for new entries (no_entries_after): an "
                        "opening-range breakout found now is expired. Run orb tomorrow after %s, or replay "
                        "today with --at 10:15." % (clock_label(cutoff), tz_label, until.strftime("%H:%M"),
                                                      clock_label(first_close)))
    return day, cutoff


def scan_orb(stage1, symbols, day, cutoff, or_minutes, min_price=0.0, min_volume=0, confirm=None,
             max_confirm=None, market="US"):
    """stage1 = {SYM: series} with closes (spark or v8). confirm(sym) -> series with highs/lows.
    Returns (hits, counts, failed {sym: why})."""
    n_range = or_minutes // BAR_MIN
    counts = {"requested": len(symbols), "fetched": 0, "unusable": 0, "illiquid": 0, "scanned": 0,
              "candidates": 0, "confirmed": 0, "unconfirmed": 0}
    hits, failed, cands = [], {}, []
    for sym in symbols:
        s = stage1.get(sym)
        if not s:
            continue
        counts["fetched"] += 1
        sym_market = "IN" if is_indian_symbol(sym) or market == "IN" else "US"
        bars = session_bars(s, day, cutoff, sym_market)
        if not usable(bars, day, cutoff, n_range, sym_market):
            counts["unusable"] += 1
            continue
        vol = sum(b["v"] for b in bars) if s.get("v") else (s.get("meta") or {}).get("regularMarketVolume") or 0
        if bars[-1]["c"] < min_price or vol < min_volume:
            counts["illiquid"] += 1
            continue
        counts["scanned"] += 1
        if orb_candidate(bars, n_range):
            cands.append((sym, bars, bars[-1]["c"] * vol))
    counts["candidates"] = len(cands)
    if confirm is not None and max_confirm and len(cands) > max_confirm:
        cands.sort(key=lambda x: -x[2])
        counts["unconfirmed"] = len(cands) - max_confirm
        cands = cands[:max_confirm]
    for sym, bars, _ in cands:
        full = bars
        sym_market = "IN" if is_indian_symbol(sym) or market == "IN" else "US"
        if confirm is not None:
            try:
                s2 = confirm(sym)
            except ScanError as e:
                failed[sym] = str(e)
                continue
            full = session_bars(s2, day, cutoff, sym_market)
            if not usable(full, day, cutoff, n_range, sym_market):
                failed[sym] = "the v8 chart has a missing bar"
                continue
        counts["confirmed"] += 1
        h = orb_hit(full, n_range)
        if h:
            hits.append(dict({"symbol": sym, "setups": ["orb"]}, **h))
    hits.sort(key=lambda x: (x["_t"], x["symbol"]))
    for h in hits:
        del h["_t"]
    counts["hits"] = len(hits)
    return hits, counts, failed


# ---------------------------------------------------------------- fetching

def progress(a, msg):
    if not getattr(a, "json", False) and not getattr(a, "quiet", False):
        print(msg, file=sys.stderr)


def fetch_spark(symbols, rng, interval, pause, a=None, market=None):
    """Batches of 20. -> ({SYM: series}, {SYM: error})."""
    data, failed = {}, {}
    batches = [symbols[i:i + SPARK_MAX] for i in range(0, len(symbols), SPARK_MAX)]
    for k, batch in enumerate(batches):
        if k and pause:
            SLEEP(pause)
        wanted = {to_yahoo(s, market): s for s in batch}
        try:
            got = parse_spark(yahoo_get(spark_path(batch, rng, interval, market)), wanted)
        except (ScanError, ValueError) as e:
            for s in batch:
                failed[s] = str(e)
            continue
        for s in batch:
            if s in got:
                data[s] = got[s]
            else:
                failed[s] = "no data from Yahoo"
        if a is not None and len(batches) > 25 and (k + 1) % 50 == 0:
            progress(a, "  spark %d of %d requests" % (k + 1, len(batches)))
    return data, failed


def read_symbols(a, offline_syms=None):
    if getattr(a, "symbols", None):
        syms = [s for s in re.split(r"[,\s]+", a.symbols.upper()) if s]
    elif getattr(a, "preset", None):
        p_name = a.preset.lower().replace("-", "").replace("_", "")
        preset_items = preset_list(p_name)
        if not preset_items:
            raise ScanError("unknown preset '%s'. Choices: %s" % (
                a.preset, ", ".join(sorted(INDIAN_PRESETS.keys()))))
        syms = [s for s, _ in preset_items]
    elif getattr(a, "watchlist", None):
        syms = symbols_from_file(a.watchlist)
    elif offline_syms is not None and not getattr(a, "universe", None):
        syms = sorted(offline_syms)  # offline: every chart in file unless list given
    else:
        path = getattr(a, "universe", None) or DEFAULT_UNIVERSE
        if not os.path.exists(path):
            raise ScanError("no %s yet: run `scan.py universe` first, or pass --symbols/--preset" % path)
        syms = symbols_from_file(path)
    out = []
    for s in syms:
        if s not in out:
            out.append(s)
    if getattr(a, "limit", None):
        out = out[:a.limit]
    if not out:
        raise ScanError("no symbols to scan")
    return out


def symbols_from_file(path):
    if not os.path.exists(path):
        raise ScanError("no file at %s" % path)
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    if text.lstrip().startswith(("{", "[")):
        try:
            doc = json.loads(text)
        except ValueError as e:
            raise ScanError("%s is not valid JSON: %s" % (path, e))
        items = doc.get("hits", []) if isinstance(doc, dict) else doc
        return [str(x.get("symbol") if isinstance(x, dict) else x).upper() for x in items if x]
    out = []
    for ln in text.splitlines():
        ln = ln.strip()
        if ln and not ln.startswith("#"):
            out.append(ln.split("|")[0].strip().upper())
    return out


def read_config(path):
    if not path or not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as fh:
            cfg = json.load(fh)
    except ValueError as e:
        raise ScanError("%s is not valid JSON: %s" % (path, e))
    return cfg if isinstance(cfg, dict) else {}


def write_json(path, doc):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2)
        fh.write("\n")


def money(x, market="US"):
    if market == "IN":
        return "₹{:,.2f}".format(x)
    return "${:,.2f}".format(x)


# ---------------------------------------------------------------- commands

def cmd_universe(a):
    t0 = time.time()
    # Check if Indian market preset was requested
    if getattr(a, "preset", None) or getattr(a, "market", None) == "IN":
        p_name = (getattr(a, "preset", None) or "nifty50").lower().replace("-", "").replace("_", "")
        preset_items = preset_list(p_name)
        if not preset_items:
            raise ScanError("unknown preset '%s'. Available: %s" % (
                p_name, ", ".join(sorted(INDIAN_PRESETS.keys()))))
        kept = preset_items
        if not a.no_write:
            d = os.path.dirname(a.out)
            if d:
                os.makedirs(d, exist_ok=True)
            with open(a.out, "w", encoding="utf-8") as fh:
                for sym, name in kept:
                    fh.write("%s|%s\n" % (sym, name))
        if a.json:
            print(json.dumps({"count": len(kept), "preset": p_name, "market": "IN",
                              "out": None if a.no_write else a.out,
                              "seconds": round(time.time() - t0, 1)}, indent=2))
        else:
            print("Indian Market Universe (%s): %d constituents." % (p_name.upper(), len(kept)))
            if not a.no_write:
                print("Wrote %s (one SYMBOL|Name a line)." % a.out)
        return 0 if kept else 1

    texts = {}
    for k, fname in enumerate(NASDAQ_FILES):
        if a.offline:
            p = os.path.join(a.offline, fname)
            if not os.path.exists(p):
                raise ScanError("no %s in %s" % (fname, a.offline))
            with open(p, encoding="utf-8", errors="replace") as fh:
                texts[fname] = fh.read()
        else:
            if k and a.pause:
                SLEEP(a.pause)
            try:
                texts[fname] = http_get(NASDAQ_URL + fname, timeout=60)
            except urllib.error.HTTPError as e:
                raise ScanError("nasdaqtrader.com answered HTTP %d for %s" % (e.code, fname))
            except (urllib.error.URLError, OSError) as e:
                raise ScanError("could not reach nasdaqtrader.com: %s" % getattr(e, "reason", e))
    kept, counts, created = build_universe(texts)
    if not a.no_write:
        d = os.path.dirname(a.out)
        if d:
            os.makedirs(d, exist_ok=True)
        with open(a.out, "w", encoding="utf-8") as fh:
            for sym, name in kept:
                fh.write("%s|%s\n" % (sym, name))
    dropped = {k: v for k, v in counts.items() if k not in ("keep", "rows")}
    if a.json:
        print(json.dumps({"count": len(kept), "rows": counts.get("rows", 0), "files_created": created,
                          "dropped": dropped, "out": None if a.no_write else a.out,
                          "seconds": round(time.time() - t0, 1)}, indent=2))
    else:
        print("Nasdaq Trader symbol directories%s: %s rows." % (
            (", files of %s ET" % created) if created else "", "{:,}".format(counts.get("rows", 0))))
        print("Kept %s US-listed common stocks, ordinary shares and ADRs." % "{:,}".format(len(kept)))
        print("Dropped: " + ", ".join("%s %s" % ("{:,}".format(v), k)
                                      for k, v in sorted(dropped.items(), key=lambda kv: -kv[1])) + ".")
        if not a.no_write:
            print("Wrote %s (one SYMBOL|Name a line)." % a.out)
    return 0 if kept else 1


def cmd_daily(a):
    t0 = time.time()
    cfg = read_config(getattr(a, "config", "trader/config.json"))
    offline_syms = None
    if a.offline:
        data, failed = load_offline(a.offline)
        offline_syms = sorted(set(data) | set(failed))
    symbols = read_symbols(a, offline_syms)
    market = detect_market(symbols, getattr(a, "preset", None), cfg, getattr(a, "market", None))
    now = parse_now(a.now)
    if not a.offline:
        progress(a, "Fetching daily closes for %s symbols (%d spark requests)..."
                 % ("{:,}".format(len(symbols)), (len(symbols) + SPARK_MAX - 1) // SPARK_MAX))
        data, failed = fetch_spark(symbols, "3mo", "1d", a.pause, a, market)
    hits, counts, last_date = scan_daily(data, symbols, now, a.min_price, a.min_volume, market)
    nfailed = len(symbols) - counts["fetched"]
    as_of = ("%s close" % last_date.isoformat()) if last_date else None
    doc = {"created_by": "trader-scanner", "created_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "mode": "daily",
           "market": market, "as_of": as_of, "universe": len(symbols), "scanned": counts["scanned"],
           "failed": nfailed, "source": "yahoo", "filters": {"min_price": a.min_price, "min_volume": a.min_volume},
           "note": NOTE, "hits": hits}
    if a.offline:
        doc["offline"] = os.path.basename(a.offline.rstrip("/"))
    if not a.no_write:
        write_json(a.out, doc)
    if a.json:
        print(json.dumps(dict(doc, counts=counts, seconds=round(time.time() - t0, 1)), indent=2))
        return 0 if hits else 1
    tz_label = "IST" if market == "IN" else "ET"
    curr_sym = "₹" if market == "IN" else "$"
    local = to_ist(now) if market == "IN" else to_eastern(now)
    print("Daily scan, %s %s, closes through %s (Yahoo spark, daily closes only)." % (
        local.strftime("%a %Y-%m-%d %H:%M"), tz_label, last_date.isoformat() if last_date else "nothing"))
    print("Fetched %s of %s charts%s. %s passed the liquidity filter (%s%s+ and %s+ shares)%s; "
          "%s scanned." % ("{:,}".format(counts["fetched"]), "{:,}".format(len(symbols)),
                           (" (%s failed or had no data)" % "{:,}".format(nfailed)) if nfailed else "",
                           "{:,}".format(counts["fetched"] - counts["illiquid"]), curr_sym, _n(a.min_price),
                           "{:,}".format(a.min_volume),
                           (", %d had under %d days" % (counts["short"], MIN_DAILY_BARS)) if counts["short"] else "",
                           "{:,}".format(counts["scanned"])))
    if hits:
        per = [(k, sum(1 for h in hits if k in h["setups"])) for k, _ in SETUPS]
        print("%d chart%s matched a setup: %s." % (len(hits), "" if len(hits) == 1 else "s",
                                                  ", ".join("%d %s" % (n, LABEL[k]) for k, n in per if n)))
        print("Most traded first (price x volume):")
    else:
        print("No setups.")
    fmt_daily = "  %-12s %10s  RSI %5.1f  %s" if market == "IN" else "  %-6s %10s  RSI %5.1f  %s"
    for h in hits[:a.show]:
        print(fmt_daily % (h["symbol"], money(h["price"], market), h["rsi"],
                           ", ".join(LABEL[t] for t in h["setups"])))
    if len(hits) > a.show:
        print("  ... and %d more in the watchlist (--show N for more)." % (len(hits) - a.show))
    if not a.no_write:
        print("Wrote %s (mode daily, %d hit%s)." % (a.out, len(hits), "" if len(hits) == 1 else "s"))
    print(NOTE + " Next: pick one chart, then /trader-news TICKER.")
    return 0 if hits else 1


def _n(x):
    x = float(x)
    return str(int(x)) if x == int(x) else "%.2f" % x


def cmd_orb(a):
    t0 = time.time()
    cfg = read_config(a.config)
    offline_syms = None
    data = failed = None
    explicit_day = None
    if a.offline:
        data, failed = load_offline(a.offline)
        offline_syms = sorted(set(data) | set(failed))
    symbols = read_symbols(a, offline_syms)
    market = detect_market(symbols, getattr(a, "preset", None), cfg, getattr(a, "market", None))
    now = parse_now(a.now)
    at = parse_hhmm(a.at) if a.at else None
    default_until = "15:15" if market == "IN" else "15:30"
    until = parse_hhmm(a.until or cfg.get("no_entries_after") or default_until, "--until")

    if a.offline and not a.now:
        to_local = ts_to_ist if market == "IN" else ts_to_eastern
        days = [to_local(s["t"][-1]).date() for s in data.values() if s["t"]]
        if days:
            explicit_day = max(days)
            if at is None:
                raise ScanError("offline replay: give --at HH:MM or --now \"YYYY-MM-DD HH:MM\"")

    day, cutoff = orb_window(now, at, a.or_minutes, until, explicit_day, market)
    confirm = None
    if not a.offline:
        today = (to_ist(parse_now(None)) if market == "IN" else to_eastern(parse_now(None))).date()
        if (today - day).days > 6:
            raise ScanError("Yahoo's free 5-minute history is short: replay %s with --offline" % day.isoformat())
        rng = "1d" if day == today else "5d"
        progress(a, "Stage 1: 5-minute closes for %s symbols (%d spark requests)..."
                 % ("{:,}".format(len(symbols)), (len(symbols) + SPARK_MAX - 1) // SPARK_MAX))
        data, failed = fetch_spark(symbols, rng, "%dm" % BAR_MIN, a.pause, a, market)
        done = []

        def confirm(sym):
            if a.pause:
                SLEEP(a.pause)
            done.append(sym)
            if len(done) % 50 == 0:
                progress(a, "  stage 2: %d v8 charts so far" % len(done))
            return parse_chart(yahoo_get(chart_path(sym, rng, "%dm" % BAR_MIN, market)), sym)

    hits, counts, failed2 = scan_orb(data, symbols, day, cutoff, a.or_minutes, a.min_price, a.min_volume, confirm,
                                     a.max_confirm, market)
    failed = dict(failed or {})
    failed.update(failed2)
    nfailed = len([s for s in symbols if s in failed])
    tz_label = "IST" if market == "IN" else "ET"
    as_of = "%s %s %s" % (day.isoformat(), cutoff.strftime("%H:%M"), tz_label)
    doc = {"created_by": "trader-scanner", "created_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "mode": "orb",
           "market": market, "as_of": as_of, "universe": len(symbols), "scanned": counts["scanned"],
           "failed": nfailed, "unconfirmed": counts["unconfirmed"], "source": "yahoo", "or_minutes": a.or_minutes,
           "interval": "%dm" % BAR_MIN,
           "filters": {"min_price": a.min_price, "min_volume": a.min_volume}, "note": NOTE, "hits": hits}
    if a.offline:
        doc["offline"] = os.path.basename(a.offline.rstrip("/"))
    if not a.no_write:
        write_json(a.out, doc)
    if a.json:
        print(json.dumps(dict(doc, counts=counts, seconds=round(time.time() - t0, 1)), indent=2))
        return 0 if hits else 1

    open_lbl = "9:15" if market == "IN" else "9:30"
    print("Opening-range breakout scan, %s %s %s: %d-minute range (%s to %s), 5-minute bars, Yahoo."
          % (day_label(day), clock_label(cutoff), tz_label, a.or_minutes, open_lbl,
             clock_label(slot(day, a.or_minutes // BAR_MIN, market))))
    print("Fetched %s of %s charts%s; %s usable%s; %s candidates, %s confirmed with real highs and lows."
          % ("{:,}".format(counts["fetched"]), "{:,}".format(len(symbols)),
             (" (%s failed or had no data)" % "{:,}".format(nfailed)) if nfailed else "",
             "{:,}".format(counts["scanned"]),
             (" (%s under the price / volume floor)" % "{:,}".format(counts["illiquid"])) if counts["illiquid"] else "",
             "{:,}".format(counts["candidates"]), "{:,}".format(counts["confirmed"])))
    if counts["unconfirmed"]:
        print("PARTIAL: %s more candidates were not checked (only the %s most traded are; --max-confirm 0 "
              "checks all)." % ("{:,}".format(counts["unconfirmed"]), "{:,}".format(a.max_confirm)))
    if hits:
        print("%d of %s charts closed above their opening-range high by %s:"
              % (len(hits), ("the %s checked" % "{:,}".format(counts["confirmed"])) if counts["unconfirmed"]
                 else "{:,}".format(counts["scanned"]), clock_label(cutoff)))
        fmt_orb = "  %8s  %-12s %10s  range %s to %s  %+.2f%% over" if market == "IN" else "  %8s  %-6s %10s  range %s to %s  %+.2f%% over"
        for h in hits[:a.show]:
            print(fmt_orb % (
                h["at"], h["symbol"], money(h["price"], market), _p(h["or_low"]), _p(h["or_high"]), h["pct_over"]))
        if len(hits) > a.show:
            print("  ... and %d more in the watchlist (--show N for more)." % (len(hits) - a.show))
    else:
        print("No chart closed above its opening-range high by %s." % clock_label(cutoff))
    if not a.no_write:
        print("Wrote %s (mode orb, %d hit%s)." % (a.out, len(hits), "" if len(hits) == 1 else "s"))
    print(NOTE + " Next: pick one chart, then /trader-news TICKER and /trader-entry TICKER.")
    return 0 if hits else 1


def _p(x):
    return "{:,.2f}".format(x)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Chart Scanner (trader-scanner): daily setups and opening-range breakouts.")
    sub = ap.add_subparsers(dest="cmd")

    p = sub.add_parser("universe", help="download symbol lists or load Indian presets -> trader/universe.txt")
    p.add_argument("--out", default=DEFAULT_UNIVERSE)
    p.add_argument("--preset", help="Indian preset (nifty50, niftybank, niftynext50, nifty100, nifty500, fno)")
    p.add_argument("--market", choices=["US", "IN"], help="market choice (US or IN)")
    p.add_argument("--offline", metavar="DIR", help="a folder with nasdaqlisted.txt and otherlisted.txt")
    p.add_argument("--no-write", action="store_true")
    p.add_argument("--pause", type=float, default=PAUSE)
    p.add_argument("--json", action="store_true")

    def common(p, min_price, min_volume):
        g = p.add_mutually_exclusive_group()
        g.add_argument("--symbols", help="comma-separated tickers instead of the universe")
        g.add_argument("--watchlist", metavar="FILE", help="a watchlist.json (hits) or a text file of tickers")
        g.add_argument("--preset", help="Indian preset: nifty50, niftybank, niftynext50, nifty100, nifty500, fno")
        p.add_argument("--market", choices=["US", "IN"], help="market: US or IN (auto-detected if omitted)")
        p.add_argument("--universe", help="symbol list, one SYMBOL|Name a line (default %s)" % DEFAULT_UNIVERSE)
        p.add_argument("--limit", type=int, help="scan only the first N symbols")
        p.add_argument("--min-price", type=float, default=min_price)
        p.add_argument("--min-volume", type=int, default=min_volume)
        p.add_argument("--offline", metavar="PATH", help="fixture folder or file instead of the network")
        p.add_argument("--now", help='pretend it is this time ("2026-10-19 10:15" local, or ...Z)')
        p.add_argument("--out", default="trader/watchlist.json")
        p.add_argument("--no-write", action="store_true")
        p.add_argument("--pause", type=float, default=PAUSE, help="seconds between requests (%.1f)" % PAUSE)
        p.add_argument("--show", type=int, default=50, help="hits to print (all are written)")
        p.add_argument("--quiet", action="store_true")
        p.add_argument("--json", action="store_true")

    p = sub.add_parser("daily", help="pre-market setups from daily closes")
    common(p, 5.0, 1000000)
    p = sub.add_parser("orb", help="opening-range breakouts so far today")
    common(p, 5.0, 0)
    p.add_argument("--or-minutes", type=int, default=15)
    p.add_argument("--max-confirm", type=int, default=MAX_CONFIRM,
                   help="v8 charts to fetch in stage 2, most traded first (%d; 0 = all)" % MAX_CONFIRM)
    p.add_argument("--at", help="cut the clock at this local time, HH:MM")
    p.add_argument("--until", help="no new entries after HH:MM (default: config no_entries_after, 15:30 or 15:15)")
    p.add_argument("--config", default="trader/config.json")
    a = ap.parse_args(argv)
    if not a.cmd:
        ap.print_help()
        return 1
    try:
        return {"universe": cmd_universe, "daily": cmd_daily, "orb": cmd_orb}[a.cmd](a)
    except ScanError as e:
        print("scan.py: %s" % e, file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("scan.py: stopped", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
