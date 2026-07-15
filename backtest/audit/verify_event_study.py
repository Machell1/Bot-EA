#!/usr/bin/env python3
"""ADVERSARIAL VERIFICATION of the event-study numbers (independent re-code).

Verifies, with fresh code and a fresh seed stream:
  - +4h cost-adjusted mean excess (ATR units) vs regime-matched in-session
    control portfolios, for all four symbols;
  - +72h excess for EURUSD (claimed inversion to -0.565 ATR, 21.8th pctile)
    and XAUUSD (claimed +2.069 ATR, 99.9th pctile).

Definitions mirror the prior analyst's stated methodology (same measurand),
but the implementation, RNG stream, and bookkeeping are written from scratch:
  event = unique (entry_time, side, entry) unit in the canonical ledger;
  fwd return at +k H1 bars with engine fill convention (long: ask in bid out;
  short: bid in ask out), minus round-trip commission in price, / ATR14[i-1];
  controls = all in-session warmup-ok finite-ATR bars with i+72 < n, side =
  EMA50/200 regime at i-1; 1000 seeded control portfolios of size n_events
  sampled without replacement.
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
    Config, aggregate_h1, atr_sma_of_tr, ema, in_session, load_m15,
)

FALLBACK_PTS = {"EURUSD": 10.0, "GBPUSD": 15.0, "USDJPY": 28.0, "XAUUSD": 16.0}
CANON_FILE = {
    "EURUSD": "EURUSD.json",
    "GBPUSD": "GBPUSD_relaxedgate.json",
    "USDJPY": "USDJPY.json",
    "XAUUSD": "XAUUSD.json",
}
HORIZONS = (4, 24, 72)
MAXH = max(HORIZONS)
N_PORTFOLIOS = 1000
SEED_TAG = 987_020_714  # fresh stream, unrelated to the analyst's 20260714


def pct_rank(value, dist):
    below = sum(1 for d in dist if d < value)
    equal = sum(1 for d in dist if d == value)
    return 100.0 * (below + 0.5 * equal) / len(dist)


def main() -> None:
    broker = json.loads((REPO / "backtest/deriv_broker_meta.json").read_text(encoding="utf-8"))
    cfg = Config()
    report = {}
    for symbol, fb_pts in FALLBACK_PTS.items():
        meta = broker["symbols"][symbol]
        point = float(meta["point"])
        dpppl = float(meta["trade_tick_value_loss"]) / float(meta["trade_tick_size"])
        comm_price = 2.0 * float(meta["commission"]["per_side_usd_per_lot"]) / dpppl
        bars = aggregate_h1(load_m15(REPO / f"backtest/data/derivM15/{symbol}.csv", fb_pts * point))
        closes = [b.close for b in bars]
        fast = ema(closes, cfg.ema_fast)
        slow = ema(closes, cfg.ema_slow)
        atr = atr_sma_of_tr(bars, cfg.atr_period)
        warmup = max(cfg.ema_slow + 10, cfg.donchian + 2)
        n = len(bars)
        idx_of = {b.time.isoformat(sep=" "): i for i, b in enumerate(bars)}

        def fwd(i, side, k):
            j = i + k
            if side > 0:
                return (bars[j].open - (bars[i].open + bars[i].spread) - comm_price) / atr[i - 1]
            return ((bars[i].open - (bars[j].open + bars[j].spread)) - comm_price) / atr[i - 1]

        canon = json.loads((REPO / "backtest/results" / CANON_FILE[symbol]).read_text(encoding="utf-8"))["measured"]
        units = sorted({(t["entry_time"], t["side"]) for t in canon["trades"]})
        events = []
        for etime, side in units:
            i = idx_of.get(etime)
            if i is None or i < warmup or not math.isfinite(atr[i - 1]) or i + MAXH >= n:
                continue
            events.append((i, side))

        eligible = []
        for i in range(warmup, n - MAXH):
            if not in_session(bars[i].time, cfg):
                continue
            if not math.isfinite(atr[i - 1]) or atr[i - 1] <= 0.0:
                continue
            if fast[i - 1] > slow[i - 1]:
                eligible.append((i, 1))
            elif fast[i - 1] < slow[i - 1]:
                eligible.append((i, -1))

        sym = {"n_events": len(events), "n_units_ledger": len(units),
               "n_eligible": len(eligible), "horizons": {}}
        for k in HORIZONS:
            act = [fwd(i, s, k) for i, s in events]
            act_mean = statistics.fmean(act)
            ctrl_all = [fwd(i, s, k) for i, s in eligible]
            means = []
            for p in range(N_PORTFOLIOS):
                rng = random.Random(SEED_TAG + 7919 * k + p)
                sample = rng.sample(range(len(eligible)), len(events))
                means.append(statistics.fmean(ctrl_all[j] for j in sample))
            ctrl_mu = statistics.fmean(means)
            sym["horizons"][f"h{k}"] = {
                "actual_mean_atr": act_mean,
                "control_portfolio_mean_atr": ctrl_mu,
                "excess_atr": act_mean - ctrl_mu,
                "percentile": pct_rank(act_mean, means),
                "population_control_mean_atr": statistics.fmean(ctrl_all),
            }
        report[symbol] = sym
        h4, h72 = sym["horizons"]["h4"], sym["horizons"]["h72"]
        print(f"{symbol}: n={len(events)} | +4h act {h4['actual_mean_atr']:+.3f} "
              f"exc {h4['excess_atr']:+.3f} pct {h4['percentile']:.1f} | "
              f"+72h act {h72['actual_mean_atr']:+.3f} exc {h72['excess_atr']:+.3f} "
              f"pct {h72['percentile']:.1f} (ctrl pop {h72['population_control_mean_atr']:+.3f})")

    out = REPO / "backtest/audit/verify_event_study_results.json"
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
