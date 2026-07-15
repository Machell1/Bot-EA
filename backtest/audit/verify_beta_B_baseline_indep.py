#!/usr/bin/env python3
"""Adversarial verification of the beta-regime analyst's BASELINE claim:

  "Long/flat EMA50/200-cross baseline under identical costs and sizing nets
   +$35,060 across the 4 symbols (EURUSD -4,087, GBPUSD -3,516,
   USDJPY +12,057, XAUUSD +30,606) vs the EA's -$2,654."

This is an INDEPENDENT re-implementation from the spec, not a re-run of
beta_regime_02_baselines.py. Spec (engine conventions):

  * H1 bars from the engine's own load_m15/aggregate_h1 (fallback spreads
    EURUSD 10 pts, GBPUSD 15, USDJPY 28, XAUUSD 16).
  * Decision uses bar (i-1) EMA50/EMA200 of closes; long when fast>slow,
    else flat. Fill at bar i open: long entry at ask=open+spread, long exit
    at bid=open. Final open position closes at the last bar's close (bid).
  * Sizing identical to the EA: lots = floor_volume(
        (balance*0.35%/1.10) / (2.5*ATR14[i-1] * $per-price-per-lot)).
    Compounds on closed balance. Sensitivity run: fixed 100k sizing.
  * Costs identical: $2.5/side/lot commission; MT5 swap points per lot per
    calendar-date change observed in the bar stream, tripled when the new
    day's weekday == 3 (Thursday rollover), engine convention.
  * No stop, no session filter (deliberately the dumbest baseline).

Also computes: risk-scaled buy-and-hold (enter at first tradable bar, hold
to the end, same sizing/costs) to check the XAUUSD +$67k / USDJPY +$31.7k
(incl. carry) claims, and warmup-anchored price drift. Stdlib only,
deterministic.
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

REPO = Path(r"C:/Users/Sanique Richards/Documents/Homework Heroes/Pokemon/Bot-EA")
sys.path.insert(0, str(REPO))

from backtest.ftmo_quant_backtest import (  # noqa: E402
    aggregate_h1,
    atr_sma_of_tr,
    ema,
    floor_volume,
    load_m15,
)

FALLBACK_PTS = {"EURUSD": 10.0, "GBPUSD": 15.0, "USDJPY": 28.0, "XAUUSD": 16.0}
INITIAL = 100_000.0
WARMUP = 210  # ema_slow + 10, the EA's first possible signal index


def simulate(bars, meta, *, mode: str, compound: bool) -> dict:
    """mode: 'cross_long_flat' or 'buy_hold'."""
    point = float(meta["point"])
    dppl = float(meta["trade_tick_value_loss"]) / float(meta["trade_tick_size"])
    comm = float(meta["commission"]["per_side_usd_per_lot"])
    vmin = float(meta["volume_min"])
    vmax = float(meta["volume_max"])
    vstep = float(meta["volume_step"])
    swap = meta.get("swap") or {}
    swap_long_usd = float(swap.get("long_points", 0.0)) * point * dppl
    triple_wd = int(swap.get("triple_rollover_weekday", 3))

    closes = [b.close for b in bars]
    f = ema(closes, 50)
    s = ema(closes, 200)
    atr = atr_sma_of_tr(bars, 14)

    balance = INITIAL
    lots = 0.0
    entry_px = 0.0
    trade_pnls = []
    swap_total = 0.0
    n_swap_charges = 0.0  # in single-night equivalents
    last_date = bars[WARMUP].time.date()
    peak = INITIAL
    max_dd = 0.0

    for i in range(WARMUP, len(bars)):
        b = bars[i]

        # financing at calendar-date rollover (engine convention), long only
        d = b.time.date()
        if d != last_date:
            if lots > 0.0:
                mult = 3.0 if b.time.weekday() == triple_wd else 1.0
                chg = swap_long_usd * lots * mult
                balance += chg
                swap_total += chg
                n_swap_charges += mult
            last_date = d

        want_long = True if mode == "buy_hold" else (f[i - 1] > s[i - 1])

        # exit at open (bid) on regime flip
        if lots > 0.0 and not want_long:
            pnl = (b.open - entry_px) * lots * dppl - 2.0 * comm * lots
            balance += pnl
            trade_pnls.append(pnl)
            lots = 0.0

        # entry at open (ask)
        if lots == 0.0 and want_long and math.isfinite(atr[i - 1]) and atr[i - 1] > 0:
            base = balance if compound else INITIAL
            raw = (base * 0.35 / 100.0 / 1.10) / (2.5 * atr[i - 1] * dppl)
            new_lots = floor_volume(raw, vmin, vmax, vstep)
            if new_lots > 0.0:
                lots = new_lots
                entry_px = b.open + b.spread
            if mode == "buy_hold" and new_lots == 0.0:
                continue

        # close-mark equity / drawdown
        eq = balance
        if lots > 0.0:
            eq += (b.close - entry_px) * lots * dppl - comm * lots
        if eq > peak:
            peak = eq
        dd = 100.0 * (peak - eq) / peak
        if dd > max_dd:
            max_dd = dd

    if lots > 0.0:
        last = bars[-1]
        pnl = (last.close - entry_px) * lots * dppl - 2.0 * comm * lots
        balance += pnl
        trade_pnls.append(pnl)

    wins = sum(p for p in trade_pnls if p > 0)
    losses = -sum(p for p in trade_pnls if p < 0)
    return {
        "net": round(balance - INITIAL, 2),
        "return_pct": round(100.0 * (balance / INITIAL - 1.0), 3),
        "round_trips": len(trade_pnls),
        "profit_factor": round(wins / losses, 3) if losses else None,
        "max_dd_pct_closemark": round(max_dd, 2),
        "swap_total": round(swap_total, 2),
        "swap_nights_equiv": n_swap_charges,
    }


def main():
    meta_all = json.loads(
        (REPO / "backtest" / "deriv_broker_meta.json").read_text(encoding="utf-8")
    )["symbols"]
    out = {}
    tot_cross = 0.0
    for sym, pts in FALLBACK_PTS.items():
        meta = meta_all[sym]
        bars = aggregate_h1(
            load_m15(
                REPO / "backtest" / "data" / "derivM15" / f"{sym}.csv",
                pts * float(meta["point"]),
            )
        )
        cross = simulate(bars, meta, mode="cross_long_flat", compound=True)
        cross_fixed = simulate(bars, meta, mode="cross_long_flat", compound=False)
        bh = simulate(bars, meta, mode="buy_hold", compound=True)
        tot_cross += cross["net"]
        out[sym] = {
            "bars": len(bars),
            "window": [bars[WARMUP].time.isoformat(sep=" "), bars[-1].time.isoformat(sep=" ")],
            "drift_pct_warmup_close_to_end": round(
                100.0 * (bars[-1].close / bars[WARMUP].close - 1.0), 2
            ),
            "cross_long_flat": cross,
            "cross_long_flat_fixed100k_sizing": cross_fixed,
            "buy_hold_risk_scaled": bh,
        }
        print(sym, "done", flush=True)
    out["TOTAL_cross_long_flat_net"] = round(tot_cross, 2)
    dest = REPO / "backtest" / "audit" / "verify_beta_B_results.json"
    dest.write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
