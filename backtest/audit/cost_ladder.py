#!/usr/bin/env python3
"""COST DECOMPOSITION audit for the FTMO Quant EA screening backtest.

Two independent decompositions (honesty rule: every headline number is derived
two ways):

A) RE-RUN COST LADDER (decisions adapt to the cost environment):
     rung 1  GROSS      zero spread + zero commission + zero swap
     rung 2  +SPREAD    fallback spread only (canonical points per symbol)
     rung 3  +COMM      spread + $2.50/side/lot commission
     rung 4  +SWAP      spread + commission + swap  (== canonical "measured")
   Deltas between rungs mix true cost with PATH DIVERGENCE (different trade
   sets), so a trade-set overlap diagnostic between rung 1 and rung 2 is
   reported alongside.

B) SAME-PATH DECOMPOSITION on the canonical (rung 4) fills:
     net = gross_ex_spread - spread_bill - commission - swap(+/-)
   where gross_ex_spread = sum of side*(exit-entry)*$/price + one spread per
   round-turn unit (spread_price * original_lots * $/price/lot: longs pay it
   at the ask entry, shorts pay it across their ask-space exits), commission
   and swap are the engine's own booked amounts recorded tranche-by-tranche.
   This holds the trade set fixed and is the authoritative attribution.

The engine source is patched AT LOAD TIME with one pure recording line inside
book() (records what book() already computed; changes nothing). Rung 4 must
reproduce the canonical results JSON to the cent or the script aborts.

Also: a spread response curve (net$ vs spread points, full costs) per symbol,
used both for the break-even spread answer and to expose how noisy/monotone
the response surface actually is; plus an analytic break-even estimate
(gross$ / spread-$-per-point) as the second derivation.

Stdlib only. No randomness anywhere in the engine or this script.

Run from repo root:  python backtest/audit/cost_ladder.py
Output: backtest/audit/cost_ladder_results.json (+ stdout tables)
"""

from __future__ import annotations

import copy
import json
import sys
import types
from pathlib import Path

REPO = Path(r"C:/Users/Sanique Richards/Documents/Homework Heroes/Pokemon/Bot-EA")
sys.path.insert(0, str(REPO))

ENGINE_PATH = REPO / "backtest" / "ftmo_quant_backtest.py"
DATA_DIR = REPO / "backtest" / "data" / "derivM15"
RESULTS_DIR = REPO / "backtest" / "results"
OUT_PATH = REPO / "backtest" / "audit" / "cost_ladder_results.json"

# Canonical run parameters (reproduced to the cent below before use).
SYMBOLS = {
    "EURUSD": {"fallback_pts": 10.0, "canonical_json": "EURUSD.json"},
    "GBPUSD": {"fallback_pts": 15.0, "canonical_json": "GBPUSD_relaxedgate.json"},
    "USDJPY": {"fallback_pts": 28.0, "canonical_json": "USDJPY.json"},
    "XAUUSD": {"fallback_pts": 16.0, "canonical_json": "XAUUSD.json"},
}
INITIAL = 100_000.0
SPLIT = 0.70
MAXSPREAD = 1e9  # gate never binds (spread is a constant fallback per run)
CURVE_PTS = [0, 4, 8, 12, 16, 20, 24, 28, 32, 36, 40]


def load_patched_engine() -> types.ModuleType:
    """Exec the engine source with ONE recording line added inside book()."""
    src = ENGINE_PATH.read_text(encoding="utf-8")
    anchor = ("        trades.append(\n            Trade(\n"
              "                unit.entry_time.isoformat(sep=\" \"),")
    if anchor not in src:
        raise RuntimeError("patch anchor not found in engine source")
    record = (
        "        UNIT_LEDGER.append((unit.entry_time.isoformat(sep=\" \"),"
        " unit.entry, unit.side, close_lots, unit.original_lots,"
        " unit.risk_budget, reported, gross, reported - pnl, reason,"
        " bar.time.isoformat(sep=\" \"), unit.entry_index >= split_index))\n"
    )
    src = src.replace(anchor, record + anchor, 1)
    src += "\nUNIT_LEDGER = []\n"
    mod = types.ModuleType("ftmo_engine_patched")
    mod.__file__ = str(ENGINE_PATH)
    sys.modules["ftmo_engine_patched"] = mod  # dataclass machinery needs this
    exec(compile(src, str(ENGINE_PATH), "exec"), mod.__dict__)
    return mod


ENG = load_patched_engine()
_M15_CACHE: dict[str, list] = {}


def bars_for(symbol: str, spread_pts: float, point: float):
    """H1 bars with a constant spread, from a cached zero-spread M15 parse."""
    if symbol not in _M15_CACHE:
        _M15_CACHE[symbol] = ENG.load_m15(DATA_DIR / f"{symbol}.csv", 0.0)
    spread = spread_pts * point
    m15 = [
        ENG.Bar(b.time, b.open, b.high, b.low, b.close, spread)
        for b in _M15_CACHE[symbol]
    ]
    return ENG.aggregate_h1(m15)


def strip_meta(meta: dict, *, commission: bool, swap: bool) -> dict:
    out = copy.deepcopy(meta)
    if not commission:
        out.pop("commission", None)
    if not swap:
        out.pop("swap", None)
    return out


def run(bars, meta) -> tuple[dict, list]:
    ENG.UNIT_LEDGER.clear()
    res = ENG.run_backtest(
        bars, meta, ENG.Config(),
        initial_balance=INITIAL, spread_multiplier=1.0,
        split_fraction=SPLIT, max_spread_points=MAXSPREAD,
    )
    return res, list(ENG.UNIT_LEDGER)


def group_units(ledger) -> dict:
    """Tranches -> round-turn units keyed by (entry_time, entry, side)."""
    units: dict = {}
    for (etime, entry, side, close_lots, orig_lots, rb, reported, gross,
         swap_part, reason, xtime, oos) in ledger:
        key = (etime, entry, side)
        u = units.setdefault(key, {
            "pnl": 0.0, "gross": 0.0, "swap": 0.0, "rb": rb,
            "lots": orig_lots, "closed": 0.0, "oos": oos,
        })
        assert abs(u["rb"] - rb) < 1e-9 and u["oos"] == oos
        u["pnl"] += reported
        u["gross"] += gross
        u["swap"] += swap_part
        u["closed"] += close_lots
    for u in units.values():
        assert abs(u["closed"] - u["lots"]) < 1e-9, "unit not fully closed"
        u["commission"] = 5.0 * u["lots"]  # 2 sides x $2.50/lot, engine formula
    return units


def bucket(units, pred) -> dict:
    sel = [u for u in units.values() if pred(u)]
    n = len(sel)
    rs = [u["pnl"] / u["rb"] for u in sel if u["rb"]]
    return {
        "units": n,
        "net_usd": sum(u["pnl"] for u in sel),
        "mean_unit_r": sum(rs) / len(rs) if rs else None,
        "mean_risk_budget_usd": (sum(u["rb"] for u in sel) / n) if n else None,
    }


def rung(symbol, spread_pts, *, commission, swap, meta_full, point) -> dict:
    bars = bars_for(symbol, spread_pts, point)
    meta = strip_meta(meta_full, commission=commission, swap=swap)
    res, ledger = run(bars, meta)
    units = group_units(ledger)
    dppl = float(meta_full["trade_tick_value_loss"]) / float(meta_full["trade_tick_size"])
    sum_lots = sum(u["lots"] for u in units.values())
    return {
        "spread_pts": spread_pts, "commission": commission, "swap": swap,
        "net_usd": res["ending_balance"] - INITIAL,
        "return_pct": res["return_pct"],
        "tranches": res["all"]["trades"],
        "tranche_expectancy_r": res["all"]["expectancy_r"],
        "pf_all": res["all"]["profit_factor"], "pf_oos": res["oos"]["profit_factor"],
        "all": bucket(units, lambda u: True),
        "is": bucket(units, lambda u: not u["oos"]),
        "oos": bucket(units, lambda u: u["oos"]),
        "sum_lots": sum_lots,
        "direct_spread_bill_usd": spread_pts * point * sum_lots * dppl,
        "direct_commission_bill_usd": 5.0 * sum_lots if commission else 0.0,
        "_units": units,  # stripped before JSON dump
    }


def same_path_decomposition(r4_units, spread_pts, point, dppl) -> dict:
    """Authoritative attribution on the canonical trade set (fills fixed)."""
    def agg(pred):
        sel = [u for u in r4_units.values() if pred(u)]
        n = len(sel)
        spread_bill = spread_pts * point * dppl * sum(u["lots"] for u in sel)
        gross_incl = sum(u["gross"] for u in sel)      # entry/exit price P&L (spread embedded)
        comm = sum(u["commission"] for u in sel)
        swap = sum(u["swap"] for u in sel)             # engine-booked (credit > 0 possible)
        net = sum(u["pnl"] for u in sel)
        rb = sum(u["rb"] for u in sel)
        # reconciliation: net == gross - comm + swap  (engine identity)
        assert abs(net - (gross_incl - comm + swap)) < 0.01, (net, gross_incl, comm, swap)
        out = {
            "units": n,
            "gross_ex_spread_usd": gross_incl + spread_bill,
            "spread_bill_usd": spread_bill,
            "commission_usd": comm,
            "swap_usd_signed": -swap,  # positive = cost, negative = credit
            "net_usd": net,
        }
        if n and rb:
            mrb = rb / n
            out["per_unit_r"] = {
                "gross_ex_spread": out["gross_ex_spread_usd"] / rb,
                "spread": spread_bill / rb,
                "commission": comm / rb,
                "swap": -swap / rb,
                "net": net / rb,
            }
            out["mean_risk_budget_usd"] = mrb
        return out
    return {
        "all": agg(lambda u: True),
        "is": agg(lambda u: not u["oos"]),
        "oos": agg(lambda u: u["oos"]),
    }


def overlap(units_a, units_b) -> dict:
    """How much of the trade set survives a cost change (keyed by entry_time,
    side; entry price differs by construction when spread changes)."""
    ka = {(k[0], k[2]) for k in units_a}
    kb = {(k[0], k[2]) for k in units_b}
    common = ka & kb
    pnl_a_common = sum(u["pnl"] for k, u in units_a.items() if (k[0], k[2]) in common)
    pnl_b_common = sum(u["pnl"] for k, u in units_b.items() if (k[0], k[2]) in common)
    pnl_a_only = sum(u["pnl"] for k, u in units_a.items() if (k[0], k[2]) not in common)
    pnl_b_only = sum(u["pnl"] for k, u in units_b.items() if (k[0], k[2]) not in common)
    return {
        "units_a": len(ka), "units_b": len(kb), "common": len(common),
        "pnl_a_common": pnl_a_common, "pnl_b_common": pnl_b_common,
        "pnl_a_only": pnl_a_only, "pnl_b_only": pnl_b_only,
    }


def main() -> None:
    broker = json.loads((REPO / "backtest" / "deriv_broker_meta.json").read_text(encoding="utf-8"))
    out = {"initial_balance": INITIAL, "split": SPLIT, "symbols": {}}

    for symbol, info in SYMBOLS.items():
        meta_full = broker["symbols"][symbol]
        point = float(meta_full["point"])
        dppl = float(meta_full["trade_tick_value_loss"]) / float(meta_full["trade_tick_size"])
        fb = info["fallback_pts"]

        # ---- rung 4 first: verify canonical reproduction to the cent ----
        r4 = rung(symbol, fb, commission=True, swap=True, meta_full=meta_full, point=point)
        canon = json.loads((RESULTS_DIR / info["canonical_json"]).read_text(encoding="utf-8"))
        cm = canon["measured"]
        assert abs(r4["net_usd"] - cm["all"]["net_profit"]) < 0.01, (
            symbol, r4["net_usd"], cm["all"]["net_profit"])
        assert r4["tranches"] == cm["all"]["trades"]
        print(f"{symbol}: rung-4 reproduces canonical measured "
              f"(net ${r4['net_usd']:.2f}, {r4['tranches']} tranches) OK")

        r1 = rung(symbol, 0.0, commission=False, swap=False, meta_full=meta_full, point=point)
        r2 = rung(symbol, fb, commission=False, swap=False, meta_full=meta_full, point=point)
        r3 = rung(symbol, fb, commission=True, swap=False, meta_full=meta_full, point=point)

        same_path = same_path_decomposition(r4["_units"], fb, point, dppl)
        ov12 = overlap(r1["_units"], r2["_units"])

        # spread response curve, full costs (commission + swap on)
        curve = []
        for pts in CURVE_PTS:
            rr = rung(symbol, float(pts), commission=True, swap=True,
                      meta_full=meta_full, point=point)
            curve.append({"spread_pts": pts, "net_usd": rr["net_usd"],
                          "units": rr["all"]["units"]})

        # analytic break-even (second derivation): gross at canonical path
        # divided by the direct spread bill per point on the canonical lots.
        g = same_path["all"]
        bill_per_pt = point * dppl * sum(u["lots"] for u in r4["_units"].values())
        analytic_be = (
            (g["gross_ex_spread_usd"] - g["commission_usd"] - g["swap_usd_signed"])
            / bill_per_pt if bill_per_pt else None
        )

        ladder = {}
        for name, r in (("1_gross", r1), ("2_plus_spread", r2),
                        ("3_plus_commission", r3), ("4_plus_swap_canonical", r4)):
            ladder[name] = {k: v for k, v in r.items() if k != "_units"}

        deltas = {
            "rerun_spread_cost_usd": r1["net_usd"] - r2["net_usd"],
            "rerun_commission_cost_usd": r2["net_usd"] - r3["net_usd"],
            "rerun_swap_cost_usd": r3["net_usd"] - r4["net_usd"],
            "rerun_total_cost_usd": r1["net_usd"] - r4["net_usd"],
            "note": ("re-run deltas mix cost with path divergence; "
                     "same_path block is the authoritative attribution"),
        }

        out["symbols"][symbol] = {
            "fallback_spread_pts": fb, "point": point, "dollars_per_price_per_lot": dppl,
            "ladder": ladder,
            "rerun_deltas": deltas,
            "same_path": same_path,
            "rung1_vs_rung2_trade_set_overlap": ov12,
            "spread_response_curve_full_costs": curve,
            "analytic_breakeven_spread_pts_same_path": analytic_be,
        }

        def fmt(r):
            return (f"net ${r['net_usd']:>10.2f}  units {r['all']['units']:>3d}  "
                    f"unitR {r['all']['mean_unit_r']:>8.4f}  "
                    f"IS ${r['is']['net_usd']:>9.2f} (R {r['is']['mean_unit_r']:>8.4f})  "
                    f"OOS ${r['oos']['net_usd']:>9.2f} (R {r['oos']['mean_unit_r']:>8.4f})")
        print(f"  1 GROSS   {fmt(r1)}")
        print(f"  2 +SPREAD {fmt(r2)}")
        print(f"  3 +COMM   {fmt(r3)}")
        print(f"  4 +SWAP   {fmt(r4)}")
        sp = same_path["all"]
        print(f"  SAME-PATH (canonical fills): gross_ex_spread ${sp['gross_ex_spread_usd']:.2f} "
              f"- spread ${sp['spread_bill_usd']:.2f} - comm ${sp['commission_usd']:.2f} "
              f"- swap ${sp['swap_usd_signed']:.2f} = net ${sp['net_usd']:.2f}")
        pr = sp["per_unit_r"]
        print(f"  per-unit R: gross {pr['gross_ex_spread']:+.4f} spread -{pr['spread']:.4f} "
              f"comm -{pr['commission']:.4f} swap {-pr['swap']:+.4f} net {pr['net']:+.4f}")
        for scope in ("is", "oos"):
            s = same_path[scope]
            print(f"    {scope.upper():>3}: gross_ex_spread ${s['gross_ex_spread_usd']:>9.2f} "
                  f"net ${s['net_usd']:>9.2f} "
                  f"(R gross {s['per_unit_r']['gross_ex_spread']:+.4f} net {s['per_unit_r']['net']:+.4f})")
        print(f"  overlap rung1 vs rung2: {ov12['common']}/{ov12['units_a']} vs {ov12['units_b']} common; "
              f"pnl common {ov12['pnl_a_common']:.0f} -> {ov12['pnl_b_common']:.0f}; "
              f"only-in-1 {ov12['pnl_a_only']:.0f}, only-in-2 {ov12['pnl_b_only']:.0f}")
        print(f"  spread curve (full costs): "
              + " ".join(f"{c['spread_pts']}:{c['net_usd']:.0f}" for c in curve))
        print(f"  analytic same-path breakeven spread: "
              f"{analytic_be:.1f} pts (canonical modeled: {fb} pts)")
        print()

    OUT_PATH.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"written {OUT_PATH}")


if __name__ == "__main__":
    main()
