"""Paper-trading executor for every recommendation (Alpaca PAPER only). Runs on GitHub Actions.

Accounts (keys in repo secrets, never in code or logs)
  A  "rules"    ALPACA_PAPER_KEY_ID   / ALPACA_PAPER_SECRET_KEY     arms: brief, tqqq, orb (filtered)
  B  "control"  ALPACA_PAPER_KEY_ID_B / ALPACA_PAPER_SECRET_KEY_B   arms: orb (unfiltered), rulec, scan

Every order is a record in paper/book.json until it is closed and written to scans/paper/ledger.json
(one row per leg). Records survive across days, so multi-day holds (hold_days 1-4) work.

Actions
  check     read-only: account status, equity, clock, options level (proves the keys and the paper endpoint)
  test      far-away brackets (long, short, OTO) on A and B, read back, cancel (validates order formats)
  tick      do everything due now (idempotent; logs in paper/state/<date>.json):
              09:35-10:00  Rule C (B): short non-earnings gap-ups of 1.0-2.5 ATR, stop open+2 ATR, cover MOC
              queue        brief / TQQQ items from paper/queue/<date>.json (long or short, intraday or swing)
              10:00-10:15  scanner daily setups (B): hits + prior-day high levels for the scan arm
              10:15-11:30  ORB (A filtered + B all) and scan-arm level breakouts (B), once
              every tick   manage the book: cancel unfilled entries at cancel_at, move the T2 stop to
                           breakeven once T1 fills, time exits (flat-by, swing time stop, event days)
              close-20min  flatten intraday records (MOC for Rule C), then reconcile positions with the book
              close+5min   ledger (per leg: R, slippage, MFE/MAE, fill realism) and scans/paper/stats.json
  flatten   cancel all orders and close all positions on A and B now (emergency)

Limits on account A, PER ARM (user decision 31 Oct): $100 risk ($50 mixed regime / event day),
<= $5,000 notional, <= 2 entries/day, <= 2 open, <= 1 open per sector, arm day P/L <= -$200 stops the
arm, arm week P/L <= -$400 pauses it. Account A as a whole stops at -$600 on the day (safety).
The user's real 2-a-day sleeve is measured as an overlay in paper_stats.py.

Safety: refuses any base URL other than https://paper-api.alpaca.markets.
"""
import json
import math
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

PAPER_BASE = "https://paper-api.alpaca.markets"
DATA_BASE = "https://data.alpaca.markets"
NY = ZoneInfo("America/New_York")
ROOT = Path(__file__).resolve().parent
QUEUE = ROOT / "paper" / "queue"
STATE = ROOT / "paper" / "state"
BOOK = ROOT / "paper" / "book.json"
LEDGER = ROOT / "scans" / "paper"
MAX_NOTIONAL = 5000.0
ARM_MAX_ENTRIES = 2
ARM_MAX_OPEN = 2
ARM_DAY_STOP = -200.0
ARM_WEEK_STOP = -400.0
ACCOUNT_A_DAY_STOP = -600.0
B_CAPS = {"orb": 8, "rulec": 2, "scan": 6, "orb_short": 8}     # entries per day per arm on the control account
ORB_N = int(os.environ.get("ORB_N") or 8)
SCAN_N = int(os.environ.get("SCAN_N") or 6)
NEWS_BLOCK_TAGS = {"deal", "halt"}
NEWS_BLOCK_ITEMS = {"1.03", "2.01", "5.01"}    # bankruptcy, completed acquisition, change in control
OPEN_ST = {"new", "accepted", "pending_new", "partially_filled", "held", "accepted_for_bidding",
           "pending_replace", "pending_cancel", "calculated"}
CLOSE_MIN = 16 * 60
LOG: list = []


def log(*a):
    s = " ".join(str(x) for x in a)
    LOG.append(s)
    print(s, flush=True)


class Alpaca:
    def __init__(self, name: str, key_env: str, secret_env: str):
        self.name = name
        self.key = os.environ.get(key_env, "").strip()
        self.secret = os.environ.get(secret_env, "").strip()
        base = (os.environ.get("ALPACA_BASE_URL") or PAPER_BASE).rstrip("/")
        if base.endswith("/v2"):
            base = base[:-3]
        if base != PAPER_BASE:
            raise SystemExit(f"REFUSED: {base} is not the paper endpoint")
        self.base = base

    @property
    def ok(self) -> bool:
        return bool(self.key and self.secret)

    def _req(self, method: str, url: str, body=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method, headers={
            "APCA-API-KEY-ID": self.key, "APCA-API-SECRET-KEY": self.secret,
            "Content-Type": "application/json", "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                raw = r.read()
                return r.status, (json.loads(raw) if raw else None)
        except urllib.error.HTTPError as e:
            try:
                return e.code, json.loads(e.read() or b"{}")
            except Exception:
                return e.code, {"message": str(e)}

    def get(self, path, **q):
        qs = ("?" + "&".join(f"{k}={v}" for k, v in q.items())) if q else ""
        return self._req("GET", self.base + path + qs)

    def post(self, path, body):
        return self._req("POST", self.base + path, body)

    def patch(self, path, body):
        return self._req("PATCH", self.base + path, body)

    def delete(self, path):
        return self._req("DELETE", self.base + path)

    def last_price(self, sym: str):
        st, d = self._req("GET", f"{DATA_BASE}/v2/stocks/{sym}/trades/latest?feed=iex")
        if st == 200 and d and d.get("trade"):
            return float(d["trade"]["p"])
        return None

    def shortable(self, sym: str) -> str | None:
        """None if the symbol can be sold short now, else the reason."""
        s, a = self.get(f"/v2/assets/{sym}")
        if s != 200:
            return f"asset lookup {s}"
        if not a.get("shortable"):
            return "not shortable"
        if not a.get("easy_to_borrow"):
            return "not easy to borrow"
        return None


A = Alpaca("A", "ALPACA_PAPER_KEY_ID", "ALPACA_PAPER_SECRET_KEY")
B = Alpaca("B", "ALPACA_PAPER_KEY_ID_B", "ALPACA_PAPER_SECRET_KEY_B")
ACCTS = {"A": A, "B": B}


# ---------------------------------------------------------------- time & files
def now_ny() -> datetime:
    override = os.environ.get("NOW_NY")
    if override:
        return datetime.strptime(override, "%Y-%m-%d %H:%M").replace(tzinfo=NY)
    return datetime.now(timezone.utc).astimezone(NY)


def hm(s: str) -> int:
    h, m = s.split(":")
    return int(h) * 60 + int(m)


def minutes(t: datetime) -> int:
    return t.hour * 60 + t.minute


def r2(x: float) -> float:
    return round(x + (1e-9 if x >= 0 else -1e-9), 2)


def load_json(p: Path, default):
    try:
        return json.loads(p.read_text())
    except Exception:
        return default


def save_json(p: Path, obj):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, indent=1, default=str))


MOC_SEND = 15   # Rule C cover at market from 15:45 ET (15:47 / 15:52 ticks)
MOC_LEAD = 10   # Alpaca rejects / expires MOC orders sent after 15:50 ET
FLAT_LEAD = 20   # minutes before the close: GitHub cron can lag, and a market order after 16:00 waits for tomorrow


def flat_cutoff() -> str:
    """20 min before today's close: 15:40 normally, 12:40 on a 13:00 early close."""
    m = CLOSE_MIN - FLAT_LEAD
    return f"{m // 60:02d}:{m % 60:02d}"


def eff_flat(flat_by: str | None) -> str:
    fb = flat_by or flat_cutoff()
    return fb if hm(fb) <= hm(flat_cutoff()) else flat_cutoff()


def iso_week(d: str):
    return datetime.strptime(d, "%Y-%m-%d").date().isocalendar()[:2]


def sgn(side: str) -> int:
    return 1 if side == "long" else -1


# ---------------------------------------------------------------- outside data (Yahoo, TradingView)
def _yahoo(sym: str, rng: str, interval: str) -> list:
    url = f"https://query2.finance.yahoo.com/v8/finance/chart/{sym}?range={rng}&interval={interval}"
    req = urllib.request.Request(url, headers={"User-Agent": "tv-scanner-paper/1.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        d = json.loads(r.read())["chart"]["result"][0]
    q = d["indicators"]["quote"][0]
    out = []
    for ts, o, h, l, c in zip(d.get("timestamp") or [], q["open"], q["high"], q["low"], q["close"]):
        if None not in (o, h, l, c):
            out.append((datetime.fromtimestamp(ts, NY), float(o), float(h), float(l), float(c)))
    return out


ALPACA_TF = {"1d": "1Day", "5m": "5Min", "1m": "1Min"}
RANGE_DAYS = {"1d": 1, "5d": 7, "3mo": 95}


def _alpaca_bars(sym: str, rng: str, interval: str) -> list:
    """Same shape from Alpaca market data: SIP (consolidated) for anything older than 16 minutes, which the free
    plan allows; the last 16 minutes come from IEX (real time, one exchange)."""
    acct = A if A.ok else B
    now = datetime.now(timezone.utc)
    start = (now.astimezone(NY) - timedelta(days=RANGE_DAYS.get(rng, 5))).replace(hour=0, minute=0)
    out = {}
    for feed, end in (("sip", now - timedelta(minutes=16)), ("iex", now)):
        tok = None
        while True:
            q = {"timeframe": ALPACA_TF[interval], "start": start.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                 "end": end.strftime("%Y-%m-%dT%H:%M:%SZ"), "feed": feed, "limit": "10000", "adjustment": "raw"}
            if tok:
                q["page_token"] = tok
            st, d = acct._req("GET", f"{DATA_BASE}/v2/stocks/{sym}/bars?" + "&".join(f"{k}={v}" for k, v in q.items()))
            if st != 200 or not d:
                break
            for b in d.get("bars") or []:
                t = datetime.fromisoformat(b["t"].replace("Z", "+00:00")).astimezone(NY)
                if interval == "1d":
                    t = t.replace(hour=16, minute=0)
                if feed == "sip" or t not in out:
                    out[t] = (t, float(b["o"]), float(b["h"]), float(b["l"]), float(b["c"]))
            tok = d.get("next_page_token")
            if not tok:
                break
        start = now - timedelta(minutes=16)
    bars = [out[k] for k in sorted(out)]
    if rng == "1d" and interval != "1d":
        bars = [b for b in bars if b[0].date() == now.astimezone(NY).date()]
    # regular session only, like Yahoo
    return [b for b in bars if interval == "1d" or hm("09:30") <= minutes(b[0]) < 16 * 60]


def yahoo_chart(sym: str, rng: str, interval: str) -> list:
    """[(datetime NY, open, high, low, close)]: Yahoo chart JSON, retried once; Alpaca bars if Yahoo fails
    (Yahoo answered 429 to GitHub runners on 3 Nov)."""
    for attempt in range(2):
        try:
            return _yahoo(sym, rng, interval)
        except Exception as e:  # noqa: BLE001
            err = e
            time.sleep(2)
    log(f"Yahoo {sym} {rng}/{interval} failed ({str(err)[:60]}); using Alpaca bars")
    return _alpaca_bars(sym, rng, interval)


def yahoo_5m(sym: str) -> list:
    return yahoo_chart(sym, "1d", "5m")


def daily_before(sym: str, day) -> list:
    """Completed daily bars strictly before `day` (no look-ahead)."""
    return [b for b in yahoo_chart(sym, "3mo", "1d") if b[0].date() < day]


def atr14(bars: list) -> float | None:
    if len(bars) < 15:
        return None
    trs = [max(h - l, abs(h - bars[i - 1][4]), abs(l - bars[i - 1][4]))
           for i, (_, o, h, l, c) in enumerate(bars) if i > 0]
    return sum(trs[-14:]) / 14.0


TV_COLS = ["name", "sector", "close", "change", "premarket_change", "ATR", "market_cap_basic",
           "average_volume_10d_calc", "earnings_release_next_date", "earnings_release_date", "update_mode"]


def tv_map(day: str, refresh: bool = False) -> dict:
    """{SYM: {sector, earn_next, earn_last, premarket_change, change}} for the large-cap universe; cached per day."""
    path = STATE / f"{day}-tv.json"
    cached = load_json(path, None)
    if cached and not refresh:
        return cached
    import tv_scan
    cookies = None
    try:
        import tv_auth
        cookies = (tv_auth.retrieve_tradingview_cookie() or {}).get("cookies")
    except Exception as e:  # noqa: BLE001
        log("tv auth failed (delayed data):", str(e)[:120])
    spec = {"filter": tv_scan.BASE_FILTERS, "sort": {"sortBy": "market_cap_basic", "sortOrder": "desc"}, "limit": 2000}
    try:
        rows = tv_scan.run_scan(spec, cookies, TV_COLS)
    except Exception as e:  # noqa: BLE001
        log("tv scan with extra columns failed, retrying core columns:", str(e)[:120])
        rows = tv_scan.run_scan(spec, cookies, [c for c in TV_COLS if c != "earnings_release_date"])
    out = {}
    for r in rows:
        def dt(v):
            return datetime.fromtimestamp(v, NY).date().isoformat() if v else None
        out[r["name"]] = {"sector": r.get("sector"), "earn_next": dt(r.get("earnings_release_next_date")),
                          "earn_last": dt(r.get("earnings_release_date")),
                          "premarket_change": r.get("premarket_change"), "change": r.get("change"),
                          "atr": r.get("ATR"), "close": r.get("close"), "mode": r.get("update_mode"),
                          "dollar_vol": (r.get("average_volume_10d_calc") or 0) * (r.get("close") or 0)}
    save_json(path, out)
    return out


# ---------------------------------------------------------------- book
SHARE_CLASS = {"GOOG": "GOOGL", "BRK.A": "BRK.B", "FOX": "FOXA", "NWS": "NWSA", "LEN.B": "LEN",
               "HEI.A": "HEI", "BF.A": "BF.B", "UHAL.B": "UHAL", "ZG": "Z", "LBRDA": "LBRDK", "FWONA": "FWONK"}


def family(sym: str) -> str:
    return SHARE_CLASS.get(sym, sym)


def load_book() -> list:
    return load_json(BOOK, [])


def save_book(book: list):
    save_json(BOOK, book)


def summarize_order(o: dict) -> dict:
    return {"id": o.get("id"), "type": o.get("type"), "side": o.get("side"), "status": o.get("status"),
            "qty": float(o.get("qty") or 0), "filled_qty": float(o.get("filled_qty") or 0),
            "avg": float(o["filled_avg_price"]) if o.get("filled_avg_price") else None,
            "filled_at": o.get("filled_at"), "stop_price": o.get("stop_price"),
            "limit_price": o.get("limit_price"), "tif": o.get("time_in_force")}


def refresh(rec: dict):
    """Pull the entry order, its legs and our own exit orders from the broker into the record."""
    acct = ACCTS[rec["account"]]
    s, o = acct.get(f"/v2/orders/{rec['broker_id']}", nested="true")
    if s != 200 or not isinstance(o, dict):
        rec["refresh_error"] = s
        return
    e = summarize_order(o)
    rec["entry_status"], rec["entry_filled"], rec["entry_avg"], rec["filled_at"] = \
        e["status"], e["filled_qty"], e["avg"], e["filled_at"]
    rec["legs"] = [summarize_order(x) for x in (o.get("legs") or [])]
    exits = []
    for xid in rec.get("exit_order_ids", []):
        s2, x = acct.get(f"/v2/orders/{xid}")
        if s2 == 200 and isinstance(x, dict):
            exits.append(summarize_order(x))
    rec["exits"] = exits
    rec["exit_filled"] = sum(x["filled_qty"] for x in rec["legs"] + exits)
    rec["remaining"] = rec["entry_filled"] - rec["exit_filled"]


def tp_hit(rec: dict) -> bool:
    return any(x["type"] == "limit" and x["filled_qty"] > 0 for x in rec.get("legs", []))


def open_children(rec: dict) -> list:
    return [x for x in rec.get("legs", []) + rec.get("exits", []) if x["status"] in OPEN_ST]


def close_record(rec: dict, why: str, tif: str = "day"):
    """Cancel the record's bracket legs and send our own exit for whatever is still held."""
    acct = ACCTS[rec["account"]]
    refresh(rec)
    if rec.get("entry_status") in OPEN_ST:
        acct.delete(f"/v2/orders/{rec['broker_id']}")       # stop any further entry fills
    for x in open_children(rec):
        acct.delete(f"/v2/orders/{x['id']}")
    for _ in range(8):                                      # the broker frees the shares once legs are cancelled
        refresh(rec)
        if not open_children(rec):
            break
        time.sleep(1)
    qty = int(round(rec.get("remaining") or 0))
    if qty <= 0:
        rec["closed"] = True
        return True
    if minutes(now_ny()) >= CLOSE_MIN:
        # a market order now would sit until tomorrow's open: leave it and flag it
        rec.setdefault("events", []).append({"at": now_ny().strftime("%Y-%m-%d %H:%M"),
                                             "what": f"close ({why}) NOT sent: market closed", "qty": qty})
        log(f"[{rec['account']}] {rec['id']}: market closed, {qty} left open")
        return False
    n = len(rec.get("exit_order_ids", [])) + 1
    body = {"symbol": rec["symbol"], "qty": str(qty), "side": "sell" if rec["side"] == "long" else "buy",
            "type": "market", "time_in_force": tif, "client_order_id": f"{rec['id'][:40]}-x{n}"}
    s, d = acct.post("/v2/orders", body)
    ok = s in (200, 201)
    if ok:
        rec.setdefault("exit_order_ids", []).append(d["id"])
        rec["exit_reason"] = why
    rec.setdefault("events", []).append({"at": now_ny().strftime("%Y-%m-%d %H:%M"), "what": f"close ({why}) {tif}",
                                         "qty": qty, "http": s, "msg": None if ok else (d or {}).get("message")})
    log(f"[{rec['account']}] close {rec['id']} {qty} ({why}, {tif}) -> {s}")
    return ok


# ---------------------------------------------------------------- orders
def bracket(o: dict) -> dict:
    """o: symbol, qty, side (long|short), entry_type (market|limit|stop_limit), limit_price, stop_price,
    stop_loss, take_profit (None -> OTO with the stop only), id, hold_days."""
    side = "buy" if o.get("side", "long") == "long" else "sell"
    body = {"symbol": o["symbol"], "qty": str(int(o["qty"])), "side": side, "type": o["entry_type"],
            "time_in_force": "gtc" if (o.get("hold_days") or 0) > 0 else "day",
            "client_order_id": o["id"][:48], "stop_loss": {"stop_price": f"{r2(o['stop_loss']):.2f}"}}
    if o.get("take_profit") is not None:
        body["order_class"] = "bracket"
        body["take_profit"] = {"limit_price": f"{r2(o['take_profit']):.2f}"}
    else:
        body["order_class"] = "oto"
    if o["entry_type"] in ("limit", "stop_limit"):
        body["limit_price"] = f"{r2(o['limit_price']):.2f}"
    if o["entry_type"] == "stop_limit":
        body["stop_price"] = f"{r2(o['stop_price']):.2f}"
    return body


def size(entry: float, stop: float, risk_usd: float) -> int:
    per = abs(entry - stop)
    if per <= 0:
        return 0
    return max(0, min(math.floor(risk_usd / per), math.floor(MAX_NOTIONAL / entry)))


def place(o: dict, day: str, st: dict, book: list) -> list:
    """Place one order (or a T1/T2 pair when take_profit_2 is set) and add the records to the book."""
    acct = ACCTS[o["account"]]
    if any(r["base"] == o["id"] for r in book):
        log(f"{o['id']}: already in the book, not placed again")
        return []
    side = o.get("side", "long")
    if any(r["account"] == o["account"] and r["symbol"] == o["symbol"] and r["side"] != side and is_open(r)
           for r in book):
        # Alpaca nets long and short in one symbol into one position: never hold both sides in one account
        log(f"{o['id']}: opposite side already open in {o['symbol']} on {o['account']}; not placed")
        st.setdefault("skipped", []).append({"id": o["id"], "why": "opposite side open in the same account"})
        return []
    legs = [o]
    if o.get("take_profit_2"):
        q1 = int(o["qty"]) // 2
        legs = [dict(o, qty=q1, id=o["id"] + "-t1"),
                dict(o, qty=int(o["qty"]) - q1, take_profit=o["take_profit_2"], id=o["id"] + "-t2")]
        if q1 < 1:
            legs = [dict(o, take_profit=o["take_profit"])]
    recs = []
    for leg in legs:
        if int(leg["qty"]) < 1:
            continue
        s, d = acct.post("/v2/orders", bracket(leg))
        ok = s in (200, 201)
        plan_entry = leg.get("plan_entry") or leg.get("limit_price") or leg.get("stop_price")
        rec = {"id": leg["id"], "base": o["id"], "account": acct.name, "arm": leg["arm"],
               "setup": leg.get("setup"), "symbol": leg["symbol"], "side": leg.get("side", "long"),
               "qty": int(leg["qty"]), "entry_type": leg["entry_type"], "plan_entry": plan_entry,
               "limit_price": leg.get("limit_price"), "stop": leg["stop_loss"], "target": leg.get("take_profit"),
               "placed_day": day, "placed_at": now_ny().strftime("%H:%M"),
               "cancel_at": leg.get("cancel_at") or "11:30", "flat_by": eff_flat(leg.get("flat_by")),
               "hold_days": int(leg.get("hold_days") or 0), "time_stop_days": int(leg.get("time_stop_days") or 2),
               "exit_mode": leg.get("exit_mode") or "flat", "sector": leg.get("sector"),
               "planned_risk": round(int(leg["qty"]) * abs(plan_entry - leg["stop_loss"]), 2),
               "features": leg.get("features") or {}, "last_at_placement": leg.get("last_px"),
               "http": s, "status": "placed" if ok else "rejected", "broker_id": (d or {}).get("id"),
               "message": None if ok else (d or {}).get("message"), "sessions": [day]}
        if leg["id"].endswith("-t1"):
            rec["be_target"] = o["id"] + "-t2"        # when T1 fills, the T2 stop moves to breakeven
        log(f"[{acct.name}] {rec['status']} {leg['arm']} {leg.get('side', 'long')} {leg['symbol']} x{leg['qty']} "
            f"{leg['entry_type']} stop {leg['stop_loss']} tgt {leg.get('take_profit')} -> {s} {rec['message'] or ''}")
        st.setdefault("placed", []).append({k: rec[k] for k in ("id", "account", "arm", "symbol", "side", "qty",
                                                                 "status", "http", "message", "placed_at")})
        if ok:
            book.append(rec)
        recs.append(rec)
    return recs


# ---------------------------------------------------------------- P/L and guards
def rec_pnl(rec: dict, last: float | None) -> float:
    if not rec.get("entry_filled") or rec.get("entry_avg") is None:
        return 0.0
    s = sgn(rec["side"])
    pnl = 0.0
    for x in rec.get("legs", []) + rec.get("exits", []):
        if x["filled_qty"] and x["avg"] is not None:
            pnl += s * (x["avg"] - rec["entry_avg"]) * x["filled_qty"]
    rem = rec.get("remaining") or 0
    if rem and last is not None:
        pnl += s * (last - rec["entry_avg"]) * rem
    return pnl


def is_open(rec: dict) -> bool:
    return not rec.get("closed") and (rec.get("entry_status") in OPEN_ST or (rec.get("remaining") or 0) > 0
                                      or "entry_status" not in rec)


def arm_day_pnl(book: list, account: str, arm: str, day: str, prices: dict) -> float:
    tot = 0.0
    for r in book:
        if r["account"] == account and r["arm"] == arm and (r["placed_day"] == day or is_open(r)):
            tot += rec_pnl(r, prices.get(r["symbol"]))
    return tot


def arm_week_closed(account: str, arm: str, day: str) -> float:
    wk = iso_week(day)
    return sum(float(r.get("pnl") or 0) for r in load_json(LEDGER / "ledger.json", [])
               if r.get("account") == account and r.get("arm") == arm and r.get("closed_day")
               and r["closed_day"] < day and iso_week(r["closed_day"]) == wk)


def guard(account: str, arm: str, day: str, book: list, sym: str, sector: str | None) -> str | None:
    """Per-arm limits on A; entry caps on B. None = allowed."""
    acct = ACCTS[account]
    today = [r for r in book if r["account"] == account and r["arm"] == arm and r["placed_day"] == day
             and not r.get("cancelled_unfilled")]
    bases_today = {r["base"] for r in today}
    if account == "B":
        cap = B_CAPS.get(arm, 4)
        return f"{arm} cap {cap}/day reached" if len(bases_today) >= cap else None
    if len(bases_today) >= ARM_MAX_ENTRIES:
        return f"{arm}: {len(bases_today)} entries today"
    open_recs = [r for r in book if r["account"] == "A" and r["arm"] == arm and is_open(r)]
    if len({r["base"] for r in open_recs}) >= ARM_MAX_OPEN:
        return f"{arm}: {len({r['base'] for r in open_recs})} open"
    if sector and any(r.get("sector") == sector and r["symbol"] != sym for r in open_recs):
        return f"{arm}: already holds a {sector} position"
    prices = {r["symbol"]: acct.last_price(r["symbol"]) for r in book if r["account"] == "A" and is_open(r)}
    dp = arm_day_pnl(book, "A", arm, day, prices)
    if dp <= ARM_DAY_STOP:
        return f"{arm}: day P/L {dp:.2f} <= {ARM_DAY_STOP}"
    wp = arm_week_closed("A", arm, day) + dp
    if wp <= ARM_WEEK_STOP:
        return f"{arm}: week P/L {wp:.2f} <= {ARM_WEEK_STOP} (weekly pause)"
    s, a = acct.get("/v2/account")
    if s == 200 and float(a["equity"]) - float(a["last_equity"]) <= ACCOUNT_A_DAY_STOP:
        return f"account A day P/L <= {ACCOUNT_A_DAY_STOP}"
    return None


# ---------------------------------------------------------------- queue (brief + TQQQ setups)
def run_queue(day: str, st: dict, t: datetime, book: list):
    q = load_json(QUEUE / f"{day}.json", [])
    done = set(st.setdefault("queue_done", []))
    for item in q:
        qid = item["id"]
        if qid in done:
            continue
        if minutes(t) < hm(item.get("not_before") or "09:35"):
            continue

        def skip(why):
            log(f"queue {qid}: {why}")
            st.setdefault("skipped", []).append({"id": qid, "why": why})
            done.add(qid)

        if minutes(t) >= hm(item.get("cancel_at") or "11:30"):
            skip(f"missed window (now {t:%H:%M})")
            continue
        item = dict(item, account=item.get("account", "A"), arm=item.get("arm") or item.get("source") or "brief",
                    side=item.get("side", "long"))
        acct = ACCTS[item["account"]]
        sym = item["symbol"]
        sector = item.get("sector")
        if sector is None and sym not in ("TQQQ", "SQQQ"):
            try:
                sector = (tv_map(day).get(sym) or {}).get("sector")
            except Exception as e:  # noqa: BLE001
                log("sector lookup failed", str(e)[:100])
        item["sector"] = sector
        why = guard(item["account"], item["arm"], day, book, sym, sector)
        if why:
            skip(f"skipped ({why})")
            continue
        if item["side"] == "short":
            why = acct.shortable(sym)
            if why:
                skip(f"short not possible: {why}")
                continue
        px = acct.last_price(sym)
        if px is None:
            skip("no live price")
            continue
        va, vb = item.get("void_above"), item.get("void_below")
        if (va is not None and px > va) or (vb is not None and px < vb):
            skip(f"void: last {px} outside [{vb}, {va}]")
            continue
        pe, sl = item.get("plan_entry") or item.get("limit_price"), item.get("stop_loss")
        if pe and sl and item.get("entry_type") == "limit":
            s = 1 if item["side"] == "long" else -1
            R = abs(pe - sl)
            if R > 0 and s * (pe - px) > 0 and s * (px - sl) < MIN_CUSHION_R * R:
                # marketable limit would fill next to the stop (4 Nov SQQQ: filled 0.04 above the stop, out in 1 min)
                skip(f"last {px} within {MIN_CUSHION_R}R of the stop {sl} (entry {pe}); not taken")
                continue
        if item.get("qty") in (None, "", 0):
            item["qty"] = size(item.get("plan_entry") or item["limit_price"] or item["stop_price"],
                               item["stop_loss"], float(item.get("risk_usd") or 100))
        if int(item["qty"]) < 1:
            skip("size 0")
            continue
        item["last_px"] = px
        evfb = load_json(ROOT / "paper" / "events.json", {}).get(day, {}).get("flat_by")
        if evfb and (item.get("hold_days") or 0) <= 0 and hm(evfb) < hm(item.get("flat_by") or flat_cutoff()):
            item["flat_by"] = evfb          # the day's event / outage flat-by applies to queued intraday tickets too
        place(item, day, st, book)
        done.add(qid)
    st["queue_done"] = sorted(done)


# ---------------------------------------------------------------- Rule C (account B)
def run_rulec(day: str, st: dict, t: datetime, book: list):
    """Frozen rule C (research-protocol 14 Oct): short non-earnings gap-ups >= +1.5% and 1.0-2.5 ATR,
    stop open + 2 ATR, cover at the close; max 2 a day by largest gap in ATR; $100 = 2 ATR x shares."""
    if not B.ok:
        st["rulec"] = {"skipped": "account B keys missing"}
        return
    d = t.date()
    tv = tv_map(day, refresh=True)
    prev_days = {b[0].date() for b in daily_before("SPY", d)[-1:]}
    cands = sorted([(v.get("premarket_change") or v.get("change") or 0, k) for k, v in tv.items()
                    if (v.get("premarket_change") or v.get("change") or 0) >= 1.0], reverse=True)[:30]
    found, rejected = [], []
    for _, sym in cands:
        info = tv[sym]
        if info.get("earn_next") == day or info.get("earn_last") in ({day} | {p.isoformat() for p in prev_days}):
            rejected.append({"symbol": sym, "why": "earnings"})
            continue
        try:
            daily = daily_before(sym, d)
            bars = [b for b in yahoo_5m(sym) if b[0].date() == d]
        except Exception as e:  # noqa: BLE001
            rejected.append({"symbol": sym, "why": f"data: {str(e)[:60]}"})
            continue
        if not daily or not bars or minutes(bars[0][0]) != hm("09:30"):
            rejected.append({"symbol": sym, "why": "no open bar"})
            continue
        pc, atr, op = daily[-1][4], atr14(daily), bars[0][1]
        if not atr or pc < 20:
            rejected.append({"symbol": sym, "why": "atr/price"})
            continue
        gap_pct, gap_atr = (op / pc - 1) * 100, (op - pc) / atr
        if gap_pct >= 1.5 and 1.0 <= gap_atr <= 2.5:
            found.append({"symbol": sym, "open": round(op, 4), "prev_close": pc, "atr": round(atr, 4),
                          "gap_pct": round(gap_pct, 2), "gap_atr": round(gap_atr, 3), "sector": info.get("sector")})
        else:
            rejected.append({"symbol": sym, "why": f"gap {gap_pct:.2f}% / {gap_atr:.2f} ATR"})
    found.sort(key=lambda x: -x["gap_atr"])
    placed = []
    for c in found:
        if len(placed) >= 2:
            break
        sym = c["symbol"]
        why = guard("B", "rulec", day, book, sym, None) or B.shortable(sym)
        if why:
            c["skip"] = why
            continue
        stop = r2(c["open"] + 2 * c["atr"])
        px = B.last_price(sym)
        if px is None or px >= stop:
            c["skip"] = f"last {px} at/above the rule stop {stop}"
            continue
        qty = min(math.floor(100 / (2 * c["atr"])), math.floor(MAX_NOTIONAL / c["open"]))
        if qty < 1:
            c["skip"] = "size 0"
            continue
        o = {"id": f"rulec-B-{day}-{sym}", "account": "B", "arm": "rulec", "setup": "rule_c", "symbol": sym,
             "side": "short", "qty": qty, "entry_type": "market", "plan_entry": c["open"], "stop_loss": stop,
             "take_profit": None, "exit_mode": "moc", "flat_by": flat_cutoff(), "cancel_at": "10:00",
             "sector": c["sector"], "last_px": px, "features": c}
        place(o, day, st, book)
        placed.append(sym)
    st["rulec"] = {"at": t.strftime("%H:%M"), "candidates": found, "rejected": rejected[:40], "placed": placed}


# ---------------------------------------------------------------- ORB + scanner level breakouts
def regime() -> str:
    """SPY & QQQ prior close vs EMA20 (Yahoo daily closes, today excluded)."""
    out = {}
    for sym in ("SPY", "QQQ"):
        closes = [b[4] for b in daily_before(sym, now_ny().date())]
        ema = closes[0]
        for c in closes[1:]:
            ema = c * (2 / 21) + ema * (19 / 21)
        out[sym] = closes[-1] > ema
    if out["SPY"] and out["QQQ"]:
        return "long"
    if not out["SPY"] and not out["QQQ"]:
        return "short"
    return "mixed"


def spy_or_state(t: datetime) -> dict:
    """Logged feature (not a rule): SPY vs its own 15-minute opening range."""
    bars = [b for b in yahoo_5m("SPY") if b[0].date() == t.date()]
    orb = [b for b in bars if hm("09:30") <= minutes(b[0]) < hm("09:45")]
    done = [b for b in bars if minutes(b[0]) + 5 <= minutes(t)]
    if len(orb) < 3 or not done:
        return {"state": None}
    hi, lo, last = max(b[2] for b in orb), min(b[3] for b in orb), done[-1][4]
    return {"or_high": hi, "or_low": lo, "last": last,
            "state": "above" if last > hi else "below" if last < lo else "inside"}


def news_flags(TS, syms: list) -> dict:
    """{SYM: [reasons]} from trader-news (Yahoo headlines + SEC 8-Ks, last 18 h)."""
    if not syms:
        return {}
    res = TS.tool("trader-news/news.py", "--tickers", *syms, "--hours", "18", "--dry-run", "--json", timeout=300)
    items = ((res.get("data") or {}).get("items")) or []
    out = {s: [] for s in syms}
    for it in items:
        s = (it.get("ticker") or "").upper()
        if s not in out:
            continue
        bad = (set(it.get("tags") or []) & NEWS_BLOCK_TAGS) or (set(it.get("items") or []) & NEWS_BLOCK_ITEMS)
        if bad:
            out[s].append(f"{','.join(sorted(bad))}: {(it.get('title') or '')[:80]}")
    out["_status"] = res.get("code")
    return out


def prepare_scan(day: str, st: dict, t: datetime):
    """Scanner daily setups -> top SCAN_N liquid hits with yesterday's high as the breakout level."""
    import trader_skills as TS
    TS.WORK.mkdir(exist_ok=True)
    TS.write_config()
    uni = TS.largecaps()
    res = TS.tool("trader-scanner/scan.py", "daily", "--watchlist", str(uni), "--min-volume", "0",
                  "--json", "--show", "200")
    hits = sorted((res.get("data") or {}).get("hits", []), key=lambda h: -TS.LIQ.get(h["symbol"], 0))
    tv = tv_map(day)
    picks, fams = [], set()
    for h in hits:
        if len(picks) >= SCAN_N:
            break
        sym = h["symbol"]
        if (tv.get(sym) or {}).get("earn_next") == day or family(sym) in fams:
            continue
        fams.add(family(sym))
        try:
            prev = daily_before(sym, t.date())[-1]
        except Exception:  # noqa: BLE001
            continue
        picks.append({"symbol": sym, "setups": h.get("setups"), "level": r2(prev[2]), "prev_close": prev[4],
                      "sector": (tv.get(sym) or {}).get("sector")})
    st["scan"] = {"at": t.strftime("%H:%M"), "counts": (res.get("data") or {}).get("counts"),
                  "hits": len(hits), "picks": picks}


MIN_CUSHION_R = 0.5   # queue limit entries: skip when the last price is already within 0.5R of the stop
CHASE_R = 0.2   # S3 (3 Nov): never enter more than 0.2R past the trigger (was 0.5R; 2 Nov slippage 0.15R avg)


def entry_window_order(px: float, entry: float, stop: float, side: str = "long") -> dict | None:
    """Last price must sit between the stop and entry + 0.2R (mirrored for shorts).
    Past the trigger: a limit at trigger + 0.1R (no chasing; it may not fill). Back behind the trigger: a
    stop-limit at the trigger, limit trigger + 0.1R."""
    s = 1 if side == "long" else -1
    R = abs(entry - stop)
    past = s * (px - entry)                      # > 0: price beyond the trigger in the trade's direction
    if past > CHASE_R * R or s * (px - stop) <= 0:
        return None
    if past >= 0:
        return {"entry_type": "limit", "limit_price": entry + s * 0.1 * R}
    return {"entry_type": "stop_limit", "stop_price": entry, "limit_price": entry + s * 0.1 * R}


def alpaca_multi_5m(syms: list, day, end_utc) -> dict:
    """{SYM: [(dt NY, o, h, l, c)]} 5-minute SIP bars from 09:30 to end_utc (the free plan allows SIP data
    older than 15 minutes)."""
    acct = B if B.ok else A
    start = datetime.combine(day, datetime.min.time()).replace(hour=9, minute=30, tzinfo=NY).astimezone(timezone.utc)
    out = {}
    for i in range(0, len(syms), 50):
        tok = None
        while True:
            q = {"symbols": ",".join(syms[i:i + 50]), "timeframe": "5Min", "feed": "sip", "limit": "10000",
                 "start": start.strftime("%Y-%m-%dT%H:%M:%SZ"), "end": end_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
                 "adjustment": "raw"}
            if tok:
                q["page_token"] = tok
            s_, d = acct._req("GET", f"{DATA_BASE}/v2/stocks/bars?" + "&".join(f"{k}={v}" for k, v in q.items()))
            if s_ != 200 or not d:
                break
            for sym, bars in (d.get("bars") or {}).items():
                for b in bars:
                    tt = datetime.fromisoformat(b["t"].replace("Z", "+00:00")).astimezone(NY)
                    out.setdefault(sym, []).append((tt, float(b["o"]), float(b["h"]), float(b["l"]), float(b["c"])))
            tok = d.get("next_page_token")
            if not tok:
                break
    return out


def run_orb_short(day: str, st: dict, t: datetime, book: list, tv: dict, reg: str, flat_by: str, spy: dict):
    """S2 (3 Nov, B only): opening-range BREAKDOWN shorts, the mirror of the ORB longs.
    15-min range 09:30-09:45; trigger = first 5-min bar from 09:45 that closes below the range low; entry = that
    close; stop = range midpoint; target 2R; flat by the cutoff. Top 8 triggered names by $ volume (top 120
    liquid names scanned). Risk $100 in a short regime, else $50. Logged with regime and SPY's range state."""
    if not B.ok:
        return
    risk = 100.0 if reg == "short" else 50.0
    ev = load_json(ROOT / "paper" / "events.json", {}).get(day, {})
    if ev.get("risk_usd"):
        risk = min(risk, float(ev["risk_usd"]))
    uni = [k for k, v in sorted(tv.items(), key=lambda kv: -(kv[1].get("dollar_vol") or 0))[:120]]
    end = datetime.now(timezone.utc) - timedelta(minutes=16)
    if os.environ.get("NOW_NY"):
        end = t.astimezone(timezone.utc) - timedelta(minutes=16)
    bars = alpaca_multi_5m(uni, t.date(), end)
    hits = []
    for sym, bs in bars.items():
        bs = [b for b in bs if b[0].date() == t.date()]
        rng = [b for b in bs if hm("09:30") <= minutes(b[0]) < hm("09:45")]
        if len(rng) < 3:
            continue
        hi, lo = max(b[2] for b in rng), min(b[3] for b in rng)
        trig = next((b for b in bs if minutes(b[0]) >= hm("09:45") and b[4] < lo), None)
        if not trig:
            continue
        entry, stop = trig[4], (hi + lo) / 2
        if stop - entry < 0.01:
            continue
        hits.append({"symbol": sym, "trigger": trig[0].strftime("%H:%M"), "or_high": hi, "or_low": lo,
                     "entry": r2(entry), "stop": r2(stop), "target": r2(entry - 2 * (stop - entry)),
                     "dollar_vol": tv.get(sym, {}).get("dollar_vol"), "earnings_today": tv.get(sym, {}).get("earn_next") == day})
    hits.sort(key=lambda h: -(h["dollar_vol"] or 0))
    seen, uniq = set(), []
    for h in hits:                      # one share class per company (GOOG/GOOGL)
        if family(h["symbol"]) not in seen:
            seen.add(family(h["symbol"]))
            uniq.append(h)
    hits = uniq
    for h in hits[:B_CAPS["orb_short"]]:
        sym = h["symbol"]
        why = guard("B", "orb_short", day, book, sym, None) or B.shortable(sym)
        if why:
            h["skip"] = why
            continue
        px = B.last_price(sym)
        if px is None:
            h["skip"] = "no price"
            continue
        eo = entry_window_order(px, h["entry"], h["stop"], side="short")
        if eo is None:
            h["skip"] = f"last {px} outside entry window ({h['entry'] - CHASE_R * (h['stop'] - h['entry']):.2f}..{h['stop']})"
            continue
        qty = size(min(px, h["entry"]), h["stop"], risk)
        if qty < 1:
            continue
        o = dict(eo, id=f"orbs-B-{day}-{sym}", account="B", arm="orb_short", setup="orb_breakdown", symbol=sym,
                 side="short", qty=qty, stop_loss=h["stop"], take_profit=h["target"], plan_entry=h["entry"],
                 flat_by=flat_by, cancel_at="11:30", sector=tv.get(sym, {}).get("sector"), last_px=px,
                 features={"regime": reg, "spy_or": spy.get("state"), "trigger": h["trigger"],
                           "earnings_today": h["earnings_today"]})
        place(o, day, st, book)
    st["orb_short"] = {"at": t.strftime("%H:%M"), "scanned": len(bars), "hits": len(hits), "risk_usd": risk,
                       "plans": hits[:B_CAPS["orb_short"] + 4]}


def run_orb(day: str, st: dict, t: datetime, book: list):
    import trader_skills as TS
    reg = regime()
    risk = 100.0 if reg == "long" else 50.0
    ev = load_json(ROOT / "paper" / "events.json", {}).get(day, {})
    flat_by = eff_flat(os.environ.get("FLAT_BY") or ev.get("flat_by") or flat_cutoff())
    if ev.get("risk_usd"):
        risk = min(risk, float(ev["risk_usd"]))
    if ev.get("no_orb"):
        st["orb"] = {"skipped": f"events.json: {ev.get('note', 'no ORB today')}"}
        log(st["orb"]["skipped"])
        return
    # exit plan, Time Machine and risk check see the same flat-by and risk as the paper order
    os.environ["RISK_USD"] = str(risk)
    TS.FLAT_BY = flat_by
    TS.WORK.mkdir(exist_ok=True)
    TS.write_config()
    try:
        spy = spy_or_state(t)
    except Exception as e:  # noqa: BLE001
        spy = {"state": None, "error": str(e)[:200]}
    # cut the clock at the last 5-minute boundary at least 3 minutes old: Yahoo publishes a bar a minute or
    # two after it closes, and the scanner drops a symbol when any slot is missing ("unusable")
    cut_m = ((minutes(t) - 3) // 5) * 5
    res = TS.orb(f"{cut_m // 60:02d}:{cut_m % 60:02d}", ORB_N)
    try:
        tv = tv_map(day)
    except Exception as e:  # noqa: BLE001
        log("tv map failed", str(e)[:120])
        tv = {}

    # scanner level breakouts (B only): same exit tools, ATR stop because a level entry has no opening-range stop
    if "scan" not in st:
        try:
            prepare_scan(day, st, t)
        except Exception as e:  # noqa: BLE001
            st["scan"] = {"error": str(e)[:200], "picks": []}
    scan_plans = []
    for pk in st.get("scan", {}).get("picks", []):
        s = pk["symbol"]
        ent_path, plan_path = f"trader/entry_lvl_{s}.json", f"trader/plan_lvl_{s}.json"
        ent = TS.tool("trader-entry/entry.py", "check", s, "--setup", "level", "--level", str(pk["level"]),
                      "--write", ent_path, "--json")
        row = {"symbol": s, "entry": ent, "pick": pk}
        if (ent.get("data") or {}).get("status") == "triggered":
            ex = TS.tool("trader-exit/exit.py", "plan", "--from", ent_path, "--stop", "atr:2", "--target", "2R",
                         "--flat-by", flat_by, "--write", plan_path, "--json")
            row["exit"] = ex
            if ex.get("code") == 0:
                row["timemachine"] = TS.tool("trader-timemachine/timemachine.py", "run", "--plan", plan_path,
                                             "--until", flat_by, "--json")
        scan_plans.append(row)

    triggered = [p["symbol"] for p in res.get("plans", []) + scan_plans
                 if ((p.get("entry") or {}).get("data") or {}).get("status") == "triggered"]
    try:
        news = news_flags(TS, sorted(set(triggered)))
    except Exception as e:  # noqa: BLE001
        news = {"_error": str(e)[:200]}

    summary = []

    def handle(p, arm, setup):
        sym = p["symbol"]
        ent = (p.get("entry") or {}).get("data") or {}
        ex = (p.get("exit") or {}).get("data") or {}
        tm = (p.get("timemachine") or {}).get("data") or {}
        rk = ((((p.get("risk") or {}).get("data") or {}).get("results")) or [{}])[0]
        info = tv.get(sym) or {}
        row = {"symbol": sym, "arm": arm, "setup": setup, "trigger": (ent.get("trigger") or {}).get("time"),
               "entry": ex.get("entry"), "stop": ex.get("stop"), "target": ex.get("target"),
               "verdict": tm.get("verdict"),
               "hist": {k: (tm.get("history") or {}).get(k) for k in ("triggered", "target", "stop", "avg_R")},
               "risk": rk.get("verdict"), "earnings_today": info.get("earn_next") == day,
               "news": news.get(sym) or [], "sector": info.get("sector")}
        summary.append(row)
        if ent.get("status") != "triggered" or not ex.get("entry") or not ex.get("stop"):
            return
        entry, stop, target = float(ex["entry"]), float(ex["stop"]), float(ex["target"])
        feats = {"regime": reg, "spy_or": spy.get("state"), "tm_verdict": tm.get("verdict"),
                 "tm_hist": row["hist"], "risk": rk.get("verdict"), "news": row["news"],
                 "trigger": row["trigger"], "setups": (p.get("pick") or {}).get("setups")}
        accts = ("A", "B") if arm == "orb" else ("B",)
        for an in accts:
            acct = ACCTS[an]
            if not acct.ok:
                continue
            if an == "A":
                v = tm.get("verdict") or ""
                h = tm.get("history") or {}
                fails = []
                if not (v.startswith("Leans toward the target") or v.startswith("Strongly toward the target")):
                    fails.append("tm verdict")
                if (h.get("triggered") or 0) < 20 or (h.get("avg_R") or 0) <= 0:
                    fails.append("history")
                if rk.get("verdict") != "PASS":
                    fails.append("risk")
                if reg == "short":
                    fails.append("regime short")
                if row["earnings_today"]:
                    fails.append("earnings today")
                if row["news"]:
                    fails.append("news: " + "; ".join(row["news"])[:120])
                if fails:
                    row["A_skip"] = fails
                    continue
            why = guard(an, arm, day, book, sym, info.get("sector") if an == "A" else None)
            if why:
                row[f"{an}_skip"] = why
                log(f"{arm} {sym}: {an} skipped ({why})")
                continue
            px = acct.last_price(sym)
            if px is None:
                row[f"{an}_skip"] = "no price"
                continue
            eo = entry_window_order(px, entry, stop)
            if eo is None:
                row[f"{an}_skip"] = f"last {px} outside entry window ({stop}..{entry + CHASE_R * (entry - stop):.2f})"
                continue
            qty = size(max(px, entry), stop, risk)
            if qty < 1:
                continue
            o = dict(eo, id=f"{arm}-{an}-{day}-{sym}", account=an, arm=arm, setup=setup, symbol=sym, side="long",
                     qty=qty, stop_loss=stop, take_profit=target, plan_entry=entry, flat_by=flat_by,
                     cancel_at="11:30", sector=info.get("sector"), last_px=px, features=feats)
            place(o, day, st, book)

    for p in res.get("plans", []):
        handle(p, "orb", "orb")
    for p in scan_plans:
        handle(p, "scan", "level:" + "+".join((p.get("pick") or {}).get("setups") or []))
    try:
        run_orb_short(day, st, t, book, tv, reg, flat_by, spy)
    except Exception as e:  # noqa: BLE001
        log(f"ERROR in orb_short: {type(e).__name__}: {str(e)[:200]}")
        st.setdefault("errors", []).append({"at": t.strftime("%H:%M"), "phase": "orb_short", "error": str(e)[:200]})
    st["orb"] = {"at": t.strftime("%H:%M"), "regime": reg, "risk_usd": risk, "flat_by": flat_by, "spy_or": spy,
                 "counts": ((res.get("scan") or {}).get("data") or {}).get("counts"), "plans": summary,
                 "news_status": news.get("_status", news.get("_error"))}


# ---------------------------------------------------------------- managing the book
def manage(day: str, st: dict, t: datetime, book: list):
    m = minutes(t)
    cut = hm(flat_cutoff())
    ev = load_json(ROOT / "paper" / "events.json", {}).get(day, {})
    by_id = {r["id"]: r for r in book}
    for rec in book:
        if rec.get("closed"):
            continue
        try:
            if day not in rec.setdefault("sessions", []):
                rec["sessions"].append(day)
            refresh(rec)
            if "entry_status" not in rec:
                continue
            acct = ACCTS[rec["account"]]
            # 1. unfilled entries expire at cancel_at on the entry day (GTC swing entries included)
            if rec["entry_status"] in OPEN_ST and (day > rec["placed_day"] or m >= hm(rec["cancel_at"])):
                acct.delete(f"/v2/orders/{rec['broker_id']}")
                log(f"[{rec['account']}] cancel unfilled entry {rec['id']} (filled {rec['entry_filled']})")
                refresh(rec)
                if not rec.get("entry_filled"):
                    rec["cancelled_unfilled"] = True
                    rec["closed"] = True
                    continue
            # 2. T1 filled -> arm the sibling's breakeven move and mark T1 reached for the time stop
            if tp_hit(rec) and rec.get("be_target") in by_id:
                sib = by_id[rec["be_target"]]
                sib["be_armed"] = True
                sib["t1_reached"] = True
            if tp_hit(rec):
                rec["t1_reached"] = True
            if rec.get("be_armed") and not rec.get("be_done") and (rec.get("remaining") or 0) > 0 \
                    and rec.get("entry_avg"):
                stop_leg = next((x for x in rec["legs"] if x["type"] in ("stop", "stop_limit")
                                 and x["status"] in OPEN_ST), None)
                if stop_leg:
                    be = r2(rec["entry_avg"])
                    cur = float(stop_leg["stop_price"] or 0)
                    better = be > cur if rec["side"] == "long" else be < cur
                    if better:
                        s, d = acct.patch(f"/v2/orders/{stop_leg['id']}", {"stop_price": f"{be:.2f}"})
                        rec.setdefault("events", []).append({"at": t.strftime("%Y-%m-%d %H:%M"),
                                                             "what": f"stop -> breakeven {be}", "http": s,
                                                             "msg": None if s in (200, 201) else (d or {}).get("message")})
                        log(f"[{rec['account']}] {rec['id']} stop {cur} -> {be} (T1 filled) -> {s}")
                        if s in (200, 201):
                            rec["be_done"] = True
                    else:
                        rec["be_done"] = True
            # 3. time exits
            if (rec.get("remaining") or 0) > 0 and m < CLOSE_MIN and rec["hold_days"] <= 0 \
                    and rec["placed_day"] < day and m >= hm("09:30"):
                # an intraday position left over from an earlier session (missed close-out): close it now
                close_record(rec, "stale intraday (missed close-out)")
            elif (rec.get("remaining") or 0) > 0 and m < CLOSE_MIN:
                if rec["exit_mode"] == "moc" and ev.get("flat_by") and m >= hm(ev["flat_by"]) and m < cut \
                        and not any(x["type"] == "market" and x["status"] in OPEN_ST for x in rec["exits"]):
                    # scheduler-outage day: events.json flat_by also moves the Rule C cover earlier (market)
                    close_record(rec, "event flat_by (instead of MOC)")
                elif rec["exit_mode"] == "moc":
                    # Alpaca PAPER does not fill MOC (tif=cls) orders: on 4 Nov (sent 15:53) and 5 Nov (sent 15:37)
                    # every one expired and the shorts stayed open overnight. Cover at market from 15:45 instead
                    # (the 15:47 / 15:52 ticks), the nearest we get to the close.
                    pending = any(x["type"] == "market" and x["status"] in OPEN_ST and x.get("tif") == "day"
                                  for x in rec["exits"])
                    if m >= CLOSE_MIN - MOC_SEND and not pending:
                        close_record(rec, "close (market near the close; paper MOC unsupported)")
                elif rec["hold_days"] <= 0:
                    pending = any(x["type"] == "market" and x["status"] in OPEN_ST for x in rec["exits"])
                    if m >= hm(eff_flat(rec["flat_by"])) and not pending:
                        close_record(rec, "flat-by")
                elif m >= cut:
                    n = len(rec["sessions"])      # trading sessions held, entry day = 1
                    why = ("hold limit" if n >= rec["hold_days"] else
                           "time stop (no T1)" if n >= rec["time_stop_days"] and not rec.get("t1_reached") else
                           "event day" if ev.get("flat_swing") else None)
                    if why and not any(x["type"] == "market" and x["status"] in OPEN_ST for x in rec["exits"]):
                        close_record(rec, why)
            refresh(rec)
            if (rec.get("remaining") or 0) <= 0 and rec.get("entry_status") not in OPEN_ST \
                    and not open_children(rec):
                rec["closed"] = True
        except Exception as e:  # noqa: BLE001
            log(f"manage {rec.get('id')}: {type(e).__name__}: {str(e)[:200]}")
            st.setdefault("errors", []).append({"at": t.strftime("%H:%M"), "phase": f"manage {rec.get('id')}",
                                                "error": str(e)[:200]})


def reconcile(day: str, st: dict, t: datetime, book: list):
    """Before the close: every position must be explained by a swing or MOC record still in the book.
    Anything else (an orphan, or an intraday close that did not go through) is closed at market."""
    for an, acct in ACCTS.items():
        if not acct.ok:
            continue
        s, pos = acct.get("/v2/positions")
        if s != 200:
            continue
        for p in pos or []:
            sym = p["symbol"]
            q = abs(float(p["qty"]))
            actual = q if p.get("side", "long") == "long" else -q
            expect = sum(sgn(r["side"]) * (r.get("remaining") or 0) for r in book
                         if r["account"] == an and r["symbol"] == sym and not r.get("closed")
                         and (r["hold_days"] > 0 or r["exit_mode"] == "moc"))
            in_flight = sum(sgn(r["side"]) * (r.get("remaining") or 0) for r in book
                            if r["account"] == an and r["symbol"] == sym and not r.get("closed")
                            and r["hold_days"] <= 0 and r["exit_mode"] != "moc"
                            and any(x["status"] in OPEN_ST for x in r.get("exits", [])))
            diff = actual - expect - in_flight
            if abs(diff) < 1e-9:
                continue
            s2, orders = acct.get("/v2/orders", status="open", symbols=sym, nested="true")
            keep = {x["id"] for r in book if r["account"] == an and r["symbol"] == sym and not r.get("closed")
                    and (r["hold_days"] > 0 or r["exit_mode"] == "moc") for x in r.get("legs", []) + r.get("exits", [])}
            for o in (orders or []) if s2 == 200 else []:
                ids = [o["id"]] + [lg["id"] for lg in (o.get("legs") or [])]
                if not set(ids) & keep:
                    acct.delete(f"/v2/orders/{o['id']}")
            time.sleep(1)
            body = {"symbol": sym, "qty": str(int(abs(diff))), "side": "sell" if diff > 0 else "buy",
                    "type": "market", "time_in_force": "day",
                    "client_order_id": f"fix-{an}-{sym}-{t:%Y%m%d%H%M}"[:48]}
            s3, d = acct.post("/v2/orders", body)
            st.setdefault("reconcile", []).append({"at": t.strftime("%H:%M"), "account": an, "symbol": sym,
                                                   "actual": actual, "expected": expect, "in_flight": in_flight,
                                                   "order_http": s3, "msg": (d or {}).get("message")})
            log(f"[{an}] reconcile {sym}: actual {actual} expected {expect} in-flight {in_flight} -> {s3}")


def flatten_all():
    for acct in (A, B):
        if not acct.ok:
            continue
        s1, _ = acct.delete("/v2/orders")
        s2, d = acct.delete("/v2/positions?cancel_orders=true")
        log(f"[{acct.name}] flatten: cancel orders {s1}, close positions {s2} ({len(d or [])})")


# ---------------------------------------------------------------- ledger
def minute_bars(sym: str, day) -> list:
    try:
        return [b for b in yahoo_chart(sym, "1d", "1m") if b[0].date() == day]
    except Exception:  # noqa: BLE001
        return []


def ts_ny(s: str | None):
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(NY) if s else None


def leg_row(rec: dict, day: str, t: datetime) -> dict:
    s = sgn(rec["side"])
    fills = [x for x in rec.get("legs", []) + rec.get("exits", []) if x["filled_qty"] and x["avg"] is not None]
    qx = sum(x["filled_qty"] for x in fills)
    avg_exit = sum(x["avg"] * x["filled_qty"] for x in fills) / qx if qx else None
    last_fill = max(fills, key=lambda x: x["filled_at"] or "") if fills else None
    kind = None
    if last_fill:
        kind = {"limit": "target", "stop": "stop", "stop_limit": "stop"}.get(last_fill["type"], rec.get("exit_reason"))
        if last_fill["type"] == "stop" and rec.get("be_done"):
            kind = "breakeven stop"
    qty = rec["entry_filled"]
    pnl = round(s * (avg_exit - rec["entry_avg"]) * qty, 2) if avg_exit is not None else None
    rps = abs(rec["plan_entry"] - rec["stop"])
    risk = round(qty * rps, 2)
    row = {"date": rec["placed_day"], "closed_day": day, "account": rec["account"], "arm": rec["arm"],
           "setup": rec.get("setup"), "id": rec["id"], "base": rec["base"], "symbol": rec["symbol"],
           "side": rec["side"], "qty": qty, "entry_type": rec["entry_type"], "plan_entry": rec["plan_entry"],
           "stop": rec["stop"], "target": rec.get("target"), "avg_entry": rec["entry_avg"],
           "avg_exit": round(avg_exit, 4) if avg_exit is not None else None, "exit": kind, "pnl": pnl,
           "planned_risk": risk, "R": round(pnl / risk, 3) if (pnl is not None and risk) else None,
           "slippage_R": round(s * (rec["entry_avg"] - rec["plan_entry"]) / rps, 3) if rps else None,
           "filled_at": rec.get("filled_at"), "exited_at": last_fill["filled_at"] if last_fill else None,
           "hold_days": rec["hold_days"], "sessions": len(rec.get("sessions", [])), "be_done": rec.get("be_done"),
           "sector": rec.get("sector"), "features": rec.get("features")}
    # Rule C rule-faithful R: from the official open to the exit, in units of the rule's 2 ATR risk
    f = rec.get("features") or {}
    if rec["arm"] == "rulec" and f.get("open") and f.get("atr") and avg_exit is not None:
        row["rule_R"] = round((f["open"] - avg_exit) / (2 * f["atr"]), 3)
    # same-day intraday path: MFE/MAE and fill realism (1-minute bars, today only)
    t_in, t_out = ts_ny(rec.get("filled_at")), ts_ny(row["exited_at"])
    if t_in and t_out and t_in.date() == t_out.date() == t.date() and rps:
        bars = minute_bars(rec["symbol"], t.date())
        held = [b for b in bars if b[0].timestamp() + 60 > t_in.timestamp() and b[0] <= t_out]
        if held:
            hi, lo = max(b[2] for b in held), min(b[3] for b in held)
            fav, adv = (hi - rec["entry_avg"], rec["entry_avg"] - lo) if s > 0 else (rec["entry_avg"] - lo, hi - rec["entry_avg"])
            row["mfe_R"], row["mae_R"] = round(fav / rps, 2), round(adv / rps, 2)
        placed = datetime.strptime(f"{rec['placed_day']} {rec['placed_at']}", "%Y-%m-%d %H:%M").replace(tzinfo=NY)
        if rec["entry_type"] == "limit" and rec.get("limit_price"):
            L = float(rec["limit_price"])
            win = [b for b in bars if placed <= b[0] <= t_in]
            row["entry_through"] = any((b[3] <= L - 0.01) if s > 0 else (b[2] >= L + 0.01) for b in win) if win else None
        else:
            row["entry_through"] = True        # marketable / stop entries fill through the book
        if kind == "target" and rec.get("target"):
            T = float(rec["target"])
            win = [b for b in bars if t_in <= b[0] <= t_out]
            through = any((b[2] >= T + 0.01) if s > 0 else (b[3] <= T - 0.01) for b in win) if win else None
            row["target_through"] = through
            if through is False:
                # pessimistic alternative: the target never traded through, so assume no fill and follow the
                # bars after it: stop first -> stop, else the bar close at the flat-by time
                after = [b for b in bars if b[0] > t_out and minutes(b[0]) <= hm(eff_flat(rec["flat_by"]))]
                alt = None
                for b in after:
                    if (b[3] <= rec["stop"]) if s > 0 else (b[2] >= rec["stop"]):
                        alt = rec["stop"]
                        break
                if alt is None and after:
                    alt = after[-1][4]
                if alt is not None:
                    row["R_alt"] = round(s * (alt - rec["entry_avg"]) * qty / risk, 3)
    return row


def ledger(day: str, st: dict, t: datetime, book: list):
    rows, keep, unfilled = [], [], []
    for rec in book:
        try:
            refresh(rec)
        except Exception as e:  # noqa: BLE001
            log("ledger refresh", rec.get("id"), e)
        if not rec.get("entry_filled"):
            if rec.get("entry_status") in OPEN_ST and rec["hold_days"] > 0 and rec["placed_day"] == day:
                keep.append(rec)            # should not happen (cancel_at), kept for safety
            else:
                unfilled.append({"id": rec["id"], "account": rec["account"], "arm": rec["arm"],
                                 "symbol": rec["symbol"], "status": rec.get("entry_status")})
            continue
        if (rec.get("remaining") or 0) > 0 or open_children(rec):
            keep.append(rec)                # swing still open (or an intraday leftover: flagged below)
            if rec["hold_days"] <= 0 and rec["exit_mode"] != "moc":
                st.setdefault("errors", []).append({"at": t.strftime("%H:%M"), "phase": "ledger",
                                                    "error": f"intraday {rec['id']} still open after the close"})
            continue
        try:
            rows.append(leg_row(rec, day, t))
        except Exception as e:  # noqa: BLE001
            log("ledger row", rec["id"], e)
            keep.append(rec)
    book[:] = keep
    st["ledger"] = rows
    st["unfilled"] = unfilled
    path = LEDGER / "ledger.json"
    allrows = [r for r in load_json(path, []) if r.get("id") not in {x["id"] for x in rows}] + rows
    save_json(path, allrows)
    log(f"ledger {day}: {len(rows)} legs closed, {len(unfilled)} unfilled, {len(keep)} still open")
    try:
        import paper_stats
        paper_stats.main()
    except Exception as e:  # noqa: BLE001
        log("stats failed", e)


# ---------------------------------------------------------------- actions
def trading_close(t: datetime) -> int | None:
    acct = A if A.ok else B
    s, cal = acct.get("/v2/calendar", start=t.date().isoformat(), end=t.date().isoformat())
    if s == 200 and cal and cal[0]["date"] == t.date().isoformat():
        return hm(cal[0].get("close") or "16:00")
    return None


def tick():
    global CLOSE_MIN
    t = now_ny()
    day = t.date().isoformat()
    close = trading_close(t)
    if close is None:
        log(f"{day}: not a trading day")
        return
    CLOSE_MIN = close
    sp = STATE / f"{day}.json"
    st = load_json(sp, {"date": day})
    st["close"] = f"{close // 60:02d}:{close % 60:02d}"
    st.setdefault("ticks", []).append(t.strftime("%H:%M"))
    book = load_book()
    m = minutes(t)
    cut = hm(flat_cutoff())

    def phase(name, fn, *a):
        try:
            fn(*a)
        except Exception as e:  # noqa: BLE001
            log(f"ERROR in {name}: {type(e).__name__}: {str(e)[:300]}")
            st.setdefault("errors", []).append({"at": t.strftime("%H:%M"), "phase": name, "error": str(e)[:300]})

    try:
        if hm("09:35") <= m < hm("10:00") and "rulec" not in st:
            phase("rulec", run_rulec, day, st, t, book)
            st.setdefault("rulec", {"skipped": "failed; see errors"})
        if m < cut:
            phase("queue", run_queue, day, st, t, book)
        if hm("10:00") <= m < hm("10:15") and "scan" not in st:
            phase("scan", prepare_scan, day, st, t)
        if hm("10:15") <= m < hm("11:30") and not st.get("orb"):
            phase("orb", run_orb, day, st, t, book)
            if not st.get("orb"):
                st["orb_attempts"] = st.get("orb_attempts", 0) + 1
                if st["orb_attempts"] >= 2:
                    st["orb"] = {"skipped": "ORB failed twice; see errors"}
        elif m >= hm("11:30") and not st.get("orb"):
            st["orb"] = {"skipped": f"tick at {t:%H:%M} is past 11:30; ORB stale"}
        save_book(book)
        phase("manage", manage, day, st, t, book)
        save_book(book)
        if cut <= m < close:
            if st.get("close_pass"):
                phase("reconcile", reconcile, day, st, t, book)
            st["close_pass"] = st.get("close_pass", 0) + 1
        if m >= close + 5:
            phase("ledger", ledger, day, st, t, book)
    finally:
        save_book(book)
        st.setdefault("log", []).extend(LOG)
        save_json(sp, st)


def check():
    out = {}
    for acct in (A, B):
        if not acct.ok:
            out[acct.name] = "keys missing"
            continue
        s, a = acct.get("/v2/account")
        s2, c = acct.get("/v2/clock")
        px = acct.last_price("SPY")
        a = a or {}
        out[acct.name] = {"http": s, "status": a.get("status"), "equity": a.get("equity"), "cash": a.get("cash"),
                          "buying_power": a.get("buying_power"), "shorting_enabled": a.get("shorting_enabled"),
                          "options_approved_level": a.get("options_approved_level"),
                          "options_trading_level": a.get("options_trading_level"),
                          "options_buying_power": a.get("options_buying_power"),
                          "account_tail": str(a.get("account_number", ""))[-4:],
                          "clock": c if s2 == 200 else s2, "spy_last_iex": px, "endpoint": acct.base}
        # options contracts endpoint reachable? (read-only)
        s3, oc = acct.get("/v2/options/contracts", underlying_symbols="TQQQ", limit="1")
        out[acct.name]["options_contracts_http"] = s3
        out[acct.name]["options_sample"] = ((oc or {}).get("option_contracts") or [None])[0] if s3 == 200 else None
        log(acct.name, json.dumps(out[acct.name], default=str))
    # data fallbacks (Alpaca bars) and Yahoo reachability, so an outage is known before it matters
    data = {}
    for rng, iv in (("3mo", "1d"), ("5d", "5m")):
        try:
            bars = _alpaca_bars("SPY", rng, iv)
            data[f"alpaca_{iv}"] = {"bars": len(bars), "last": str(bars[-1]) if bars else None}
        except Exception as e:  # noqa: BLE001
            data[f"alpaca_{iv}"] = {"error": str(e)[:120]}
    try:
        prev = daily_before("SPY", now_ny().date())[-1][0].date()
        mb = alpaca_multi_5m(["SPY", "AAPL", "XOM"], prev,
                             datetime.combine(prev, datetime.min.time()).replace(hour=16, tzinfo=NY).astimezone(timezone.utc))
        data["alpaca_multi_5m"] = {k: len(v) for k, v in mb.items()}
    except Exception as e:  # noqa: BLE001
        data["alpaca_multi_5m"] = str(e)[:120]
    try:
        data["yahoo_1d"] = len(_yahoo("SPY", "3mo", "1d"))
    except Exception as e:  # noqa: BLE001
        data["yahoo_1d"] = str(e)[:120]
    log("data", json.dumps(data))
    save_json(LEDGER / "check.json", {"at": now_ny().isoformat(), "accounts": out, "data": data})


def test():
    """Far-away orders that cannot fill: long + short brackets, a short OTO; read back, cancel."""
    out = {}
    for acct in (A, B):
        if not acct.ok:
            continue
        px = acct.last_price("SPY") or 700.0
        stamp = int(datetime.now().timestamp())
        cases = {
            "long_limit": dict(side="long", entry_type="limit", limit_price=px * 0.5, stop_loss=px * 0.45, take_profit=px * 0.6),
            "long_stop_limit": dict(side="long", entry_type="stop_limit", stop_price=px * 1.5, limit_price=px * 1.51,
                                    stop_loss=px * 1.4, take_profit=px * 1.7),
            "short_limit": dict(side="short", entry_type="limit", limit_price=px * 1.5, stop_loss=px * 1.6, take_profit=px * 1.4),
            "short_stop_limit": dict(side="short", entry_type="stop_limit", stop_price=px * 0.5, limit_price=px * 0.49,
                                     stop_loss=px * 0.6, take_profit=px * 0.4),
            "short_oto_limit": dict(side="short", entry_type="limit", limit_price=px * 1.5, stop_loss=px * 1.6, take_profit=None),
            "long_limit_gtc": dict(side="long", entry_type="limit", limit_price=px * 0.5, stop_loss=px * 0.45,
                                   take_profit=px * 0.6, hold_days=2),
        }
        res = {}
        for name, c in cases.items():
            o = dict(c, symbol="SPY", qty=1, id=f"test-{acct.name}-{name}-{stamp}")
            s, d = acct.post("/v2/orders", bracket(o))
            d = d or {}
            res[name] = {"http": s, "status": d.get("status"), "order_class": d.get("order_class"),
                         "tif": d.get("time_in_force"), "legs": len(d.get("legs") or []), "message": d.get("message")}
            if s in (200, 201):
                if name == "long_limit":
                    # breakeven move works on a held stop leg?
                    leg = next((x for x in (d.get("legs") or []) if x.get("type") == "stop"), None)
                    if leg:
                        ps, pd = acct.patch(f"/v2/orders/{leg['id']}", {"stop_price": f"{px * 0.46:.2f}"})
                        res[name]["patch_stop_http"] = ps
                        res[name]["patch_stop_msg"] = (pd or {}).get("message")
                cs, _ = acct.delete(f"/v2/orders/{d['id']}")
                res[name]["cancel_http"] = cs
        res["SPY_shortable"] = acct.shortable("SPY") or "ok"
        out[acct.name] = res
        log(acct.name, json.dumps(res))
    save_json(LEDGER / "test.json", {"at": now_ny().isoformat(), "results": out})


def main() -> int:
    action = (sys.argv[1] if len(sys.argv) > 1 else os.environ.get("ACTION") or "tick").strip()
    {"check": check, "test": test, "tick": tick, "flatten": flatten_all}[action]()
    return 0


if __name__ == "__main__":
    sys.exit(main())
