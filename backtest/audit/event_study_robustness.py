#!/usr/bin/env python3
"""Second-way verification of the entry event study (honesty rule: re-derive
surprising results with different machinery before reporting).

Checks, per symbol, all-units scope:
1. Event bootstrap (resample events with replacement, 2000 draws) -> 90% CI of
   the actual mean forward ATR return; compare to the POPULATION control mean
   (all eligible regime-matched in-session bars). Different statistic from the
   seeded control-portfolio percentile in entry_event_study.py.
2. Median-based comparison (robust to a few catastrophic events).
3. Alternative fill convention: exit at close[i+k-1] instead of open[i+k], and
   a zero-cost variant, to confirm the sign of the excess is not a fill or
   cost artifact.
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
BOOT = 2000
SEED = 987654321


def main() -> None:
    broker = json.loads((REPO / "backtest/deriv_broker_meta.json").read_text(encoding="utf-8"))
    config = Config()
    out = {}
    for symbol in FALLBACK_PTS:
        meta = broker["symbols"][symbol]
        point = float(meta["point"])
        dpppl = float(meta["trade_tick_value_loss"]) / float(meta["trade_tick_size"])
        comm_rt = 2.0 * float(meta["commission"]["per_side_usd_per_lot"]) / dpppl
        bars = aggregate_h1(
            load_m15(REPO / f"backtest/data/derivM15/{symbol}.csv", FALLBACK_PTS[symbol] * point)
        )
        closes = [b.close for b in bars]
        fast = ema(closes, config.ema_fast)
        slow = ema(closes, config.ema_slow)
        atr = atr_sma_of_tr(bars, config.atr_period)
        warmup = max(config.ema_slow + 10, config.donchian + 2)
        n = len(bars)
        t2i = {b.time.isoformat(sep=" "): i for i, b in enumerate(bars)}

        def ret(i, side, k, fill="open", cost=True):
            j = i + k
            c = comm_rt if cost else 0.0
            sp_in = bars[i].spread if cost else 0.0
            if fill == "open":
                exit_mid = bars[j].open
                sp_out = bars[j].spread if cost else 0.0
            else:
                exit_mid = bars[j - 1].close
                sp_out = bars[j - 1].spread if cost else 0.0
            if side > 0:
                r = (exit_mid) - (bars[i].open + sp_in)
            else:
                r = bars[i].open - (exit_mid + sp_out)
            return (r - c) / atr[i - 1]

        canon = json.loads(
            (REPO / "backtest/results" / CANON_FILE[symbol]).read_text(encoding="utf-8")
        )["measured"]
        units = sorted({(t["entry_time"], t["side"], t["entry"]) for t in canon["trades"]})
        events = []
        for etime, side, _ in units:
            i = t2i[etime]
            if i >= warmup and i + max(HORIZONS) < n and math.isfinite(atr[i - 1]):
                events.append((i, side))

        eligible = []
        for i in range(warmup, n - max(HORIZONS)):
            if not in_session(bars[i].time, config):
                continue
            if not math.isfinite(atr[i - 1]) or atr[i - 1] <= 0.0:
                continue
            if fast[i - 1] > slow[i - 1]:
                eligible.append((i, 1))
            elif fast[i - 1] < slow[i - 1]:
                eligible.append((i, -1))

        rng = random.Random(SEED)
        sym = {}
        for k in HORIZONS:
            actual = [ret(i, s, k) for i, s in events]
            pop_ctrl = [ret(i, s, k) for i, s in eligible]
            pop_mu = statistics.fmean(pop_ctrl)
            boots = []
            for _ in range(BOOT):
                sample = [actual[rng.randrange(len(actual))] for _ in range(len(actual))]
                boots.append(statistics.fmean(sample))
            boots.sort()
            lo, hi = boots[int(0.05 * BOOT)], boots[int(0.95 * BOOT) - 1]
            alt = statistics.fmean(ret(i, s, k, fill="close") for i, s in events)
            alt_ctrl = statistics.fmean(ret(i, s, k, fill="close") for i, s in eligible)
            nc = statistics.fmean(ret(i, s, k, cost=False) for i, s in events)
            nc_ctrl = statistics.fmean(ret(i, s, k, cost=False) for i, s in eligible)
            sym[f"h{k}"] = {
                "actual_mean": statistics.fmean(actual),
                "boot90_lo": lo,
                "boot90_hi": hi,
                "pop_ctrl_mean": pop_mu,
                "excess": statistics.fmean(actual) - pop_mu,
                "ci_excludes_ctrl": not (lo <= pop_mu <= hi),
                "actual_median": statistics.median(actual),
                "ctrl_median": statistics.median(pop_ctrl),
                "excess_median": statistics.median(actual) - statistics.median(pop_ctrl),
                "excess_altfill": alt - alt_ctrl,
                "excess_nocost": nc - nc_ctrl,
            }
        out[symbol] = sym
        print(f"\n{symbol} (n={len(events)} units)")
        for k in HORIZONS:
            h = sym[f"h{k}"]
            print(
                f"  +{k:>2}h act {h['actual_mean']:+.3f} CI90[{h['boot90_lo']:+.3f},{h['boot90_hi']:+.3f}] "
                f"ctl {h['pop_ctrl_mean']:+.3f} sep={h['ci_excludes_ctrl']} | "
                f"med_exc {h['excess_median']:+.3f} | altfill_exc {h['excess_altfill']:+.3f} | "
                f"nocost_exc {h['excess_nocost']:+.3f}"
            )
    path = REPO / "backtest/audit/event_study_robustness_results.json"
    path.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"\nwrote {path}")


if __name__ == "__main__":
    main()
