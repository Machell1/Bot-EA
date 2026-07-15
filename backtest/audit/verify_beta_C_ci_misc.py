#!/usr/bin/env python3
"""Residual spot-checks for the beta-regime verification:
  C1. EURUSD unit bootstrap CI95 (claimed [-5,061, +7,103], seed-independent
      to sampling error) - recomputed with a fresh seed.
  C2. Plain GBPUSD ledger net (caveat f: -1.01%).
  C3. XAUUSD DD-normalized comparison: EA $/DD-pt vs cross baseline $/DD-pt
      (claimed ~9x in the baseline's favor).
Stdlib only.
"""

from __future__ import annotations

import json
import random
from pathlib import Path

REPO = Path(r"C:/Users/Sanique Richards/Documents/Homework Heroes/Pokemon/Bot-EA")


def units_from(fname):
    d = json.loads((REPO / "backtest" / "results" / fname).read_text(encoding="utf-8"))
    m = d["measured"]
    units = {}
    for t in m["trades"]:
        key = (t["entry_time"], t["side"], t["entry"])
        units[key] = units.get(key, 0.0) + t["pnl"]
    return m, list(units.values())


def main():
    out = {}

    # C1: EURUSD CI95, fresh seed 777, 20,000 resamples
    _, upnls = units_from("EURUSD.json")
    rng = random.Random(777)
    n = len(upnls)
    sums = []
    for _ in range(20_000):
        sums.append(sum(upnls[rng.randrange(n)] for _ in range(n)))
    sums.sort()
    out["eurusd_ci95_net"] = [round(sums[int(0.025 * len(sums))], 2),
                              round(sums[int(0.975 * len(sums))], 2)]
    out["eurusd_p_net_le_0"] = round(sum(1 for s in sums if s <= 0.0) / len(sums), 4)

    # C2: plain GBPUSD ledger
    d = json.loads((REPO / "backtest" / "results" / "GBPUSD.json").read_text(encoding="utf-8"))
    out["gbpusd_plain_net"] = round(d["measured"]["all"]["net_profit"], 2)
    out["gbpusd_plain_return_pct"] = round(d["measured"]["return_pct"], 3)

    # C3: XAUUSD DD-normalized ratio (EA ledger vs my independent baseline run)
    xm, _ = units_from("XAUUSD.json")
    ea_net = xm["all"]["net_profit"]
    ea_dd = xm["max_equity_drawdown_pct"]
    b = json.loads((REPO / "backtest" / "audit" / "verify_beta_B_results.json").read_text(encoding="utf-8"))
    base = b["XAUUSD"]["cross_long_flat"]
    ea_ratio = ea_net / ea_dd
    base_ratio = base["net"] / base["max_dd_pct_closemark"]
    out["xauusd_ea_usd_per_dd_pt"] = round(ea_ratio, 1)
    out["xauusd_cross_usd_per_dd_pt"] = round(base_ratio, 1)
    out["xauusd_dd_normalized_ratio"] = round(base_ratio / ea_ratio, 2)

    dest = REPO / "backtest" / "audit" / "verify_beta_C_results.json"
    dest.write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
