#!/usr/bin/env python3
"""Second-way verification for the filter attribution ladder.

1. Signal-level census, independent of position state: at every in-session bar
   with finite ATR, evaluate the raw breakout (R0 signal), then which of the
   filters (buffer, candle, HTF-H4, HTF-D1, vol regime) each candidate passes.
   This re-derives, without the portfolio path, (a) the veto rate of each
   filter and (b) whether the vol-regime filter ever binds (the ladder found
   R3 == R4 exactly on EURUSD/GBPUSD/XAUUSD).

2. IS/OOS delta table per rung addition from the ladder JSON (question c).

Deterministic; no randomness.
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

REPO = Path(r"C:/Users/Sanique Richards/Documents/Homework Heroes/Pokemon/Bot-EA")
sys.path.insert(0, str(REPO))

from backtest.ftmo_quant_backtest import (  # noqa: E402
    Config,
    aggregate_h1,
    atr_sma_of_tr,
    ema,
    htf_ema_aligned,
    in_session,
    load_m15,
    sma,
)

SYMBOLS = {
    "EURUSD": dict(csv="EURUSD.csv", fallback_pts=10.0),
    "GBPUSD": dict(csv="GBPUSD.csv", fallback_pts=15.0),
    "USDJPY": dict(csv="USDJPY.csv", fallback_pts=28.0),
    "XAUUSD": dict(csv="XAUUSD.csv", fallback_pts=16.0),
}


def census(symbol: str, spec: dict, broker: dict) -> dict:
    meta = broker["symbols"][symbol]
    fallback_spread = spec["fallback_pts"] * float(meta["point"])
    bars = aggregate_h1(load_m15(REPO / "backtest/data/derivM15" / spec["csv"], fallback_spread))
    config = Config()  # full canonical config values for thresholds
    closes = [b.close for b in bars]
    fast = ema(closes, config.ema_fast)
    slow = ema(closes, config.ema_slow)
    atr = atr_sma_of_tr(bars, config.atr_period)
    hf_al, hs_al = htf_ema_aligned(bars, config.htf_factor, config.htf_fast, config.htf_slow)
    hf2_al, hs2_al = htf_ema_aligned(bars, config.htf2_factor, config.htf2_fast, config.htf2_slow)
    atr_sma_arr = sma([v if math.isfinite(v) else 0.0 for v in atr], config.vol_avg_len)
    split_index = int(len(bars) * 0.70)

    counts = dict(
        raw=0,
        fail_buffer=0,
        fail_candle_after_buffer=0,
        fail_htf_h4=0,
        fail_htf_d1=0,
        fail_vol=0,
        fail_vol_unconditional=0,
        pass_all=0,
        dir_residue=0,
    )
    warmup = max(config.ema_slow + 10, config.donchian + 2)
    for index in range(warmup, len(bars)):
        bar = bars[index]
        if not in_session(bar.time, config):
            continue
        prev = index - 1
        if not math.isfinite(atr[prev]):
            continue
        prior = bars[index - config.donchian - 1 : index - 1]
        upper = max(b.high for b in prior)
        lower = min(b.low for b in prior)
        close = bars[prev].close
        side = 0
        if close > upper and fast[prev] > slow[prev] and fast[prev] > fast[prev - 1]:
            side = 1
        elif close < lower and fast[prev] < slow[prev] and fast[prev] < fast[prev - 1]:
            side = -1
        if not side:
            continue
        counts["raw"] += 1

        breakout = bars[prev]
        # direction residue at rung 0 (body_min=0, wick_max=1): candle must
        # close in the breakout direction
        if (side > 0 and not breakout.close > breakout.open) or (
            side < 0 and not breakout.close < breakout.open
        ):
            counts["dir_residue"] += 1

        # vol regime, measured unconditionally on every raw candidate
        base = atr_sma_arr[prev]
        vol_ok = bool(base and base > 0.0 and config.vol_ratio_min <= atr[prev] / base <= config.vol_ratio_max)
        if not vol_ok:
            counts["fail_vol_unconditional"] += 1

        # sequential (documented order): buffer -> candle -> H4 -> D1 -> vol
        buffer = config.entry_buffer_atr * atr[prev]
        if side > 0 and not close > upper + buffer or side < 0 and not close < lower - buffer:
            counts["fail_buffer"] += 1
            continue
        candle_range = breakout.high - breakout.low
        if candle_range <= 0:
            counts["fail_candle_after_buffer"] += 1
            continue
        body = abs(breakout.close - breakout.open)
        upper_wick = breakout.high - max(breakout.open, breakout.close)
        lower_wick = min(breakout.open, breakout.close) - breakout.low
        strong = body / candle_range >= config.candle_body_min
        ok = (
            strong and breakout.close > breakout.open and upper_wick / candle_range <= config.candle_wick_max
            if side > 0
            else strong and breakout.close < breakout.open and lower_wick / candle_range <= config.candle_wick_max
        )
        if not ok:
            counts["fail_candle_after_buffer"] += 1
            continue
        hf, hs = hf_al[index], hs_al[index]
        if not (math.isfinite(hf) and math.isfinite(hs)) or (side > 0 and not hf > hs) or (side < 0 and not hf < hs):
            counts["fail_htf_h4"] += 1
            continue
        hf2, hs2 = hf2_al[index], hs2_al[index]
        if not (math.isfinite(hf2) and math.isfinite(hs2)) or (side > 0 and not hf2 > hs2) or (side < 0 and not hf2 < hs2):
            counts["fail_htf_d1"] += 1
            continue
        if not vol_ok:
            counts["fail_vol"] += 1
            continue
        counts["pass_all"] += 1
    counts["bars"] = len(bars)
    counts["split_index"] = split_index
    return counts


def main() -> None:
    broker = json.loads((REPO / "backtest/deriv_broker_meta.json").read_text(encoding="utf-8"))
    out = {}
    print("SIGNAL CENSUS (position-independent; sequential vetoes in documented order)")
    print(f"{'sym':7s} {'raw':>5s} {'buf':>5s} {'cndl':>5s} {'H4':>5s} {'D1':>5s} {'vol':>4s} {'pass':>5s} | vol_uncond dir_residue")
    for sym, spec in SYMBOLS.items():
        c = census(sym, spec, broker)
        out[sym] = c
        print(
            f"{sym:7s} {c['raw']:5d} {c['fail_buffer']:5d} {c['fail_candle_after_buffer']:5d} "
            f"{c['fail_htf_h4']:5d} {c['fail_htf_d1']:5d} {c['fail_vol']:4d} {c['pass_all']:5d} | "
            f"{c['fail_vol_unconditional']:10d} {c['dir_residue']:11d}"
        )
    (REPO / "backtest/audit/filter_census_results.json").write_text(
        json.dumps(out, indent=2), encoding="utf-8"
    )

    # ---- delta table (question c) from the ladder results ----
    d = json.load(open(REPO / "backtest/audit/filter_attribution_results.json", encoding="utf-8"))
    order = ["R0_raw_breakout", "R1_plus_buffer", "R2_plus_candle", "R3_plus_htf", "R4_plus_vol_FULL"]
    names = ["+buffer", "+candle", "+HTF", "+vol"]
    print("\nDELTA per filter addition (unguarded diagnostic, unit-level):")
    print(f"{'sym':7s} {'filter':8s} {'dIS_net':>10s} {'dOOS_net':>10s} {'dIS_mean':>9s} {'dOOS_mean':>9s} {'dN':>5s}")
    for sym in SYMBOLS:
        for k in range(4):
            a = d[sym][order[k]]["diag_unguarded"]["unit"]
            b = d[sym][order[k + 1]]["diag_unguarded"]["unit"]
            dis = b["is_"]["net"] - a["is_"]["net"]
            doo = b["oos"]["net"] - a["oos"]["net"]
            dmi = (b["is_"]["mean"] or 0) - (a["is_"]["mean"] or 0)
            dmo = (b["oos"]["mean"] or 0) - (a["oos"]["mean"] or 0)
            dn = b["all"]["units"] - a["all"]["units"]
            print(f"{sym:7s} {names[k]:8s} {dis:+10.2f} {doo:+10.2f} {dmi:+9.2f} {dmo:+9.2f} {dn:+5d}")


if __name__ == "__main__":
    main()
