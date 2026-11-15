#!/usr/bin/env python3
"""Order tickets and the decision log for trader-broker. Standard library only.

This tool never talks to a broker and never places anything. It does its
work around the one moment that matters, your yes:

  show      renders each proposed order as a plain-English ticket plus the
            exact JSON Claude will send to Alpaca's place_stock_order tool.
            A bracket order shows its stop-loss, take-profit and the max loss
            at the stop. An order is READY only if trader-risk passed THIS
            exact order today (matched by fingerprint) and nobody has decided
            on it yet.
  approve   records your explicit yes for ONE order, with your reply word for
            word. It refuses "yes to all", "sure", "ok", a yes that names a
            different stock or amount, and any order that is not READY.
  decline   records a no.
  result    records what happened after the Allow once / Deny card:
            placed (with the broker's order id), denied, or failed.
  fill      records a fill in trader/portfolio.json (cash and shares).
  flatten   builds a sell ticket that closes a symbol's open position by
            flat_by. It still goes through trader-risk and your yes.
  log       prints today's decisions.

    python3 ticket.py show                    # every order, READY or not
    python3 ticket.py show --index 1 --json   # one ticket as JSON
    python3 ticket.py approve --index 1 --reply "yes"
    python3 ticket.py result --index 1 --status placed --broker-id 61e6...
    python3 ticket.py fill --symbol TSLA --side buy --qty 8 --price 364.20
    python3 ticket.py flatten --symbol TSLA --qty 8
    python3 ticket.py log

Everything is written to trader/state/broker.json (append only). Dates are
New York dates; --now "2026-10-19 10:15" pins the clock.
"""

import argparse
import datetime
import hashlib
import json
import os
import re
import sys

ALPACA_FIELDS = ("symbol", "side", "qty", "notional", "type", "time_in_force", "limit_price",
                 "stop_price", "trail_price", "trail_percent", "extended_hours", "client_order_id",
                 "order_class", "take_profit_limit_price", "stop_loss_stop_price",
                 "stop_loss_limit_price", "advanced_instructions")
STRING_FIELDS = ("qty", "notional", "limit_price", "stop_price", "trail_price", "trail_percent",
                 "take_profit_limit_price", "stop_loss_stop_price", "stop_loss_limit_price")

INDIAN_FIELDS = ("exchange", "product", "instrument_token", "trigger_price", "validity",
                 "disclosed_quantity", "order_type", "transaction_type")
CORE_ORDER_FIELDS = ALPACA_FIELDS + INDIAN_FIELDS

UPSTOX_FIELDS = ("quantity", "product", "validity", "price", "tag", "instrument_token",
                 "order_type", "transaction_type", "disclosed_quantity", "trigger_price", "is_amo")
KITE_FIELDS = ("tradingsymbol", "exchange", "transaction_type", "order_type", "quantity",
               "product", "validity", "price", "trigger_price", "tag")

TIF_WORDS = {"day": "good for today", "gtc": "good until canceled", "opg": "at the open",
             "cls": "at the close", "ioc": "immediate or cancel", "fok": "fill or kill"}
YES_WORDS = ("yes", "y")
BATCH_WORDS = {"all", "every", "everything", "rest", "remaining", "them", "both", "batch", "each"}
DOUBT_WORDS = {"no", "not", "dont", "don't", "wait", "cancel", "stop", "but", "maybe", "later",
               "hold", "unless", "if"}


class TicketError(Exception):
    pass


# ---------------------------------------------------------------- time (New York & India, no tz files)

IST_OFFSET = datetime.timedelta(hours=5, minutes=30)


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


def to_ist(utc):
    """UTC -> IST (Asia/Kolkata, UTC+05:30). Fixed offset, no DST."""
    return utc.replace(tzinfo=None) + IST_OFFSET


def ist_to_utc(local):
    """IST -> naive UTC."""
    return local.replace(tzinfo=None) - IST_OFFSET


def is_indian_symbol(symbol):
    if not symbol:
        return False
    s = str(symbol).strip().upper()
    if s.endswith(".NS") or s.endswith(".BO"):
        return True
    if s.startswith("NSE:") or s.startswith("BSE:"):
        return True
    if s.startswith("NSE_EQ|") or s.startswith("BSE_EQ|"):
        return True
    return False


def is_indian_market(cfg=None, order=None, symbol=None):
    if symbol and is_indian_symbol(symbol):
        return True
    for obj in (order, cfg):
        if not isinstance(obj, dict):
            continue
        sym = obj.get("symbol") or obj.get("tradingsymbol") or obj.get("ticker")
        if sym and is_indian_symbol(sym):
            return True
        curr = str(obj.get("currency") or "").upper()
        if curr in ("INR", "₹", "RS", "RUPEES"):
            return True
        mkt = str(obj.get("market") or "").upper()
        if mkt in ("IN", "INDIA", "NSE", "BSE"):
            return True
        exch = str(obj.get("exchange") or "").upper()
        if exch in ("NSE_EQ", "BSE_EQ", "NSE_FO", "BSE_FO", "NSE", "BSE"):
            return True
        brk = str(obj.get("broker") or "").lower()
        if brk in ("upstox", "zerodha", "kite"):
            return True
    return False


def parse_now(text, is_in=False):
    """ISO time -> naive UTC. A time without an offset is taken as local time (IST if Indian, else New York)."""
    if not text:
        return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0, tzinfo=None)
    t = text.strip().replace("Z", "+00:00")
    try:
        dt = datetime.datetime.fromisoformat(t)
    except ValueError:
        lbl = "IST / New York" if is_in else "New York"
        raise TicketError("--now wants a time like \"2026-10-19 10:15\" (%s) or ...Z (UTC)" % lbl)
    if dt.tzinfo is None:
        return ist_to_utc(dt) if is_in else eastern_to_utc(dt)
    return (dt - dt.utcoffset()).replace(tzinfo=None)


def ny_iso(now_utc):
    """naive UTC -> "2026-10-19T10:15:00-04:00" (New York wall clock with its offset)."""
    off = eastern_offset(now_utc)
    return "%s%+03d:00" % (to_eastern(now_utc).replace(microsecond=0).isoformat(), off)


def local_iso(now_utc, is_in=False):
    if is_in:
        return "%s+05:30" % to_ist(now_utc).replace(microsecond=0).isoformat()
    return ny_iso(now_utc)


# ---------------------------------------------------------------- files

def read_json(path, what, required=True):
    if not path or not os.path.exists(path):
        if required:
            raise TicketError("no %s at %s" % (what, path))
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except json.JSONDecodeError as e:
        raise TicketError("%s is not valid JSON: %s" % (path, e))


def write_json(path, data):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
        fh.write("\n")
    os.replace(tmp, path)


def rec_day(rec):
    """The trading day a decision belongs to: its "date" (the day it was made under), else the day of "at"."""
    return str(rec.get("date") or str(rec.get("at", ""))[:10])


# ---------------------------------------------------------------- the order

def fingerprint(order):
    """Same function as trader-risk/check.py: identifies the exact order."""
    core = {k: order[k] for k in CORE_ORDER_FIELDS if k in order and order[k] is not None}
    blob = json.dumps(core, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def _str_num(v):
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        f = float(v)
        return str(int(f)) if f == int(f) else repr(f)
    return str(v)


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


INSTRUMENT_URL = "https://assets.upstox.com/market-quote/instruments/exchange/%s.json.gz"
_MASTER = {}


def load_instrument_master(exchange="NSE", path=None, cache_dir="trader", max_age_h=24):
    """{TRADING_SYMBOL: row} for the exchange's cash equities, from Upstox's public instrument master
    (no login). Cached for a day; `path` (or $TRADER_INSTRUMENTS) reads a saved file instead (offline/tests)."""
    import gzip
    import time
    import urllib.request
    path = path or os.environ.get("TRADER_INSTRUMENTS")
    key = (exchange, path)
    if key in _MASTER:
        return _MASTER[key]
    cache = os.path.join(cache_dir, "instruments-%s.json.gz" % exchange)
    if path:
        src = path
    elif os.path.isfile(cache) and time.time() - os.path.getmtime(cache) < max_age_h * 3600:
        src = cache
    else:
        try:
            req = urllib.request.Request(INSTRUMENT_URL % exchange, headers={"User-Agent": "trader-skill"})
            with urllib.request.urlopen(req, timeout=60) as r:
                blob = r.read()
        except Exception as exc:
            raise TicketError("could not download the Upstox instrument list (%s). Set TRADER_INSTRUMENTS to a "
                              "saved file, or put the instrument_token in the order" % exc)
        os.makedirs(cache_dir, exist_ok=True)
        with open(cache, "wb") as fh:
            fh.write(blob)
        src = cache
    opener = gzip.open if src.endswith(".gz") else open
    with opener(src, "rt", encoding="utf-8") as fh:
        rows = json.load(fh)
    seg = "%s_EQ" % exchange
    _MASTER[key] = {str(x.get("trading_symbol", "")).upper(): x for x in rows
                    if x.get("segment") == seg and x.get("instrument_type") in ("EQ", "BE", "SM", None)}
    return _MASTER[key]


def resolve_upstox_instrument(symbol, exchange="NSE_EQ", path=None):
    """Bare/.NS symbol -> master row (ISIN-based instrument_key, tick_size in paise, lot and freeze qty)."""
    clean = str(symbol).strip().upper().replace(".NS", "").replace(".BO", "")
    row = load_instrument_master(exchange.split("_")[0], path=path).get(clean)
    if not row:
        raise TicketError("%s is not a tradable %s equity in the Upstox instrument list: no ticket" % (clean, exchange))
    return row


def upstox_tool_input(order, cfg=None):
    """Exact arguments for upstox_place_order following Upstox API v2 conventions."""
    cfg = cfg or {}
    sym = str(order.get("symbol", order.get("tradingsymbol", ""))).strip().upper()
    clean_sym = sym.replace(".NS", "").replace(".BO", "")
    exchange = str(order.get("exchange") or cfg.get("exchange") or ("BSE_EQ" if ".BO" in sym else "NSE_EQ")).upper()

    qty = int(float(order.get("quantity") or order.get("qty") or 0))
    prod_raw = str(order.get("product") or cfg.get("product") or "I").upper()
    product = "I" if prod_raw in ("I", "MIS") else "D"

    val_raw = str(order.get("validity") or order.get("time_in_force") or "DAY").upper()
    validity = "DAY" if val_raw in ("DAY", "") else val_raw

    typ = str(order.get("order_type") or order.get("type") or "MARKET").upper()
    typ_map = {"MARKET": "MARKET", "LIMIT": "LIMIT", "STOP": "SL-M", "STOP_LIMIT": "SL",
               "SL": "SL", "SL-M": "SL-M", "SL_M": "SL-M"}
    order_type = typ_map.get(typ, typ)

    side = str(order.get("transaction_type") or order.get("side") or "BUY").upper()

    price = 0.0
    if order_type in ("LIMIT", "SL"):
        price = float(order.get("price") or order.get("limit_price") or 0.0)

    trigger_price = 0.0
    if order_type in ("SL", "SL-M"):
        trigger_price = float(order.get("trigger_price") or order.get("stop_price") or order.get("stop_loss_stop_price") or 0.0)

    token = order.get("instrument_token")
    if not (token and re.search(r"\|IN[EF][0-9A-Z]{9}$", str(token))):  # a NSE_EQ|SYMBOL token is a rejected key
        # Upstox keys are ISIN based (NSE_EQ|INE002A01018); NSE_EQ|RELIANCE is rejected
        row = resolve_upstox_instrument(clean_sym, exchange)
        token = row["instrument_key"]
        freeze = _f(row.get("freeze_quantity"))
        if freeze and qty > freeze:
            raise TicketError("%s: quantity %d is over the exchange freeze limit %d per order" % (clean_sym, qty, freeze))

    out = {
        "quantity": qty,
        "product": product,
        "validity": validity,
        "price": price,
        "tag": order.get("tag") or "trading-skill",
        "instrument_token": token,
        "order_type": order_type,
        "transaction_type": side,
        "disclosed_quantity": int(order.get("disclosed_quantity", 0)),
        "trigger_price": trigger_price,
        "is_amo": bool(order.get("is_amo", False)),
    }
    if order_type in ("MARKET", "SL-M"):
        out["market_protection"] = float(cfg.get("market_protection") or 2)  # % band so it cannot fill at a wild price
    return out


def upstox_stop_input(order, cfg=None):
    """The protective stop as its own order (a plain Upstox order cannot carry one): SELL SL-M at the stop,
    same product and instrument. Place it only after the entry fills. None if the order has no stop."""
    stop = _f(order.get("stop_loss_stop_price")) or _f((order.get("plan") or {}).get("stop"))
    if not stop:
        return None
    base = upstox_tool_input(dict(order, order_type="MARKET", side="BUY"), cfg)
    return dict(base, transaction_type="SELL", order_type="SL-M", trigger_price=stop, price=0.0,
                tag=(order.get("tag") or "trading-skill") + "-stop")


def kite_tool_input(order, cfg=None):
    """Exact arguments for kite_place_order following Zerodha Kite Connect conventions."""
    cfg = cfg or {}
    sym = str(order.get("tradingsymbol") or order.get("symbol", "")).strip().upper()
    clean_sym = sym.replace(".NS", "").replace(".BO", "")
    exch_raw = str(order.get("exchange") or cfg.get("exchange") or ("BSE" if ".BO" in sym else "NSE")).upper()
    exchange = "NSE" if "NSE" in exch_raw else ("BSE" if "BSE" in exch_raw else exch_raw)

    qty = int(float(order.get("quantity") or order.get("qty") or 0))
    prod_raw = str(order.get("product") or cfg.get("product") or "MIS").upper()
    product = "MIS" if prod_raw in ("I", "MIS") else ("CNC" if prod_raw in ("D", "CNC") else prod_raw)

    val_raw = str(order.get("validity") or order.get("time_in_force") or "DAY").upper()
    validity = "DAY" if val_raw in ("DAY", "") else val_raw

    typ = str(order.get("order_type") or order.get("type") or "MARKET").upper()
    typ_map = {"MARKET": "MARKET", "LIMIT": "LIMIT", "STOP": "SL-M", "STOP_LIMIT": "SL",
               "SL": "SL", "SL-M": "SL-M", "SL_M": "SL-M"}
    order_type = typ_map.get(typ, typ)

    side = str(order.get("transaction_type") or order.get("side") or "BUY").upper()

    price = 0.0
    if order_type in ("LIMIT", "SL"):
        price = float(order.get("price") or order.get("limit_price") or 0.0)

    trigger_price = 0.0
    if order_type in ("SL", "SL-M"):
        trigger_price = float(order.get("trigger_price") or order.get("stop_price") or order.get("stop_loss_stop_price") or 0.0)

    return {
        "tradingsymbol": clean_sym,
        "exchange": exchange,
        "transaction_type": side,
        "order_type": order_type,
        "quantity": qty,
        "product": product,
        "validity": validity,
        "price": price,
        "trigger_price": trigger_price,
        "tag": order.get("tag") or "trading-skill",
    }


def tool_input(order, cfg=None):
    """The exact arguments for broker order execution.
    For Alpaca: Alpaca's place_stock_order parameters.
    For Upstox: Upstox API v2 upstox_place_order parameters.
    For Zerodha/Kite: kite_place_order parameters."""
    broker = str((cfg or {}).get("broker") or order.get("broker") or "alpaca").lower()
    if broker == "upstox":
        return upstox_tool_input(order, cfg)
    if broker in ("zerodha", "kite"):
        return kite_tool_input(order, cfg)
    out = {}
    for k in ALPACA_FIELDS:
        if k in order and order[k] is not None:
            out[k] = _str_num(order[k]) if k in STRING_FIELDS else order[k]
    return out


def _money(v, currency="USD"):
    f = float(v)
    cur = str(currency or "USD").upper()
    sym = "₹" if cur in ("INR", "₹", "RS", "RUPEES") else "$"
    s = "{:,.2f}".format(f)
    return sym + (s[:-3] if s.endswith(".00") else s)


def _p(v):
    return "%.2f" % float(v)


def is_bracket(order):
    oc = str(order.get("order_class") or "").lower()
    legs = order.get("take_profit_limit_price") is not None or order.get("stop_loss_stop_price") is not None
    return oc == "bracket" or (not oc and legs)


def max_loss(order):
    """(dollars, stop) lost if the stop fills exactly at the stop, or (None, stop)."""
    stop = _f(order.get("stop_loss_stop_price")) or _f(order.get("trigger_price")) or _f((order.get("plan") or {}).get("stop"))
    q = _f(order.get("qty")) or _f(order.get("quantity"))
    if not stop or not q:
        return None, stop
    plan = order.get("plan") or {}
    if str(order.get("type", order.get("order_type", "market"))).lower() == "limit" and _f(order.get("limit_price") or order.get("price")):
        entry = _f(order.get("limit_price") or order.get("price"))
    elif _f(plan.get("max_loss")) is not None:
        return round(_f(plan["max_loss"]), 2), stop
    else:
        entry = _f(plan.get("entry"))
    if not entry:
        return None, stop
    risk = abs(entry - stop)
    return round(q * int(round(risk * 100)) / 100.0, 2), stop


def plain_parts(order):
    """The ticket in plain words, one part per line."""
    side = str(order.get("side", order.get("transaction_type", ""))).upper()
    sym = order.get("symbol", order.get("tradingsymbol", "?"))
    typ = str(order.get("type", order.get("order_type", "market"))).lower()
    tif = TIF_WORDS.get(str(order.get("time_in_force", order.get("validity", "day"))).lower(),
                        order.get("time_in_force", order.get("validity")))
    is_in = is_indian_market(None, order, sym)
    cur = "INR" if is_in else "USD"
    exch_extra = ""
    if order.get("exchange") or order.get("product"):
        exch_extra = " (%s%s)" % (order.get("exchange", "NSE_EQ"), (", " + order["product"]) if order.get("product") else "")

    if is_bracket(order):
        how = "limit %s" % _p(order.get("limit_price") or order.get("price")) if typ == "limit" and (order.get("limit_price") or order.get("price")) else typ
        parts = ["%s %s %s%s, %s, %s" % (side, _str_num(order.get("qty") or order.get("quantity")), sym, exch_extra, how, tif)]
        if order.get("stop_loss_stop_price") is not None or order.get("trigger_price") is not None:
            stop_val = order.get("stop_loss_stop_price") or order.get("trigger_price")
            leg = "stop-loss %s" % _p(stop_val)
            if order.get("stop_loss_limit_price") is not None:
                leg += " (limit %s)" % _p(order["stop_loss_limit_price"])
            parts.append(leg)
        if order.get("take_profit_limit_price") is not None:
            parts.append("take-profit %s" % _p(order["take_profit_limit_price"]))
        loss, stop = max_loss(order)
        if loss is not None:
            parts.append("max loss about %s if the stop fills at %s (it can fill worse in a fast market)"
                         % (_money(loss, cur), _p(stop)))
        return parts
    if order.get("notional") is not None:
        what = "%s of %s" % (_money(order["notional"], cur), sym)
    else:
        q = _str_num(order.get("qty") or order.get("quantity"))
        what = "%s share%s of %s" % (q, "" if q == "1" else "s", sym)
    if typ == "limit":
        how = "limit order at %s" % _money(order.get("limit_price") or order.get("price"), cur)
    elif typ in ("stop", "sl-m", "sl_m"):
        how = "stop order at %s" % _money(order.get("stop_price") or order.get("trigger_price"), cur)
    elif typ in ("stop_limit", "sl"):
        how = "stop %s, limit %s" % (_money(order.get("stop_price") or order.get("trigger_price"), cur),
                                     _money(order.get("limit_price") or order.get("price"), cur))
    else:
        how = "%s order" % typ.replace("_", " ")
    return ["%s %s%s, %s, %s" % (side, what, exch_extra, how, tif)]


def plain(order):
    """BUY $1,250 of AAPL, market order, good for today
    BUY 8 TSLA, market, good for today / stop-loss 358.32 / take-profit 370.04 / max loss about $46.88 ..."""
    return " / ".join(plain_parts(order))


def amount_numbers(order):
    nums = []
    for k in ("notional", "qty", "quantity", "limit_price", "price", "stop_loss_stop_price", "trigger_price",
              "take_profit_limit_price", "stop_loss_limit_price"):
        if order.get(k) is not None:
            v = _f(order[k])
            if v is not None:
                nums.append(v)
    loss, _ = max_loss(order)
    if loss is not None:
        nums.append(loss)
    return nums


def parse_yes(reply, order, batch_symbols=()):
    """Is this reply an explicit yes for THIS one order? Returns (ok, why)."""
    raw = (reply or "").strip()
    if not raw:
        return False, "the reply is empty"
    if "?" in raw:
        return False, "the reply is a question, not a yes"
    words = re.findall(r"[a-z']+", raw.lower())
    if not words or words[0] not in YES_WORDS:
        return False, "only a reply that starts with \"yes\" counts (not ok, sure, go, do it)"
    if BATCH_WORDS & set(words):
        return False, "a yes covers one order at a time, not a batch"
    if DOUBT_WORDS & set(words[1:]):
        return False, "the reply has a condition or a doubt in it; ask again"
    sym = str(order.get("symbol", order.get("tradingsymbol", ""))).upper()
    tokens = set(re.findall(r"[A-Z0-9.\-]{1,20}", raw.upper()))
    others = {s.upper() for s in batch_symbols} - {sym, sym.replace(".NS", "").replace(".BO", "")}
    other_bases = {s.split(".")[0] for s in others if s}
    named = (tokens & others) | (tokens & other_bases)
    if named:
        return False, "the reply names %s, but this order is for %s" % (", ".join(sorted(named)), sym)
    nums = [float(n.replace(",", "")) for n in re.findall(r"\d[\d,]*(?:\.\d+)?", raw)]
    if nums:
        want = amount_numbers(order)
        if not any(abs(n - w) < 0.005 for n in nums for w in want):
            return False, "the reply says %s, but this order is %s" % (
                ", ".join(_str_num(n) for n in nums), plain(order))
    return True, "explicit yes for %s" % plain(order)


# ---------------------------------------------------------------- state

def load_state(path):
    return read_json(path, "broker state", required=False) or {"orders": []}


def latest(state, fp, today):
    """The latest decision recorded today for this fingerprint, or None."""
    found = None
    for rec in state.get("orders", []):
        if rec.get("fingerprint") == fp and rec_day(rec) == today:
            found = rec
    return found


def risk_verdict(risk, fp, today):
    if not risk:
        return None, "trader-risk has not checked these orders (run check.py)"
    if risk.get("date") != today:
        return None, "the risk check is from %s, not today; run check.py again" % risk.get("date")
    for r in risk.get("results", []):
        if r.get("fingerprint") == fp:
            return r, None
    return None, "this exact order was not in the last risk check (it changed or is new); run check.py again"


def status_of(order, risk, state, today):
    """(ready, label, risk_result)"""
    fp = fingerprint(order)
    rv, why = risk_verdict(risk, fp, today)
    if rv is None:
        return False, "NOT READY: " + why, None
    if rv.get("verdict") != "PASS":
        return False, "BLOCKED by trader-risk: " + "; ".join(rv.get("reasons", [])), rv
    rec = latest(state, fp, today)
    if rec:
        st = rec.get("status")
        if st == "approved":
            return False, "APPROVED by you at %s, waiting for the place_stock_order call" % rec.get("at"), rv
        return False, "DONE: %s at %s" % (st, rec.get("at")), rv
    return True, "READY: trader-risk PASS, waiting for your yes", rv


def kind_of(order, rv):
    """entry or exit, as trader-risk saw it (older checks: a buy is an entry)."""
    if rv and rv.get("kind"):
        return rv["kind"]
    return "entry" if str(order.get("side", "")).lower() == "buy" else "exit"


# ---------------------------------------------------------------- rendering

def render(order, idx, total, cfg, risk, state, today):
    ready, label, rv = status_of(order, risk, state, today)
    broker = str(cfg.get("broker", "alpaca")).lower()
    paper = cfg.get("paper", True)
    acct = "PAPER account" if paper else "LIVE ACCOUNT, REAL MONEY"
    broker_name = "Interactive Brokers" if broker == "ibkr" else ("Upstox" if broker == "upstox" else ("Zerodha Kite" if broker in ("zerodha", "kite") else "Alpaca"))
    lines = ["ORDER %d of %d, %s, %s" % (idx, total, acct, broker_name)]
    for part in plain_parts(order):
        lines.append("  " + part)
    if order.get("why"):
        lines.append("  why: " + order["why"])
    lines.append("  " + label)
    for w in (rv or {}).get("warnings", []):
        lines.append("  note: " + w)
    if broker == "ibkr":
        lines.append("  Claude drafts this with the Interactive Brokers connector. IBKR sends it to your")
        lines.append("  IBKR account, and it only goes to the market after you approve it there.")
    elif broker == "upstox":
        lines.append("  Claude will call upstox_place_order with exactly:")
        for l in json.dumps(tool_input(order, cfg), indent=2).splitlines():
            lines.append("    " + l)
        if is_bracket(order):
            stop_in = upstox_stop_input(order, cfg)
            if stop_in:
                lines.append("  NO STOP is attached to the order above. Right after it fills, Claude must place this")
                lines.append("  second order (its own yes), or you are unprotected:")
                for l in json.dumps(stop_in, indent=2).splitlines():
                    lines.append("    " + l)
            if order.get("take_profit_limit_price"):
                lines.append("  Take-profit %s is not sent either: place it as a separate SELL LIMIT if you want it."
                             % order["take_profit_limit_price"])
    elif broker in ("zerodha", "kite"):
        lines.append("  Claude will call kite_place_order with exactly:")
        for l in json.dumps(tool_input(order, cfg), indent=2).splitlines():
            lines.append("    " + l)
        if is_bracket(order):
            lines.append("  Zerodha places the entry order. Set the GTT stop-loss and take-profit triggers.")
    else:
        lines.append("  Claude will call place_stock_order with exactly:")
        for l in json.dumps(tool_input(order, cfg), indent=2).splitlines():
            lines.append("    " + l)
        if is_bracket(order):
            lines.append("  Alpaca places the entry, then the stop-loss and the take-profit as two orders")
            lines.append("  that cancel each other: when one fills, the other is canceled.")
    if ready:
        lines.append("  Reply \"yes\" to place this one order, or \"no\" to skip it.")
        if broker != "ibkr":
            lines.append("  Claude's own card then asks again: Allow once places it, Deny stops it.")
    return "\n".join(lines), ready


def ticket_json(order, idx, cfg, risk, state, today):
    ready, label, rv = status_of(order, risk, state, today)
    loss, stop = max_loss(order)
    broker = str(cfg.get("broker", "alpaca")).lower()
    tool_name = "upstox_place_order" if broker == "upstox" else ("kite_place_order" if broker in ("zerodha", "kite") else "place_stock_order")
    return {"index": idx, "ready": ready, "status": label, "plain": plain(order),
            "fingerprint": fingerprint(order), "paper": bool(cfg.get("paper", True)),
            "broker": cfg.get("broker", "alpaca"), "tool": tool_name,
            "tool_input": tool_input(order, cfg), "why": order.get("why", ""),
            "bracket": is_bracket(order), "max_loss": loss, "stop": stop}


# ---------------------------------------------------------------- fills -> portfolio

def apply_fill(portfolio_path, cfg, symbol, side, qty, price, date):
    """Record a filled order in trader/portfolio.json: cash and shares, with a
    cost-basis lot per buy (oldest lots are relieved first on a sell)."""
    pf = read_json(portfolio_path, "portfolio", required=False)
    if pf is None:
        budget = cfg.get("budget")
        if not budget:
            raise TicketError("no %s yet and no budget in the config to start it from" % portfolio_path)
        pf = {"cash": float(budget), "positions": []}
    symbol = symbol.upper()
    qty, price = float(qty), float(price)
    if qty <= 0 or price <= 0:
        raise TicketError("a fill needs a positive qty and price")
    cost = round(qty * price, 2)
    pos = None
    for p in pf.setdefault("positions", []):
        if str(p.get("ticker", "")).upper() == symbol:
            pos = p
    if side == "buy":
        if pos is None:
            pos = {"ticker": symbol, "shares": 0.0, "lots": []}
            pf["positions"].append(pos)
        lots = pos.setdefault("lots", [])
        lots.append({"id": "%s-%d" % (symbol, len(lots) + 1), "date": date, "shares": qty, "cost": cost})
        pos["shares"] = round(float(pos.get("shares", 0)) + qty, 6)
        pf["cash"] = round(float(pf.get("cash", 0)) - cost, 2)
    else:
        if pos is None or float(pos.get("shares", 0)) + 1e-9 < qty:
            raise TicketError("cannot record selling %s %s: the portfolio holds %s"
                              % (_str_num(qty), symbol, pos.get("shares", 0) if pos else 0))
        left = qty
        for lot in sorted(pos.get("lots", []), key=lambda l: l.get("date", "")):
            if left <= 1e-9:
                break
            take = min(left, float(lot["shares"]))
            per = float(lot["cost"]) / float(lot["shares"]) if float(lot["shares"]) else 0
            lot["cost"] = round(float(lot["cost"]) - per * take, 2)
            lot["shares"] = round(float(lot["shares"]) - take, 6)
            left -= take
        pos["lots"] = [l for l in pos.get("lots", []) if float(l["shares"]) > 1e-9]
        pos["shares"] = round(float(pos["shares"]) - qty, 6)
        if pos["shares"] <= 1e-9:
            pf["positions"].remove(pos)
        pf["cash"] = round(float(pf.get("cash", 0)) + cost, 2)
    write_json(portfolio_path, pf)
    return pf


def log_fill(state_path, now, today, symbol, side, qty, price, is_in=False):
    """A fill in the log, so trader-risk can count day trades (a buy and a sell the same day)."""
    state = load_state(state_path)
    state.setdefault("orders", []).append({
        "at": local_iso(now, is_in=is_in), "date": today, "status": "filled", "symbol": symbol.upper(), "side": side,
        "qty": _str_num(float(qty)), "price": float(price),
        "plain": "%s %s %s at %s" % (side.upper(), _str_num(float(qty)), symbol.upper(), _p(price))})
    write_json(state_path, state)


# ---------------------------------------------------------------- commands

def _clock(a, is_in=False):
    now = parse_now(getattr(a, "now", None), is_in=is_in)
    today = getattr(a, "today", None) or (to_ist(now).date().isoformat() if is_in else to_eastern(now).date().isoformat())
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", today):
        raise TicketError("--today wants a date like 2026-10-19")
    return now, today


def _load(a):
    doc = read_json(a.orders, "orders file (run trader-budget, or ticket.py flatten, first)")
    orders = doc.get("orders", doc) if isinstance(doc, dict) else doc
    if not isinstance(orders, list) or not orders:
        raise TicketError("no orders in %s" % a.orders)
    cfg = read_json(a.config, "config", required=False) or {}
    risk = read_json(a.risk, "risk check", required=False)
    state = load_state(a.state)
    is_in = is_indian_market(cfg, orders[0] if orders else None)
    now, today = _clock(a, is_in=is_in)
    return orders, cfg, risk, state, today, now


def _pick(orders, index):
    if index is None:
        raise TicketError("say which order: --index N")
    if not 1 <= index <= len(orders):
        raise TicketError("there is no order %d (there are %d)" % (index, len(orders)))
    return orders[index - 1]


def cmd_show(a):
    orders, cfg, risk, state, today, now = _load(a)
    picks = [(a.index, _pick(orders, a.index))] if a.index else list(enumerate(orders, 1))
    if a.json:
        out = [ticket_json(o, i, cfg, risk, state, today) for i, o in picks]
        print(json.dumps(out[0] if a.index else out, indent=2))
        return 0 if any(t["ready"] for t in out) else 1
    blocks, any_ready = [], False
    for i, o in picks:
        text, ready = render(o, i, len(orders), cfg, risk, state, today)
        blocks.append(text)
        any_ready = any_ready or ready
    print("\n\n".join(blocks))
    print("\nOne order at a time: a yes covers one ticket, never the batch. Education and tooling, "
          "not financial advice.")
    return 0 if any_ready else 1


def cmd_approve(a):
    orders, cfg, risk, state, today, now = _load(a)
    order = _pick(orders, a.index)
    ready, label, rv = status_of(order, risk, state, today)
    if not ready:
        raise TicketError("order %d is not ready: %s" % (a.index, label))
    ok, why = parse_yes(a.reply, order, [o.get("symbol", "") for o in orders])
    if not ok:
        print("Not recorded: %s. Ask the user again with the ticket for order %d." % (why, a.index))
        return 1
    loss, stop = max_loss(order)
    is_in = is_indian_market(cfg, order)
    at_str = local_iso(now, is_in=is_in)
    sym = str(order.get("symbol") or order.get("tradingsymbol", "")).upper()
    side = str(order.get("side") or order.get("transaction_type", "")).lower()
    qty = order.get("qty") or order.get("quantity")
    broker = str(cfg.get("broker", "alpaca")).lower()
    rec = {"at": at_str, "date": today, "index": a.index, "fingerprint": fingerprint(order),
           "symbol": sym, "side": side,
           "qty": qty, "kind": kind_of(order, rv), "max_loss": loss, "stop": stop,
           "plain": plain(order), "status": "approved", "reply": a.reply,
           "paper": bool(cfg.get("paper", True)), "broker": cfg.get("broker", "alpaca")}
    state.setdefault("orders", []).append(rec)
    write_json(a.state, state)
    print("Recorded your yes for order %d: %s." % (a.index, plain(order)))
    if broker == "ibkr":
        print("Now draft it with the Interactive Brokers connector; approve it in your IBKR account.")
    elif broker == "upstox":
        print("Now call upstox_place_order with exactly:")
        print(json.dumps(tool_input(order, cfg), indent=2))
        print("Claude's card shows Allow once / Deny. Then: ticket.py result --index %d --status "
              "placed|denied|failed" % a.index)
    elif broker in ("zerodha", "kite"):
        print("Now call kite_place_order with exactly:")
        print(json.dumps(tool_input(order, cfg), indent=2))
        print("Claude's card shows Allow once / Deny. Then: ticket.py result --index %d --status "
              "placed|denied|failed" % a.index)
    else:
        print("Now call place_stock_order with exactly:")
        print(json.dumps(tool_input(order, cfg), indent=2))
        print("Claude's card shows Allow once / Deny. Then: ticket.py result --index %d --status "
              "placed|denied|failed" % a.index)
    return 0


def cmd_decline(a):
    orders, cfg, risk, state, today, now = _load(a)
    order = _pick(orders, a.index)
    is_in = is_indian_market(cfg, order)
    at_str = local_iso(now, is_in=is_in)
    sym = str(order.get("symbol") or order.get("tradingsymbol", "")).upper()
    side = str(order.get("side") or order.get("transaction_type", "")).lower()
    rec = {"at": at_str, "date": today, "index": a.index, "fingerprint": fingerprint(order),
           "symbol": sym, "side": side,
           "plain": plain(order), "status": "declined", "reply": a.reply or "no"}
    state.setdefault("orders", []).append(rec)
    write_json(a.state, state)
    print("Recorded: you said no to order %d (%s). Nothing was sent." % (a.index, plain(order)))
    return 0


def cmd_result(a):
    orders, cfg, risk, state, today, now = _load(a)
    order = _pick(orders, a.index)
    fp = fingerprint(order)
    rec = latest(state, fp, today)
    if not rec or rec.get("status") != "approved":
        raise TicketError("order %d has no yes recorded today; a result can only follow an approval" % a.index)
    is_in = is_indian_market(cfg, order)
    at_str = local_iso(now, is_in=is_in)
    new = {"at": at_str, "date": today, "index": a.index, "fingerprint": fp, "symbol": rec["symbol"],
           "side": rec["side"], "plain": rec["plain"], "status": a.status}
    for k in ("qty", "kind", "max_loss", "stop"):
        if rec.get(k) is not None:
            new[k] = rec[k]
    if a.broker_id:
        new["broker_order_id"] = a.broker_id
    if a.note:
        new["note"] = a.note
    state["orders"].append(new)
    write_json(a.state, state)
    print("Recorded order %d as %s." % (a.index, a.status))
    if a.status == "placed" and a.filled_qty and a.filled_price:
        apply_fill(a.portfolio, cfg, rec["symbol"], rec["side"], a.filled_qty, a.filled_price, today)
        log_fill(a.state, now, today, rec["symbol"], rec["side"], a.filled_qty, a.filled_price, is_in=is_in)
        print("Added the fill to %s." % a.portfolio)
    elif a.status == "placed":
        print("When it fills, record it: ticket.py fill --symbol %s --side %s --qty Q --price P"
              % (rec["symbol"], rec["side"]))
        if rec.get("kind") == "entry" and is_bracket(order):
            print("When the stop-loss or the take-profit fills later, record that sell the same way.")
    return 0


def cmd_fill(a):
    cfg = read_json(a.config, "config", required=False) or {}
    is_in = is_indian_market(cfg, None, a.symbol)
    now = parse_now(a.now, is_in=is_in)
    date = a.date or (to_ist(now).date().isoformat() if is_in else to_eastern(now).date().isoformat())
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", date):
        raise TicketError("--date wants a date like 2026-10-19")
    cur = "INR" if is_in else "USD"
    pf = apply_fill(a.portfolio, cfg, a.symbol, a.side, a.qty, a.price, date)
    log_fill(a.state, now, date, a.symbol, a.side, a.qty, a.price, is_in=is_in)
    print("Recorded %s %s %s at %s on %s in %s. Cash now %s." % (a.side, a.qty, a.symbol.upper(),
                                                                _money(a.price, cur), date, a.portfolio,
                                                                _money(pf["cash"], cur)))
    return 0


def cmd_flatten(a):
    cfg = read_json(a.config, "config", required=False) or {}
    sym = a.symbol.strip().upper()
    is_in = is_indian_market(cfg, None, sym)
    now, today = _clock(a, is_in=is_in)
    source = "the broker, get_open_position"
    if a.qty is not None:
        qty = _f(a.qty)
        if qty is None or qty <= 0:
            raise TicketError("--qty wants the number of shares you hold, like --qty 8")
    else:
        pf = read_json(a.portfolio, "portfolio", required=False) or {}
        qty = sum(_f(p.get("shares", p.get("qty"))) or 0 for p in pf.get("positions", [])
                  if str(p.get("ticker") or p.get("symbol") or "").upper() == sym)
        source = a.portfolio
        if qty <= 1e-9:
            raise TicketError("nothing to flatten: no %s shares in %s. Check the broker (get_open_position "
                              "%s) and pass --qty if you do hold some" % (sym, a.portfolio, sym))
    plan = read_json(a.plan, "plan", required=False) or {}
    default_flat = "15:15" if is_in else "15:55"
    tz_label = "IST" if is_in else "New York"
    flat_by = (plan.get("flat_by") if str(plan.get("symbol", "")).upper() == sym else None) \
        or cfg.get("flat_by") or default_flat
    broker = str(cfg.get("broker") or ("upstox" if is_in else "alpaca")).lower()
    order = {"symbol": sym, "side": "sell", "qty": _str_num(qty), "type": "market", "time_in_force": "day",
             "why": "flat by %s %s: closes the %s day trade before the close (qty from %s)"
             % (flat_by, tz_label, sym, source)}
    if is_in:
        order["exchange"] = str(cfg.get("exchange") or ("BSE_EQ" if ".BO" in sym else "NSE_EQ")).upper()
        prod = str(cfg.get("product") or "I").upper()
        order["product"] = "I" if prod in ("I", "MIS") else "D"
        order["currency"] = "INR"
    old = read_json(a.orders, "orders", required=False)
    replaced = len(old.get("orders", [])) if isinstance(old, dict) else 0
    doc = {"created_by": "trader-broker", "created_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "status": "proposed",
           "paper": bool(cfg.get("paper", True)),
           "note": "Flatten ticket. Nothing is placed until trader-risk passes it and you say yes in the chat.",
           "source": "ticket.py flatten %s on %s" % (sym, today), "orders": [order]}
    write_json(a.orders, doc)
    print("Flatten %s: %s (flat by %s %s)." % (sym, plain(order), flat_by, tz_label))
    print("Wrote it to %s%s." % (a.orders, (" (it replaced %d earlier proposal%s)"
                                             % (replaced, "" if replaced == 1 else "s")) if replaced else ""))
    if a.qty is None:
        print("The qty comes from %s. Check it against get_open_position %s before you say yes." % (source, sym))
    print("Next:")
    print("  1. trader-risk: check.py. A sell that only closes what you hold is never blocked by the daily")
    print("     limits or the entry window.")
    print("  2. ticket.py show, then the user's yes for this one order.")
    if is_in or broker in ("upstox", "zerodha", "kite"):
        print("  3. Cancel any open bracket triggers / SL orders before sending the market exit.")
    else:
        print("  3. If the bracket's stop-loss and take-profit are still open, Alpaca holds these shares for them")
        print("     and rejects a new sell. Cancel them first: get_orders (status open, nested true), then")
        print("     cancel_order_by_id for each open leg (Needs approval). Then place_stock_order with the ticket.")
    return 0


def cmd_log(a):
    state = load_state(a.state)
    now, today = _clock(a)
    rows = [r for r in state.get("orders", []) if a.all or rec_day(r) == today]
    if not rows:
        print("No decisions recorded%s." % ("" if a.all else " today"))
        return 1
    for r in rows:
        extra = (" id " + r["broker_order_id"]) if r.get("broker_order_id") else ""
        reply = (' reply "%s"' % r["reply"]) if r.get("reply") and r["status"] in ("approved", "declined") else ""
        cur = "INR" if is_indian_symbol(r.get("symbol")) else "USD"
        loss = (" max loss %s" % _money(r["max_loss"], cur)) if r.get("max_loss") is not None and r["status"] == "placed" else ""
        idx = ("#%s" % r["index"]) if r.get("index") is not None else "  "
        print("%s  %s %-9s %s%s%s%s" % (r.get("at"), idx, r.get("status"), r.get("plain"), reply, loss, extra))
    placed = sum(1 for r in rows if r.get("status") == "placed")
    print("%d placed%s." % (placed, "" if a.all else " today"))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="Order tickets and your yes (trader-broker). Never places orders.")
    sub = ap.add_subparsers(dest="cmd")

    def clock(p):
        p.add_argument("--now", help="pretend it is this market time (IST for NSE/BSE, New York for US), e.g. \"2026-10-19 10:15\"")
        p.add_argument("--today", help="YYYY-MM-DD (default: today in the market's timezone)")

    def common(p):
        p.add_argument("--orders", default="trader/orders.json")
        p.add_argument("--config", default="trader/config.json")
        p.add_argument("--risk", default="trader/state/risk.json")
        p.add_argument("--state", default="trader/state/broker.json")
        clock(p)

    p = sub.add_parser("show", help="render the tickets")
    common(p)
    p.add_argument("--index", type=int)
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("approve", help="record the user's explicit yes for one order")
    common(p)
    p.add_argument("--index", type=int, required=True)
    p.add_argument("--reply", required=True, help="the user's reply, word for word")
    p = sub.add_parser("decline", help="record a no")
    common(p)
    p.add_argument("--index", type=int, required=True)
    p.add_argument("--reply")
    p = sub.add_parser("result", help="record what happened after the Allow once / Deny card")
    common(p)
    p.add_argument("--index", type=int, required=True)
    p.add_argument("--status", required=True, choices=["placed", "denied", "failed"])
    p.add_argument("--broker-id")
    p.add_argument("--note")
    p.add_argument("--filled-qty")
    p.add_argument("--filled-price")
    p.add_argument("--portfolio", default="trader/portfolio.json")
    p = sub.add_parser("fill", help="record a fill in trader/portfolio.json")
    p.add_argument("--symbol", required=True)
    p.add_argument("--side", required=True, choices=["buy", "sell"])
    p.add_argument("--qty", required=True)
    p.add_argument("--price", required=True)
    p.add_argument("--date", help="YYYY-MM-DD (default: today in the market's timezone)")
    p.add_argument("--now")
    p.add_argument("--portfolio", default="trader/portfolio.json")
    p.add_argument("--config", default="trader/config.json")
    p.add_argument("--state", default="trader/state/broker.json")
    p = sub.add_parser("flatten", help="a sell ticket that closes a symbol's position by flat_by")
    p.add_argument("--symbol", required=True)
    p.add_argument("--qty", help="shares you hold, from get_open_position (default: trader/portfolio.json)")
    p.add_argument("--orders", default="trader/orders.json", help="where the ticket is written")
    p.add_argument("--portfolio", default="trader/portfolio.json")
    p.add_argument("--plan", default="trader/plan.json")
    p.add_argument("--config", default="trader/config.json")
    clock(p)
    p = sub.add_parser("log", help="today's decisions")
    p.add_argument("--state", default="trader/state/broker.json")
    clock(p)
    p.add_argument("--all", action="store_true")
    a = ap.parse_args(argv)
    if not a.cmd:
        ap.print_help()
        return 1
    try:
        return {"show": cmd_show, "approve": cmd_approve, "decline": cmd_decline, "result": cmd_result,
                "fill": cmd_fill, "flatten": cmd_flatten, "log": cmd_log}[a.cmd](a)
    except TicketError as e:
        print("ticket.py: %s" % e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
