#!/usr/bin/env python3
"""Audit step 0: reproduce the canonical 'measured' path for all four symbols
before running any counterfactual. If these numbers do not match the canonical
ledgers exactly, every downstream comparison is void.

Canonical ledgers: backtest/results/{EURUSD,GBPUSD_relaxedgate,USDJPY,XAUUSD}.json
Fallback spreads (points): EURUSD 10, GBPUSD 15, USDJPY 28, XAUUSD 16.
max_spread_points is irrelevant at 1x constant fallback spread as long as it is
>= the fallback; we use 100 and verify equality against the stored ledgers.
"""
import json
import sys
import time
from pathlib import Path

REPO = Path(r"C:/Users/Sanique Richards/Documents/Homework Heroes/Pokemon/Bot-EA")
sys.path.insert(0, str(REPO))

from backtest.ftmo_quant_backtest import Config, run_backtest, aggregate_h1, load_m15

FALLBACK_PTS = {"EURUSD": 10.0, "GBPUSD": 15.0, "USDJPY": 28.0, "XAUUSD": 16.0}
CANON_FILE = {
    "EURUSD": "EURUSD.json",
    "GBPUSD": "GBPUSD_relaxedgate.json",
    "USDJPY": "USDJPY.json",
    "XAUUSD": "XAUUSD.json",
}


def main() -> None:
    broker = json.loads((REPO / "backtest/deriv_broker_meta.json").read_text(encoding="utf-8"))
    ok = True
    for symbol, pts in FALLBACK_PTS.items():
        meta = broker["symbols"][symbol]
        fallback = pts * float(meta["point"])
        bars = aggregate_h1(load_m15(REPO / f"backtest/data/derivM15/{symbol}.csv", fallback))
        t0 = time.perf_counter()
        res = run_backtest(
            bars,
            meta,
            Config(),
            initial_balance=100_000.0,
            spread_multiplier=1.0,
            split_fraction=0.70,
            max_spread_points=100.0,
        )
        dt = time.perf_counter() - t0
        canon = json.loads((REPO / "backtest/results" / CANON_FILE[symbol]).read_text(encoding="utf-8"))["measured"]
        match_ret = abs(res["return_pct"] - canon["return_pct"]) < 1e-9
        match_n = res["all"]["trades"] == canon["all"]["trades"]
        # trade-by-trade equality
        match_trades = res["trades"] == canon["trades"]
        ok = ok and match_ret and match_n and match_trades
        print(
            f"{symbol}: repro ret {res['return_pct']:+.4f}% vs canon {canon['return_pct']:+.4f}% "
            f"| trades {res['all']['trades']} vs {canon['all']['trades']} "
            f"| ledger identical: {match_trades} | {dt:.2f}s/run | bars {res['bars']}"
        )
    print("ALL MATCH" if ok else "MISMATCH - STOP")


if __name__ == "__main__":
    main()
