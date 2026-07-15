#!/usr/bin/env python3
"""Audit dimension: IS THE ENTRY SIGNAL BETTER THAN RANDOM?  Part 1 of 2.

Event study at actual entries vs regime-matched random in-session controls.

Design (no look-ahead anywhere):
- Units: canonical ledger tranches grouped by (entry_time, side, entry). Each
  unit (fresh breakout entry or pyramid add) is one event.
- Forward return at +4h/+24h/+72h, measured in H1 BAR steps (k bars ahead;
  bars are consecutive trading hours, so weekend gaps are compressed, applied
  identically to signal events and controls).
- Fill model, identical for events and controls, same as the engine:
  long  enters at ask = open[i] + spread[i], exits at bid = open[i+k]
  short enters at bid = open[i],          exits at ask = open[i+k] + spread[i+k]
  minus round-trip commission (2 x $2.5/lot converted to price), all divided by
  ATR(14, SMA-of-TR)[i-1] -- the exact ATR array the engine trades off.
- Controls: bars that pass the SAME eligibility the strategy faces
  (warmup >= max(ema_slow+10, donchian+2), in_session 08:00-17:00 excl 12:00,
  weekday<5 and Friday<17 via the engine's in_session(), finite ATR[i-1],
  i+72 within data). Control side = prevailing H1 EMA50/200 direction at i-1
  (fast>slow -> long, fast<slow -> short) so controls are regime-matched and
  pay the same costs. Actual events falling outside the strict eligibility set
  (e.g. too close to end of data) are dropped from BOTH sides of the
  comparison and counted.
- N_SEEDS control portfolios, each sampling n_events eligible bars without
  replacement; the percentile of the actual mean within the seed-mean
  distribution is the test statistic.

Outputs backtest/audit/entry_event_study_results.json and prints a table.
"""
import json
import math
import random
import statistics
import sys
from pathlib import Path

REPO = Path(r"C:/Users/Sanique Richards/Documents/Homework Heroes/Pokemon/Bot-EA")
sys.path.insert(0, str(REPO))

from backtest.ftmo_quant_backtest import (
    Config,
    aggregate_h1,
    atr_sma_of_tr,
    ema,
    in_session,
    load_m15,
)

FALLBACK_PTS = {"EURUSD": 10.0, "GBPUSD": 15.0, "USDJPY": 28.0, "XAUUSD": 16.0}
CANON_FILE = {
    "EURUSD": "EURUSD.json",
    "GBPUSD": "GBPUSD_relaxedgate.json",
    "USDJPY": "USDJPY.json",
    "XAUUSD": "XAUUSD.json",
}
HORIZONS = (4, 24, 72)
N_SEEDS = 1000
BASE_SEED = 20260714


def percentile_rank(value: float, dist: list[float]) -> float:
    below = sum(1 for d in dist if d < value)
    equal = sum(1 for d in dist if d == value)
    return 100.0 * (below + 0.5 * equal) / len(dist)


def study_symbol(symbol: str, broker: dict) -> dict:
    config = Config()
    meta = broker["symbols"][symbol]
    point = float(meta["point"])
    dpppl = float(meta["trade_tick_value_loss"]) / float(meta["trade_tick_size"])
    commission_rt_price = 2.0 * float(meta["commission"]["per_side_usd_per_lot"]) / dpppl
    fallback = FALLBACK_PTS[symbol] * point

    bars = aggregate_h1(load_m15(REPO / f"backtest/data/derivM15/{symbol}.csv", fallback))
    closes = [b.close for b in bars]
    fast = ema(closes, config.ema_fast)
    slow = ema(closes, config.ema_slow)
    atr = atr_sma_of_tr(bars, config.atr_period)
    warmup = max(config.ema_slow + 10, config.donchian + 2)
    n = len(bars)
    time_to_index = {b.time.isoformat(sep=" "): i for i, b in enumerate(bars)}

    def fwd_ret_atr(i: int, side: int, k: int) -> float:
        """Cost-adjusted forward return in ATR units, engine fill convention."""
        j = i + k
        if side > 0:
            entry = bars[i].open + bars[i].spread
            exit_ = bars[j].open
        else:
            entry = bars[i].open
            exit_ = bars[j].open + bars[j].spread
        return (side * (exit_ - entry) - commission_rt_price) / atr[i - 1]

    # ---- actual entry events from the canonical ledger -------------------
    canon = json.loads(
        (REPO / "backtest/results" / CANON_FILE[symbol]).read_text(encoding="utf-8")
    )["measured"]
    units: dict[tuple, dict] = {}
    for tr in canon["trades"]:
        key = (tr["entry_time"], tr["side"], tr["entry"])
        u = units.setdefault(key, {"exit_last": tr["exit_time"], "oos": tr["oos"]})
        u["exit_last"] = max(u["exit_last"], tr["exit_time"])
    events = []
    dropped_horizon = dropped_other = 0
    sanity_bad_entry = 0
    for (etime, side, eprice), u in sorted(units.items()):
        i = time_to_index.get(etime)
        if i is None:
            dropped_other += 1
            continue
        if i < warmup or not math.isfinite(atr[i - 1]):
            dropped_other += 1
            continue
        if i + max(HORIZONS) >= n:
            dropped_horizon += 1
            continue
        # sanity: ledger entry price must equal recomputed engine fill
        recomputed = bars[i].open + bars[i].spread if side > 0 else bars[i].open
        if abs(recomputed - eprice) > 1e-9:
            sanity_bad_entry += 1
        # fresh vs pyramid add: fresh if entry_time not inside another unit's
        # open interval (entry, last_exit]
        fresh = True
        for (etime2, _s2, _e2), u2 in units.items():
            if etime2 < etime <= u2["exit_last"] and (etime2, _s2, _e2) != (etime, side, eprice):
                fresh = False
                break
        events.append({"i": i, "side": side, "fresh": fresh, "oos": u["oos"]})

    # ---- eligible control universe ---------------------------------------
    eligible: list[tuple[int, int]] = []  # (index, regime side)
    for i in range(warmup, n - max(HORIZONS)):
        if not in_session(bars[i].time, config):
            continue
        if not math.isfinite(atr[i - 1]) or atr[i - 1] <= 0.0:
            continue
        if fast[i - 1] > slow[i - 1]:
            side = 1
        elif fast[i - 1] < slow[i - 1]:
            side = -1
        else:
            continue
        eligible.append((i, side))

    # precompute regime-direction returns for every eligible bar
    ctrl_ret = {k: [fwd_ret_atr(i, s, k) for (i, s) in eligible] for k in HORIZONS}

    def summarize(evts: list[dict]) -> dict:
        n_ev = len(evts)
        out = {
            "n_events": n_ev,
            "n_long": sum(1 for e in evts if e["side"] > 0),
            "n_short": sum(1 for e in evts if e["side"] < 0),
        }
        if n_ev == 0:
            return out
        for k in HORIZONS:
            actual = [fwd_ret_atr(e["i"], e["side"], k) for e in evts]
            actual_mean = statistics.fmean(actual)
            seed_means = []
            for s in range(N_SEEDS):
                rng = random.Random(BASE_SEED + 1000 * k + s)
                idxs = rng.sample(range(len(eligible)), n_ev)
                seed_means.append(statistics.fmean(ctrl_ret[k][j] for j in idxs))
            ctrl_mu = statistics.fmean(seed_means)
            ctrl_sd = statistics.stdev(seed_means)
            out[f"h{k}"] = {
                "actual_mean_atr": actual_mean,
                "actual_sd_atr": statistics.stdev(actual) if n_ev > 1 else None,
                "control_mean_atr": ctrl_mu,
                "control_seedmean_sd": ctrl_sd,
                "excess_atr": actual_mean - ctrl_mu,
                "excess_z": (actual_mean - ctrl_mu) / ctrl_sd if ctrl_sd else None,
                "percentile_vs_controls": percentile_rank(actual_mean, seed_means),
                "population_ctrl_mean_atr": statistics.fmean(ctrl_ret[k]),
            }
        return out

    result = {
        "symbol": symbol,
        "n_units_ledger": len(units),
        "dropped_horizon_tail": dropped_horizon,
        "dropped_other": dropped_other,
        "sanity_bad_entry_price": sanity_bad_entry,
        "n_eligible_control_bars": len(eligible),
        "control_long_share_pct": 100.0 * sum(1 for _, s in eligible if s > 0) / len(eligible),
        "n_seeds": N_SEEDS,
        "all_units": summarize(events),
        "fresh_only": summarize([e for e in events if e["fresh"]]),
        "adds_only": summarize([e for e in events if not e["fresh"]]),
        "oos_units": summarize([e for e in events if e["oos"]]),
    }
    return result


def main() -> None:
    broker = json.loads((REPO / "backtest/deriv_broker_meta.json").read_text(encoding="utf-8"))
    results = {}
    for symbol in FALLBACK_PTS:
        res = study_symbol(symbol, broker)
        results[symbol] = res
        a = res["all_units"]
        print(
            f"\n{symbol}: units={a['n_events']} (L{a['n_long']}/S{a['n_short']}) "
            f"eligible_ctrl_bars={res['n_eligible_control_bars']} "
            f"dropped_tail={res['dropped_horizon_tail']} sanity_bad={res['sanity_bad_entry_price']}"
        )
        for scope in ("all_units", "fresh_only", "adds_only", "oos_units"):
            s = res[scope]
            if s["n_events"] == 0:
                continue
            row = [
                f"  {scope:<10} n={s['n_events']:>3}"
            ]
            for k in HORIZONS:
                h = s[f"h{k}"]
                row.append(
                    f"+{k}h: act {h['actual_mean_atr']:+.3f} ctl {h['control_mean_atr']:+.3f} "
                    f"exc {h['excess_atr']:+.3f} pct {h['percentile_vs_controls']:5.1f}"
                )
            print(" | ".join(row))
    out = REPO / "backtest/audit/entry_event_study_results.json"
    out.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"\nwrote {out}")


if __name__ == "__main__":
    main()
