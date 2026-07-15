#!/usr/bin/env python3
"""Adversarial verification (independent re-derivation) of the beta-regime
analyst's LEDGER-derived claims. Reads only the canonical results JSONs; all
statistics are recomputed from scratch with fresh code.

Claims checked here:
  A1. 4-symbol total net = -$2,654.44 (EURUSD +1,062.49, GBPUSD_relaxedgate
      -1,232.75, USDJPY -4,213.07, XAUUSD +1,728.90).
  A2. EURUSD side decomposition: long -$2,742 (68 units, PF 0.72),
      short +$3,804 (32 units, PF 2.19).
  A3. EURUSD OOS: +$1,899 of the +$2,278 OOS net from 5 short units (83%).
  A4. EURUSD windows: 2023 shorts +$1,949, 2026 shorts +$1,899, 2024 -$2,114.
  A5. EURUSD bootstrap p(net<=0) ~ 0.369; top-3 units = +$1,805 = 170% of net.
  A6. XAUUSD: 2025 net -$1,246, OOS net -$899 (PF 0.86), long side 145/158
      units PF 1.04, short +$985 over 13 units; bootstrap p ~ 0.325; top-3 =
      118% of net.
  A7. USDJPY: long PF 0.79 / short PF 0.21, 2024 longs -$1,546, p ~ 0.93.
  A8. GBPUSD: long +$172 (PF 1.01) / short -$1,405 (PF 0.66), p ~ 0.65.

Units are reconstructed by grouping scale-out tranches on
(entry_time, side, entry). Bootstrap: resample units iid with replacement;
canonical run = 10,000 resamples seed 12345, robustness = 100 seeds x 2,000.
Stdlib only.
"""

from __future__ import annotations

import json
import random
from pathlib import Path

REPO = Path(r"C:/Users/Sanique Richards/Documents/Homework Heroes/Pokemon/Bot-EA")
RESULTS = REPO / "backtest" / "results"

LEDGERS = {
    "EURUSD": "EURUSD.json",
    "GBPUSD": "GBPUSD_relaxedgate.json",
    "USDJPY": "USDJPY.json",
    "XAUUSD": "XAUUSD.json",
}


def pf(pnls):
    wins = sum(p for p in pnls if p > 0)
    losses = -sum(p for p in pnls if p < 0)
    return round(wins / losses, 3) if losses else None


def build_units(trades):
    """Group tranches into units keyed by (entry_time, side, entry)."""
    units = {}
    for t in trades:
        key = (t["entry_time"], t["side"], t["entry"])
        u = units.setdefault(
            key,
            {"entry_time": t["entry_time"], "side": t["side"], "pnl": 0.0,
             "risk": 0.0, "oos": t["oos"], "exit_years": set()},
        )
        u["pnl"] += t["pnl"]
        # tranche risk = pnl / r (engine: r = reported / tranche_risk);
        # summing tranche risks over a fully-closed unit returns the unit's
        # full risk budget.
        if t["r"]:
            u["risk"] += t["pnl"] / t["r"]
        u["exit_years"].add(t["exit_time"][:4])
    return list(units.values())


def bootstrap(pnls, n_resamples, seed):
    rng = random.Random(seed)
    n = len(pnls)
    neg = 0
    for _ in range(n_resamples):
        s = 0.0
        for _ in range(n):
            s += pnls[rng.randrange(n)]
        if s <= 0.0:
            neg += 1
    return neg / n_resamples


def main():
    out = {}
    total_net = 0.0
    for symbol, fname in LEDGERS.items():
        d = json.loads((RESULTS / fname).read_text(encoding="utf-8"))
        m = d["measured"]
        trades = m["trades"]
        net = m["all"]["net_profit"]
        total_net += net

        units = build_units(trades)
        upnls = [u["pnl"] for u in units]
        longs = [u for u in units if u["side"] > 0]
        shorts = [u for u in units if u["side"] < 0]

        # per-year unit attribution: assign unit to the year of its LAST exit
        tranche_year = {}
        for t in trades:
            key = (t["entry_time"], t["side"], t["entry"])
            y = t["exit_time"][:4]
            tranche_year[key] = max(tranche_year.get(key, y), y)
        year_side = {}
        for u, key in zip(units, [(u["entry_time"], u["side"], None) for u in units]):
            pass  # placeholder; year attribution done below directly on tranches

        # simpler + exact: yearly pnl by tranche exit year, split by side
        yearly = {}
        for t in trades:
            y = t["exit_time"][:4]
            side = "long" if t["side"] > 0 else "short"
            yearly.setdefault(y, {"long": 0.0, "short": 0.0})
            yearly[y][side] += t["pnl"]

        # OOS decomposition
        oos_units = [u for u in units if u["oos"]]
        oos_net = sum(u["pnl"] for u in oos_units)
        oos_short = [u for u in oos_units if u["side"] < 0]
        oos_trades = [t for t in trades if t["oos"]]

        # top-3 concentration
        top3 = sorted(upnls, reverse=True)[:3]
        top3_sum = sum(p for p in top3 if p > 0)

        # bootstrap: canonical + 100-seed robustness
        p_canon = bootstrap(upnls, 10_000, 12345)
        ps = [bootstrap(upnls, 2_000, s) for s in range(1, 101)]
        p_mean = sum(ps) / len(ps)

        out[symbol] = {
            "ledger_net": round(net, 2),
            "ledger_oos_net": round(m["oos"]["net_profit"], 2),
            "ledger_oos_pf": round(m["oos"]["profit_factor"], 3) if m["oos"]["profit_factor"] else None,
            "ledger_is_pf": None,
            "max_equity_dd_pct": round(m["max_equity_drawdown_pct"], 2),
            "n_tranches": len(trades),
            "n_units": len(units),
            "units_long": len(longs),
            "units_short": len(shorts),
            "long_net": round(sum(u["pnl"] for u in longs), 2),
            "short_net": round(sum(u["pnl"] for u in shorts), 2),
            "long_pf_tranches": pf([t["pnl"] for t in trades if t["side"] > 0]),
            "short_pf_tranches": pf([t["pnl"] for t in trades if t["side"] < 0]),
            "yearly_by_side": {y: {k: round(v, 2) for k, v in s.items()} for y, s in sorted(yearly.items())},
            "oos_net_from_units": round(oos_net, 2),
            "oos_short_units": len(oos_short),
            "oos_short_net": round(sum(u["pnl"] for u in oos_short), 2),
            "top3_unit_pnls": [round(p, 2) for p in top3],
            "top3_sum": round(top3_sum, 2),
            "top3_pct_of_net": round(100.0 * top3_sum / net, 1) if net else None,
            "bootstrap_p_net_le_0_seed12345_10k": round(p_canon, 4),
            "bootstrap_p_mean_100seeds_2k": round(p_mean, 4),
            "bootstrap_p_min_max": [round(min(ps), 4), round(max(ps), 4)],
            "mean_unit_r": round(sum(u["pnl"] / u["risk"] for u in units if u["risk"]) / len(units), 4),
        }
        # IS PF for completeness
        is_trades = [t for t in trades if not t["oos"]]
        out[symbol]["ledger_is_pf"] = pf([t["pnl"] for t in is_trades])

    out["TOTAL_net_4_symbols"] = round(total_net, 2)

    dest = REPO / "backtest" / "audit" / "verify_beta_A_results.json"
    dest.write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
