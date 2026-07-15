#!/usr/bin/env python3
"""Cross-check of the bh_risk_scaled baseline in beta_regime_02_baselines.py.

Recomputes buy-and-hold from first principles (no shared position loop):
entry at bars[210].open+spread, lots = floor(balance*0.35%/1.10 / (2.5*ATR209*dpppl)),
swap = sum over calendar-date changes among bar timestamps (3x when the new
date's weekday is Thursday), exit at bars[-1].close. Prints every component so
the part-2 output can be reconciled exactly.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(r"C:/Users/Sanique Richards/Documents/Homework Heroes/Pokemon/Bot-EA")
sys.path.insert(0, str(REPO))

from backtest.ftmo_quant_backtest import aggregate_h1, atr_sma_of_tr, floor_volume, load_m15  # noqa: E402

SYMBOLS = {"EURUSD": 10.0, "GBPUSD": 15.0, "USDJPY": 28.0, "XAUUSD": 16.0}
WARMUP = 210

meta_all = json.loads((REPO / "backtest" / "deriv_broker_meta.json").read_text(encoding="utf-8"))["symbols"]
out = {}
for symbol, fallback_pts in SYMBOLS.items():
    meta = meta_all[symbol]
    point = float(meta["point"])
    dpppl = float(meta["trade_tick_value_loss"]) / float(meta["trade_tick_size"])
    bars = aggregate_h1(load_m15(REPO / "backtest" / "data" / "derivM15" / f"{symbol}.csv", fallback_pts * point))
    atr = atr_sma_of_tr(bars, 14)
    spread = bars[WARMUP].spread
    entry = bars[WARMUP].open + spread
    dist = 2.5 * atr[WARMUP - 1]
    lots = floor_volume(100_000 * 0.0035 / 1.10 / (dist * dpppl), float(meta["volume_min"]), float(meta["volume_max"]), float(meta["volume_step"]))
    exit_price = bars[-1].close
    gross = (exit_price - entry) * lots * dpppl
    commission = 2.0 * 2.5 * lots

    # swap: engine convention = one charge per calendar-date change, 3x Thursday
    nights = 0.0
    prev_day = bars[WARMUP].time.date()
    for b in bars[WARMUP + 1 :]:
        d = b.time.date()
        if d != prev_day:
            nights += 3.0 if b.time.weekday() == 3 else 1.0
            prev_day = d
    swap_per_night = float(meta["swap"]["long_points"]) * point * dpppl
    swap_total = swap_per_night * nights * lots

    net = gross - commission + swap_total
    out[symbol] = {
        "entry_time": bars[WARMUP].time.isoformat(sep=" "),
        "entry": entry,
        "exit": exit_price,
        "atr_at_entry": round(atr[WARMUP - 1], 6),
        "lots": lots,
        "gross": round(gross, 2),
        "commission": round(commission, 2),
        "night_equivalents": nights,
        "swap_per_night_per_lot": round(swap_per_night, 4),
        "swap_total": round(swap_total, 2),
        "net": round(net, 2),
        "return_pct": round(net / 1000.0, 3),
    }
print(json.dumps(out, indent=2))
