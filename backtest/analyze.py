"""Diagnostics for the framework backtest (no changes to the strategy rules).

1. Bracket the intrabar-ordering uncertainty: re-run the main variant with OPTIMISTIC ordering
   (target before stop when both are inside a bar). True result lies between the two.
2. Raw signal behaviour with no stops/targets: forward returns from the OPEN on gap days,
   measured in ATR units, vs. a no-gap baseline from the same universe. Entry at the open
   is known at decision time; returns only use later prices (no look-ahead).
3. Breakdown of the main variant: by year, gap size, setup, regime, exit reason.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import backtest as bt  # noqa: E402

OUT = Path(__file__).parent / "results"


def optimistic_sim(sig, fut, hold, slip):
    """Same as bt.simulate_trade but resolves same-bar ambiguity in OUR favour."""
    side = sig.side
    pc, o, atr = sig.pc, sig.open, sig.atr
    if abs(sig.gap_atr) > 1.5:
        setup, entry = "pullback", (o + pc) / 2
        d0 = fut.iloc[0]
        if not (d0.low <= entry if side == 1 else d0.high >= entry):
            return {"setup": setup, "filled": False}
    else:
        setup, entry = "gap_and_go", o * (1 + slip * side)
    raw = sig.p_low if side == 1 else sig.p_high
    dist = max((entry - raw) * side, 0.5 * atr)
    if dist > 1.5 * atr:
        return {"setup": setup, "filled": True, "skipped": "wide"}
    stop, t1, t2 = entry - side * dist, entry + side * 1.5 * dist, entry + side * 2.5 * dist
    shares = int(min(sig.risk_usd / dist, 5000 / entry))
    if shares < 1:
        return {"setup": setup, "filled": True, "skipped": "size"}
    half, rem, pnl, t1_hit, cur = shares / 2, float(shares), 0.0, False, stop
    maxd = 1 if hold == "intraday" else 4
    days = 0
    for k in range(min(maxd, len(fut))):
        b = fut.iloc[k]
        days = k + 1
        best, worst = (b.high, b.low) if side == 1 else (b.low, b.high)
        reach = lambda p, lvl: p >= lvl if side == 1 else p <= lvl  # noqa: E731
        if not t1_hit and reach(best, t1):
            pnl += half * (t1 - entry) * side
            rem -= half
            t1_hit, cur = True, entry
        if t1_hit and reach(best, t2):
            pnl += rem * (t2 - entry) * side
            rem = 0
            break
        if (worst <= cur if side == 1 else worst >= cur):
            pnl += rem * (cur * (1 - slip * side) - entry) * side
            rem = 0
            break
        if k == maxd - 1 or (hold == "swing" and k == 1 and not t1_hit) or k == len(fut) - 1:
            pnl += rem * (b.close * (1 - slip * side) - entry) * side
            rem = 0
            break
    r = shares * dist
    return {"setup": setup, "filled": True, "skipped": None, "entry": entry, "stop": stop, "t1": t1, "t2": t2,
            "shares": shares, "risk_usd": r, "pnl": pnl, "R": pnl / r, "exit": "opt", "days": days, "t1_hit": t1_hit}


def forward_returns(df):
    g = df.groupby("ticker", group_keys=False)
    df["c_d0"] = df.close
    df["c_d1"] = g["close"].shift(-1)
    df["c_d3"] = g["close"].shift(-3)
    for col, name in [("c_d0", "open_to_close_d0"), ("c_d1", "open_to_close_d1"), ("c_d3", "open_to_close_d3")]:
        df[name] = (df[col] - df.open) / df.atr
    return df


def main():
    df = bt.add_indicators(bt.load())
    regime = bt.regime_table(df)
    start = df.date.min() + pd.Timedelta(days=90)
    lines = []

    # 1) optimistic vs conservative ordering
    orig = bt.simulate_trade
    res = {}
    for name, fn in [("conservative", orig), ("optimistic", optimistic_sim)]:
        bt.simulate_trade = fn
        for hold in ["intraday", "swing"]:
            t = bt.run(df, regime, hold=hold, trend_filter=True, regime_filter=True, slip=0.0005)
            t = t[t.date >= start]
            res[(name, hold)] = bt.summarize(t, f"{hold}|trend=on|regime=on|{name}")
    bt.simulate_trade = orig
    lines.append("## 1. Intrabar ordering bracket (main rules, portfolio-constrained)\n")
    lines.append(pd.DataFrame(res.values()).to_string(index=False))

    # 2) raw forward returns from the open, in ATR units
    d = forward_returns(df)
    d = d[(d.date >= start) & ~d.ticker.isin(["SPY", "QQQ"])].dropna(subset=["atr", "pc", "sma10", "sma20", "avgvol20"])
    d = d[(d.pc >= 20) & (d.pc <= 1500) & ((d.avgvol20 >= 2e6) | (d.avgdv20 >= 2e8))].join(regime, on="date")
    d["trend_up"] = (d.pc > d.sma10) & (d.pc > d.sma20)
    d["trend_dn"] = (d.pc < d.sma10) & (d.pc < d.sma20)
    buckets = [("gap up 1.5%+", d.gap >= 0.015), ("gap up >1.5 ATR", d.gap_atr > 1.5),
               ("gap up 1.5%+ & trend up", (d.gap >= 0.015) & d.trend_up),
               ("gap up 1.5%+ & trend up & regime not short", (d.gap >= 0.015) & d.trend_up & (d.regime != "short")),
               ("no gap (|gap|<0.5%) baseline", d.gap.abs() < 0.005),
               ("no gap & trend up baseline", (d.gap.abs() < 0.005) & d.trend_up),
               ("gap down 1.5%+", d.gap <= -0.015),
               ("gap down 1.5%+ & trend down & regime short", (d.gap <= -0.015) & d.trend_dn & (d.regime == "short"))]
    rows = []
    for name, m in buckets:
        x = d[m]
        row = {"bucket": name, "n": len(x)}
        for c in ["open_to_close_d0", "open_to_close_d1", "open_to_close_d3"]:
            v = x[c].dropna()
            row[c + "_ATR_mean"] = round(v.mean(), 3)
            row[c + "_t"] = round(v.mean() / (v.std() / np.sqrt(len(v))), 1) if len(v) > 2 else None
            row[c + "_%up"] = round((v > 0).mean(), 3)
        rows.append(row)
    lines.append("\n\n## 2. Raw forward returns from the OPEN on signal days (ATR units; no stops/targets; no costs)\n")
    lines.append(pd.DataFrame(rows).to_string(index=False))

    # gap-size gradient for longs (trend up, regime not short)
    x = d[(d.gap >= 0.015) & d.trend_up & (d.regime != "short")].copy()
    x["gap_bucket"] = pd.cut(x.gap_atr, [0, 1, 1.5, 2.5, 4, 99], labels=["<1 ATR", "1-1.5", "1.5-2.5", "2.5-4", ">4"])
    g = x.groupby("gap_bucket", observed=True).agg(n=("gap", "size"), d0=("open_to_close_d0", "mean"),
                                                   d1=("open_to_close_d1", "mean"), d3=("open_to_close_d3", "mean"))
    lines.append("\n\n## 2b. Long gap-ups (trend up, regime ok) by gap size — mean forward return from open, ATR units\n")
    lines.append(g.round(3).to_string())

    # 3) breakdown of main conservative variant
    t = pd.read_csv(OUT / "trades_swing_trend-on_regime-on.csv", parse_dates=["date", "exit_date"])
    lines.append("\n\n## 3. Main variant (swing|trend on|regime on, conservative) breakdowns\n")
    t["year"] = t.date.dt.year
    for col in ["year", "setup", "regime", "side", "exit"]:
        lines.append(f"\n-- by {col}\n" + t.groupby(col).agg(n=("R", "size"), avg_R=("R", "mean"), win=("pnl", lambda s: (s > 0).mean()),
                                                              pnl=("pnl", "sum")).round(3).to_string())
    text = "\n".join(lines)
    (OUT / "diagnostics.txt").write_text(text)
    print(text)


if __name__ == "__main__":
    main()
