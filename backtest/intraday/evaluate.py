"""Apply the frozen protocol (claude/research-protocol-2026-11-05.md) to the build / h3 / h4 outputs.

Design: every variant. Validation: only the best design variant. Holdout: only if validation passes.
usage: python evaluate.py --inp out/ --res results/
"""
from __future__ import annotations

import argparse
import glob
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import common as C  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]


def pit_filter(df: pd.DataFrame) -> pd.DataFrame:
    """Keep (date, sym) only when sym was in the latest S&P 500 snapshot dated strictly before date."""
    m = pd.read_parquet(ROOT / "research" / "data" / "membership.parquet")
    snaps = np.sort(m.date.unique())
    d = pd.to_datetime(df["date"]).values
    idx = np.searchsorted(snaps, d, side="left") - 1
    df = df.assign(snap=np.where(idx >= 0, snaps[np.clip(idx, 0, None)], np.datetime64("NaT", "ns")))
    keep = df.merge(m.rename(columns={"date": "snap", "ticker": "sym"}).assign(_in=True), on=["snap", "sym"], how="left")
    return df[keep["_in"].fillna(False).values].drop(columns="snap")


def judge(name: str, variants: dict, min_n: int) -> tuple[dict, list]:
    """variants: {var: trades DataFrame with date, R, R2, fill_slot, entry, risk_ps, side}."""
    res = {"hypothesis": name, "design": {}, "min_n": min_n}
    md = [f"## {name}", "", "**Design 2017–2021 (all variants)**", "",
          "| Variant | n | days | avg R | t (by day) | CI by day | win | longs avg R (n) | shorts avg R (n) | 2× cost avg R |",
          "|---|---|---|---|---|---|---|---|---|---|"]
    for v, df in variants.items():
        d = df[C.in_window(df["date"], "design")]
        s = C.day_stats(d)
        s2 = C.day_stats(d, "R2") if len(d) else {}
        sides = {k: C.day_stats(d[d.side == k]) for k in (1, -1)}
        s["R2"] = s2.get("avg_R")
        s["long"], s["short"] = sides[1], sides[-1]
        res["design"][v] = s
        if s["n"]:
            md.append(f"| {v} | {s['n']} | {s['days']} | {s['avg_R']:+.3f} | {s['t_day']} | {s['ci_day']} | {s['win']} | "
                      f"{sides[1].get('avg_R')} ({sides[1].get('n')}) | {sides[-1].get('avg_R')} ({sides[-1].get('n')}) | {s['R2']} |")
        else:
            md.append(f"| {v} | 0 | | | | | | | | |")
    cand = {v: s for v, s in res["design"].items() if s.get("n")}
    if not cand:
        res["verdict"] = "no trades"
        return res, md + ["", "No trades.", ""]
    best = max(cand, key=lambda v: cand[v]["avg_R"])
    b = cand[best]
    res["best"] = best
    md += ["", f"Best design variant: **{best}** (avg {b['avg_R']:+.3f}R, t {b['t_day']})."]
    if not (b["avg_R"] > 0 and (b["t_day"] or 0) >= 2):
        res["verdict"] = "FAIL (design: best variant avg R <= 0 or t < 2)"
        md += [f"**Verdict: {res['verdict']}.** Validation not run.", ""]
        return res, md
    df = variants[best]
    v = df[C.in_window(df["date"], "validation")]
    sv = C.day_stats(v)
    sv["R2"] = C.day_stats(v, "R2").get("avg_R")
    sv["sleeve"] = C.sleeve(v)
    res["validation"] = sv
    checks = {"avg>=+0.10R": sv.get("avg_R", -9) >= 0.10, "ci_low_by_day>0": (sv.get("ci_day") or [-1])[0] > 0,
              f"n>={min_n}": sv["n"] >= min_n, "2x_cost>0": (sv["R2"] or -1) > 0,
              "sleeve_usd>0": sv["sleeve"]["total_usd"] > 0}
    res["validation_checks"] = checks
    md += ["", "**Validation 2022–2023 (best variant only)**", "", "```", json.dumps(sv, indent=1), "```",
           "Checks: " + ", ".join(f"{k} {'PASS' if ok else 'fail'}" for k, ok in checks.items())]
    if not all(checks.values()):
        res["verdict"] = "FAIL (validation)"
        md += ["", f"**Verdict: {res['verdict']}.** Holdout not read.", ""]
        return res, md
    hdf = df[C.in_window(df["date"], "holdout")]
    sh = C.day_stats(hdf)
    sh["R2"] = C.day_stats(hdf, "R2").get("avg_R")
    sh["sleeve"] = C.sleeve(hdf)
    res["holdout"] = sh
    ok = sh.get("avg_R", -1) > 0
    res["verdict"] = "PASS (to forward paper on B)" if ok else "FAIL (holdout)"
    md += ["", "**Holdout 2024 – Oct 2026**", "", "```", json.dumps(sh, indent=1), "```", "",
           f"**Verdict: {res['verdict']}.**", ""]
    return res, md


def load(inp: Path, stem: str) -> pd.DataFrame:
    fs = sorted(glob.glob(str(inp / f"{stem}_*.parquet")))
    return pd.concat([pd.read_parquet(f) for f in fs], ignore_index=True) if fs else pd.DataFrame()


def h1_h2(inp: Path):
    out, md, calib = {}, [], []
    reg = pd.read_parquet(inp / "regime.parquet")
    reg["date"] = pd.to_datetime(reg["date"])
    h1 = load(inp, "h1")
    if not h1.empty:
        h1["date"] = pd.to_datetime(h1["date"])
        h1 = pit_filter(h1)
        days = load(inp, "days")[["date", "sym", "adv20"]]
        days["date"] = pd.to_datetime(days["date"])
        h1 = h1.merge(days, on=["date", "sym"], how="left")
        sel = {}
        for v in ("a", "b", "d"):
            x = h1[h1["var"] == v].sort_values("adv20", ascending=False)
            x = x.groupby(["date", "side"], group_keys=False).head(8)
            if v == "a":
                c = x[x.date >= "2026-11-02"][["date", "sym", "side", "filled", "R", "exit"]]
                calib = c.assign(date=c.date.dt.date.astype(str)).to_dict("records")
            sel[v] = x[x.filled == True].copy()  # noqa: E712
        b = sel["b"].merge(reg[["date", "regime"]], on="date", how="left")
        sel["c"] = b[b.side == b.regime].drop(columns="regime")
        r, m = judge("H1: current ORB arms (15-min range, OR-mid stop, 2R)", {k: sel[k] for k in "abcd"}, 200)
        r["calibration_2_4_nov"] = calib
        out["H1"], md = r, md + m + ["Calibration (variant a, 2–4 Nov; compare with the paper ledger):", "",
                                     "```", json.dumps(calib, indent=0, default=str), "```", ""]
    h2 = load(inp, "h2")
    if not h2.empty:
        h2["date"] = pd.to_datetime(h2["date"])
        h2 = pit_filter(h2)
        top = (h2[h2["var"] == "k0.10"].sort_values("rvol", ascending=False)
               .groupby("date").head(20)[["date", "sym"]])
        h2 = h2.merge(top, on=["date", "sym"])
        vs = {v: h2[(h2["var"] == v) & (h2.filled == True)].copy() for v in sorted(h2["var"].unique())}  # noqa: E712
        r, m = judge("H2: Stocks-in-Play 5-min ORB (top 20 RVOL, k x ATR stop, exit 15:40)", vs, 200)
        out["H2"], md = r, md + m
    return out, md


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--inp", default="out")
    ap.add_argument("--res", default=str(Path(__file__).parent / "results"))
    a = ap.parse_args()
    inp, res = Path(a.inp), Path(a.res)
    res.mkdir(parents=True, exist_ok=True)
    out, md = {}, ["# Intraday / earnings research results (protocol 2026-11-05)", ""]
    if (inp / "regime.parquet").exists():
        o, m = h1_h2(inp)
        out.update(o)
        md += m
        cov = [json.loads(Path(f).read_text()) for f in glob.glob(str(inp / "coverage_*.json"))]
        out["coverage"] = {"requested": sum(c["requested"] for c in cov), "no_data": sum(len(c["no_data"]) for c in cov),
                           "no_data_symbols": sorted(s for c in cov for s in c["no_data"]),
                           "stock_days": sum(c["rows"] for c in cov)}
        md += ["## Coverage", "", f"Symbols requested {out['coverage']['requested']}, without Alpaca data "
               f"{out['coverage']['no_data']}, stock-days {out['coverage']['stock_days']}.", ""]
    for name, stem, mn, title in (("H3", "h3", 200, "H3: post-earnings drift on the surprise (daily)"),
                                  ("H4", "h4", 100, "H4: mechanised TQQQ plan")):
        f = inp / f"{stem}.parquet"
        if f.exists():
            df = pd.read_parquet(f)
            df["date"] = pd.to_datetime(df["date"])
            vs = {v: g.copy() for v, g in df.groupby("var")}
            r, m = judge(title, vs, mn)
            meta = inp / f"{stem}_meta.json"
            if meta.exists():
                r["meta"] = json.loads(meta.read_text())
                m.insert(1, "Data: " + json.dumps(r["meta"]))
            out[name], md = r, md + m
        elif (inp / f"{stem}_meta.json").exists():
            out[name] = json.loads((inp / f"{stem}_meta.json").read_text())
            md += [f"## {title}", "", "Not run: " + json.dumps(out[name]), ""]
    (res / "results.json").write_text(json.dumps(out, indent=1, default=str))
    (res / "results.md").write_text("\n".join(md) + "\n")
    print("\n".join(md))


if __name__ == "__main__":
    main()
