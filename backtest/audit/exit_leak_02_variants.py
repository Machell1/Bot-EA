#!/usr/bin/env python3
"""Exit-leak audit, step 2: exact swap reconciliation + engine mechanism ladder.

Part A: re-runs the unit reconciliation charging swap on the REMAINING lots
after each partial close (the engine's actual behavior). The unit ledger must
reconcile to float noise, proving the unit grouping and R arithmetic exact.

Part B: full engine re-runs with one exit mechanism removed at a time (FTMO
guards ON, canonical spreads, identical data) to attribute expectancy
destruction causally, system effects included:

  base           : canonical config
  pyramid_off    : pyramid_enabled=False              (task-required A/B)
  no_trail       : trail_start_r=1e9                  (ATR trail off)
  no_tp2         : reward_risk=1e9                    (TP2 off -> trail off too)
  no_be_full     : PATCHED engine (hardcoded BE-at-TP1 removed) + break_even_r=1e9
  no_tp1         : tp1_r=1e9                          (TP1/TP2/trail/pyramid off,
                                                       1R BE move remains)
  stop_flip_only : tp1_r=1e9, break_even_r=1e9        (orig stop / EMA flip /
                                                       weekend / guards only)
  stop_flip_only_no_wk : + friday_close=100           (holds over weekends)
  no_weekend     : friday_close=100                   (everything else on)

The no_be_full variant patches exactly one engine line
(`unit.stop = unit.entry` after TP1 -> `pass`); combined with break_even_r=1e9
no break-even move exists anywhere while TP1/TP2/trail stay live.
"""
import json
import sys
from pathlib import Path

REPO = Path(r"C:/Users/Sanique Richards/Documents/Homework Heroes/Pokemon/Bot-EA")
sys.path.insert(0, str(REPO))

import backtest.ftmo_quant_backtest as eng  # noqa: E402
from backtest.ftmo_quant_backtest import Config, aggregate_h1, load_m15  # noqa: E402

PARAMS = json.loads((REPO / "backtest/audit/exit_leak_00_repro_params.json").read_text())
CFG = Config()

# ---------------------------------------------------------------- patched engine
SRC = (REPO / "backtest/ftmo_quant_backtest.py").read_text(encoding="utf-8")
BE_LINE = "unit.stop = unit.entry  # trail original order to break-even"
assert SRC.count(BE_LINE) == 1
PATCHED = SRC.replace(BE_LINE, "pass  # AUDIT: hardcoded BE-at-TP1 disabled")
import types  # noqa: E402
_mod = types.ModuleType("ftmo_quant_backtest_no_be")
sys.modules["ftmo_quant_backtest_no_be"] = _mod
exec(compile(PATCHED, "ftmo_quant_backtest_no_be", "exec"), _mod.__dict__)
run_backtest_no_be = _mod.run_backtest


def unit_totals(trades, bars, atr, spread, dppl, swap_long, swap_short, point):
    """Group tranches into units; return (n_units, total_price_R, oos_price_R,
    max_reconciliation_err_usd) with swap charged on remaining lots."""
    time_to_idx = {b.time.isoformat(sep=" "): i for i, b in enumerate(bars)}
    groups = {}
    for t in trades:
        groups.setdefault(t["entry_time"], []).append(t)
    n, tot, tot_oos, max_err = 0, 0.0, 0.0, 0.0
    for entry_time, rows in groups.items():
        shares = [t["pnl"] / t["r"] for t in rows]  # risk_budget * w_i
        risk_budget = sum(shares)
        weights = [s / risk_budget for s in shares]
        e = time_to_idx[entry_time]
        side = rows[0]["side"]
        entry = rows[0]["entry"]
        ir = CFG.stop_atr * atr[e - 1] + spread
        lots = risk_budget / (ir * dppl)
        r_act = sum(w * side * (t["exit"] - entry) / ir for t, w in zip(rows, weights))
        # exact swap: iterate nights; remaining weight = 1 - sum(w of tranches
        # already closed strictly before that bar). Engine books swap at the
        # day-change bar BEFORE processing that bar's exits.
        order = sorted(zip(rows, weights), key=lambda p: p[0]["exit_time"])
        last_idx = max(time_to_idx[t["exit_time"]] for t in rows)
        pts = swap_long if side > 0 else swap_short
        swap_usd = 0.0
        prev_day = bars[e].time.date()
        for j in range(e + 1, last_idx + 1):
            d = bars[j].time.date()
            if d != prev_day:
                mult = 3.0 if bars[j].time.weekday() == 3 else 1.0
                bar_time = bars[j].time.isoformat(sep=" ")
                remaining = 1.0 - sum(w for t, w in order if t["exit_time"] < bar_time)
                swap_usd += pts * point * dppl * lots * remaining * mult
                prev_day = d
        expect_pnl = r_act * risk_budget - 2 * 2.5 * lots + swap_usd
        max_err = max(max_err, abs(expect_pnl - sum(t["pnl"] for t in rows)))
        n += 1
        tot += r_act
        if rows[0]["oos"]:
            tot_oos += r_act
    return n, tot, tot_oos, max_err


VARIANTS = [
    ("base", {}, False),
    ("pyramid_off", {"pyramid_enabled": False}, False),
    ("no_trail", {"trail_start_r": 1e9}, False),
    ("no_tp2", {"reward_risk": 1e9}, False),
    ("no_be_full", {"break_even_r": 1e9}, True),
    ("no_tp1", {"tp1_r": 1e9}, False),
    ("stop_flip_only", {"tp1_r": 1e9, "break_even_r": 1e9}, False),
    ("stop_flip_only_no_wk", {"tp1_r": 1e9, "break_even_r": 1e9,
                              "friday_close": 100}, False),
    ("no_weekend", {"friday_close": 100}, False),
]


def main():
    broker = json.loads((REPO / "backtest/deriv_broker_meta.json").read_text())
    out = {}
    for sym, prm in PARAMS.items():
        meta = broker["symbols"][sym]
        point = float(meta["point"])
        dppl = float(meta["trade_tick_value_loss"]) / float(meta["trade_tick_size"])
        spread = prm["fallback_spread_points"] * point
        swap_long = float(meta["swap"]["long_points"])
        swap_short = float(meta["swap"]["short_points"])
        bars = aggregate_h1(load_m15(REPO / "backtest/data/derivM15" / f"{sym}.csv", spread))
        atr = eng.atr_sma_of_tr(bars, CFG.atr_period)
        out[sym] = {}
        for name, overrides, patched in VARIANTS:
            runner = run_backtest_no_be if patched else eng.run_backtest
            res = runner(bars, meta, Config(**overrides),
                         initial_balance=100_000.0, spread_multiplier=1.0,
                         split_fraction=0.70,
                         max_spread_points=prm["max_spread_points"])
            n, tot_r, oos_r, err = unit_totals(
                res["trades"], bars, atr, spread, dppl, swap_long, swap_short, point)
            reasons = {}
            for t in res["trades"]:
                reasons[t["reason"]] = reasons.get(t["reason"], 0) + 1
            out[sym][name] = {
                "return_pct": res["return_pct"],
                "net_profit": res["all"]["net_profit"],
                "oos_net_profit": res["oos"]["net_profit"],
                "trades": res["all"]["trades"],
                "units": n, "unit_r_total": tot_r, "oos_unit_r_total": oos_r,
                "max_recon_err_usd": err,
                "official_breach": res["official_rule_breach"],
                "reasons": reasons,
            }
            v = out[sym][name]
            print(f"{sym:7s} {name:22s} ret {v['return_pct']:+7.3f}%  "
                  f"units {n:4d}  unitR {tot_r:+8.2f}  oosR {oos_r:+7.2f}  "
                  f"reconErr ${err:.4f}", flush=True)
        print()
    (REPO / "backtest/audit/exit_leak_02_variants.json").write_text(
        json.dumps(out, indent=2))
    print("saved backtest/audit/exit_leak_02_variants.json")


if __name__ == "__main__":
    main()
