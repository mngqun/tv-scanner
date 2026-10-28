"""Runs Jake Schincariol's day-trader-skill tools (MIT) on GitHub Actions and commits the results.

The Claude workspace cannot reach Yahoo / SEC / Nasdaq Trader / the House Clerk, so the daily brief
dispatches this through .github/workflows/trader-skills.yml and reads scans/trader/<mode>_latest.json.

Modes
  premarket  large-cap universe (TradingView, our framework filters) -> scan.py daily ->
             news.py on the hits + extra tickers -> ptr.py new (House PTRs)
  orb        scan.py orb on the large-cap universe -> for the top hits: entry.py check ->
             exit.py plan (or-mid stop, 2R target, flat 15:55) -> timemachine.py run --until 15:55
  tm         one explicit plan through the Time Machine (SYMBOL, DATE, ENTRY, STOP, TARGET, ENTRY_TIME)

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
        "min_order_usd": 5, "max_loss_per_trade_usd": 100, "max_daily_loss_usd": 200,
        "max_trades_per_day": 2, "max_orders_per_day": 10, "min_reward_risk": 1.5, "require_stop": True,
        "no_entries_before": "09:45", "no_entries_after": "15:30", "flat_by": "15:55",
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
    return path


def premarket(extra: list[str]) -> dict:
    uni = largecaps()
    n = len(uni.read_text().split())
    scan = tool("trader-scanner/scan.py", "daily", "--watchlist", str(uni), "--json", "--show", "200")
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
    hits = (scan.get("data") or {}).get("hits", [])
    plans = []
    for h in hits[:top_n]:
        s = h["symbol"]
        ent_path, plan_path = f"trader/entry_{s}.json", f"trader/plan_{s}.json"
        ent = tool("trader-entry/entry.py", "check", s, "--write", ent_path, "--json")
        row = {"symbol": s, "entry": ent}
        if (ent.get("data") or {}).get("status") == "triggered":
            ex = tool("trader-exit/exit.py", "plan", "--from", ent_path, "--stop", "or-mid", "--target", "2R",
                      "--write", plan_path, "--json")
            row["exit"] = ex
            if ex.get("code") == 0:
                row["timemachine"] = tool("trader-timemachine/timemachine.py", "run", "--plan", plan_path,
                                          "--until", "15:55", "--json")
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
