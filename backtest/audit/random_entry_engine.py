#!/usr/bin/env python3
"""Audit dimension: IS THE ENTRY SIGNAL BETTER THAN RANDOM?  Part 2 of 2.

Engine-level randomization: replace the module-level signal() (Donchian
breakout + ATR buffer + candle confirmation + EMA stack/slope) with a seeded
random signal that
  - fires only at/after the same warmup index and only when ATR[i-1] is finite,
  - fires LONG only when the H1 EMA50/200 stack is bullish and SHORT only when
    bearish (regime-matched, same as the event-study controls),
  - fires with the real signal's measured per-regime per-bar rate, so raw fire
    frequency and side mix match the real signal in expectation.
Everything else is untouched: HTF confluence + vol-regime context_ok, session,
daily caps, FTMO guards, sizing, scale-out/trail exits, pyramiding, swap,
commission, 1x fallback spread (measured path). 200 seeds per symbol.

The percentile of the CANONICAL real net return within the random-entry return
distribution answers: does the entry timing rule add anything the rest of the
system does not already provide?

Note on clustering: real fires cluster in breakout runs, i.i.d. random fires do
not, so random runs can convert more fires into trades. Trade counts are
reported; per-tranche expectancy_r percentile is included to normalize.
"""
import json
import math
import random
import statistics
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

REPO = Path(r"C:/Users/Sanique Richards/Documents/Homework Heroes/Pokemon/Bot-EA")
sys.path.insert(0, str(REPO))

FALLBACK_PTS = {"EURUSD": 10.0, "GBPUSD": 15.0, "USDJPY": 28.0, "XAUUSD": 16.0}
CANON_FILE = {
    "EURUSD": "EURUSD.json",
    "GBPUSD": "GBPUSD_relaxedgate.json",
    "USDJPY": "USDJPY.json",
    "XAUUSD": "XAUUSD.json",
}
N_SEEDS = 200
BASE_SEED = {"EURUSD": 11_000_000, "GBPUSD": 12_000_000, "USDJPY": 13_000_000, "XAUUSD": 14_000_000}


def measure_real_signal(symbol: str):
    """Load bars and measure the real signal's per-regime fire rates."""
    import backtest.ftmo_quant_backtest as eng

    broker = json.loads((REPO / "backtest/deriv_broker_meta.json").read_text(encoding="utf-8"))
    meta = broker["symbols"][symbol]
    fallback = FALLBACK_PTS[symbol] * float(meta["point"])
    bars = eng.aggregate_h1(eng.load_m15(REPO / f"backtest/data/derivM15/{symbol}.csv", fallback))
    config = eng.Config()
    closes = [b.close for b in bars]
    fast = eng.ema(closes, config.ema_fast)
    slow = eng.ema(closes, config.ema_slow)
    atr = eng.atr_sma_of_tr(bars, config.atr_period)
    warmup = max(config.ema_slow + 10, config.donchian + 2)

    bull_bars = bear_bars = long_fires = short_fires = 0
    for i in range(warmup, len(bars)):
        if not math.isfinite(atr[i - 1]):
            continue
        s = eng.signal(i, bars, fast, slow, config, atr)
        if fast[i - 1] > slow[i - 1]:
            bull_bars += 1
            if s == 1:
                long_fires += 1
        elif fast[i - 1] < slow[i - 1]:
            bear_bars += 1
            if s == -1:
                short_fires += 1
    p_long = long_fires / bull_bars if bull_bars else 0.0
    p_short = short_fires / bear_bars if bear_bars else 0.0
    stats = {
        "bull_bars": bull_bars,
        "bear_bars": bear_bars,
        "long_fires": long_fires,
        "short_fires": short_fires,
        "p_long_given_bull": p_long,
        "p_short_given_bear": p_short,
    }
    return bars, meta, config, fast, slow, atr, warmup, stats


def unit_count_and_long_share(trades: list[dict]):
    units = {(t["entry_time"], t["side"], t["entry"]) for t in trades}
    n = len(units)
    n_long = sum(1 for _, s, _ in units if s > 0)
    return n, (100.0 * n_long / n if n else None)


def run_symbol_chunk(args):
    symbol, seed_lo, seed_hi = args
    import backtest.ftmo_quant_backtest as eng

    bars, meta, config, fast_a, slow_a, atr_a, warmup, stats = measure_real_signal(symbol)
    p_long = stats["p_long_given_bull"]
    p_short = stats["p_short_given_bear"]
    rows = []
    real_signal = eng.signal
    try:
        for seed in range(seed_lo, seed_hi):
            rng = random.Random(BASE_SEED[symbol] + seed)
            draws = [rng.random() for _ in range(len(bars))]
            fires = 0

            def rand_signal(index, bars_, fast, slow, config_, atr=None):
                nonlocal fires
                if index < warmup:
                    return 0
                if atr is not None and not math.isfinite(atr[index - 1]):
                    return 0
                if fast[index - 1] > slow[index - 1]:
                    if draws[index] < p_long:
                        fires += 1
                        return 1
                elif fast[index - 1] < slow[index - 1]:
                    if draws[index] < p_short:
                        fires += 1
                        return -1
                return 0

            eng.signal = rand_signal
            res = eng.run_backtest(
                bars,
                meta,
                config,
                initial_balance=100_000.0,
                spread_multiplier=1.0,
                split_fraction=0.70,
                max_spread_points=100.0,
            )
            n_units, long_share = unit_count_and_long_share(res["trades"])
            rows.append(
                {
                    "seed": seed,
                    "return_pct": res["return_pct"],
                    "expectancy_r": res["all"]["expectancy_r"],
                    "oos_pf": res["oos"]["profit_factor"],
                    "n_tranches": res["all"]["trades"],
                    "n_units": n_units,
                    "long_share_pct": long_share,
                    "fires_called": fires,
                    "official_breach": res["official_rule_breach"],
                }
            )
    finally:
        eng.signal = real_signal
    return symbol, stats, rows


def percentile_rank(value, dist):
    below = sum(1 for d in dist if d < value)
    equal = sum(1 for d in dist if d == value)
    return 100.0 * (below + 0.5 * equal) / len(dist)


def quantile(sorted_vals, q):
    idx = min(len(sorted_vals) - 1, max(0, int(round(q * (len(sorted_vals) - 1)))))
    return sorted_vals[idx]


def main() -> None:
    jobs = []
    chunk = 25
    for symbol in FALLBACK_PTS:
        for lo in range(0, N_SEEDS, chunk):
            jobs.append((symbol, lo, min(lo + chunk, N_SEEDS)))
    results: dict[str, list] = {s: [] for s in FALLBACK_PTS}
    sig_stats: dict[str, dict] = {}
    with ProcessPoolExecutor(max_workers=6) as pool:
        for symbol, stats, rows in pool.map(run_symbol_chunk, jobs):
            sig_stats[symbol] = stats
            results[symbol].extend(rows)

    report = {}
    for symbol in FALLBACK_PTS:
        canon = json.loads(
            (REPO / "backtest/results" / CANON_FILE[symbol]).read_text(encoding="utf-8")
        )["measured"]
        real_units, real_long_share = unit_count_and_long_share(canon["trades"])
        rows = sorted(results[symbol], key=lambda r: r["seed"])
        rets = sorted(r["return_pct"] for r in rows)
        exps = sorted(r["expectancy_r"] for r in rows if r["expectancy_r"] is not None)
        report[symbol] = {
            "real_signal_stats": sig_stats[symbol],
            "n_seeds": len(rows),
            "real_return_pct": canon["return_pct"],
            "real_expectancy_r": canon["all"]["expectancy_r"],
            "real_n_units": real_units,
            "real_long_share_pct": real_long_share,
            "random_return_mean": statistics.fmean(rets),
            "random_return_sd": statistics.stdev(rets),
            "random_return_q05": quantile(rets, 0.05),
            "random_return_q25": quantile(rets, 0.25),
            "random_return_q50": quantile(rets, 0.50),
            "random_return_q75": quantile(rets, 0.75),
            "random_return_q95": quantile(rets, 0.95),
            "real_return_percentile": percentile_rank(canon["return_pct"], rets),
            "random_expectancy_mean": statistics.fmean(exps),
            "real_expectancy_percentile": percentile_rank(canon["all"]["expectancy_r"], exps),
            "random_n_units_mean": statistics.fmean(r["n_units"] for r in rows),
            "random_long_share_mean": statistics.fmean(
                r["long_share_pct"] for r in rows if r["long_share_pct"] is not None
            ),
            "random_breach_count": sum(1 for r in rows if r["official_breach"]),
            "rows": rows,
        }
        r = report[symbol]
        print(
            f"{symbol}: real {r['real_return_pct']:+.2f}% -> pct {r['real_return_percentile']:.1f} "
            f"| rand mean {r['random_return_mean']:+.2f}% sd {r['random_return_sd']:.2f} "
            f"q05 {r['random_return_q05']:+.2f} med {r['random_return_q50']:+.2f} q95 {r['random_return_q95']:+.2f} "
            f"| units real {r['real_n_units']} vs rand {r['random_n_units_mean']:.0f} "
            f"| longshare real {r['real_long_share_pct']:.0f}% rand {r['random_long_share_mean']:.0f}% "
            f"| expR pct {r['real_expectancy_percentile']:.1f}"
        )

    out = REPO / "backtest/audit/random_entry_engine_results.json"
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
