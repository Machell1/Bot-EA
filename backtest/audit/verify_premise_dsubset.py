#!/usr/bin/env python3
"""Verification part 2: (a) D-subset (engine signal()) selection differential
for USDJPY h12 and GBPUSD h24/h48, using the ENGINE's own indicator arrays
(the analyst's claim: USDJPY D-minus-rest +0.45 [+0.05,+0.86] at h12;
GBPUSD -0.07 h24 / -0.47 h48). (b) cluster-robust +-2SE for the USDJPY-2026
h12 inversion (claimed -0.365 with CI excluding zero) - a third method on
top of my month- and week-block bootstraps.
Stdlib only, seeded."""
from __future__ import annotations

import math
import random
import statistics
import sys
from pathlib import Path

REPO = Path(r"C:/Users/Sanique Richards/Documents/Homework Heroes/Pokemon/Bot-EA")
sys.path.insert(0, str(REPO))

import backtest.ftmo_quant_backtest as eng  # noqa: E402
from backtest.ftmo_quant_backtest import Config, aggregate_h1, atr_sma_of_tr, ema, load_m15  # noqa: E402

CFG = Config()
DON = 20
MIN_INDEX = 210
SEED = 99173


def build(symbol):
    bars = aggregate_h1(load_m15(REPO / "backtest" / "data" / "derivM15" / f"{symbol}.csv", 0.0))
    closes = [b.close for b in bars]
    fast = ema(closes, CFG.ema_fast)
    slow = ema(closes, CFG.ema_slow)
    atr = atr_sma_of_tr(bars, CFG.atr_period)
    events = []
    for b in range(MIN_INDEX, len(bars) - 48):
        a = atr[b]
        if not (math.isfinite(a) and a > 0.0):
            continue
        hi = max(x.high for x in bars[b - DON:b])
        lo = min(x.low for x in bars[b - DON:b])
        c = bars[b].close
        if c > hi:
            d = 1
        elif c < lo:
            d = -1
        else:
            continue
        sig = eng.signal(b + 1, bars, fast, slow, CFG, atr)  # engine's literal path
        events.append({
            "month": (bars[b].time.year, bars[b].time.month),
            "year": bars[b].time.year,
            "dir": d,
            "D": sig == d,
            "fwd": {h: (bars[b + h].close - c) / a for h in (12, 24, 48)},
        })
    return events


def d_minus_rest(events, h, n_boot=2000, seed=SEED):
    agg = {}
    for e in events:
        m = agg.setdefault(e["month"], [0.0, 0, 0.0, 0])
        v = e["dir"] * e["fwd"][h]
        if e["D"]:
            m[0] += v; m[1] += 1
        else:
            m[2] += v; m[3] += 1
    months = sorted(agg)
    nd = sum(agg[m][1] for m in months)
    nr = sum(agg[m][3] for m in months)
    point = sum(agg[m][0] for m in months) / nd - sum(agg[m][2] for m in months) / nr
    rng = random.Random(seed)
    boots = []
    for _ in range(n_boot):
        s = [months[rng.randrange(len(months))] for _ in months]
        sd = sum(agg[m][0] for m in s); cd = sum(agg[m][1] for m in s)
        sr = sum(agg[m][2] for m in s); cr = sum(agg[m][3] for m in s)
        if cd and cr:
            boots.append(sd / cd - sr / cr)
    boots.sort()
    return point, boots[int(0.025 * len(boots))], boots[int(0.975 * len(boots))], nd, nr


def cluster_se(pairs):
    n = len(pairs)
    xbar = statistics.fmean(v for _, v in pairs)
    per = {}
    for k, v in pairs:
        per[k] = per.get(k, 0.0) + (v - xbar)
    g = len(per)
    var = sum(u * u for u in per.values()) / (n * n) * (g / (g - 1) if g > 1 else 1.0)
    return xbar, math.sqrt(var), g


for sym, hs in (("USDJPY", (12,)), ("GBPUSD", (24, 48)), ("EURUSD", (24,)), ("XAUUSD", (24,))):
    ev = build(sym)
    for h in hs:
        p, lo, hi, nd, nr = d_minus_rest(ev, h)
        print(f"{sym} D-minus-rest h={h}: {p:+.4f} [{lo:+.4f},{hi:+.4f}] nD={nd} nRest={nr}")
    if sym == "USDJPY":
        pairs = [(e["month"], e["dir"] * e["fwd"][12]) for e in ev if e["year"] == 2026]
        m, se, g = cluster_se(pairs)
        print(f"USDJPY 2026 h12: mean={m:+.4f} cluster(month) 2SE=[{m-2*se:+.4f},{m+2*se:+.4f}] "
              f"g={g} n={len(pairs)}")
        # plain (iid, anti-conservative) t as a bounding check
        vals = [v for _, v in pairs]
        se_iid = statistics.stdev(vals) / math.sqrt(len(vals))
        print(f"USDJPY 2026 h12: iid t={m/se_iid:+.2f} (anti-conservative bound)")
