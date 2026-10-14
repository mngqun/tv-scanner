"""Design-only exploration of hypothesis C (fade non-earnings gap-ups). 2015-2020 only."""
import numpy as np, pandas as pd, engine as E
px = E.build(); u = E.universe(px)
u = u.assign(trend_up=(u.pc > u.sma10) & (u.pc > u.sma20))
d = E.period(u, E.DESIGN).copy(); C = E.COST
for k in range(0, 4):
    d[f"s_o_c{k}"] = -(d[f"c{k}"] / d.open - 1) - C        # short at open, cover at close dk
ne = (d.earn_day == False) & d.earn_covered               # noqa: E712  (scheduled-earnings flag known pre-open)
rows = []
def S(m, col, lab): rows.append(E.stats(d.loc[m, col], d.loc[m, "date"], lab))
for lo, hi in [(0.5, 1), (1, 1.5), (1.5, 2.5), (2.5, 99), (1, 2.5)]:
    m = ne & (d.gap >= 0.015) & (d.gap_atr >= lo) & (d.gap_atr < hi)
    for k in [0, 1, 3]:
        S(m, f"s_o_c{k}", f"C short non-earn gap+ {lo}-{hi} ATR, cover close d{k}")
base = ne & (d.gap >= 0.015) & (d.gap_atr >= 1) & (d.gap_atr < 2.5)
for reg in ["long", "mixed", "short"]:
    S(base & (d.regime == reg), "s_o_c0", f"C(1-2.5) regime {reg}, d0")
S(base & d.trend_up, "s_o_c0", "C(1-2.5) stock trend up, d0")
S(base & ~d.trend_up, "s_o_c0", "C(1-2.5) stock NOT trend up, d0")
S((d.earn_day == True) & (d.gap >= 0.015) & (d.gap_atr >= 1) & (d.gap_atr < 2.5), "s_o_c0", "contrast: EARNINGS gap+ 1-2.5 short d0")  # noqa: E712
out = "### C exploration (design 2015-2020, net of 0.10% round trip, short side)\n" + pd.DataFrame(rows).to_string(index=False)
x = d[base].assign(year=lambda f: f.date.dt.year)
yr = x.groupby("year").agg(n=("s_o_c0", "size"), d0_mean_pct=("s_o_c0", lambda s: round(100*s.mean(), 3)),
                           d1_mean_pct=("s_o_c1", lambda s: round(100*s.mean(), 3)),
                           d3_mean_pct=("s_o_c3", lambda s: round(100*s.mean(), 3)))
out += "\n\n### C(1-2.5 ATR non-earnings) by year\n" + yr.to_string()
# intraday MAE for short d0 (adverse = high above open) in ATR
mae = ((x.h0 - x.open) / x.atr).quantile([.5, .75, .9, .95]).round(2)
out += "\n\n### C d0 adverse excursion (high - open, ATR)\n" + mae.to_string()
open("results_design_c.txt", "w").write(out); print(out)
