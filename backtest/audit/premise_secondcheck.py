#!/usr/bin/env python3
"""Second-way verification of premise_breakout_drift.py (honesty rule:
re-derive surprising results independently).

Independent code path: own CSV parser, own H1 aggregation, own ATR loop,
own event detection (no imports from the engine except nothing at all).

Checks:
 1. Reproduce pooled signed continuation per symbol/horizon; must match the
    main script to ~1e-9 (same definitions, independent implementation).
 2. Sign-PERMUTATION test (1000 perms, seeded): shuffle breakout directions
    across events; keeps event times + long/short mix, so the null already
    absorbs drift at event times.  Reports the two-sided p-value of the real
    continuation.  This is a cleaner drift control than the all-bars drift.
 3. Per-direction decomposition (up continuation vs down continuation) --
    is the "effect" just one side of a trending symbol?
 4. Cost yardstick: fallback spread + $5/lot round-trip commission expressed
    in mean-ATR units, to size any drift against what a round trip costs.
"""
from __future__ import annotations

import csv
import json
import math
import random
import statistics
from datetime import datetime
from pathlib import Path

REPO = Path(r"C:/Users/Sanique Richards/Documents/Homework Heroes/Pokemon/Bot-EA")
SYMBOLS = ["EURUSD", "GBPUSD", "USDJPY", "XAUUSD"]
HORIZONS = [1, 4, 12, 24, 48]
DONCHIAN = 20
ATR_P = 14
MIN_INDEX = 210
HMAX = 48
SEED = 777
N_PERM = 1000
FALLBACK_SPREAD_PTS = {"EURUSD": 10, "GBPUSD": 15, "USDJPY": 28, "XAUUSD": 16}


def load_h1(symbol: str):
    path = REPO / "backtest" / "data" / "derivM15" / f"{symbol}.csv"
    rows = []
    with path.open(newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            rows.append(
                (
                    datetime.fromisoformat(r["time"]),
                    float(r["open"]),
                    float(r["high"]),
                    float(r["low"]),
                    float(r["close"]),
                )
            )
    groups: dict = {}
    for t, o, h, l, c in rows:
        key = t.replace(minute=0, second=0, microsecond=0)
        groups.setdefault(key, []).append((t, o, h, l, c))
    bars = []
    for key in sorted(groups):
        g = sorted(groups[key])
        minutes = [x[0].minute for x in g]
        if len(g) != 4 or minutes != [0, 15, 30, 45]:
            continue
        bars.append(
            (
                key,
                g[0][1],
                max(x[2] for x in g),
                min(x[3] for x in g),
                g[-1][4],
            )
        )
    return bars


def atr14(bars):
    n = len(bars)
    out = [math.nan] * n
    trs = [0.0] * n
    for i in range(1, n):
        pc = bars[i - 1][4]
        trs[i] = max(bars[i][2], pc) - min(bars[i][3], pc)
    for i in range(ATR_P, n):
        out[i] = sum(trs[i - ATR_P + 1 : i + 1]) / ATR_P
    return out


def main():
    meta = json.loads((REPO / "backtest" / "deriv_broker_meta.json").read_text())
    print(f"{'sym':<7}{'h':>4}{'cont(2nd)':>11}{'perm p':>8}"
          f"{'up_cont':>9}{'dn_cont':>9}{'n_up':>6}{'n_dn':>6}")
    for symbol in SYMBOLS:
        bars = load_h1(symbol)
        atr = atr14(bars)
        events = []  # (dir, {h: fwd_atr})
        for b in range(MIN_INDEX, len(bars) - HMAX):
            a = atr[b]
            if not (math.isfinite(a) and a > 0.0):
                continue
            hi20 = max(bars[j][2] for j in range(b - DONCHIAN, b))
            lo20 = min(bars[j][3] for j in range(b - DONCHIAN, b))
            c = bars[b][4]
            if c > hi20:
                d = 1
            elif c < lo20:
                d = -1
            else:
                continue
            events.append((d, {h: (bars[b + h][4] - c) / a for h in HORIZONS}))
        dirs = [e[0] for e in events]
        n_up = sum(1 for d in dirs if d == 1)
        n_dn = len(dirs) - n_up
        rng = random.Random(SEED)
        for h in HORIZONS:
            fwds = [e[1][h] for e in events]
            cont = statistics.fmean(d * f for d, f in zip(dirs, fwds))
            up = statistics.fmean(f for d, f in zip(dirs, fwds) if d == 1)
            dn = statistics.fmean(-f for d, f in zip(dirs, fwds) if d == -1)
            # sign permutation
            perm_stats = []
            pd = list(dirs)
            for _ in range(N_PERM):
                rng.shuffle(pd)
                perm_stats.append(statistics.fmean(d * f for d, f in zip(pd, fwds)))
            mu = statistics.fmean(perm_stats)
            n_extreme = sum(1 for p in perm_stats if abs(p - mu) >= abs(cont - mu))
            pval = (n_extreme + 1) / (N_PERM + 1)
            print(f"{symbol:<7}{h:>4}{cont:>11.4f}{pval:>8.3f}"
                  f"{up:>9.4f}{dn:>9.4f}{n_up:>6}{n_dn:>6}")
        # cost yardstick
        m = meta["symbols"][symbol]
        point = m["point"]
        spread_price = FALLBACK_SPREAD_PTS[symbol] * point
        tick_val = m["trade_tick_value_loss"]  # $ per tick per lot
        tick_size = m["trade_tick_size"]
        # $5 round-trip commission per lot -> price-equivalent move
        comm_price = 5.0 / (tick_val / tick_size)
        mean_atr = statistics.fmean(a for a in atr if math.isfinite(a))
        rt_cost_atr = (spread_price + comm_price) / mean_atr
        print(f"{symbol:<7} mean ATR14(H1)={mean_atr:.5f}  spread={spread_price:.5f}"
              f" ({spread_price/mean_atr:.3f} ATR)  round-trip cost="
              f"{(spread_price+comm_price):.5f} ({rt_cost_atr:.3f} ATR)")


if __name__ == "__main__":
    main()
