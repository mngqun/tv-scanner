"""Runs Jake Schincariol's day-trader-skill tools (MIT) on GitHub Actions and commits the results.

The Claude workspace cannot reach Yahoo / SEC / Nasdaq Trader / the House Clerk, so the daily brief
dispatches this through .github/workflows/trader-skills.yml and reads scans/trader/<mode>_latest.json.

Modes
  premarket  large-cap universe (TradingView, our framework filters) -> scan.py daily ->
             news.py on the hits + extra tickers -> ptr.py new (House PTRs)
  orb        scan.py orb on the large-cap universe -> for the top hits: entry.py check ->
             exit.py plan (or-mid stop, 2R target, flat 15:55) -> timemachine.py run --until 15:55
  tm         one explicit plan through the Time Machine (SYMBOL, DATE, ENTRY, STOP, TARGET, ENTRY_TIME)
  check      after the brief's shortlist: facts.py (SEC earnings numbers) on ANALYST tickers, and
             check.py (Risk Manager) on the brief's long setups in ORDERS, checked together as one day
             ("SYM:ENTRY:STOP:TARGET:QTY[:HH:MM]" separated by ";", HH:MM = planned entry time, NY)
  copycat    trader-copycat 13F diff for the CIKs in TICKERS (default: the pack's bundled filers)
  The orb mode also sizes each planned breakout with size.py and runs check.py on it.

Tools live in vendor/day-trader-skill (cloned at a pinned commit by the workflow). They are read-only:
nothing here can place an order. The SEC User-Agent comes from the SEC_USER_AGENT secret, else
trader_state/sec_user_agent.txt (this repo is private); trader/config.json is never committed.
"""
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
TOOLS = ROOT / "vendor" / "day-trader-skill"
WORK = ROOT / "trader_state"          # the tools' cwd; trader/ lives inside it
OUT = ROOT / "scans" / "trader"
NY = ZoneInfo("America/New_York")
FLAT_BY = os.environ.get("FLAT_BY") or "15:55"
LIQ: dict = {}   # e.g. 13:55 on an FOMC day


def tool(rel: str, *args: str, timeout: int = 900) -> dict:
    """Run one tool with --json; return {code, data|stdout, stderr}."""
    cmd = [sys.executable, "-I", str(TOOLS / rel), *args]
    try:
        p = subprocess.run(cmd, cwd=WORK, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"cmd": " ".join(args), "code": "timeout"}
    res = {"cmd": rel + " " + " ".join(args), "code": p.returncode, "stderr": p.stderr[-2000:]}
    try:
        res["data"] = json.loads(p.stdout)
    except json.JSONDecodeError:
        res["stdout"] = p.stdout[-6000:]
    return res


def sec_user_agent() -> str:
    """SEC_USER_AGENT secret if set, else trader_state/sec_user_agent.txt (private repo)."""
    ua = os.environ.get("SEC_USER_AGENT", "").strip()
    f = WORK / "sec_user_agent.txt"
    return ua or (f.read_text().strip() if f.exists() else "")


def write_config() -> None:
    cfg = {
        # mirrors claude/screening-framework.md (10k sleeve, $100 risk, -$200 day, 2 trades)
        "budget": 10000, "cash_buffer_pct": 10, "max_position_pct": 50, "max_order_usd": 5000,
        "min_order_usd": 5, "max_loss_per_trade_usd": float(os.environ.get("RISK_USD") or 100), "max_daily_loss_usd": 200,
        "max_trades_per_day": 2, "max_orders_per_day": 10, "min_reward_risk": 1.5, "require_stop": True,
        "no_entries_before": "09:45", "no_entries_after": "15:30", "flat_by": FLAT_BY,
        "fractional": False, "broker": "alpaca", "paper": True, "blocked": [],
        "allow_options": False, "allow_margin": False, "allow_short": False, "pdt_guard": False,
        "sec_user_agent": sec_user_agent(),
    }
    (WORK / "trader").mkdir(parents=True, exist_ok=True)
    (WORK / "trader" / "config.json").write_text(json.dumps(cfg, indent=1))


def largecaps() -> Path:
    """Our universe: NYSE/Nasdaq stocks + ADRs, mkt cap >= $10B, price >= $20, 10d avg vol > 1M."""
    import tv_scan  # same filters as the pre-market scan; delayed data is fine for a symbol list
    spec = {"filter": tv_scan.BASE_FILTERS, "sort": {"sortBy": "market_cap_basic", "sortOrder": "desc"},
            "limit": 2000}
    rows = tv_scan.run_scan(spec, None)
    syms = sorted({r["name"] for r in rows if r.get("name")})
    path = WORK / "trader" / "largecaps.txt"
    path.write_text("\n".join(syms) + "\n")
    # average $ traded per day (10d avg volume x price) to rank scanner hits by liquidity
    LIQ.update({r["name"]: (r.get("average_volume_10d_calc") or 0) * (r.get("close") or 0) for r in rows})
    return path


def premarket(extra: list[str]) -> dict:
    uni = largecaps()
    n = len(uni.read_text().split())
    scan = tool("trader-scanner/scan.py", "daily", "--watchlist", str(uni), "--min-volume", "0", "--json", "--show", "200")
    hits = (scan.get("data") or {}).get("hits", [])
    top = [h["symbol"] for h in hits[:25]]
    names = list(dict.fromkeys(extra + top))
    news = tool("trader-news/news.py", "--tickers", *names, "--hours", "18", "--dry-run", "--json") if names else None
    pelosi = tool("trader-pelosi/ptr.py", "new", "--read", "--json")
    return {"universe_size": n, "scan": scan, "news_tickers": names, "news": news, "pelosi": pelosi}


def orb(at: str | None, top_n: int) -> dict:
    uni = largecaps()
    args = ["orb", "--watchlist", str(uni), "--json", "--show", "200"] + (["--at", at] if at else [])
    scan = tool("trader-scanner/scan.py", *args)
    hits = sorted((scan.get("data") or {}).get("hits", []), key=lambda h: -LIQ.get(h["symbol"], 0))
    plans = []
    for h in hits[:top_n]:
        s = h["symbol"]
        ent_path, plan_path = f"trader/entry_{s}.json", f"trader/plan_{s}.json"
        ent = tool("trader-entry/entry.py", "check", s, "--write", ent_path, "--json")
        row = {"symbol": s, "entry": ent}
        if (ent.get("data") or {}).get("status") == "triggered":
            ex = tool("trader-exit/exit.py", "plan", "--from", ent_path, "--stop", "or-mid", "--target", "2R",
                      "--flat-by", FLAT_BY, "--write", plan_path, "--json")
            row["exit"] = ex
            if ex.get("code") == 0:
                row["timemachine"] = tool("trader-timemachine/timemachine.py", "run", "--plan", plan_path,
                                          "--until", FLAT_BY, "--json")
                ord_path = f"trader/orders_{s}.json"
                row["budget"] = tool("trader-budget/size.py", "--plan", plan_path, "--limit",
                                     "--write", ord_path, "--json")
                if row["budget"].get("code") == 0:
                    row["risk"] = tool("trader-risk/check.py", "--orders", ord_path, "--no-write", "--json")
        plans.append(row)
    return {"universe_size": len(uni.read_text().split()), "scan": scan, "plans": plans}


def tm() -> dict:
    e = os.environ
    args = ["run", "--symbol", e["TM_SYMBOL"], "--date", e["TM_DATE"], "--entry", e["TM_ENTRY"],
            "--stop", e["TM_STOP"], "--target", e["TM_TARGET"], "--entry-time", e["TM_ENTRY_TIME"],
            "--until", e.get("TM_UNTIL") or "15:55", "--json"]
    if e.get("TM_SETUP"):
        args += ["--setup", e["TM_SETUP"]]
    return {"timemachine": tool("trader-timemachine/timemachine.py", *args)}


def check(analyst: list[str], orders_spec: str) -> dict:
    out = {}
    if analyst:
        out["analyst"] = tool("trader-analyst/facts.py", *analyst, "--quarters", "6", "--json", timeout=600)
    orders, at = [], None
    for part in [x.strip() for x in orders_spec.split(";") if x.strip()]:
        f = part.split(":")
        sym, entry, stop, target, qty = f[0].upper(), float(f[1]), float(f[2]), float(f[3]), int(float(f[4]))
        if len(f) >= 7:
            at = at or f"{f[5]}:{f[6]}"
        risk = round(entry - stop, 2)
        orders.append({
            "symbol": sym, "side": "buy", "qty": str(qty), "type": "limit", "limit_price": f"{entry:.2f}",
            "time_in_force": "day", "order_class": "bracket",
            "take_profit_limit_price": f"{target:.2f}", "stop_loss_stop_price": f"{stop:.2f}",
            "why": "daily brief long setup (paper)",
            "plan": {"entry": entry, "stop": stop, "target": target, "risk_per_share": risk,
                     "max_loss": round(qty * risk, 2), "setup": "brief", "flat_by": "15:55"}})
    if orders:
        (WORK / "trader" / "orders_brief.json").write_text(json.dumps(orders, indent=1))
        today = datetime.now(NY).strftime("%Y-%m-%d")
        out["risk_checked_at"] = f"{today} {at or '09:45'}"
        out["orders"] = orders
        out["risk"] = tool("trader-risk/check.py", "--orders", "trader/orders_brief.json", "--no-write",
                           "--now", out["risk_checked_at"], "--json")
    return out


COPYCAT_TOOL = ROOT / "vendor" / "trader-skill" / "trader-copycat" / "edgar13f.py"


def copycat(ciks: list[str]) -> dict:
    """trader-copycat (trader-skill pack): latest 13F diff (new / sold out / added / trimmed) per filer.
    Default filers: every CIK in the pack's bundled filers.json. Context only: 13F data is up to 45 days old."""
    if not ciks:
        filers = json.loads((COPYCAT_TOOL.parent / "filers.json").read_text())["filers"]
        ciks = [f["cik"] for f in filers]
    out = {}
    for cik in ciks:
        cmd = [sys.executable, "-I", str(COPYCAT_TOOL), "diff", cik, "--combine", "--json", "--user-agent", sec_user_agent()]
        try:
            p = subprocess.run(cmd, cwd=WORK, capture_output=True, text=True, timeout=600)
            try:
                out[cik] = {"code": p.returncode, "data": json.loads(p.stdout)}
            except json.JSONDecodeError:
                out[cik] = {"code": p.returncode, "stdout": p.stdout[-3000:], "stderr": p.stderr[-1500:]}
        except subprocess.TimeoutExpired:
            out[cik] = {"code": "timeout"}
    return {"copycat": out}


def main() -> int:
    mode = (sys.argv[1] if len(sys.argv) > 1 else os.environ.get("MODE", "premarket")).strip()
    extra = [t.strip().upper() for t in os.environ.get("TICKERS", "").replace(" ", ",").split(",") if t.strip()]
    WORK.mkdir(exist_ok=True)
    OUT.mkdir(parents=True, exist_ok=True)
    write_config()
    now = datetime.now(timezone.utc)
    result = {"mode": mode, "generated_utc": now.isoformat(timespec="seconds"),
              "generated_new_york": now.astimezone(NY).isoformat(timespec="seconds"),
              "tools": "github.com/Jakeschincariol/day-trader-skill @ " + os.environ.get("TOOLS_SHA", "?"),
              "sec_user_agent_set": bool(sec_user_agent())}
    if mode == "premarket":
        result.update(premarket(extra))
    elif mode == "orb":
        result.update(orb(os.environ.get("ORB_AT") or None, int(os.environ.get("TOP_N") or 8)))
    elif mode == "tm":
        result.update(tm())
    elif mode == "copycat":
        result.update(copycat(extra))
    elif mode == "check":
        an = [t.strip().upper() for t in os.environ.get("ANALYST", "").replace(" ", ",").split(",") if t.strip()]
        result.update(check(an, os.environ.get("ORDERS", "")))
        result["risk_usd"] = float(os.environ.get("RISK_USD") or 100)
    else:
        print("unknown mode", mode)
        return 2
    stamp = now.astimezone(NY).strftime("%Y-%m-%d_%H%M")
    for name in (f"{mode}_latest.json", f"{mode}_{stamp}.json"):
        (OUT / name).write_text(json.dumps(result, indent=1, default=str))
    print(json.dumps({k: v for k, v in result.items() if k in ("mode", "generated_new_york", "universe_size")}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
