"""Part B of protocol #4 (claude/research-protocol-2026-11-07.md): SPY/QQQ core bucket with SMA200 timing.

Daily adjusted closes from yfinance; cash earns ^IRX. Signal at the close, position changes at the NEXT close.
Writes results/results.md + results.json.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf

HERE = Path(__file__).resolve().parent
COST = 0.0005
WIN = {"design": ("1999-03-10", "2012-12-31"), "validation": ("2013-01-01", "2026-10-30")}


def load() -> pd.DataFrame:
    px = {}
    for t in ("SPY", "QQQ", "TQQQ", "^IRX"):
        d = yf.download(t, start="1993-01-01", auto_adjust=True, progress=False)
        c = d["Close"]
        px[t] = c.iloc[:, 0] if isinstance(c, pd.DataFrame) else c
    df = pd.DataFrame(px).sort_index()
    df["rf"] = (df["^IRX"].ffill() / 100 / 252).fillna(0)
    return df


def stats(r: pd.Series, rf: pd.Series, pos: pd.Series | None = None) -> dict:
    r = r.dropna()
    if r.empty:
        return {}
    eq = (1 + r).cumprod()
    yrs = len(r) / 252
    dd = (eq / eq.cummax() - 1).min()
    ex = r - rf.reindex(r.index).fillna(0)
    yearly = (1 + r).groupby(r.index.year).prod() - 1
    s = {"CAGR_%": round(100 * (eq.iloc[-1] ** (1 / yrs) - 1), 2), "vol_%": round(100 * r.std() * np.sqrt(252), 2),
         "sharpe": round(ex.mean() / ex.std() * np.sqrt(252), 2) if ex.std() > 0 else None,
         "maxDD_%": round(100 * dd, 1), "worst_year_%": round(100 * yearly.min(), 1),
         "growth_of_10k": round(10000 * eq.iloc[-1], 0), "years": round(yrs, 1)}
    if pos is not None:
        p = pos.reindex(r.index).fillna(0)
        s["switches_per_year"] = round(p.diff().abs().sum() / yrs, 1)
        s["time_invested_%"] = round(100 * p.mean(), 1)
    return s


def timing(asset_r: pd.Series, sig_px: pd.Series, rf: pd.Series, band: float = 0.0) -> tuple[pd.Series, pd.Series]:
    sma = sig_px.rolling(200).mean()
    state, out = 0, []
    for p, m in zip(sig_px.values, sma.values):
        if np.isnan(m):
            out.append(np.nan)
            continue
        if state == 0 and p > m * (1 + band):
            state = 1
        elif state == 1 and p < m * (1 - band):
            state = 0
        out.append(state)
    sig = pd.Series(out, index=sig_px.index)
    pos = sig.shift(1)        # decided at today's close, applied from the next close-to-close return
    r = pos * asset_r + (1 - pos) * rf - pos.diff().abs().fillna(0) * COST
    return r.where(pos.notna()), pos


def main():
    df = load()
    rf = df["rf"]
    rq, rs = df["QQQ"].pct_change(), df["SPY"].pct_change()
    syn = 3 * rq - 2 * rf - 0.0095 / 252
    real = df["TQQQ"].pct_change()
    both = pd.concat([syn, real], axis=1).dropna()
    track = {"corr": round(both.corr().iloc[0, 1], 4),
             "annual_diff_%": round(100 * (((1 + both.iloc[:, 1]).prod() / (1 + both.iloc[:, 0]).prod()) ** (252 / len(both)) - 1), 2),
             "from": str(both.index[0].date())}
    strat = {"B0 SPY buy&hold": (rs, None), "B0 QQQ buy&hold": (rq, None), "B0 TQQQ(syn) buy&hold": (syn, None)}
    strat["B1 QQQ / SMA200"] = timing(rq, df["QQQ"], rf)
    strat["B2 TQQQ(syn) / QQQ SMA200"] = timing(syn, df["QQQ"], rf)
    strat["B3 TQQQ(syn) / QQQ SMA200 2% band"] = timing(syn, df["QQQ"], rf, 0.02)
    strat["B4 SPY / SMA200"] = timing(rs, df["SPY"], rf)
    out = {"tracking_syn_vs_real_TQQQ": track, "windows": {}}
    md = ["# Core bucket results (protocol 2026-11-07, Part B)", "",
          f"Synthetic vs real TQQQ since {track['from']}: correlation {track['corr']}, real minus synthetic "
          f"{track['annual_diff_%']} %/yr.", ""]
    for w, (a, b) in WIN.items():
        md += [f"## {w} {a} → {b}", "", "| Strategy | CAGR % | Vol % | Sharpe | Max DD % | Worst year % | $10k → | Switches/yr | In market % |",
               "|---|---|---|---|---|---|---|---|---|"]
        out["windows"][w] = {}
        for k, (r, pos) in strat.items():
            rr = r.loc[a:b]
            s = stats(rr, rf, pos.loc[a:b] if pos is not None else None)
            out["windows"][w][k] = s
            md.append(f"| {k} | {s.get('CAGR_%')} | {s.get('vol_%')} | {s.get('sharpe')} | {s.get('maxDD_%')} | "
                      f"{s.get('worst_year_%')} | {s.get('growth_of_10k')} | {s.get('switches_per_year', '')} | {s.get('time_invested_%', '')} |")
        md.append("")
    # pass bar (both windows, vs the same asset's buy & hold)
    bench = {"B1 QQQ / SMA200": "B0 QQQ buy&hold", "B2 TQQQ(syn) / QQQ SMA200": "B0 TQQQ(syn) buy&hold",
             "B3 TQQQ(syn) / QQQ SMA200 2% band": "B0 TQQQ(syn) buy&hold", "B4 SPY / SMA200": "B0 SPY buy&hold"}
    md += ["## Pass bar (both windows)", ""]
    out["verdicts"] = {}
    for k, bk in bench.items():
        ok, notes = True, []
        for w in WIN:
            s, b = out["windows"][w][k], out["windows"][w][bk]
            dd_ok = s["maxDD_%"] >= 0.7 * b["maxDD_%"]          # drawdowns are negative: at least 30% smaller
            sh_ok = (s["sharpe"] or -9) >= (b["sharpe"] or -9)
            if k.startswith(("B2", "B3")):
                cg_ok = s["CAGR_%"] > out["windows"][w]["B0 QQQ buy&hold"]["CAGR_%"]
            else:
                cg_ok = s["CAGR_%"] >= b["CAGR_%"] - 2
            ok &= dd_ok and sh_ok and cg_ok
            notes.append(f"{w}: DD {'ok' if dd_ok else 'fail'}, Sharpe {'ok' if sh_ok else 'fail'}, CAGR {'ok' if cg_ok else 'fail'}")
        out["verdicts"][k] = "PASS" if ok else "FAIL"
        md.append(f"- **{k}: {'PASS' if ok else 'FAIL'}** ({'; '.join(notes)})")
    res = HERE / "results"
    res.mkdir(exist_ok=True)
    (res / "results.json").write_text(json.dumps(out, indent=1))
    (res / "results.md").write_text("\n".join(md) + "\n")
    print("\n".join(md))


if __name__ == "__main__":
    main()
