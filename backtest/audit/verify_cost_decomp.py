#!/usr/bin/env python3
"""ADVERSARIAL VERIFICATION of the cost-decomposition audit (cost_ladder.py).

Independent re-derivation of the load-bearing numbers using a DIFFERENT
instrumentation mechanism than the prior analyst:

  * Prior analyst: exec()'d a source-patched engine with a recording line
    inside book().
  * This script: imports the REAL module `backtest.ftmo_quant_backtest` and
    monkeypatches the module-level Position dataclass with a recording
    subclass (captures entry_time/side/entry/original_lots/risk_budget at
    construction). Trades are grouped into round-turn units from the engine's
    own returned ledger, keyed (entry_time, side, entry), and matched to the
    recorded risk budgets. No engine source is modified.

Verified claims:
  1. Rung-4 (fallback spread + commission + swap) reproduces each canonical
     results JSON to the cent, tranche-by-tranche.
  2. Frictionless re-run (zero spread, no commission, no swap) gross:
     net $, unit count, mean per-unit R, and the IS/OOS split.
  3. Same-path cost attribution on the CANONICAL PUBLISHED ledger
     (backtest/results/*.json trades, not a re-run): tranche lots are
     reconstructed from pnl/r (unit_risk_i = pnl_i / r_i; lots_i =
     orig_lots * unit_risk_i / risk_budget), then
        price_pnl_i = side * (exit - entry) * lots_i * dppl
        commission_i = 2 * 2.5 * lots_i
        swap_i = pnl_i - (price_pnl_i - commission_i)
     with reconciliation checks (sum lots_i == orig_lots; swap only on the
     final tranche; totals == canonical net to the cent).
     gross_ex_spread = price_pnl + spread_pts * point * dppl * orig_lots.
  4. EURUSD break-even spread: full-cost re-runs on a grid around 20 pts +
     the analytic same-path estimate.
  5. XAUUSD non-monotone spread response (net at 0/8/16 pts, full costs).

Stdlib only; the engine and this script are fully deterministic (no RNG).
Run from anywhere:  python backtest/audit/verify_cost_decomp.py
Output: backtest/audit/verify_cost_decomp_results.json (+ stdout)
"""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

REPO = Path(r"C:/Users/Sanique Richards/Documents/Homework Heroes/Pokemon/Bot-EA")
sys.path.insert(0, str(REPO))

import backtest.ftmo_quant_backtest as eng  # noqa: E402

DATA_DIR = REPO / "backtest" / "data" / "derivM15"
RESULTS_DIR = REPO / "backtest" / "results"
OUT_PATH = REPO / "backtest" / "audit" / "verify_cost_decomp_results.json"

SYMBOLS = {
    "EURUSD": {"fallback_pts": 10.0, "canonical_json": "EURUSD.json"},
    "GBPUSD": {"fallback_pts": 15.0, "canonical_json": "GBPUSD_relaxedgate.json"},
    "USDJPY": {"fallback_pts": 28.0, "canonical_json": "USDJPY.json"},
    "XAUUSD": {"fallback_pts": 16.0, "canonical_json": "XAUUSD.json"},
}
INITIAL = 100_000.0
SPLIT = 0.70

# ---------------------------------------------------------------- recording
POS_LOG: list[tuple] = []
_RealPosition = eng.Position


class RecordingPosition(_RealPosition):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        POS_LOG.append(
            (
                self.entry_time.isoformat(sep=" "),
                self.side,
                self.entry,
                self.original_lots,
                self.risk_budget,
                self.entry_index,
            )
        )


eng.Position = RecordingPosition

_M15_CACHE: dict[str, list] = {}


def bars_for(symbol: str, spread_pts: float, point: float):
    if symbol not in _M15_CACHE:
        _M15_CACHE[symbol] = eng.load_m15(DATA_DIR / f"{symbol}.csv", 0.0)
    spread = spread_pts * point
    m15 = [eng.Bar(b.time, b.open, b.high, b.low, b.close, spread) for b in _M15_CACHE[symbol]]
    return eng.aggregate_h1(m15)


def make_meta(meta_full: dict, *, commission: bool, swap: bool) -> dict:
    out = copy.deepcopy(meta_full)
    if not commission:
        out.pop("commission", None)
    if not swap:
        out.pop("swap", None)
    return out


def run(symbol: str, spread_pts: float, meta_full: dict, *, commission: bool, swap: bool):
    point = float(meta_full["point"])
    bars = bars_for(symbol, spread_pts, point)
    meta = make_meta(meta_full, commission=commission, swap=swap)
    POS_LOG.clear()
    res = eng.run_backtest(
        bars,
        meta,
        eng.Config(),
        initial_balance=INITIAL,
        spread_multiplier=1.0,
        split_fraction=SPLIT,
        max_spread_points=1e9,
        enforce_ftmo_guards=True,
    )
    return res, list(POS_LOG)


def unit_stats(trades: list[dict], poslog: list[tuple]) -> dict:
    """Group tranches into round-turn units and attach recorded risk budgets."""
    reg = {(t[0], t[1], t[2]): {"lots": t[3], "rb": t[4]} for t in poslog}
    if len(reg) != len(poslog):
        raise RuntimeError("duplicate unit keys in position log")
    units: dict = {}
    for t in trades:
        key = (t["entry_time"], t["side"], t["entry"])
        u = units.setdefault(key, {"pnl": 0.0, "oos": t["oos"], "tranches": []})
        assert u["oos"] == t["oos"]
        u["pnl"] += t["pnl"]
        u["tranches"].append(t)
    for key, u in units.items():
        if key not in reg:
            raise RuntimeError(f"unit {key} missing from position log")
        u["rb"] = reg[key]["rb"]
        u["lots"] = reg[key]["lots"]
    return units


def bucket(units: dict, pred) -> dict:
    sel = [u for u in units.values() if pred(u)]
    rs = [u["pnl"] / u["rb"] for u in sel]
    return {
        "units": len(sel),
        "net_usd": round(sum(u["pnl"] for u in sel), 2),
        "mean_unit_r": round(sum(rs) / len(rs), 4) if rs else None,
        "mean_rb_usd": round(sum(u["rb"] for u in sel) / len(sel), 2) if sel else None,
    }


def same_path(units: dict, spread_pts: float, point: float, dppl: float) -> dict:
    """Attribution on the canonical published ledger; lots reconstructed from
    pnl/r (independent of the prior analyst's book() patch)."""
    for u in units.values():
        lots_sum = 0.0
        price_pnl = comm = swap = 0.0
        n = len(u["tranches"])
        for i, t in enumerate(u["tranches"]):
            if t["r"] == 0:
                raise RuntimeError("r==0 tranche; lots not reconstructable")
            unit_risk_i = t["pnl"] / t["r"]
            lots_i = u["lots"] * unit_risk_i / u["rb"]
            lots_sum += lots_i
            p = t["side"] * (t["exit"] - t["entry"]) * lots_i * dppl
            c = 5.0 * lots_i
            s = t["pnl"] - (p - c)
            if i < n - 1 and abs(s) > 0.01:
                raise RuntimeError(f"swap on non-final tranche: {s}")
            price_pnl += p
            comm += c
            swap += s
        if abs(lots_sum - u["lots"]) > 1e-6:
            raise RuntimeError(f"lots do not reconcile: {lots_sum} vs {u['lots']}")
        u["price_pnl"] = price_pnl
        u["comm"] = comm
        u["swap"] = swap  # signed: negative = cost
        u["spread_bill"] = spread_pts * point * dppl * u["lots"]
        recon = price_pnl - comm + swap
        if abs(recon - u["pnl"]) > 0.01:
            raise RuntimeError(f"unit does not reconcile: {recon} vs {u['pnl']}")

    def agg(pred):
        sel = [u for u in units.values() if pred(u)]
        rb = sum(u["rb"] for u in sel)
        n = len(sel)
        gross_ex = sum(u["price_pnl"] + u["spread_bill"] for u in sel)
        spread_bill = sum(u["spread_bill"] for u in sel)
        comm = sum(u["comm"] for u in sel)
        swap_cost = -sum(u["swap"] for u in sel)  # positive = cost
        net = sum(u["pnl"] for u in sel)
        out = {
            "units": n,
            "gross_ex_spread_usd": round(gross_ex, 2),
            "spread_bill_usd": round(spread_bill, 2),
            "commission_usd": round(comm, 2),
            "swap_cost_usd": round(swap_cost, 2),
            "net_usd": round(net, 2),
        }
        if rb:
            out["per_unit_r"] = {
                "gross_ex_spread": round(gross_ex / rb, 4),
                "spread": round(spread_bill / rb, 4),
                "commission": round(comm / rb, 4),
                "swap": round(swap_cost / rb, 4),
                "net": round(net / rb, 4),
                "total_costs": round((spread_bill + comm + swap_cost) / rb, 4),
            }
        return out

    return {
        "all": agg(lambda u: True),
        "is": agg(lambda u: not u["oos"]),
        "oos": agg(lambda u: u["oos"]),
    }


def main() -> None:
    broker = json.loads((REPO / "backtest" / "deriv_broker_meta.json").read_text(encoding="utf-8"))
    out: dict = {"initial": INITIAL, "split": SPLIT, "symbols": {}}

    for symbol, info in SYMBOLS.items():
        meta_full = broker["symbols"][symbol]
        point = float(meta_full["point"])
        dppl = float(meta_full["trade_tick_value_loss"]) / float(meta_full["trade_tick_size"])
        fb = info["fallback_pts"]

        # ---- 1. canonical reproduction, tranche-by-tranche -------------
        canon = json.loads((RESULTS_DIR / info["canonical_json"]).read_text(encoding="utf-8"))
        cm = canon["measured"]
        r4, log4 = run(symbol, fb, meta_full, commission=True, swap=True)
        assert abs(r4["ending_balance"] - INITIAL - cm["all"]["net_profit"]) < 0.005, (
            symbol,
            r4["ending_balance"] - INITIAL,
            cm["all"]["net_profit"],
        )
        assert len(r4["trades"]) == len(cm["trades"])
        for a, b in zip(r4["trades"], cm["trades"]):
            assert a == b, (symbol, a, b)
        repro = {
            "canonical_net_usd": round(cm["all"]["net_profit"], 2),
            "rerun_net_usd": round(r4["ending_balance"] - INITIAL, 2),
            "tranches": len(r4["trades"]),
            "identical_tranche_ledgers": True,
        }

        # ---- 2. frictionless gross -------------------------------------
        r1, log1 = run(symbol, 0.0, meta_full, commission=False, swap=False)
        u1 = unit_stats(r1["trades"], log1)
        gross = {
            "net_usd": round(r1["ending_balance"] - INITIAL, 2),
            "all": bucket(u1, lambda u: True),
            "is": bucket(u1, lambda u: not u["oos"]),
            "oos": bucket(u1, lambda u: u["oos"]),
        }
        # cross-check: sum of unit pnl == ending balance delta
        assert abs(gross["all"]["net_usd"] - gross["net_usd"]) < 0.01

        # ---- 3. same-path attribution on the PUBLISHED ledger ----------
        u4 = unit_stats(cm["trades"], log4)
        sp = same_path(u4, fb, point, dppl)
        assert abs(sp["all"]["net_usd"] - cm["all"]["net_profit"]) < 0.01

        out["symbols"][symbol] = {
            "fallback_pts": fb,
            "reproduction": repro,
            "frictionless_gross": gross,
            "same_path_attribution": sp,
        }

        print(f"== {symbol} ==")
        print(f"  repro: canonical {repro['canonical_net_usd']} == rerun {repro['rerun_net_usd']} "
              f"({repro['tranches']} identical tranches)")
        g = gross
        print(f"  frictionless gross: net ${g['net_usd']:>9.2f}  units {g['all']['units']:>3d} "
              f"unitR {g['all']['mean_unit_r']:+.4f}  "
              f"IS ${g['is']['net_usd']:>8.2f} (R {g['is']['mean_unit_r']:+.4f})  "
              f"OOS ${g['oos']['net_usd']:>8.2f} (R {g['oos']['mean_unit_r']:+.4f})")
        a = sp["all"]
        print(f"  same-path: gross_ex_spread ${a['gross_ex_spread_usd']:.2f} "
              f"- spread ${a['spread_bill_usd']:.2f} - comm ${a['commission_usd']:.2f} "
              f"- swap ${a['swap_cost_usd']:.2f} = net ${a['net_usd']:.2f}")
        pr = a["per_unit_r"]
        print(f"  per-unit R: gross {pr['gross_ex_spread']:+.4f} costs -{pr['total_costs']:.4f} "
              f"(spread {pr['spread']:.4f} comm {pr['commission']:.4f} swap {pr['swap']:+.4f}) "
              f"net {pr['net']:+.4f}")
        for scope in ("is", "oos"):
            s = sp[scope]
            print(f"    {scope.upper():>3}: units {s['units']:>3d} gross_ex ${s['gross_ex_spread_usd']:>9.2f} "
                  f"net ${s['net_usd']:>9.2f}")
        print()

    # ---- 4. EURUSD break-even spread grid (full costs) -----------------
    meta_eu = broker["symbols"]["EURUSD"]
    grid = {}
    for pts in (8, 12, 16, 18, 19, 20, 21, 22, 24, 28):
        r, _ = run("EURUSD", float(pts), meta_eu, commission=True, swap=True)
        grid[pts] = round(r["ending_balance"] - INITIAL, 2)
    sp_eu = out["symbols"]["EURUSD"]["same_path_attribution"]["all"]
    bill_per_pt = sp_eu["spread_bill_usd"] / SYMBOLS["EURUSD"]["fallback_pts"]
    analytic_be = (
        sp_eu["gross_ex_spread_usd"] - sp_eu["commission_usd"] - sp_eu["swap_cost_usd"]
    ) / bill_per_pt
    out["eurusd_breakeven"] = {
        "full_cost_net_by_spread_pts": grid,
        "analytic_same_path_be_pts": round(analytic_be, 2),
    }
    print("EURUSD full-cost net by spread pts:", grid)
    print(f"EURUSD analytic same-path break-even: {analytic_be:.1f} pts")

    # ---- 5. XAUUSD spread-response non-monotonicity ---------------------
    meta_xu = broker["symbols"]["XAUUSD"]
    xg = {}
    for pts in (0, 4, 8, 12, 16, 20):
        r, _ = run("XAUUSD", float(pts), meta_xu, commission=True, swap=True)
        xg[pts] = round(r["ending_balance"] - INITIAL, 2)
    out["xauusd_fullcost_net_by_spread_pts"] = xg
    print("XAUUSD full-cost net by spread pts:", xg)

    OUT_PATH.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"\nwritten {OUT_PATH}")


if __name__ == "__main__":
    main()
