#!/usr/bin/env python3
"""Exit-leak audit, step 0: reproduce the canonical ledgers exactly.

Finds, per symbol, the max_spread_points that makes a fresh run_backtest()
produce a trade ledger identical to the canonical results JSON. Everything
downstream (unit grouping, counterfactual exits, engine variants) relies on
this reproduction being exact.
"""
import json
import sys
from pathlib import Path

REPO = Path(r"C:/Users/Sanique Richards/Documents/Homework Heroes/Pokemon/Bot-EA")
sys.path.insert(0, str(REPO))

from backtest.ftmo_quant_backtest import (  # noqa: E402
    Config, run_backtest, aggregate_h1, load_m15,
)

CANON = {
    "EURUSD": ("EURUSD.json", 10.0),
    "GBPUSD": ("GBPUSD_relaxedgate.json", 15.0),
    "USDJPY": ("USDJPY.json", 28.0),
    "XAUUSD": ("XAUUSD.json", 16.0),
}

def trades_key(trades):
    return [
        (t["entry_time"], t["exit_time"], t["side"], round(t["entry"], 10),
         round(t["exit"], 10), round(t["pnl"], 6), round(t["r"], 9), t["reason"])
        for t in trades
    ]

def main():
    broker = json.loads((REPO / "backtest/deriv_broker_meta.json").read_text())
    out = {}
    for sym, (fname, fb_pts) in CANON.items():
        meta = broker["symbols"][sym]
        canon = json.loads((REPO / "backtest/results" / fname).read_text())
        canon_trades = canon["measured"]["trades"]
        fb_spread = fb_pts * float(meta["point"])
        bars = aggregate_h1(load_m15(REPO / "backtest/data/derivM15" / f"{sym}.csv", fb_spread))
        found = None
        for msp in (25.0, 30.0, 35.0, 40.0, 50.0, 100.0, 1e9):
            res = run_backtest(bars, meta, Config(), initial_balance=100_000.0,
                               spread_multiplier=1.0, split_fraction=0.70,
                               max_spread_points=msp)
            if trades_key(res["trades"]) == trades_key(canon_trades):
                found = (msp, res["return_pct"], canon["measured"]["return_pct"])
                break
        print(sym, "->", found if found else "NO MATCH", flush=True)
        if found:
            out[sym] = {"file": fname, "fallback_spread_points": fb_pts,
                        "max_spread_points": found[0],
                        "return_pct_repro": found[1], "return_pct_canon": found[2]}
    (REPO / "backtest/audit/exit_leak_00_repro_params.json").write_text(
        json.dumps(out, indent=2))
    print(json.dumps(out, indent=2))

if __name__ == "__main__":
    main()
