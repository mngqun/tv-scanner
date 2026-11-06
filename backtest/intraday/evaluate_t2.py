"""Apply protocol #3 (claude/research-protocol-2026-11-06.md) to the T2 build outputs."""
from __future__ import annotations

import argparse
import glob
import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from evaluate import judge, pit_filter  # noqa: E402

TITLES = {"T2a": "T2a: VWAP trend continuation (high-vol top 50, RVOL15 >= 1.5)",
          "T2b": "T2b: opening drive, first pull-back to VWAP (gap >= 1 ATR)",
          "T2c": "T2c: VWAP over-extension fade (X x ATR, RSI14 5-min)"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--inp", default="out_t2")
    ap.add_argument("--res", default=str(Path(__file__).parent / "results_t2"))
    ap.add_argument("--parts", type=int, default=4)
    a = ap.parse_args()
    inp, res = Path(a.inp), Path(a.res)
    res.mkdir(parents=True, exist_ok=True)
    covs = [json.loads(Path(f).read_text()) for f in glob.glob(str(inp / "t2coverage_*.json"))]
    if len(covs) < a.parts or any(c.get("partial") for c in covs):
        print(f"incomplete: {len(covs)} parts")
        return
    days = pd.concat([pd.read_parquet(f) for f in glob.glob(str(inp / "t2days_*.parquet"))], ignore_index=True)
    tr = pd.concat([pd.read_parquet(f) for f in glob.glob(str(inp / "t2trades_*.parquet"))], ignore_index=True)
    days["date"], tr["date"] = pd.to_datetime(days["date"]), pd.to_datetime(tr["date"])
    days = pit_filter(days)
    elig = days[days.adv20 >= 1e8].sort_values("atrp", ascending=False).groupby("date").head(50)
    gap = int((elig.atrp < 0.015).sum())
    sel = elig[elig.rvol15 >= 1.5][["date", "sym"]]
    tr = tr.merge(sel, on=["date", "sym"])
    out, md = {}, ["# T2 results (protocol 2026-11-06, frozen)", ""]
    for hyp in ("T2a", "T2b", "T2c"):
        x = tr[tr.hyp == hyp].sort_values(["date", "var", "sig_slot"])
        x = x.groupby(["date", "var"], group_keys=False).head(5)
        vs = {v: g.copy() for v, g in x.groupby("var")}
        r, m = judge(TITLES[hyp], vs, 200, t_min=3.0)
        out[hyp], md = r, md + m
    out["coverage"] = {"requested": sum(c["requested"] for c in covs), "no_data": sum(len(c["no_data"]) for c in covs),
                       "stock_days": sum(c["rows"] for c in covs),
                       "failed_chunks": sum(len(c.get("failed_chunks", [])) for c in covs),
                       "top50_rows_below_prefilter": gap, "top50_rows": int(len(elig)),
                       "top50_with_rvol": int(len(sel))}
    md += ["## Coverage", "", "```", json.dumps(out["coverage"], indent=1), "```"]
    (res / "results.json").write_text(json.dumps(out, indent=1, default=str))
    (res / "results.md").write_text("\n".join(md) + "\n")
    print("\n".join(md))


if __name__ == "__main__":
    main()
