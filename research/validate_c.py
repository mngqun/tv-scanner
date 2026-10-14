"""Run FROZEN rule C (see research-protocol-2026-10-14.md) on all periods; validation = 2021-2022, run once."""
import numpy as np, pandas as pd, engine as E
px = E.build(); u = E.universe(px)
sig = u[(u.earn_day == False) & u.earn_covered & (u.gap >= 0.015) & (u.gap_atr >= 1.0) & (u.gap_atr < 2.5)].copy()  # noqa: E712
ctrl = u[u.gap.abs() < 0.005].copy()

def trade_returns(x, side_cost):
    entry = x.open * (1 - side_cost)                 # short fill, worse by slippage
    stop = x.open + 2.0 * x.atr
    hit = x.h0 >= stop
    exit_ = np.where(hit, stop * (1 + side_cost + 0.0005), x.c0 * (1 + side_cost))
    return pd.Series((entry - exit_) / x.open, index=x.index), hit

out = []
for name, per in [("DESIGN 2015-2020", E.DESIGN), ("VALIDATION 2021-2022", E.VALID), ("2023-2026 (reported only)", E.LATE)]:
    x = E.period(sig, per); c = E.period(ctrl, per)
    r1, hit = trade_returns(x, 0.0005); r2, _ = trade_returns(x, 0.0010)
    cr = -(c.c0 / c.open - 1) - 0.0010              # control: same short at open / cover at close, no-gap days
    s1 = E.stats(r1, x.date, "rule C, base costs"); s2 = E.stats(r2, x.date, "rule C, 2x costs")
    sc = E.stats(cr, c.date, "control: short no-gap days")
    out.append(f"\n## {name}\n" + pd.DataFrame([s1, s2, sc]).to_string(index=False) + f"\nstop-hit rate {hit.mean():.3f}")
    # by year
    yy = pd.DataFrame({"r": r1, "y": x.date.dt.year}).groupby("y").r.agg(["size", "mean"])
    yy["mean"] = (100 * yy["mean"]).round(3); out.append(yy.rename(columns={"size": "n", "mean": "mean_%"}).to_string())
    # portfolio sim ($10k rules)
    x = x.assign(r=r1, rank=-x.gap_atr).sort_values(["date", "rank"])
    trades = []
    for date, day in x.groupby("date"):
        wk = date.isocalendar()[:2]
        realized = sum(t["pnl"] for t in trades if t["wk"] == wk)  # all prior trades closed same day -> known
        if realized <= -400:
            continue
        for _, s in day.head(2).iterrows():
            sh = int(min(100 / (2.0 * s.atr), 5000 / s.open))
            if sh < 1:
                continue
            trades.append({"date": date, "wk": wk, "pnl": sh * s.open * s.r, "R": sh * s.open * s.r / (sh * 2.0 * s.atr)})
    t = pd.DataFrame(trades)
    eq = t.pnl.cumsum(); dd = (eq - eq.cummax()).min()
    yrs = (t.date.max() - t.date.min()).days / 365.25
    out.append(f"portfolio: trades {len(t)} ({len(t)/max(yrs*52,1):.2f}/wk), win {(t.pnl>0).mean():.3f}, avg R {t.R.mean():+.3f}, "
               f"P&L ${t.pnl.sum():,.0f} ({100*t.pnl.sum()/10000/max(yrs,1e-9):+.1f}%/yr on $10k), max DD ${dd:,.0f}")
txt = "\n".join(out); open("results_validation_c.txt", "w").write(txt); print(txt)
