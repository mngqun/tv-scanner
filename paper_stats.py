"""Per-arm statistics for the paper test, against the go-live criteria (process-review-2026-10-30 section 5).

Reads scans/paper/ledger.json (one row per leg), groups legs into trades (same base id), and writes
scans/paper/stats.json and scans/paper/stats.md.

Per arm (account + arm; the scan arm also per setup):
  trades, win rate, avg R, total R, bootstrap 95% CI of avg R (10,000 resamples, seed 7),
  max drawdown in R, worst day in $, avg slippage in R, median MFE / MAE in R, exit mix,
  and a PESSIMISTIC avg R: trades whose limit entry never traded through are dropped (a real queue
  would likely not have filled), and target fills that never traded through use R_alt (what the
  trade would have done without that fill: stop or the close at the flat-by time).

Overlay "A sleeve (user)": what the user's real $10k sleeve would have done: account A trades from
all arms in order of fill time, at most 2 per day, no new trade after the day reaches -$200.

Go-live (all on the pessimistic numbers): >= 40 trades; avg R >= +0.10 and CI low > 0;
max drawdown <= 8R; no day below -$200 (A arms); avg slippage <= 0.05R.
"""
import json
import random
import statistics
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LEDGER = ROOT / "scans" / "paper"


def trades_from(rows: list) -> list:
    by = defaultdict(list)
    for r in rows:
        if r.get("pnl") is None or not r.get("planned_risk"):
            continue
        by[(r["account"], r["base"])].append(r)
    out = []
    for (acct, base), legs in by.items():
        risk = sum(x["planned_risk"] for x in legs)
        pnl = sum(x["pnl"] for x in legs)
        doubtful = any(x.get("entry_through") is False for x in legs)
        pnl_alt = 0.0
        for x in legs:
            if x.get("target_through") is False and x.get("R_alt") is not None:
                pnl_alt += x["R_alt"] * x["planned_risk"]
            else:
                pnl_alt += x["pnl"]
        slip = [x["slippage_R"] for x in legs if x.get("slippage_R") is not None]
        f = legs[0]
        out.append({"account": acct, "arm": f["arm"], "setup": f.get("setup"), "base": base, "date": f["date"],
                    "closed_day": f.get("closed_day"), "symbol": f["symbol"], "side": f["side"],
                    "filled_at": min((x.get("filled_at") or "") for x in legs), "pnl": round(pnl, 2),
                    "risk": risk, "R": pnl / risk, "R_pess": None if doubtful else pnl_alt / risk,
                    "slippage_R": statistics.mean(slip) if slip else None,
                    "mfe_R": max((x["mfe_R"] for x in legs if x.get("mfe_R") is not None), default=None),
                    "mae_R": max((x["mae_R"] for x in legs if x.get("mae_R") is not None), default=None),
                    "exits": [x.get("exit") for x in legs], "rule_R": f.get("rule_R"),
                    "features": f.get("features") or {}})
    out.sort(key=lambda x: (x["date"], x["filled_at"]))
    return out


def boot_ci(xs: list, n: int = 10000, seed: int = 7):
    if len(xs) < 2:
        return None, None
    rnd = random.Random(seed)
    k = len(xs)
    means = sorted(sum(rnd.choice(xs) for _ in range(k)) / k for _ in range(n))
    return round(means[int(0.025 * n)], 3), round(means[int(0.975 * n) - 1], 3)


def max_dd(rs: list) -> float:
    peak = cum = dd = 0.0
    for r in rs:
        cum += r
        peak = max(peak, cum)
        dd = max(dd, peak - cum)
    return round(dd, 2)


def summarize(ts: list, a_arm: bool) -> dict:
    if not ts:
        return {"trades": 0}
    rs = [t["R"] for t in ts]
    pess = [t["R_pess"] for t in ts if t["R_pess"] is not None]
    lo, hi = boot_ci(pess)
    days = defaultdict(float)
    for t in ts:
        days[t["closed_day"] or t["date"]] += t["pnl"]
    slip = [t["slippage_R"] for t in ts if t["slippage_R"] is not None]
    mfe = [t["mfe_R"] for t in ts if t["mfe_R"] is not None]
    mae = [t["mae_R"] for t in ts if t["mae_R"] is not None]
    exits = defaultdict(int)
    for t in ts:
        for e in t["exits"]:
            exits[e or "?"] += 1
    s = {"trades": len(ts), "win_rate": round(sum(r > 0 for r in rs) / len(rs), 3),
         "avg_R": round(statistics.mean(rs), 3), "total_R": round(sum(rs), 2),
         "pess_trades": len(pess), "pess_avg_R": round(statistics.mean(pess), 3) if pess else None,
         "pess_ci95": [lo, hi], "max_dd_R": max_dd(pess or rs), "worst_day_usd": round(min(days.values()), 2),
         "avg_slippage_R": round(statistics.mean(slip), 3) if slip else None,
         "median_mfe_R": round(statistics.median(mfe), 2) if mfe else None,
         "median_mae_R": round(statistics.median(mae), 2) if mae else None,
         "exit_mix": dict(exits), "first": ts[0]["date"], "last": ts[-1]["date"]}
    rule = [t["rule_R"] for t in ts if t.get("rule_R") is not None]
    if rule:
        s["rule_avg_R"] = round(statistics.mean(rule), 3)
    checks = {"n>=40": len(pess) >= 40,
              "avg>=+0.10R": s["pess_avg_R"] is not None and s["pess_avg_R"] >= 0.10,
              "ci_low>0": lo is not None and lo > 0,
              "max_dd<=8R": s["max_dd_R"] <= 8,
              "slippage<=0.05R": s["avg_slippage_R"] is None or s["avg_slippage_R"] <= 0.05}
    if a_arm:
        checks["no_day<-200"] = s["worst_day_usd"] >= -200
    s["go_live_checks"] = checks
    s["go_live"] = all(checks.values())
    return s


def overlay(ts: list) -> list:
    """User sleeve: account A, any arm, first 2 fills a day, none after the day is at -$200."""
    out, by_day = [], defaultdict(list)
    for t in ts:
        if t["account"] == "A":
            by_day[t["date"]].append(t)
    for d in sorted(by_day):
        pnl, n = 0.0, 0
        for t in sorted(by_day[d], key=lambda x: x["filled_at"]):
            if n >= 2 or pnl <= -200:
                break
            out.append(t)
            n += 1
            pnl += t["pnl"]
    return out


def main() -> dict:
    rows = json.loads((LEDGER / "ledger.json").read_text()) if (LEDGER / "ledger.json").exists() else []
    ts = trades_from(rows)
    arms = defaultdict(list)
    for t in ts:
        arms[f"{t['account']}:{t['arm']}"].append(t)
        if t["arm"] == "scan":
            arms[f"{t['account']}:scan:{t['setup']}"].append(t)
        if t["arm"] == "orb" and t["account"] == "B":
            spy = (t["features"] or {}).get("spy_or")
            arms[f"B:orb:spy_{spy}"].append(t)          # logged-feature split, not a rule
    stats = {k: summarize(v, k.startswith("A:")) for k, v in sorted(arms.items())}
    stats["A:sleeve(user overlay)"] = summarize(overlay(ts), True)
    doc = {"legs": len(rows), "trades": len(ts), "arms": stats}
    (LEDGER / "stats.json").write_text(json.dumps(doc, indent=1))
    lines = ["| Arm | Trades | Win % | Avg R | Pess. avg R (95% CI) | Max DD R | Worst day $ | Slip R | Go-live |",
             "|---|---|---|---|---|---|---|---|---|"]
    for k, s in stats.items():
        if not s.get("trades"):
            lines.append(f"| {k} | 0 | | | | | | | |")
            continue
        ci = s["pess_ci95"]
        lines.append(f"| {k} | {s['trades']} | {100 * s['win_rate']:.0f} | {s['avg_R']:+.2f} | "
                     f"{s['pess_avg_R'] if s['pess_avg_R'] is not None else '-'} ({ci[0]}, {ci[1]}) | "
                     f"{s['max_dd_R']} | {s['worst_day_usd']} | {s['avg_slippage_R']} | "
                     f"{'YES' if s['go_live'] else 'no'} |")
    (LEDGER / "stats.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    return doc


if __name__ == "__main__":
    main()
