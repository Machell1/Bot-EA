#!/usr/bin/env python3
"""Second derivation of the GBPUSD variant-B surprise: random-entry control with
fire probability CALIBRATED so the random runs convert ~the same number of
units as the real system (105), removing the trade-count/cost-drag confound
entirely. 200 fresh seeds. If the real return still sits >95th percentile, the
GBP timing effect at the engine level is genuine and the prior analyst's
'84.5th, none clears 95' understates it due to an under-fired control.
"""
import hashlib
import json
import math
import statistics
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

REPO = Path(r"C:/Users/Sanique Richards/Documents/Homework Heroes/Pokemon/Bot-EA")
sys.path.insert(0, str(REPO))

SYMBOL = "GBPUSD"
FB_PTS = 15.0
CANON = "GBPUSD_relaxedgate.json"
N_SEEDS = 200
# Variant B (in-session rates) produced 113 units vs real 105 -> scale down.
SCALE = 105.0 / 113.0
P_LONG_B = 0.049353301565691625   # 145/2938 in-session bull fire rate
P_SHORT_B = 0.04204946996466431   # 119/2830 in-session bear fire rate
TAG = "gbp-matched-count-2026-07-14"

_CACHE = {}


def prep():
    if "d" in _CACHE:
        return _CACHE["d"]
    import backtest.ftmo_quant_backtest as eng

    broker = json.loads((REPO / "backtest/deriv_broker_meta.json").read_text(encoding="utf-8"))
    meta = broker["symbols"][SYMBOL]
    bars = eng.aggregate_h1(
        eng.load_m15(REPO / f"backtest/data/derivM15/{SYMBOL}.csv", FB_PTS * float(meta["point"]))
    )
    cfg = eng.Config()
    warmup = max(cfg.ema_slow + 10, cfg.donchian + 2)
    _CACHE["d"] = (bars, meta, cfg, warmup)
    return _CACHE["d"]


def run_chunk(args):
    lo, hi = args
    import random

    import backtest.ftmo_quant_backtest as eng

    bars, meta, cfg, warmup = prep()
    p_long = P_LONG_B * SCALE
    p_short = P_SHORT_B * SCALE
    real = eng.signal
    rows = []
    try:
        for k in range(lo, hi):
            seed = int.from_bytes(hashlib.sha256(f"{TAG}|{k}".encode()).digest()[:8], "big")
            rng = random.Random(seed)
            draws = [rng.random() for _ in range(len(bars))]

            def rand_signal(index, bars_, fast, slow, config_, atr=None):
                if index < warmup:
                    return 0
                if atr is not None and not math.isfinite(atr[index - 1]):
                    return 0
                if fast[index - 1] > slow[index - 1]:
                    return 1 if draws[index] < p_long else 0
                if fast[index - 1] < slow[index - 1]:
                    return -1 if draws[index] < p_short else 0
                return 0

            eng.signal = rand_signal
            res = eng.run_backtest(
                bars, meta, cfg,
                initial_balance=100_000.0, spread_multiplier=1.0,
                split_fraction=0.70, max_spread_points=100.0,
            )
            units = {(t["entry_time"], t["side"], t["entry"]) for t in res["trades"]}
            rows.append({"k": k, "return_pct": res["return_pct"],
                         "expectancy_r": res["all"]["expectancy_r"], "n_units": len(units)})
    finally:
        eng.signal = real
    return rows


def pct_rank(value, dist):
    below = sum(1 for d in dist if d < value)
    equal = sum(1 for d in dist if d == value)
    return 100.0 * (below + 0.5 * equal) / len(dist)


def main() -> None:
    canon = json.loads((REPO / "backtest/results" / CANON).read_text(encoding="utf-8"))["measured"]
    jobs = [(lo, min(lo + 25, N_SEEDS)) for lo in range(0, N_SEEDS, 25)]
    rows = []
    with ProcessPoolExecutor(max_workers=6) as pool:
        for chunk in pool.map(run_chunk, jobs):
            rows.extend(chunk)
    rets = sorted(r["return_pct"] for r in rows)
    exps = sorted(r["expectancy_r"] for r in rows if r["expectancy_r"] is not None)
    out = {
        "symbol": SYMBOL, "n_seeds": len(rows),
        "p_long": P_LONG_B * SCALE, "p_short": P_SHORT_B * SCALE,
        "real_return_pct": canon["return_pct"],
        "random_return_mean": statistics.fmean(rets),
        "random_return_sd": statistics.stdev(rets),
        "random_return_median": statistics.median(rets),
        "random_positive": sum(1 for r in rets if r > 0),
        "real_return_percentile": pct_rank(canon["return_pct"], rets),
        "real_expectancy_percentile": pct_rank(canon["all"]["expectancy_r"], exps),
        "random_n_units_mean": statistics.fmean(r["n_units"] for r in rows),
        "rows": rows,
    }
    (REPO / "backtest/audit/verify_gbp_matched_count_results.json").write_text(
        json.dumps(out, indent=2), encoding="utf-8"
    )
    print(f"GBPUSD count-matched control: units rand {out['random_n_units_mean']:.0f} vs real 105 | "
          f"real {out['real_return_pct']:+.2f}% -> pctile {out['real_return_percentile']:.1f} | "
          f"rand mean {out['random_return_mean']:+.2f} med {out['random_return_median']:+.2f} "
          f"sd {out['random_return_sd']:.2f} | pos {out['random_positive']}/{len(rows)} | "
          f"expR pctile {out['real_expectancy_percentile']:.1f}")


if __name__ == "__main__":
    main()
