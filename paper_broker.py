"""Paper-trading executor for the daily brief, TQQQ plans and the ORB check (Alpaca PAPER only).

Runs on GitHub Actions (.github/workflows/paper-broker.yml). The Claude workspace cannot reach Alpaca.

Accounts (keys in repo secrets, never in code or logs):
  A  "rules"   ALPACA_PAPER_KEY_ID   / ALPACA_PAPER_SECRET_KEY      published setups + TQQQ + ORB qualifiers
  B  "control" ALPACA_PAPER_KEY_ID_B / ALPACA_PAPER_SECRET_KEY_B    every ORB top-N breakout, unfiltered

Actions
  check     read-only: account status, equity, clock (proves the keys and the paper endpoint)
  test      place one far-away bracket on A and B, read it back, cancel it (validates order formats)
  tick      do everything that is due now (idempotent; state in paper/state/<date>.json):
              - place queued orders from paper/queue/<date>.json whose not_before has passed
              - ORB at >= 10:15 ET (once): A = qualifiers, B = all triggered plans
              - cancel unfilled entries past cancel_at (default 11:30 ET)
              - close positions past their flat_by; flatten everything from 15:45 ET
              - ledger from 16:05 ET
  flatten   cancel all orders and close all positions on A and B now

Safety: refuses any base URL other than https://paper-api.alpaca.markets. Sleeve limits from
claude/screening-framework.md are enforced on A regardless of the paper balance:
risk <= $100/trade ($50 mixed regime), <= $5,000 notional, <= 2 entries/day, <= 2 open positions,
no new entries once the day's P/L <= -$200.
"""
import json
import math
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

PAPER_BASE = "https://paper-api.alpaca.markets"
DATA_BASE = "https://data.alpaca.markets"
NY = ZoneInfo("America/New_York")
ROOT = Path(__file__).resolve().parent
QUEUE = ROOT / "paper" / "queue"
STATE = ROOT / "paper" / "state"
LEDGER = ROOT / "scans" / "paper"
MAX_NOTIONAL = 5000.0
MAX_ENTRIES_A = 2
MAX_OPEN_A = 2
DAY_STOP = -200.0
ORB_N = int(os.environ.get("ORB_N") or 8)
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

    def delete(self, path):
        return self._req("DELETE", self.base + path)

    def last_price(self, sym: str):
        st, d = self._req("GET", f"{DATA_BASE}/v2/stocks/{sym}/trades/latest?feed=iex")
        if st == 200 and d and d.get("trade"):
            return float(d["trade"]["p"])
        return None


A = Alpaca("A", "ALPACA_PAPER_KEY_ID", "ALPACA_PAPER_SECRET_KEY")
B = Alpaca("B", "ALPACA_PAPER_KEY_ID_B", "ALPACA_PAPER_SECRET_KEY_B")
ACCTS = {"A": A, "B": B}


def now_ny() -> datetime:
    override = os.environ.get("NOW_NY")
    if override:
        return datetime.strptime(override, "%Y-%m-%d %H:%M").replace(tzinfo=NY)
    return datetime.now(timezone.utc).astimezone(NY)


def hm(s: str):
    h, m = s.split(":")
    return int(h) * 60 + int(m)


def minutes(t: datetime) -> int:
    return t.hour * 60 + t.minute


def r2(x: float) -> float:
    return round(x + 1e-9, 2)


# ---------------------------------------------------------------- state
def load_json(p: Path, default):
    try:
        return json.loads(p.read_text())
    except Exception:
        return default


def state_path(day: str) -> Path:
    return STATE / f"{day}.json"


# ---------------------------------------------------------------- guards (account A only)
def guard_a(day: str, st: dict) -> str | None:
    s, acct = A.get("/v2/account")
    if s != 200:
        return f"account read failed {s}"
    pnl = float(acct["equity"]) - float(acct["last_equity"])
    if pnl <= DAY_STOP:
        return f"day P/L {pnl:.2f} <= {DAY_STOP}"
    s, pos = A.get("/v2/positions")
    if s == 200 and len(pos) >= MAX_OPEN_A:
        return f"{len(pos)} open positions"
    base = {o["id"].removesuffix("-t1").removesuffix("-t2") for o in st.get("placed", [])
            if o["account"] == "A" and o.get("status") == "placed" and not o.get("cancelled_unfilled")}
    if len(base) >= MAX_ENTRIES_A:
        return f"{len(base)} entries already today"
    return None


# ---------------------------------------------------------------- orders
def bracket(o: dict) -> dict:
    """o: symbol, qty, entry_type (market|limit|stop_limit), limit_price, stop_price, stop_loss, take_profit, id."""
    body = {"symbol": o["symbol"], "qty": str(int(o["qty"])), "side": "buy", "type": o["entry_type"],
            "time_in_force": "day", "order_class": "bracket", "client_order_id": o["id"][:48],
            "take_profit": {"limit_price": f"{r2(o['take_profit']):.2f}"},
            "stop_loss": {"stop_price": f"{r2(o['stop_loss']):.2f}"}}
    if o["entry_type"] in ("limit", "stop_limit"):
        body["limit_price"] = f"{r2(o['limit_price']):.2f}"
    if o["entry_type"] == "stop_limit":
        body["stop_price"] = f"{r2(o['stop_price']):.2f}"
    return body


def size(entry: float, stop: float, risk_usd: float) -> int:
    per = entry - stop
    if per <= 0:
        return 0
    return max(0, min(math.floor(risk_usd / per), math.floor(MAX_NOTIONAL / entry)))


def place(acct: Alpaca, o: dict, day: str, st: dict) -> dict:
    """Place one bracket (or a T1/T2 pair when take_profit_2 is set). Records into state."""
    legs = [o]
    if o.get("take_profit_2"):
        q1 = int(o["qty"]) // 2
        q2 = int(o["qty"]) - q1
        legs = [dict(o, qty=q1, id=o["id"] + "-t1"), dict(o, qty=q2, take_profit=o["take_profit_2"], id=o["id"] + "-t2")]
    res = []
    for leg in legs:
        if int(leg["qty"]) < 1:
            continue
        s, d = acct.post("/v2/orders", bracket(leg))
        rec = {"account": acct.name, "id": leg["id"], "symbol": leg["symbol"], "qty": int(leg["qty"]),
               "entry": leg.get("plan_entry") or leg.get("limit_price") or leg.get("stop_price"),
               "stop": leg["stop_loss"], "target": leg["take_profit"], "source": leg.get("source"),
               "cancel_at": leg.get("cancel_at") or "11:30", "flat_by": leg.get("flat_by") or "15:45",
               "planned_risk": round(int(leg["qty"]) * ((leg.get("plan_entry") or leg.get("limit_price")
                                                        or leg.get("stop_price")) - leg["stop_loss"]), 2),
               "http": s, "status": "placed" if s in (200, 201) else "rejected",
               "broker_id": (d or {}).get("id"), "message": None if s in (200, 201) else (d or {}).get("message"),
               "at": now_ny().strftime("%H:%M"), "last_at_placement": leg.get("last_px")}
        log(f"[{acct.name}] {rec['status']} {leg['symbol']} x{leg['qty']} {leg['entry_type']} "
            f"stop {leg['stop_loss']} tgt {leg['take_profit']} -> {s} {rec['message'] or ''}")
        st.setdefault("placed", []).append(rec)
        res.append(rec)
    return {"legs": res}


# ---------------------------------------------------------------- queue (brief + TQQQ setups)
def run_queue(day: str, st: dict, t: datetime):
    q = load_json(QUEUE / f"{day}.json", [])
    done = set(st.setdefault("queue_done", []))
    for item in q:
        qid = item["id"]
        if qid in done:
            continue
        if minutes(t) < hm(item.get("not_before") or "09:35"):
            continue
        if minutes(t) >= hm(item.get("cancel_at") or "11:30"):
            log(f"queue {qid}: missed window (now {t:%H:%M}); not placed")
            done.add(qid)
            continue
        acct = ACCTS[item.get("account", "A")]
        why = guard_a(day, st) if acct is A else None
        if why:
            log(f"queue {qid}: skipped ({why})")
            st.setdefault("skipped", []).append({"id": qid, "why": why})
            done.add(qid)
            continue
        px = acct.last_price(item["symbol"])
        item = dict(item)
        va, vb = item.get("void_above"), item.get("void_below")
        if px is None:
            log(f"queue {qid}: no live price; not placed")
            st.setdefault("skipped", []).append({"id": qid, "why": "no live price"})
            done.add(qid)
            continue
        if (va is not None and px > va) or (vb is not None and px < vb):
            why = f"void: last {px} outside [{vb}, {va}]"
            log(f"queue {qid}: {why}")
            st.setdefault("skipped", []).append({"id": qid, "why": why})
            done.add(qid)
            continue
        if item.get("qty") in (None, "", 0):
            item["qty"] = size(item.get("plan_entry") or item["limit_price"], item["stop_loss"], item["risk_usd"])
        item["last_px"] = px
        place(acct, item, day, st)
        done.add(qid)
    st["queue_done"] = sorted(done)


# ---------------------------------------------------------------- ORB (trader-skills pipeline)
def regime() -> str:
    """SPY & QQQ prior close vs EMA20 (Yahoo daily closes, today excluded)."""
    out = {}
    for sym in ("SPY", "QQQ"):
        url = f"https://query2.finance.yahoo.com/v8/finance/chart/{sym}?range=3mo&interval=1d"
        req = urllib.request.Request(url, headers={"User-Agent": "tv-scanner-paper/1.0"})
        with urllib.request.urlopen(req, timeout=30) as r:
            d = json.loads(r.read())["chart"]["result"][0]
        ts, cl = d["timestamp"], d["indicators"]["quote"][0]["close"]
        today = now_ny().date()
        closes = [c for t, c in zip(ts, cl) if c is not None and datetime.fromtimestamp(t, NY).date() < today]
        ema = closes[0]
        for c in closes[1:]:
            ema = c * (2 / 21) + ema * (19 / 21)
        out[sym] = closes[-1] > ema
    if out["SPY"] and out["QQQ"]:
        return "long"
    if not out["SPY"] and not out["QQQ"]:
        return "short"
    return "mixed"


def run_orb(day: str, st: dict, t: datetime):
    import trader_skills as TS
    reg = regime()
    risk = 100.0 if reg == "long" else 50.0
    st["regime"] = reg
    os.environ["RISK_USD"] = str(risk)
    TS.WORK.mkdir(exist_ok=True)
    TS.write_config()
    res = TS.orb(None, ORB_N)
    earn_today = set()
    try:
        import tv_scan
        rows = tv_scan.run_scan({"filter": tv_scan.BASE_FILTERS, "sort": {"sortBy": "market_cap_basic",
                                 "sortOrder": "desc"}, "limit": 2000}, None)
        for r in rows:
            e = r.get("earnings_release_next_date")
            if e and datetime.fromtimestamp(e, NY).date() == t.date():
                earn_today.add(r["name"])
    except Exception as e:  # noqa: BLE001
        log("earnings lookup failed", e)
    ev = load_json(ROOT / "paper" / "events.json", {}).get(day, {})
    flat_by = os.environ.get("FLAT_BY") or ev.get("flat_by") or "15:45"
    if ev.get("risk_usd"):
        risk = min(risk, float(ev["risk_usd"]))
    if ev.get("no_orb"):
        st["orb"] = {"skipped": f"events.json: {ev.get('note', 'no ORB today')}"}
        log(st["orb"]["skipped"])
        return
    summary = []
    for p in res.get("plans", []):
        sym = p["symbol"]
        ent = (p.get("entry") or {}).get("data") or {}
        ex = (p.get("exit") or {}).get("data") or {}
        tm = (p.get("timemachine") or {}).get("data") or {}
        rk = ((((p.get("risk") or {}).get("data") or {}).get("results")) or [{}])[0]
        row = {"symbol": sym, "trigger": (ent.get("trigger") or {}).get("time"), "entry": ex.get("entry"),
               "stop": ex.get("stop"), "target": ex.get("target"), "verdict": tm.get("verdict"),
               "hist": {k: (tm.get("history") or {}).get(k) for k in ("triggered", "target", "stop", "avg_R")},
               "risk": rk.get("verdict"), "earnings_today": sym in earn_today}
        summary.append(row)
        if ent.get("status") != "triggered" or not ex.get("entry") or not ex.get("stop"):
            continue
        entry, stop, target = float(ex["entry"]), float(ex["stop"]), float(ex["target"])
        R = entry - stop
        for acct in (A, B):
            if not acct.ok:
                continue
            if acct is A:
                v = (tm.get("verdict") or "")
                h = tm.get("history") or {}
                qualifies = (v.startswith("Leans toward the target") or v.startswith("Strongly toward the target")) \
                    and (h.get("triggered") or 0) >= 20 and (h.get("avg_R") or 0) > 0 \
                    and rk.get("verdict") == "PASS" and reg != "short" and sym not in earn_today
                if not qualifies:
                    continue
                why = guard_a(day, st)
                if why:
                    log(f"ORB {sym}: A skipped ({why})")
                    continue
            px = acct.last_price(sym)
            if px is None:
                log(f"ORB {sym}: no price for {acct.name}")
                continue
            if px > entry + 0.5 * R or px <= stop:
                log(f"ORB {sym} [{acct.name}]: last {px} outside entry window ({stop}..{entry + 0.5 * R:.2f})")
                continue
            qty = size(max(px, entry), stop, risk)
            if qty < 1:
                continue
            o = {"symbol": sym, "qty": qty, "stop_loss": stop, "take_profit": target, "plan_entry": entry,
                 "source": "orb", "flat_by": flat_by, "cancel_at": "11:30",
                 "id": f"orb-{acct.name}-{day}-{sym}"}
            if px >= entry:
                o.update(entry_type="limit", limit_price=min(px * 1.001, entry + 0.5 * R))
            else:
                o.update(entry_type="stop_limit", stop_price=entry, limit_price=entry + 0.1 * R)
            place(acct, o, day, st)
    st["orb"] = {"at": t.strftime("%H:%M"), "regime": reg, "risk_usd": risk,
                 "counts": ((res.get("scan") or {}).get("data") or {}).get("counts"), "plans": summary}


# ---------------------------------------------------------------- cancels / flatten
def cancel_due(st: dict, t: datetime):
    for rec in st.get("placed", []):
        if rec.get("status") != "placed" or rec.get("cancel_done"):
            continue
        if minutes(t) < hm(rec.get("cancel_at") or "11:30"):
            continue
        acct = ACCTS[rec["account"]]
        s, o = acct.get(f"/v2/orders/{rec['broker_id']}", nested="true")
        if s == 200 and o.get("status") in ("new", "accepted", "pending_new", "partially_filled") \
                and float(o.get("filled_qty") or 0) == 0:
            cs, _ = acct.delete(f"/v2/orders/{rec['broker_id']}")
            log(f"[{acct.name}] cancel unfilled {rec['id']} -> {cs}")
            rec["cancelled_unfilled"] = True
        rec["cancel_done"] = True


def close_symbol(acct: Alpaca, sym: str):
    s, orders = acct.get("/v2/orders", status="open", nested="true", symbols=sym)
    for o in (orders or []) if s == 200 else []:
        acct.delete(f"/v2/orders/{o['id']}")
    s, d = acct.delete(f"/v2/positions/{sym}")
    log(f"[{acct.name}] close {sym} -> {s}")


def flat_due(st: dict, t: datetime):
    for rec in st.get("placed", []):
        if rec.get("status") != "placed" or rec.get("flat_done"):
            continue
        if minutes(t) >= hm(rec.get("flat_by") or "15:45"):
            close_symbol(ACCTS[rec["account"]], rec["symbol"])
            rec["flat_done"] = True


def flatten_all():
    for acct in (A, B):
        if not acct.ok:
            continue
        s1, _ = acct.delete("/v2/orders")
        s2, d = acct.delete("/v2/positions?cancel_orders=true")
        log(f"[{acct.name}] flatten: cancel orders {s1}, close positions {s2} ({len(d or [])})")


# ---------------------------------------------------------------- ledger
def ledger(day: str, st: dict):
    rows = []
    for acct in (A, B):
        if not acct.ok:
            continue
        s, orders = acct.get("/v2/orders", status="all", after=f"{day}T00:00:00Z", nested="true", limit="500")
        fills = {}
        for o in orders or []:
            for x in [o] + (o.get("legs") or []):
                q = float(x.get("filled_qty") or 0)
                if q:
                    f = fills.setdefault(x["symbol"], {"buy": 0.0, "sell": 0.0, "bq": 0.0, "sq": 0.0})
                    px = float(x["filled_avg_price"])
                    if x["side"] == "buy":
                        f["buy"] += q * px; f["bq"] += q
                    else:
                        f["sell"] += q * px; f["sq"] += q
        s, acc = acct.get("/v2/account")
        for sym, f in fills.items():
            plan = [r for r in st.get("placed", []) if r["account"] == acct.name and r["symbol"] == sym]
            risk = sum(r.get("planned_risk") or 0 for r in plan) or None
            pnl = round(f["sell"] - f["buy"], 2) if f["sq"] == f["bq"] else None
            rows.append({"date": day, "account": acct.name, "symbol": sym,
                         "source": plan[0].get("source") if plan else "manual?",
                         "qty": f["bq"], "avg_entry": round(f["buy"] / f["bq"], 4) if f["bq"] else None,
                         "avg_exit": round(f["sell"] / f["sq"], 4) if f["sq"] else None,
                         "pnl": pnl, "planned_risk": risk,
                         "R": round(pnl / risk, 3) if (pnl is not None and risk) else None,
                         "open": f["sq"] != f["bq"]})
        st.setdefault("equity", {})[acct.name] = {"equity": acc.get("equity"), "last_equity": acc.get("last_equity")} \
            if s == 200 else None
    st["ledger"] = rows
    LEDGER.mkdir(parents=True, exist_ok=True)
    all_path = LEDGER / "ledger.json"
    allrows = [r for r in load_json(all_path, []) if r["date"] != day] + rows
    all_path.write_text(json.dumps(allrows, indent=1))
    log(f"ledger {day}: {len(rows)} rows")


# ---------------------------------------------------------------- actions
def is_trading_day(t: datetime) -> bool:
    acct = A if A.ok else B
    s, cal = acct.get("/v2/calendar", start=t.date().isoformat(), end=t.date().isoformat())
    return s == 200 and bool(cal) and cal[0]["date"] == t.date().isoformat()


def tick():
    t = now_ny()
    day = t.date().isoformat()
    if not is_trading_day(t):
        log(f"{day}: not a trading day")
        return
    sp = state_path(day)
    st = load_json(sp, {"date": day})
    st.setdefault("ticks", []).append(t.strftime("%H:%M"))
    m = minutes(t)
    if m < hm("15:45"):
        run_queue(day, st, t)
    if hm("10:15") <= m < hm("11:30") and not st.get("orb"):
        run_orb(day, st, t)
    elif m >= hm("11:30") and not st.get("orb"):
        st["orb"] = {"skipped": f"tick at {t:%H:%M} is past 11:30; ORB stale"}
        log(st["orb"]["skipped"])
    cancel_due(st, t)
    flat_due(st, t)
    if m >= hm("15:45") and not st.get("flattened"):
        flatten_all()
        st["flattened"] = t.strftime("%H:%M")
    if m >= hm("16:05"):
        ledger(day, st)
    STATE.mkdir(parents=True, exist_ok=True)
    st.setdefault("log", []).extend(LOG)
    sp.write_text(json.dumps(st, indent=1, default=str))


def check():
    out = {}
    for acct in (A, B):
        if not acct.ok:
            out[acct.name] = "keys missing"
            continue
        s, a = acct.get("/v2/account")
        s2, c = acct.get("/v2/clock")
        px = acct.last_price("SPY")
        out[acct.name] = {"http": s, "status": (a or {}).get("status"), "equity": (a or {}).get("equity"),
                          "cash": (a or {}).get("cash"), "buying_power": (a or {}).get("buying_power"),
                          "shorting_enabled": (a or {}).get("shorting_enabled"),
                          "account_tail": str((a or {}).get("account_number", ""))[-4:],
                          "clock": c if s2 == 200 else s2, "spy_last_iex": px, "endpoint": acct.base}
        log(acct.name, json.dumps(out[acct.name]))
    LEDGER.mkdir(parents=True, exist_ok=True)
    (LEDGER / "check.json").write_text(json.dumps({"at": now_ny().isoformat(), "accounts": out}, indent=1))


def test():
    """One bracket per account, far from the market so it cannot fill, then cancel."""
    out = {}
    for acct in (A, B):
        if not acct.ok:
            continue
        px = acct.last_price("SPY") or 700.0
        res = {}
        for kind in ("limit", "stop_limit"):
            o = {"symbol": "SPY", "qty": 1, "entry_type": kind, "id": f"test-{acct.name}-{kind}-{int(datetime.now().timestamp())}"}
            if kind == "limit":
                o.update(limit_price=px * 0.5, stop_loss=px * 0.45, take_profit=px * 0.6)
            else:
                o.update(stop_price=px * 1.5, limit_price=px * 1.51, stop_loss=px * 1.4, take_profit=px * 1.7)
            s, d = acct.post("/v2/orders", bracket(o))
            res[kind] = {"http": s, "status": (d or {}).get("status"), "order_class": (d or {}).get("order_class"),
                         "legs": len((d or {}).get("legs") or []), "message": (d or {}).get("message")}
            if s in (200, 201):
                cs, _ = acct.delete(f"/v2/orders/{d['id']}")
                res[kind]["cancel_http"] = cs
        out[acct.name] = res
        log(acct.name, json.dumps(res))
    LEDGER.mkdir(parents=True, exist_ok=True)
    (LEDGER / "test.json").write_text(json.dumps({"at": now_ny().isoformat(), "results": out}, indent=1))


def main() -> int:
    action = (sys.argv[1] if len(sys.argv) > 1 else os.environ.get("ACTION") or "tick").strip()
    {"check": check, "test": test, "tick": tick, "flatten": flatten_all}[action]()
    return 0


if __name__ == "__main__":
    sys.exit(main())
