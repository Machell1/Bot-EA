#!/usr/bin/env python3
"""Trend-beta / regime-luck audit, part 1: canonical-ledger decomposition.

Reads the canonical 'measured' ledgers (FTMO-guarded, 1x spread) and answers:
  (1) long/short decomposition: net / PF / unit count per side, and whether the
      profitable side matches the underlying's buy-and-hold drift over the
      identical window;
  (3) concentration: share of full-period net carried by the top 3 units, plus
      a seeded bootstrap (10,000 resamples) of the unit-level R series for the
      probability that the observed net is indistinguishable from zero;
  (4) time stability: yearly nets, overall and per side, next to the symbol's
      yearly buy-and-hold drift.

Units are reconstructed from scale-out tranches by (entry_time, side, entry).
Unit risk budget is recovered exactly as sum(pnl_i / r_i) over the unit's
tranches (each tranche's r = pnl / (risk_budget * lot_fraction) and the lot
fractions of a unit sum to 1; the ledgers contain no zero-r tranches, verified
before use). Buy-and-hold drift uses the same H1 bars the engine trades.

Stdlib only. Deterministic: bootstrap seeded per symbol.
"""

from __future__ import annotations

import json
import random
import sys
from pathlib import Path

REPO = Path(r"C:/Users/Sanique Richards/Documents/Homework Heroes/Pokemon/Bot-EA")
sys.path.insert(0, str(REPO))

from backtest.ftmo_quant_backtest import aggregate_h1, load_m15  # noqa: E402

LEDGERS = {
    "EURUSD": ("EURUSD.json", 10.0),
    "GBPUSD": ("GBPUSD_relaxedgate.json", 15.0),
    "USDJPY": ("USDJPY.json", 28.0),
    "XAUUSD": ("XAUUSD.json", 16.0),
}
BOOTSTRAP_N = 10_000
SEED_BASE = 20260714


def side_summary(tranches: list[dict]) -> dict:
    profits = [t["pnl"] for t in tranches if t["pnl"] > 0.0]
    losses = [-t["pnl"] for t in tranches if t["pnl"] < 0.0]
    return {
        "tranches": len(tranches),
        "net": round(sum(t["pnl"] for t in tranches), 2),
        "gross_profit": round(sum(profits), 2),
        "gross_loss": round(sum(losses), 2),
        "profit_factor": round(sum(profits) / sum(losses), 3) if losses else None,
    }


def build_units(trades: list[dict]) -> list[dict]:
    groups: dict[tuple, list[dict]] = {}
    for t in trades:
        groups.setdefault((t["entry_time"], t["side"], t["entry"]), []).append(t)
    units = []
    for (entry_time, side, entry), ts in sorted(groups.items()):
        assert all(abs(t["r"]) > 1e-12 for t in ts), "zero-r tranche breaks risk recovery"
        risk_budget = sum(t["pnl"] / t["r"] for t in ts)
        pnl = sum(t["pnl"] for t in ts)
        units.append(
            {
                "entry_time": entry_time,
                "exit_time": max(t["exit_time"] for t in ts),
                "side": side,
                "pnl": pnl,
                "risk_budget": risk_budget,
                "r": pnl / risk_budget,
                "oos": ts[0]["oos"],
                "year": max(t["exit_time"] for t in ts)[:4],
            }
        )
    return units


def bootstrap_net(units: list[dict], seed: int) -> dict:
    rng = random.Random(seed)
    pnls = [u["pnl"] for u in units]
    n = len(pnls)
    nets = []
    for _ in range(BOOTSTRAP_N):
        nets.append(sum(pnls[rng.randrange(n)] for _ in range(n)))
    nets.sort()
    observed = sum(pnls)
    p_le_zero = sum(1 for x in nets if x <= 0.0) / BOOTSTRAP_N
    return {
        "resamples": BOOTSTRAP_N,
        "seed": seed,
        "observed_net": round(observed, 2),
        "p_resampled_net_le_zero": round(p_le_zero, 4),
        "ci95_net": [round(nets[int(0.025 * BOOTSTRAP_N)], 2), round(nets[int(0.975 * BOOTSTRAP_N)], 2)],
        "mean_unit_r": round(sum(u["r"] for u in units) / n, 4),
    }


def drift(bars, t0: str | None = None, t1: str | None = None) -> dict | None:
    sel = [
        b
        for b in bars
        if (t0 is None or b.time.isoformat(sep=" ") >= t0)
        and (t1 is None or b.time.isoformat(sep=" ") <= t1)
    ]
    if len(sel) < 2:
        return None
    ret = 100.0 * (sel[-1].close / sel[0].close - 1.0)
    return {"from": sel[0].time.isoformat(sep=" "), "to": sel[-1].time.isoformat(sep=" "), "bh_return_pct": round(ret, 2)}


def main() -> None:
    out: dict = {}
    for symbol, (ledger_name, fallback_pts) in LEDGERS.items():
        doc = json.loads((REPO / "backtest" / "results" / ledger_name).read_text(encoding="utf-8"))
        measured = doc["measured"]
        trades = measured["trades"]
        meta_point = {"EURUSD": 1e-05, "GBPUSD": 1e-05, "USDJPY": 0.001, "XAUUSD": 0.01}[symbol]
        bars = aggregate_h1(load_m15(REPO / "backtest" / "data" / "derivM15" / f"{symbol}.csv", fallback_pts * meta_point))

        units = build_units(trades)
        longs = [t for t in trades if t["side"] == 1]
        shorts = [t for t in trades if t["side"] == -1]
        long_units = [u for u in units if u["side"] == 1]
        short_units = [u for u in units if u["side"] == -1]

        # (4) yearly nets, overall and by side, next to yearly buy-and-hold
        years = sorted({u["year"] for u in units})
        yearly = {}
        for y in years:
            yearly[y] = {
                "net": round(sum(u["pnl"] for u in units if u["year"] == y), 2),
                "long_net": round(sum(u["pnl"] for u in units if u["year"] == y and u["side"] == 1), 2),
                "short_net": round(sum(u["pnl"] for u in units if u["year"] == y and u["side"] == -1), 2),
                "units": sum(1 for u in units if u["year"] == y),
                "bh_return_pct": (drift(bars, f"{y}-01-01", f"{y}-12-31") or {}).get("bh_return_pct"),
            }

        # (3) concentration
        net = sum(u["pnl"] for u in units)
        top3 = sorted(units, key=lambda u: u["pnl"], reverse=True)[:3]
        top3_sum = sum(u["pnl"] for u in top3)
        bottom3 = sorted(units, key=lambda u: u["pnl"])[:3]

        out[symbol] = {
            "ledger": ledger_name,
            "window": {"from": measured["from"], "to": measured["to"], "split_time": measured["split_time"]},
            "net_full": round(net, 2),
            "return_pct": round(measured["return_pct"], 3),
            "long_short": {
                "long": {**side_summary(longs), "units": len(long_units), "unit_sum_r": round(sum(u["r"] for u in long_units), 2)},
                "short": {**side_summary(shorts), "units": len(short_units), "unit_sum_r": round(sum(u["r"] for u in short_units), 2)},
            },
            "buy_and_hold": {
                "full": drift(bars),
                "is": drift(bars, None, measured["split_time"]),
                "oos": drift(bars, measured["split_time"], None),
            },
            "concentration": {
                "units_total": len(units),
                "top3_units_net": round(top3_sum, 2),
                "top3_share_of_net_pct": round(100.0 * top3_sum / net, 1) if net else None,
                "top3_detail": [
                    {"entry": u["entry_time"], "side": u["side"], "pnl": round(u["pnl"], 2), "r": round(u["r"], 2)} for u in top3
                ],
                "bottom3_detail": [
                    {"entry": u["entry_time"], "side": u["side"], "pnl": round(u["pnl"], 2), "r": round(u["r"], 2)} for u in bottom3
                ],
            },
            "bootstrap": bootstrap_net(units, SEED_BASE + sum(ord(c) for c in symbol)),
            "yearly": yearly,
        }

        # cross-check: tranche net must equal unit net and the ledger summary
        assert abs(net - measured["all"]["net_profit"]) < 0.01, (net, measured["all"]["net_profit"])

    dest = REPO / "backtest" / "audit" / "beta_regime_01_decomposition_results.json"
    dest.write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
