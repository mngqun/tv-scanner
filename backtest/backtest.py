"""Backtest of the CURRENT screening framework's mechanical rules (v2.3, 13 Oct 2026).

NO LOOK-AHEAD — every decision uses only information available at that moment:
  * Indicators (ATR14, SMA10/20/50, EMA20 for SPY/QQQ, 20d avg volume, prior-day low/high)
    are computed from bars up to and including the PRIOR session (shifted by one day).
  * The gap is measured at today's OPEN vs prior close (known at 09:30 ET).
  * Same-day volume, close, high and low are never used to select or size a trade.
  * Daily ranking of candidates (max 2 per day) uses only gap/ATR known at the open.
  * Intraday path is unknown with daily bars, so ambiguity is always resolved AGAINST us:
      - stop and target both inside a bar -> stop counted first;
      - targets on a pullback-entry fill day only count if the CLOSE is beyond the target
        (proves the move happened after the fill);
      - after Target 1, the runner exits at breakeven whenever that bar's low touched entry;
      - opening gaps through a stop fill at the (worse) open price.
  * Costs: 0.05% slippage per side on market/stop fills (sensitivity at 0.10%); limit fills exact.

Rules tested (from screening-framework.md):
  Universe (point-in-time): prior close $20–$1,500; 20d avg volume >= 2M shares or >= $200M/day.
  Signal: open gap >= +1.5% (long) / <= -1.5% (short) vs prior close.
  Regime: SPY & QQQ prior close vs EMA20 -> both above = long bias, both below = short bias, else mixed.
          Longs allowed in long/mixed regimes (risk $100 / $50); shorts only in short regime.
  Entry: gap > 1.5 ATR -> pullback limit at the gap midpoint (setup 1); otherwise buy at open (gap-and-go).
  Stop: prior-day low (long) / high (short); must be 0.5–1.5 ATR from entry, clamped up to 0.5 ATR,
        skipped if > 1.5 ATR.
  Targets: T1 = 1.5R (sell half, stop -> breakeven), T2 = 2.5R.
  Hold: 'intraday' (exit at close) or 'swing' (max 4 sessions; exit at close of day 2 if T1 not hit).
  Size: shares = min(risk$ / (entry-stop), $5,000 / entry). Max 2 new trades per day, max 2 open.
  Weekly loss limit -$400 -> no new trades until next week.
NOT testable with daily data (scored 0 / ignored): news catalyst quality, options flow, insider buys,
pre-market volume, CPI-eve rule, discretionary analyst review.
"""
from __future__ import annotations

import gzip
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

DATA = Path(__file__).parent / "data"
OUT = Path(__file__).parent / "results"


# ----------------------------------------------------------------------------- data
def load() -> pd.DataFrame:
    with gzip.open(DATA / "ohlcv.csv.gz", "rt") as f:
        df = pd.read_csv(f, parse_dates=["date"])
    df = df.dropna(subset=["open", "high", "low", "close"])
    df = df[(df.open > 0) & (df.high >= df.low)]
    return df.sort_values(["ticker", "date"]).reset_index(drop=True)


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    g = df.groupby("ticker", group_keys=False)
    prev_close = g["close"].shift(1)
    tr = pd.concat([df.high - df.low, (df.high - prev_close).abs(), (df.low - prev_close).abs()], axis=1).max(axis=1)
    df["tr"] = tr
    g = df.groupby("ticker", group_keys=False)
    # everything below is shifted so that row t only sees data through t-1
    df["pc"] = prev_close
    df["p_low"] = g["low"].shift(1)
    df["p_high"] = g["high"].shift(1)
    df["atr"] = g["tr"].transform(lambda s: s.rolling(14).mean().shift(1))
    df["sma10"] = g["close"].transform(lambda s: s.rolling(10).mean().shift(1))
    df["sma20"] = g["close"].transform(lambda s: s.rolling(20).mean().shift(1))
    df["sma50"] = g["close"].transform(lambda s: s.rolling(50).mean().shift(1))
    df["avgvol20"] = g["volume"].transform(lambda s: s.rolling(20).mean().shift(1))
    df["avgdv20"] = (df.close * df.volume).groupby(df.ticker).transform(lambda s: s.rolling(20).mean().shift(1))
    df["gap"] = df.open / df.pc - 1
    df["gap_atr"] = (df.open - df.pc) / df.atr
    return df


def regime_table(df: pd.DataFrame) -> pd.Series:
    out = {}
    for t in ["SPY", "QQQ"]:
        s = df[df.ticker == t].set_index("date")["close"]
        ema = s.ewm(span=20, adjust=False).mean()
        out[t] = (s > ema).shift(1)  # prior-day state
    r = pd.DataFrame(out).dropna()
    reg = np.where(r.SPY & r.QQQ, "long", np.where(~r.SPY.astype(bool) & ~r.QQQ.astype(bool), "short", "mixed"))
    return pd.Series(reg, index=r.index, name="regime")


# ----------------------------------------------------------------------------- signals
def candidates(df: pd.DataFrame, regime: pd.Series, *, trend_filter: bool, regime_filter: bool) -> pd.DataFrame:
    d = df[~df.ticker.isin(["SPY", "QQQ"])].copy()
    d = d.dropna(subset=["pc", "atr", "sma10", "sma20", "sma50", "avgvol20", "p_low", "p_high"])
    d = d[(d.pc >= 20) & (d.pc <= 1500) & ((d.avgvol20 >= 2e6) | (d.avgdv20 >= 2e8))]
    d = d.join(regime, on="date")
    d = d.dropna(subset=["regime"])
    longs = d[d.gap >= 0.015].assign(side=1)
    shorts = d[d.gap <= -0.015].assign(side=-1)
    if trend_filter:
        longs = longs[(longs.pc > longs.sma10) & (longs.pc > longs.sma20)]
        shorts = shorts[(shorts.pc < shorts.sma10) & (shorts.pc < shorts.sma20)]
    if regime_filter:
        longs = longs[longs.regime != "short"]
        shorts = shorts[shorts.regime == "short"]
    c = pd.concat([longs, shorts])
    c["risk_usd"] = np.where(c.regime == "mixed", 50.0, 100.0)
    return c


# ----------------------------------------------------------------------------- trade simulation
def simulate_trade(sig: pd.Series, fut: pd.DataFrame, hold: str, slip: float) -> dict | None:
    """sig = signal-day row; fut = this ticker's bars from signal day onward (index 0 = signal day)."""
    side = sig.side
    pc, o, atr = sig.pc, sig.open, sig.atr
    gap_atr = abs(sig.gap_atr)
    # --- entry
    if gap_atr > 1.5:
        setup = "pullback"
        entry = (o + pc) / 2
        day0 = fut.iloc[0]
        filled = day0.low <= entry if side == 1 else day0.high >= entry
        if not filled:
            return {"setup": setup, "filled": False}
    else:
        setup = "gap_and_go"
        entry = o * (1 + slip * side)
    # --- stop (structural: prior-day low/high), clamped to 0.5–1.5 ATR
    raw_stop = sig.p_low if side == 1 else sig.p_high
    dist = (entry - raw_stop) * side
    if dist < 0.5 * atr:
        dist = 0.5 * atr
    if dist > 1.5 * atr or dist <= 0:
        return {"setup": setup, "filled": True, "skipped": "stop_too_wide"}
    stop = entry - side * dist
    t1 = entry + side * 1.5 * dist
    t2 = entry + side * 2.5 * dist
    shares = int(min(sig.risk_usd / dist, 5000 / entry))
    if shares < 1:
        return {"setup": setup, "filled": True, "skipped": "size<1"}

    max_days = 1 if hold == "intraday" else 4
    half = shares / 2.0
    pnl = 0.0
    remaining = float(shares)
    cur_stop = stop
    t1_hit = False
    exit_reason = None
    days_held = 0

    def px_worse(p):  # apply slippage against us on market/stop exits
        return p * (1 - slip * side)

    for k in range(min(max_days, len(fut))):
        bar = fut.iloc[k]
        days_held = k + 1
        is_fill_day_pullback = (k == 0 and setup == "pullback")
        hi, lo, op, cl = bar.high, bar.low, bar.open, bar.close
        fav = (lambda p: p >= t1) if side == 1 else (lambda p: p <= t1)
        fav2 = (lambda p: p >= t2) if side == 1 else (lambda p: p <= t2)
        adverse = (lambda p, s: p <= s) if side == 1 else (lambda p, s: p >= s)
        best = hi if side == 1 else lo
        worst = lo if side == 1 else hi

        # gap through stop at the open (k>0)
        if k > 0 and adverse(op, cur_stop):
            pnl += remaining * (px_worse(op) - entry) * side
            remaining = 0
            exit_reason = "gap_stop" if not t1_hit else "gap_be"
            break
        # 1) stop first (conservative)
        if adverse(worst, cur_stop):
            pnl += remaining * (px_worse(cur_stop) - entry) * side
            remaining = 0
            exit_reason = "stop" if not t1_hit else "be_stop"
            break
        # 2) targets
        if not t1_hit:
            t1_ok = fav(cl) if is_fill_day_pullback else fav(best)
            if t1_ok:
                fill1 = t1 if not (k > 0 and fav(op)) else op
                pnl += half * (fill1 - entry) * side
                remaining -= half
                t1_hit = True
                cur_stop = entry
                # same bar: runner — conservative handling
                t2_ok = fav2(cl) if is_fill_day_pullback else fav2(best)
                touched_be = adverse(worst, entry)
                if touched_be:
                    pnl += remaining * 0.0  # out at breakeven
                    remaining = 0
                    exit_reason = "t1_then_be"
                    break
                if t2_ok:
                    pnl += remaining * (t2 - entry) * side
                    remaining = 0
                    exit_reason = "t2"
                    break
        else:
            if fav2(best):
                fill2 = t2 if not fav2(op) else op
                pnl += remaining * (fill2 - entry) * side
                remaining = 0
                exit_reason = "t2"
                break
        # 3) time exits at the close
        last_day = (k == max_days - 1) or (k == len(fut) - 1)
        time_stop = (hold == "swing" and k == 1 and not t1_hit)
        if last_day or time_stop:
            pnl += remaining * (px_worse(cl) - entry) * side
            remaining = 0
            exit_reason = "close_exit" if hold == "intraday" else ("time_stop_d2" if time_stop else "time_exit_d4")
            break

    risk_usd = shares * dist
    return {"setup": setup, "filled": True, "skipped": None, "entry": entry, "stop": stop, "t1": t1, "t2": t2,
            "shares": shares, "risk_usd": risk_usd, "pnl": pnl, "R": pnl / risk_usd if risk_usd else np.nan,
            "exit": exit_reason, "days": days_held, "t1_hit": t1_hit}


def run(df, regime, *, hold, trend_filter, regime_filter, slip, portfolio=True, seed=None, signal_override=None):
    cand = signal_override if signal_override is not None else candidates(
        df, regime, trend_filter=trend_filter, regime_filter=regime_filter)
    by_t = {t: g.reset_index(drop=True) for t, g in df.groupby("ticker")}
    pos_index = {t: {d: i for i, d in enumerate(g.date)} for t, g in by_t.items()}
    cand = cand.copy()
    if seed is not None:
        cand["rank"] = np.random.default_rng(seed).random(len(cand))
    else:
        cand["rank"] = -cand.gap_atr.abs()  # larger gap in ATR units first (known at open)
    cand = cand.sort_values(["date", "rank"])

    trades, open_until = [], []  # open_until: list of exit dates for open positions
    for date, day in cand.groupby("date", sort=True):
        wk = date.isocalendar()[:2]
        open_until = [d for d in open_until if d >= date]
        # weekly loss limit uses ONLY trades already closed before today (no look-ahead)
        realized_week = sum(tr["pnl"] for tr in trades
                            if tr["exit_date"] < date and tr["exit_date"].isocalendar()[:2] == wk)
        taken = 0
        for _, sig in day.iterrows():
            if portfolio:
                if taken >= 2 or len(open_until) >= 2:
                    break
                if realized_week <= -400:
                    break
            g = by_t[sig.ticker]
            i = pos_index[sig.ticker][date]
            res = simulate_trade(sig, g.iloc[i:i + 4], hold, slip)
            if res is None or not res.get("filled") or res.get("skipped"):
                continue
            exit_date = g.date.iloc[min(i + res["days"] - 1, len(g) - 1)]
            res.update({"date": date, "ticker": sig.ticker, "side": int(sig.side), "gap": sig.gap,
                        "gap_atr": sig.gap_atr, "regime": sig.regime, "exit_date": exit_date,
                        "trend_ok": bool((sig.pc > sig.sma10) and (sig.pc > sig.sma20)) if sig.side == 1
                        else bool((sig.pc < sig.sma10) and (sig.pc < sig.sma20))})
            trades.append(res)
            if portfolio:
                taken += 1
                open_until.append(exit_date)
    return pd.DataFrame(trades)


def summarize(t: pd.DataFrame, label: str) -> dict:
    if t.empty:
        return {"variant": label, "trades": 0}
    eq = t.sort_values("exit_date").pnl.cumsum()
    dd = (eq - eq.cummax()).min()
    wins = t[t.pnl > 0]
    years = (t.date.max() - t.date.min()).days / 365.25
    return {
        "variant": label, "trades": len(t), "trades_per_week": round(len(t) / max(years * 52, 1), 2),
        "win_rate": round((t.pnl > 0).mean(), 3), "avg_R": round(t.R.mean(), 3), "median_R": round(t.R.median(), 3),
        "avg_win_R": round(wins.R.mean(), 3) if len(wins) else None,
        "avg_loss_R": round(t[t.pnl <= 0].R.mean(), 3),
        "total_pnl_usd": round(t.pnl.sum(), 0), "max_drawdown_usd": round(dd, 0),
        "pct_on_10k_per_year": round(100 * t.pnl.sum() / 10000 / max(years, 1e-9), 1),
        "t1_hit_rate": round(t.t1_hit.mean(), 3),
        "R_t_stat": round(t.R.mean() / (t.R.std(ddof=1) / np.sqrt(len(t))), 2) if len(t) > 2 else None,
    }


def main() -> int:
    OUT.mkdir(exist_ok=True)
    df = add_indicators(load())
    regime = regime_table(df)
    meta = json.loads((DATA / "universe.json").read_text())
    start_trading = df.date.min() + pd.Timedelta(days=90)  # indicator warm-up
    df_bt = df  # indicators already shifted; trades limited below
    results, all_trades = [], {}

    grid = []
    for hold in ["intraday", "swing"]:
        for tf in [False, True]:
            for rf in [False, True]:
                grid.append((hold, tf, rf))
    for hold, tf, rf in grid:
        label = f"{hold}|trend={'on' if tf else 'off'}|regime={'on' if rf else 'off'}"
        t = run(df_bt, regime, hold=hold, trend_filter=tf, regime_filter=rf, slip=0.0005)
        t = t[t.date >= start_trading] if not t.empty else t
        results.append(summarize(t, label))
        all_trades[label] = t

    # framework-faithful main variant + robustness
    main_label = "swing|trend=on|regime=on"
    t_main = all_trades[main_label]
    t_slip = run(df_bt, regime, hold="swing", trend_filter=True, regime_filter=True, slip=0.001)
    t_slip = t_slip[t_slip.date >= start_trading]
    results.append(summarize(t_slip, main_label + "|slippage=0.10%"))
    # unconstrained (every qualifying signal, no portfolio limits) to measure raw signal quality
    t_all = run(df_bt, regime, hold="swing", trend_filter=True, regime_filter=True, slip=0.0005, portfolio=False)
    t_all = t_all[t_all.date >= start_trading]
    results.append(summarize(t_all, main_label + "|all_signals_no_portfolio_cap"))
    # control: same rules but RANDOM choice among the day's qualifying signals (tests the gap/ATR ranking)
    for s in range(3):
        tr = run(df_bt, regime, hold="swing", trend_filter=True, regime_filter=True, slip=0.0005, seed=s)
        tr = tr[tr.date >= start_trading]
        results.append(summarize(tr, main_label + f"|random_pick_seed{s}"))
    # control 2: NO gap requirement — random large-cap days entering at the open with identical stops/targets
    d0 = df_bt[~df_bt.ticker.isin(["SPY", "QQQ"])].dropna(subset=["pc", "atr", "sma10", "sma20", "avgvol20", "p_low", "p_high"])
    d0 = d0[(d0.pc >= 20) & (d0.pc <= 1500) & ((d0.avgvol20 >= 2e6) | (d0.avgdv20 >= 2e8)) & (d0.gap.abs() < 0.005)]
    d0 = d0[(d0.pc > d0.sma10) & (d0.pc > d0.sma20)].join(regime, on="date").dropna(subset=["regime"])
    d0 = d0[d0.regime != "short"].assign(side=1)
    d0["risk_usd"] = np.where(d0.regime == "mixed", 50.0, 100.0)
    d0 = d0.sample(frac=0.15, random_state=1)
    tc = run(df_bt, regime, hold="swing", trend_filter=False, regime_filter=False, slip=0.0005, seed=7, signal_override=d0)
    tc = tc[tc.date >= start_trading]
    results.append(summarize(tc, "CONTROL no-gap random entries (same stops/targets, swing)"))

    # SPY buy & hold over the same window
    spy = df[(df.ticker == "SPY") & (df.date >= start_trading)]
    spy_ret = spy.close.iloc[-1] / spy.close.iloc[0] - 1
    yrs = (spy.date.iloc[-1] - spy.date.iloc[0]).days / 365.25

    summary = pd.DataFrame(results)
    summary.to_csv(OUT / "summary.csv", index=False)
    for label, t in all_trades.items():
        t.to_csv(OUT / f"trades_{label.replace('|', '_').replace('=', '-')}.csv", index=False)
    t_all.to_csv(OUT / "trades_main_all_signals.csv", index=False)
    info = {"data_generated_utc": meta["generated_utc"], "tickers_with_data": meta["tickers_with_data"],
            "test_start": str(start_trading.date()), "test_end": str(df.date.max().date()),
            "spy_buy_hold_return_pct": round(100 * spy_ret, 1), "spy_years": round(yrs, 2),
            "regime_days": regime[regime.index >= start_trading].value_counts().to_dict()}
    (OUT / "info.json").write_text(json.dumps(info, indent=1, default=str))
    pd.set_option("display.width", 250)
    pd.set_option("display.max_columns", 30)
    print(json.dumps(info, indent=1, default=str))
    print(summary.to_string(index=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
