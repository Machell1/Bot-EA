#!/usr/bin/env python3
"""ADVERSARIAL VERIFICATION (independent implementation) of exit_leak_01.

Re-derives, from the canonical ledgers and raw M15 data only (no reuse of the
prior analyst's code paths):
  1. unit count per symbol (claim: 459 total)
  2. actual price-R vs exact random-in-window expectation (claim: leak +74.8R
     total; EUR +11.3 / GBP +13.9 / JPY +27.4 / XAU +22.3; IS/OOS split all +)
  3. percentile of actual vs 400 random-exit portfolios, with a DIFFERENT
     seed scheme than the prior analyst (claim: 0.5/0.5/0.0/0.0), plus a
     1000-portfolio stability check
  4. stop-terminated unit localization (claim: 294 units, actual -115.7R vs
     random +2.6R; non-stop units beat random by +43.4R)
  5. NEW decomposition not in the prior work: reprice every actual tranche at
     its exit bar's CLOSE (identical timing, benchmark-style fill) to split
     the leak into fill-model asymmetry vs in-window timing.
"""
import json
import random
import sys
from pathlib import Path

REPO = Path(r"C:/Users/Sanique Richards/Documents/Homework Heroes/Pokemon/Bot-EA")
sys.path.insert(0, str(REPO))

from backtest.ftmo_quant_backtest import (  # noqa: E402
    Config, aggregate_h1, load_m15, atr_sma_of_tr,
)

CFG = Config()
PARAMS = {
    "EURUSD": ("EURUSD.json", 10.0),
    "GBPUSD": ("GBPUSD_relaxedgate.json", 15.0),
    "USDJPY": ("USDJPY.json", 28.0),
    "XAUUSD": ("XAUUSD.json", 16.0),
}
N_PORT = 400


def close_px(bars, j, side, spread):
    return bars[j].close if side > 0 else bars[j].close + spread


def main():
    broker = json.loads((REPO / "backtest/deriv_broker_meta.json").read_text())
    grand = {"units": 0, "r_act": 0.0, "r_rand": 0.0,
             "stop_units": 0, "stop_act": 0.0, "stop_rand": 0.0,
             "nonstop_act": 0.0, "nonstop_rand": 0.0,
             "r_act_close": 0.0}
    per_sym = {}
    for sym, (fname, spts) in PARAMS.items():
        meta = broker["symbols"][sym]
        point = float(meta["point"])
        spread = spts * point
        bars = aggregate_h1(load_m15(REPO / "backtest/data/derivM15" / f"{sym}.csv", spread))
        atr = atr_sma_of_tr(bars, CFG.atr_period)
        t2i = {b.time.isoformat(sep=" "): i for i, b in enumerate(bars)}

        canon = json.loads((REPO / "backtest/results" / fname).read_text())
        trades = canon["measured"]["trades"]

        groups = {}
        for t in trades:
            groups.setdefault(t["entry_time"], []).append(t)

        units = []
        max_tp1_err = 0.0
        for et, rows in groups.items():
            side = rows[0]["side"]
            entry = rows[0]["entry"]
            assert all(r["side"] == side and r["entry"] == entry for r in rows)
            e = t2i[et]
            ir = CFG.stop_atr * atr[e - 1] + spread
            # independent cross-check of ir against booked TP1 fills
            for t in rows:
                if t["reason"] == "tp1":
                    max_tp1_err = max(max_tp1_err, abs(abs(t["exit"] - entry) - ir))
            shares = [t["pnl"] / t["r"] for t in rows]
            rb = sum(shares)
            ws = [s / rb for s in shares]
            assert abs(sum(ws) - 1.0) < 1e-9
            x = max(t2i[t["exit_time"]] for t in rows)
            r_act = sum(w * side * (t["exit"] - entry) / ir for t, w in zip(rows, ws))
            # identical timing, close-based fill (benchmark-style)
            r_act_close = sum(
                w * side * (close_px(bars, t2i[t["exit_time"]], side, spread) - entry) / ir
                for t, w in zip(rows, ws))
            vals = [side * (close_px(bars, j, side, spread) - entry) / ir
                    for j in range(e, x + 1)]
            final = max(rows, key=lambda t: t["exit_time"])
            units.append({
                "e": e, "x": x, "oos": rows[0]["oos"],
                "r_act": r_act, "r_act_close": r_act_close,
                "r_rand": sum(vals) / len(vals), "vals": vals,
                "final_reason": final["reason"],
            })
        units.sort(key=lambda u: u["e"])

        # portfolio percentile with an independent seed scheme
        r_act_total = sum(u["r_act"] for u in units)
        totals = []
        for k in range(N_PORT):
            rng = random.Random(f"verify-A-{sym}-{k}")
            totals.append(sum(u["vals"][rng.randrange(len(u["vals"]))] for u in units))
        pct400 = 100.0 * sum(1 for v in totals if v < r_act_total) / len(totals)
        totals2 = []
        for k in range(1000):
            rng = random.Random(f"verify-B-{sym}-{k}")
            totals2.append(sum(u["vals"][rng.randrange(len(u["vals"]))] for u in units))
        pct1000 = 100.0 * sum(1 for v in totals2 if v < r_act_total) / len(totals2)

        def tot(key, pred=lambda u: True):
            return sum(u[key] for u in units if pred(u))

        stop = lambda u: u["final_reason"] == "stop"
        nonstop = lambda u: u["final_reason"] != "stop"
        d = {
            "units": len(units),
            "max_tp1_ir_err": max_tp1_err,
            "r_act": r_act_total,
            "r_rand": tot("r_rand"),
            "leak": tot("r_rand") - r_act_total,
            "leak_is": tot("r_rand", lambda u: not u["oos"]) - tot("r_act", lambda u: not u["oos"]),
            "leak_oos": tot("r_rand", lambda u: u["oos"]) - tot("r_act", lambda u: u["oos"]),
            "pct400": pct400, "pct1000": pct1000,
            "stop_units": sum(1 for u in units if stop(u)),
            "stop_act": tot("r_act", stop), "stop_rand": tot("r_rand", stop),
            "nonstop_act": tot("r_act", nonstop), "nonstop_rand": tot("r_rand", nonstop),
            "r_act_close": tot("r_act_close"),
            "leak_fill": tot("r_act_close") - r_act_total,      # same timing, close fills
            "leak_timing": tot("r_rand") - tot("r_act_close"),  # close-vs-close timing
        }
        per_sym[sym] = d
        for k in ("units", "stop_units"):
            grand[k] += d[k]
        grand["r_act"] += d["r_act"]; grand["r_rand"] += d["r_rand"]
        grand["stop_act"] += d["stop_act"]; grand["stop_rand"] += d["stop_rand"]
        grand["nonstop_act"] += d["nonstop_act"]; grand["nonstop_rand"] += d["nonstop_rand"]
        grand["r_act_close"] += d["r_act_close"]

        print(f"{sym}: units={d['units']} tp1_ir_err={max_tp1_err:.2e}")
        print(f"  act {d['r_act']:+8.2f}  rand {d['r_rand']:+8.2f}  leak {d['leak']:+7.2f} "
              f"(IS {d['leak_is']:+.2f} / OOS {d['leak_oos']:+.2f})  "
              f"pctile {pct400:.2f} (n=400) / {pct1000:.2f} (n=1000)")
        print(f"  stop-final: n={d['stop_units']}  act {d['stop_act']:+8.2f}  rand {d['stop_rand']:+7.2f}")
        print(f"  fill-vs-timing: act@close {d['r_act_close']:+8.2f}  "
              f"leak_fill {d['leak_fill']:+7.2f}  leak_timing {d['leak_timing']:+7.2f}")

    g = grand
    print("\n=== TOTALS ===")
    print(f"units {g['units']}   act {g['r_act']:+.2f}   rand {g['r_rand']:+.2f}   "
          f"leak {g['r_rand']-g['r_act']:+.2f}")
    print(f"stop-final units {g['stop_units']}: act {g['stop_act']:+.2f} rand {g['stop_rand']:+.2f} "
          f"giveback {g['stop_rand']-g['stop_act']:+.2f}")
    print(f"non-stop units: act {g['nonstop_act']:+.2f} rand {g['nonstop_rand']:+.2f} "
          f"(actual beats random by {g['nonstop_act']-g['nonstop_rand']:+.2f})")
    print(f"fill-asymmetry share: act@close {g['r_act_close']:+.2f}  "
          f"leak_fill {g['r_act_close']-g['r_act']:+.2f}  "
          f"leak_timing {g['r_rand']-g['r_act_close']:+.2f}")
    (REPO / "backtest/audit/verify_exit_leak_A_results.json").write_text(
        json.dumps({"per_symbol": per_sym, "grand": grand}, indent=2))


if __name__ == "__main__":
    main()
