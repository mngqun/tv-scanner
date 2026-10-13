"""Design phase — uses ONLY 2015-2020 (protocol). Prints nothing from later periods."""
import numpy as np
import pandas as pd

import engine as E

px = E.build()
u = E.universe(px)
u = u.assign(trend_up=(u.pc > u.sma10) & (u.pc > u.sma20),
             trend_dn=(u.pc < u.sma10) & (u.pc < u.sma20),
             above50=u.pc > u.sma50)
d = E.period(u, E.DESIGN).copy()
C = E.COST
out = []


def show(title, frame):
    out.append(f"\n### {title}\n" + frame.to_string(index=False))


def S(mask, ret, label):
    x = d[mask]
    return E.stats(ret[mask], x.date, label)


# open-entry returns, exit at close of day k
for k in range(0, 5):
    d[f"r_o_c{k}"] = d[f"c{k}"] / d.open - 1 - C
# close-of-day-0 entry (MOC), exit close of day k
for k in range(1, 5):
    d[f"r_c0_c{k}"] = d[f"c{k}"] / d.c0 - 1 - C

A = (d.gap >= 0.015) & (d.gap_atr >= 1.0) & (d.gap_atr <= 2.5) & d.trend_up & (d.regime != "short")
B = d.gap <= -0.015
B2 = B & (d.regime == "short")
CTRL = d.gap.abs() < 0.005

# 1) frozen hypotheses on the design period
rows = [S(A, d.r_o_c3, "A: gap+ 1-2.5ATR trend up, open->close d3"),
        S(CTRL & d.trend_up & (d.regime != "short"), d.r_o_c3, "Control (no gap, trend up) open->close d3"),
        S(B, d.r_o_c0, "B: gap- <=-1.5%, open->close d0"),
        S(B2, d.r_o_c0, "B2: gap- & regime short, open->close d0"),
        S(CTRL, d.r_o_c0, "Control (no gap) open->close d0")]
show("1. Frozen hypotheses — DESIGN 2015-2020 (net of 0.10% round trip)", pd.DataFrame(rows))

# 2) A: holding period / entry timing
rows = [S(A, d[f"r_o_c{k}"], f"A open->close d{k}") for k in range(5)]
rows += [S(A, d[f"r_c0_c{k}"], f"A close d0 (MOC) -> close d{k}") for k in range(1, 5)]
show("2. A — holding period & entry timing", pd.DataFrame(rows))

# 3) A: gap band, earnings split, regime, trend
rows = []
for lo, hi in [(0.5, 1), (1, 1.5), (1.5, 2.5), (2.5, 4), (4, 99)]:
    m = (d.gap >= 0.015) & (d.gap_atr >= lo) & (d.gap_atr < hi) & d.trend_up & (d.regime != "short")
    rows.append(S(m, d.r_o_c3, f"gap {lo}-{hi} ATR, trend up, regime ok"))
rows.append(S(A & (d.earn_day == True), d.r_o_c3, "A & earnings day"))  # noqa: E712
rows.append(S(A & (d.earn_day == False), d.r_o_c3, "A & NOT earnings day"))  # noqa: E712
rows.append(S(A & (d.regime == "long"), d.r_o_c3, "A & regime long"))
rows.append(S(A & (d.regime == "mixed"), d.r_o_c3, "A & regime mixed"))
mA_any = (d.gap >= 0.015) & (d.gap_atr >= 1.0) & (d.gap_atr <= 2.5)
rows.append(S(mA_any & ~d.trend_up, d.r_o_c3, "gap 1-2.5 ATR but NOT trend up"))
rows.append(S(A & d.above50, d.r_o_c3, "A & above SMA50"))
show("3. A — filters", pd.DataFrame(rows))

# 4) B: splits
rows = [S(B & (d.earn_day == True), d.r_o_c0, "B & earnings day"),  # noqa: E712
        S(B & (d.earn_day == False), d.r_o_c0, "B & NOT earnings day")]  # noqa: E712
for lo, hi in [(-99, -4), (-4, -2.5), (-2.5, -1.5), (-1.5, -1), (-1, 0)]:
    m = B & (d.gap_atr >= lo) & (d.gap_atr < hi)
    rows.append(S(m, d.r_o_c0, f"B gap {lo} to {hi} ATR"))
for reg in ["long", "mixed", "short"]:
    rows.append(S(B & (d.regime == reg), d.r_o_c0, f"B & regime {reg}"))
rows.append(S(B & d.trend_up, d.r_o_c0, "B & stock trend up"))
rows.append(S(B & d.trend_dn, d.r_o_c0, "B & stock trend down"))
rows += [S(B, d[f"r_o_c{k}"], f"B open->close d{k}") for k in range(1, 4)]
show("4. B — splits", pd.DataFrame(rows))

# 5) MAE/MFE for A (3-day hold) in ATR units
x = d[A].copy()
lows = np.minimum.reduce([x.l0, x.l1, x.l2, x.l3])
highs = np.maximum.reduce([x.h0, x.h1, x.h2, x.h3])
x["mae"] = (lows - x.open) / x.atr
x["mfe"] = (highs - x.open) / x.atr
x["fin"] = (x.c3 - x.open) / x.atr
q = x[["mae", "mfe", "fin"]].quantile([.1, .25, .5, .75, .9]).round(2)
out.append("\n### 5. A — MAE / MFE / final over 3 days (ATR units, quantiles)\n" + q.to_string())
# stop-distance sweep (conservative: any touch = stop; day-1 gap-through at open)
rows = []
for X in [0.75, 1.0, 1.5, 2.0, 3.0, None]:
    if X is None:
        r = x.c3 / x.open - 1 - C
        lab = "no stop"
    else:
        stop = x.open - X * x.atr
        hit0 = x.l0 <= stop
        hit_later = (np.minimum.reduce([x.l1, x.l2, x.l3]) <= stop) & ~hit0
        exit_px = np.where(hit0, stop, np.where(hit_later, np.minimum(stop, np.where(x.o1 < stop, x.o1, stop)), x.c3))
        r = exit_px / x.open - 1 - C - np.where(hit0 | hit_later, 0.0005, 0)
        lab = f"stop {X} ATR"
    rows.append(E.stats(pd.Series(np.asarray(r)), x.date, f"A 3-day, {lab}"))
show("6. A — stop distance (3-day time exit)", pd.DataFrame(rows))

# 7) by year (design years only) for A and B
x = d.assign(year=d.date.dt.year)
rows = []
for y, g in x.groupby("year"):
    ma = (g.gap >= 0.015) & (g.gap_atr >= 1) & (g.gap_atr <= 2.5) & g.trend_up & (g.regime != "short")
    mb = g.gap <= -0.015
    rows.append({"year": y, "A_n": int(ma.sum()), "A_mean_%": round(100 * g.r_o_c3[ma].mean(), 3),
                 "B_n": int(mb.sum()), "B_mean_%": round(100 * g.r_o_c0[mb].mean(), 3),
                 "ctrl_d3_%": round(100 * g.r_o_c3[g.gap.abs() < 0.005].mean(), 3)})
show("7. By year (design period)", pd.DataFrame(rows))

text = "\n".join(out)
open("results_design.txt", "w").write(text)
print(text)
