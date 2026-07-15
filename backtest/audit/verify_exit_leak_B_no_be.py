#!/usr/bin/env python3
"""ADVERSARIAL VERIFICATION of the no_be_full ablation (exit_leak_02 claim):
  +25.0R full / +4.1R OOS / +6.92pp return summed over 4 symbols,
  GBPUSD -1.23% -> +1.75%.

Independent steps:
  1. Fresh base engine runs must reproduce the canonical returns exactly.
  2. Patched engine (hardcoded BE-at-TP1 removed, break_even_r=1e9) re-run.
  3. Unit price-R totals recomputed with MY grouping (pnl/r shares,
     ir = 2.5*ATR[e-1]+spread), not the prior analyst's function.
"""
import json
import sys
import types
from pathlib import Path

REPO = Path(r"C:/Users/Sanique Richards/Documents/Homework Heroes/Pokemon/Bot-EA")
sys.path.insert(0, str(REPO))

import backtest.ftmo_quant_backtest as eng  # noqa: E402
from backtest.ftmo_quant_backtest import Config, aggregate_h1, load_m15, atr_sma_of_tr  # noqa: E402

CFG = Config()
PARAMS = {
    "EURUSD": ("EURUSD.json", 10.0, 25.0, 1.0624885714287435),
    "GBPUSD": ("GBPUSD_relaxedgate.json", 15.0, 25.0, -1.2327542857146767),
    "USDJPY": ("USDJPY.json", 28.0, 30.0, -4.213073807413292),
    "XAUUSD": ("XAUUSD.json", 16.0, 25.0, 1.7288980000000231),
}

SRC = (REPO / "backtest/ftmo_quant_backtest.py").read_text(encoding="utf-8")
TARGET = "unit.stop = unit.entry  # trail original order to break-even"
assert SRC.count(TARGET) == 1, "BE line not unique - patch unsafe"
PATCHED = SRC.replace(TARGET, "pass  # VERIFY: BE-at-TP1 removed")
mod = types.ModuleType("ftmo_no_be_verify")
sys.modules["ftmo_no_be_verify"] = mod
exec(compile(PATCHED, "ftmo_no_be_verify", "exec"), mod.__dict__)


def unit_r(trades, bars, atr, spread):
    t2i = {b.time.isoformat(sep=" "): i for i, b in enumerate(bars)}
    groups = {}
    for t in trades:
        groups.setdefault(t["entry_time"], []).append(t)
    tot = oos = 0.0
    for et, rows in groups.items():
        side, entry = rows[0]["side"], rows[0]["entry"]
        e = t2i[et]
        ir = CFG.stop_atr * atr[e - 1] + spread
        shares = [t["pnl"] / t["r"] for t in rows]
        rb = sum(shares)
        r = sum((s / rb) * side * (t["exit"] - entry) / ir for t, s in zip(rows, shares))
        tot += r
        if rows[0]["oos"]:
            oos += r
    return len(groups), tot, oos


def main():
    broker = json.loads((REPO / "backtest/deriv_broker_meta.json").read_text())
    sums = {"dR": 0.0, "dOOS": 0.0, "dpp": 0.0}
    out = {}
    for sym, (fname, spts, maxsp, canon_ret) in PARAMS.items():
        meta = broker["symbols"][sym]
        spread = spts * float(meta["point"])
        bars = aggregate_h1(load_m15(REPO / "backtest/data/derivM15" / f"{sym}.csv", spread))
        atr = atr_sma_of_tr(bars, CFG.atr_period)
        kw = dict(initial_balance=100_000.0, spread_multiplier=1.0,
                  split_fraction=0.70, max_spread_points=maxsp)
        base = eng.run_backtest(bars, meta, Config(), **kw)
        assert abs(base["return_pct"] - canon_ret) < 1e-9, (sym, base["return_pct"], canon_ret)
        nobe = mod.run_backtest(bars, meta, Config(break_even_r=1e9), **kw)
        nb, rb_, ob = unit_r(base["trades"], bars, atr, spread)
        nn, rn, on = unit_r(nobe["trades"], bars, atr, spread)
        dR, dOOS = rn - rb_, on - ob
        dpp = nobe["return_pct"] - base["return_pct"]
        sums["dR"] += dR; sums["dOOS"] += dOOS; sums["dpp"] += dpp
        out[sym] = {"base_ret": base["return_pct"], "nobe_ret": nobe["return_pct"],
                    "base_unitR": rb_, "nobe_unitR": rn, "dR": dR, "dOOS": dOOS, "dpp": dpp,
                    "base_units": nb, "nobe_units": nn,
                    "nobe_breach": nobe["official_rule_breach"]}
        print(f"{sym}: base {base['return_pct']:+.3f}% (canon OK) -> no_be {nobe['return_pct']:+.3f}%  "
              f"dR {dR:+.2f}  dOOS {dOOS:+.2f}  dpp {dpp:+.2f}  breach={nobe['official_rule_breach']}")
    print(f"TOTAL: dR {sums['dR']:+.2f}  dOOS {sums['dOOS']:+.2f}  dpp {sums['dpp']:+.2f}")
    (REPO / "backtest/audit/verify_exit_leak_B_results.json").write_text(
        json.dumps({"per_symbol": out, "totals": sums}, indent=2))


if __name__ == "__main__":
    main()
