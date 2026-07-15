#!/usr/bin/env python3
"""ADVERSARIAL VERIFICATION of the 'signal-vs-random' engine-level headline.

Independent re-derivation (fresh code, fresh seed streams) of:
  1. Baseline: canonical measured runs reproduce trade-for-trade with the real
     signal under the engine as it exists on disk.
  2. Variant A (analyst's design, re-implemented): random signal fires i.i.d.
     per bar with the real signal's UNCONDITIONAL per-regime fire rate
     (long only in bull EMA50/200 stack, short only in bear), everything else
     in the engine untouched. 200 fresh seeds per symbol.
  3. Variant B (fairness probe): same, but the fire rate is conditioned on
     IN-SESSION bars, because run_backtest only consults signal() on
     in-session bars - the real signal fires ~2x more often in-session than
     unconditionally, so Variant A under-trades relative to the real system.

Seeds are derived from sha256(symbol|variant|k|VERIFY-TAG) - fully independent
of the prior analyst's streams (theirs: BASE 11-14M + k).
Percentile convention: mid-rank, same as the analyst (fair comparison).
Costs/gates identical for strategy and controls (same run_backtest call).
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

FALLBACK_PTS = {"EURUSD": 10.0, "GBPUSD": 15.0, "USDJPY": 28.0, "XAUUSD": 16.0}
CANON_FILE = {
    "EURUSD": "EURUSD.json",
    "GBPUSD": "GBPUSD_relaxedgate.json",
    "USDJPY": "USDJPY.json",
    "XAUUSD": "XAUUSD.json",
}
N_SEEDS = 200
VERIFY_TAG = "ftmo-quant-verify-2026-07-14"

_CACHE: dict[str, tuple] = {}


def prep(symbol: str):
    """Load bars once per process and measure per-regime fire rates two ways."""
    if symbol in _CACHE:
        return _CACHE[symbol]
    import backtest.ftmo_quant_backtest as eng

    broker = json.loads((REPO / "backtest/deriv_broker_meta.json").read_text(encoding="utf-8"))
    meta = broker["symbols"][symbol]
    bars = eng.aggregate_h1(
        eng.load_m15(
            REPO / f"backtest/data/derivM15/{symbol}.csv",
            FALLBACK_PTS[symbol] * float(meta["point"]),
        )
    )
    cfg = eng.Config()
    closes = [b.close for b in bars]
    fast = eng.ema(closes, cfg.ema_fast)
    slow = eng.ema(closes, cfg.ema_slow)
    atr = eng.atr_sma_of_tr(bars, cfg.atr_period)
    warmup = max(cfg.ema_slow + 10, cfg.donchian + 2)

    n = dict(bull=0, bear=0, lf=0, sf=0, bull_s=0, bear_s=0, lf_s=0, sf_s=0)
    for i in range(warmup, len(bars)):
        if not math.isfinite(atr[i - 1]):
            continue
        s = eng.signal(i, bars, fast, slow, cfg, atr)
        sess = eng.in_session(bars[i].time, cfg)
        if fast[i - 1] > slow[i - 1]:
            n["bull"] += 1
            n["lf"] += s == 1
            if sess:
                n["bull_s"] += 1
                n["lf_s"] += s == 1
        elif fast[i - 1] < slow[i - 1]:
            n["bear"] += 1
            n["sf"] += s == -1
            if sess:
                n["bear_s"] += 1
                n["sf_s"] += s == -1
    rates = {
        "A": (n["lf"] / n["bull"] if n["bull"] else 0.0,
              n["sf"] / n["bear"] if n["bear"] else 0.0),
        "B": (n["lf_s"] / n["bull_s"] if n["bull_s"] else 0.0,
              n["sf_s"] / n["bear_s"] if n["bear_s"] else 0.0),
    }
    _CACHE[symbol] = (bars, meta, cfg, warmup, rates, n)
    return _CACHE[symbol]


def seed_for(symbol: str, variant: str, k: int) -> int:
    h = hashlib.sha256(f"{symbol}|{variant}|{k}|{VERIFY_TAG}".encode()).digest()
    return int.from_bytes(h[:8], "big")


def units_of(trades: list[dict]):
    units = {(t["entry_time"], t["side"], t["entry"]) for t in trades}
    return len(units), sum(1 for _, s, _ in units if s > 0)


def run_chunk(args):
    symbol, variant, lo, hi = args
    import random

    import backtest.ftmo_quant_backtest as eng

    bars, meta, cfg, warmup, rates, _ = prep(symbol)
    p_long, p_short = rates[variant]
    real_signal_fn = eng.signal
    rows = []
    try:
        for k in range(lo, hi):
            rng = random.Random(seed_for(symbol, variant, k))
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
                initial_balance=100_000.0,
                spread_multiplier=1.0,
                split_fraction=0.70,
                max_spread_points=100.0,
            )
            n_units, n_long = units_of(res["trades"])
            rows.append({
                "k": k,
                "return_pct": res["return_pct"],
                "expectancy_r": res["all"]["expectancy_r"],
                "n_tranches": res["all"]["trades"],
                "n_units": n_units,
                "n_long_units": n_long,
                "official_breach": res["official_rule_breach"],
            })
    finally:
        eng.signal = real_signal_fn
    return symbol, variant, rows


def baseline_check(symbol: str) -> dict:
    """Real-signal run vs canonical measured ledger, trade for trade."""
    import backtest.ftmo_quant_backtest as eng

    bars, meta, cfg, _, _, _ = prep(symbol)
    res = eng.run_backtest(
        bars, meta, cfg,
        initial_balance=100_000.0,
        spread_multiplier=1.0,
        split_fraction=0.70,
        max_spread_points=100.0,
    )
    canon = json.loads((REPO / "backtest/results" / CANON_FILE[symbol]).read_text(encoding="utf-8"))["measured"]

    def key(t):
        return (t["entry_time"], t["exit_time"], t["side"], round(t["entry"], 8),
                round(t["exit"], 8), round(t["pnl"], 6), t["reason"])

    mine = sorted(key(t) for t in res["trades"])
    theirs = sorted(key(t) for t in canon["trades"])
    return {
        "return_pct_mine": res["return_pct"],
        "return_pct_canon": canon["return_pct"],
        "return_match": abs(res["return_pct"] - canon["return_pct"]) < 1e-9,
        "n_trades_mine": len(mine),
        "n_trades_canon": len(theirs),
        "ledger_identical": mine == theirs,
    }


def pct_rank(value, dist):
    below = sum(1 for d in dist if d < value)
    equal = sum(1 for d in dist if d == value)
    return 100.0 * (below + 0.5 * equal) / len(dist)


def main() -> None:
    report: dict = {"verify_tag": VERIFY_TAG, "n_seeds": N_SEEDS, "baseline": {}, "variants": {}}

    for symbol in FALLBACK_PTS:
        b = baseline_check(symbol)
        report["baseline"][symbol] = b
        print(f"BASELINE {symbol}: mine {b['return_pct_mine']:+.4f}% canon {b['return_pct_canon']:+.4f}% "
              f"ledger_identical={b['ledger_identical']} ({b['n_trades_mine']}/{b['n_trades_canon']} trades)")

    jobs = []
    chunk = 25
    for symbol in FALLBACK_PTS:
        for variant in ("A", "B"):
            for lo in range(0, N_SEEDS, chunk):
                jobs.append((symbol, variant, lo, min(lo + chunk, N_SEEDS)))

    collected: dict[tuple, list] = {}
    with ProcessPoolExecutor(max_workers=6) as pool:
        for symbol, variant, rows in pool.map(run_chunk, jobs):
            collected.setdefault((symbol, variant), []).extend(rows)

    for symbol in FALLBACK_PTS:
        canon = json.loads((REPO / "backtest/results" / CANON_FILE[symbol]).read_text(encoding="utf-8"))["measured"]
        real_ret = canon["return_pct"]
        real_exp = canon["all"]["expectancy_r"]
        real_units, real_long = units_of(canon["trades"])
        _, _, _, _, rates, counts = prep(symbol)
        for variant in ("A", "B"):
            rows = sorted(collected[(symbol, variant)], key=lambda r: r["k"])
            rets = sorted(r["return_pct"] for r in rows)
            exps = sorted(r["expectancy_r"] for r in rows if r["expectancy_r"] is not None)
            med = statistics.median(rets)
            entry = {
                "p_long": rates[variant][0],
                "p_short": rates[variant][1],
                "real_return_pct": real_ret,
                "real_expectancy_r": real_exp,
                "real_n_units": real_units,
                "random_return_mean": statistics.fmean(rets),
                "random_return_sd": statistics.stdev(rets),
                "random_return_median": med,
                "random_return_min": rets[0],
                "random_return_max": rets[-1],
                "random_positive_runs": sum(1 for r in rets if r > 0),
                "real_return_percentile": pct_rank(real_ret, rets),
                "random_expectancy_mean": statistics.fmean(exps) if exps else None,
                "real_expectancy_percentile": pct_rank(real_exp, exps) if exps else None,
                "random_n_units_mean": statistics.fmean(r["n_units"] for r in rows),
                "random_breach_count": sum(1 for r in rows if r["official_breach"]),
                "rows": rows,
            }
            report["variants"][f"{symbol}_{variant}"] = entry
            print(
                f"{symbol} var{variant}: real {real_ret:+.2f}% -> pctile {entry['real_return_percentile']:.1f} | "
                f"rand mean {entry['random_return_mean']:+.2f} sd {entry['random_return_sd']:.2f} "
                f"med {entry['random_return_median']:+.2f} | pos {entry['random_positive_runs']}/{len(rets)} | "
                f"units real {real_units} rand {entry['random_n_units_mean']:.0f} | "
                f"expR pctile {entry['real_expectancy_percentile']:.1f} | breaches {entry['random_breach_count']}"
            )

    out = REPO / "backtest/audit/verify_random_entry_engine_results.json"
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
