#!/usr/bin/env python3
"""Exit-leak audit, step 1: unit grouping + counterfactual exit policies.

Groups the canonical ledger tranches (tp1 0.4 / tp2 0.3 / runner residual)
into whole units, reconciles every unit against the engine's own arithmetic
(entry price, initial risk, tranche weights, commission, swap) to float
precision, then replays counterfactual exits for the SAME entries on the SAME
H1 path with IDENTICAL spread and commission:

  (a) random-in-trade : flatten the whole unit at the close of a uniformly
      random H1 bar inside the unit's actual holding window
      (exact expectation over all bars + 400-seed portfolio distribution)
  (b) fixed horizon   : flatten at the close 24/72/120 H1 bars after entry
  (c) let_run         : hold until the ORIGINAL 2.5-ATR stop or a pure 50/200
      EMA flip (no TP1/TP2/BE/trail); c2 adds the Friday-17h weekend flatten

R is price-based per unit: side*(w-avg exit - entry)/initial_risk. Commission
(2 x $2.50/side x lots) is identical for actual and every counterfactual
(one entry + full closure of the same lots), so it cancels exactly in the
leak. Swap depends on nights held, so it is reported as a separate
sensitivity per policy using the broker-meta swap points.

No look-ahead: policy (a) draws only bars the unit actually lived through;
(b)/(c) are fixed rules evaluated forward on completed bars exactly as the
engine does (EMA flip read at index-1, stop checked intra-bar pessimistically,
gap fills at the worse of stop and open).
"""
import json
import math
import random
import statistics
import sys
from pathlib import Path

REPO = Path(r"C:/Users/Sanique Richards/Documents/Homework Heroes/Pokemon/Bot-EA")
sys.path.insert(0, str(REPO))

from backtest.ftmo_quant_backtest import (  # noqa: E402
    Config, aggregate_h1, load_m15, ema, atr_sma_of_tr,
)

PARAMS = json.loads((REPO / "backtest/audit/exit_leak_00_repro_params.json").read_text())
CFG = Config()
N_SEEDS = 400
HORIZONS = (24, 72, 120)


def build_units(trades, time_to_idx):
    """Group tranches by entry_time; recover risk_budget and tranche weights
    from the engine's own r = pnl / (risk_budget * w) arithmetic."""
    groups = {}
    for t in trades:
        groups.setdefault(t["entry_time"], []).append(t)
    units = []
    for entry_time, rows in groups.items():
        risk_shares = []
        for t in rows:
            if t["r"] != 0.0:
                risk_shares.append(t["pnl"] / t["r"])
            else:
                risk_shares.append(None)
        known = [s for s in risk_shares if s is not None]
        risk_budget = sum(known)
        if any(s is None for s in risk_shares):
            # cannot recover that tranche's weight from pnl/r; distribute the
            # remainder (never triggers on these ledgers - asserted below)
            raise AssertionError(f"zero-r tranche in unit {entry_time}")
        weights = [s / risk_budget for s in risk_shares]
        assert abs(sum(weights) - 1.0) < 1e-9, (entry_time, sum(weights))
        last = max(rows, key=lambda t: (t["exit_time"], ))
        units.append({
            "entry_time": entry_time,
            "entry_index": time_to_idx[entry_time],
            "last_exit_index": time_to_idx[last["exit_time"]],
            "side": rows[0]["side"],
            "entry": rows[0]["entry"],
            "risk_budget": risk_budget,
            "tranches": [
                {"exit": t["exit"], "exit_time": t["exit_time"], "w": w,
                 "reason": t["reason"], "pnl": t["pnl"], "r": t["r"]}
                for t, w in zip(rows, weights)
            ],
            "oos": rows[0]["oos"],
            "unit_pnl": sum(t["pnl"] for t in rows),
        })
    units.sort(key=lambda u: u["entry_index"])
    return units


def nights_weighted(bars, i0, i1, triple_weekday=3):
    """Weighted rollover count between bar i0 and bar i1 (engine convention:
    a date change books swap; rollover into weekday==3 books 3x)."""
    total = 0.0
    prev_day = bars[i0].time.date()
    for j in range(i0 + 1, i1 + 1):
        d = bars[j].time.date()
        if d != prev_day:
            total += 3.0 if bars[j].time.weekday() == triple_weekday else 1.0
            prev_day = d
    return total


def exit_price(bars, j, side, spread, kind):
    """kind: close|open  (long exits at bid, short covers at ask)"""
    px = bars[j].close if kind == "close" else bars[j].open
    return px if side > 0 else px + spread


def let_run(bars, fast, slow, u, spread, weekend=False):
    """Original 2.5-ATR stop or pure 50/200 flip; optional weekend flatten.
    Returns (exit_index, exit_price, reason). Mirrors engine intra-bar order:
    flip (open) -> weekend (open) -> stop (pessimistic fill)."""
    side, entry, ir = u["side"], u["entry"], u["initial_risk"]
    stop0 = entry - side * ir
    n = len(bars)
    for j in range(u["entry_index"], n):
        b = bars[j]
        if j > u["entry_index"]:
            flipped = (side > 0 and fast[j - 1] < slow[j - 1]) or (
                side < 0 and fast[j - 1] > slow[j - 1])
            if flipped:
                return j, exit_price(bars, j, side, spread, "open"), "flip"
        if weekend and b.time.weekday() == 4 and b.time.hour >= CFG.friday_close:
            return j, exit_price(bars, j, side, spread, "open"), "weekend"
        if side > 0:
            if b.low <= stop0:
                fill = min(stop0, b.open) if b.open < stop0 else stop0
                return j, fill, "stop"
        else:
            if b.high + spread >= stop0:
                open_ask = b.open + spread
                fill = max(stop0, open_ask) if open_ask > stop0 else stop0
                return j, fill, "stop"
    return n - 1, exit_price(bars, n - 1, side, spread, "close"), "end_of_data"


def main():
    broker = json.loads((REPO / "backtest/deriv_broker_meta.json").read_text())
    report = {}
    for sym, prm in PARAMS.items():
        meta = broker["symbols"][sym]
        point = float(meta["point"])
        dppl = float(meta["trade_tick_value_loss"]) / float(meta["trade_tick_size"])
        spread = prm["fallback_spread_points"] * point
        swap_long = float(meta["swap"]["long_points"])
        swap_short = float(meta["swap"]["short_points"])

        bars = aggregate_h1(load_m15(REPO / "backtest/data/derivM15" / f"{sym}.csv", spread))
        closes = [b.close for b in bars]
        fast = ema(closes, CFG.ema_fast)
        slow = ema(closes, CFG.ema_slow)
        atr = atr_sma_of_tr(bars, CFG.atr_period)
        time_to_idx = {b.time.isoformat(sep=" "): i for i, b in enumerate(bars)}

        canon = json.loads((REPO / "backtest/results" / prm["file"]).read_text())
        units = build_units(canon["measured"]["trades"], time_to_idx)

        # ---- reconcile every unit against engine arithmetic -------------
        max_entry_err = max_ir_err = max_pnl_err = 0.0
        for u in units:
            e = u["entry_index"]
            b = bars[e]
            expect_entry = b.open + spread if u["side"] > 0 else b.open
            max_entry_err = max(max_entry_err, abs(expect_entry - u["entry"]))
            ir = CFG.stop_atr * atr[e - 1] + spread
            u["initial_risk"] = ir
            u["lots"] = u["risk_budget"] / (ir * dppl)
            # price-based actual R (gross of commission/swap)
            u["r_act"] = sum(
                t["w"] * u["side"] * (t["exit"] - u["entry"]) / ir
                for t in u["tranches"])
            # tp1 tranche cross-check: |exit-entry| == ir * tp1_r
            for t in u["tranches"]:
                if t["reason"] == "tp1":
                    max_ir_err = max(max_ir_err, abs(abs(t["exit"] - u["entry"]) - ir * CFG.tp1_r))
            # full dollar reconciliation: unit_pnl == r_act*risk_budget
            #   - 2*comm*lots + swap
            nights = nights_weighted(bars, e, u["last_exit_index"])
            pts = swap_long if u["side"] > 0 else swap_short
            swap_usd = nights * pts * point * dppl * u["lots"]
            expect_pnl = u["r_act"] * u["risk_budget"] - 2 * 2.5 * u["lots"] + swap_usd
            max_pnl_err = max(max_pnl_err, abs(expect_pnl - u["unit_pnl"]))
            u["nights_act"] = nights
            u["swap_r_act"] = swap_usd / u["risk_budget"]

        # ---- counterfactual exits ---------------------------------------
        n = len(bars)
        for u in units:
            e, x, side, ir = u["entry_index"], u["last_exit_index"], u["side"], u["initial_risk"]
            window = list(range(e, x + 1))
            # (a) exact expectation of random-in-trade close exit
            vals = [side * (exit_price(bars, j, side, spread, "close") - u["entry"]) / ir
                    for j in window]
            u["r_rand_mean"] = sum(vals) / len(vals)
            u["rand_window"] = window
            u["rand_vals"] = vals
            u["nights_rand_mean"] = sum(
                nights_weighted(bars, e, j) for j in window) / len(window)
            # (b) fixed horizons
            for h in HORIZONS:
                j = min(e + h, n - 1)
                u[f"r_h{h}"] = side * (exit_price(bars, j, side, spread, "close") - u["entry"]) / ir
                u[f"nights_h{h}"] = nights_weighted(bars, e, j)
            # (c) let-run
            for tag, wk in (("letrun", False), ("letrun_wk", True)):
                j, px, reason = let_run(bars, fast, slow, u, spread, weekend=wk)
                u[f"r_{tag}"] = side * (px - u["entry"]) / ir
                u[f"reason_{tag}"] = reason
                u[f"nights_{tag}"] = nights_weighted(bars, e, j)

        # ---- 400-seed random-exit portfolio distribution -----------------
        totals = []
        for seed in range(N_SEEDS):
            rng = random.Random(1_000_003 * seed + 17)
            totals.append(sum(u["rand_vals"][rng.randrange(len(u["rand_vals"]))]
                              for u in units))
        totals.sort()
        r_act_total = sum(u["r_act"] for u in units)
        pctile = 100.0 * sum(1 for v in totals if v < r_act_total) / len(totals)

        def agg(key, subset):
            return sum(u[key] for u in subset)

        def block(subset):
            k = len(subset)
            out = {"units": k, "r_act": agg("r_act", subset),
                   "r_rand_mean": agg("r_rand_mean", subset),
                   "r_letrun": agg("r_letrun", subset),
                   "r_letrun_wk": agg("r_letrun_wk", subset)}
            for h in HORIZONS:
                out[f"r_h{h}"] = agg(f"r_h{h}", subset)
            for kk in ("r_rand_mean", "r_letrun", "r_letrun_wk",
                       *[f"r_h{h}" for h in HORIZONS]):
                out[f"leak_{kk[2:]}"] = out[kk] - out["r_act"]
            return out

        # swap sensitivity per policy (R units, using approximate meta points)
        def swap_r(subset, nights_key):
            tot = 0.0
            for u in subset:
                pts = swap_long if u["side"] > 0 else swap_short
                tot += u[nights_key] * pts * point / u["initial_risk"]
            return tot

        # exit-reason decomposition of actual R (tranche level, price-based)
        reason_decomp = {}
        for u in units:
            for t in u["tranches"]:
                rc = t["w"] * u["side"] * (t["exit"] - u["entry"]) / u["initial_risk"]
                d = reason_decomp.setdefault(t["reason"], {"n": 0, "r": 0.0, "w": 0.0})
                d["n"] += 1
                d["r"] += rc
                d["w"] += t["w"]
        # final-reason (runner) grouping with per-group let-run leak
        final_groups = {}
        for u in units:
            fr = max(u["tranches"], key=lambda t: (t["exit_time"],))["reason"]
            g = final_groups.setdefault(fr, {"n": 0, "r_act": 0.0, "r_letrun": 0.0,
                                             "r_rand": 0.0})
            g["n"] += 1
            g["r_act"] += u["r_act"]
            g["r_letrun"] += u["r_letrun"]
            g["r_rand"] += u["r_rand_mean"]

        is_units = [u for u in units if not u["oos"]]
        oos_units = [u for u in units if u["oos"]]
        report[sym] = {
            "reconciliation": {"units": len(units),
                               "max_entry_price_err": max_entry_err,
                               "max_ir_err_vs_tp1": max_ir_err,
                               "max_unit_pnl_err_usd": max_pnl_err},
            "all": block(units), "is": block(is_units), "oos": block(oos_units),
            "random_seed_dist": {
                "n_seeds": N_SEEDS,
                "mean": sum(totals) / len(totals),
                "p5": totals[int(0.05 * N_SEEDS)],
                "p50": totals[N_SEEDS // 2],
                "p95": totals[int(0.95 * N_SEEDS)],
                "actual_total": r_act_total,
                "actual_pctile_vs_random": pctile},
            "swap_r_totals": {
                "act": sum(u["swap_r_act"] for u in units),
                "rand_mean": swap_r(units, "nights_rand_mean"),
                "letrun": swap_r(units, "nights_letrun"),
                "letrun_wk": swap_r(units, "nights_letrun_wk"),
                **{f"h{h}": swap_r(units, f"nights_h{h}") for h in HORIZONS}},
            "avg_bars_held": {
                "act": sum(u["last_exit_index"] - u["entry_index"] for u in units) / len(units),
                "letrun_reasons": {r: sum(1 for u in units if u["reason_letrun"] == r)
                                   for r in set(u["reason_letrun"] for u in units)}},
            "tranche_reason_decomp_r": reason_decomp,
            "final_reason_groups": final_groups,
        }
        print(f"{sym}: {len(units)} units reconciled "
              f"(entry_err {max_entry_err:.2e}, ir_err {max_ir_err:.2e}, "
              f"pnl_err ${max_pnl_err:.4f})", flush=True)

    out = REPO / "backtest/audit/exit_leak_01_report.json"
    slim = json.loads(json.dumps(report))
    out.write_text(json.dumps(slim, indent=2))
    print("saved", out)

    # console summary
    for sym, r in report.items():
        a = r["all"]
        print(f"\n=== {sym} ({a['units']} units) ===")
        print(f"  actual        {a['r_act']:+9.2f} R")
        print(f"  random-exit   {a['r_rand_mean']:+9.2f} R  (leak {a['leak_rand_mean']:+7.2f})  "
              f"actual pctile vs 400 seeds: {r['random_seed_dist']['actual_pctile_vs_random']:.1f}")
        print(f"  h24/h72/h120  {a['r_h24']:+8.2f} / {a['r_h72']:+8.2f} / {a['r_h120']:+8.2f} R")
        print(f"  let-run       {a['r_letrun']:+9.2f} R  (leak {a['leak_letrun']:+7.2f})")
        print(f"  let-run+wkend {a['r_letrun_wk']:+9.2f} R  (leak {a['leak_letrun_wk']:+7.2f})")
        print(f"  swap R  act {r['swap_r_totals']['act']:+.2f}  letrun {r['swap_r_totals']['letrun']:+.2f}  "
              f"h120 {r['swap_r_totals']['h120']:+.2f}")
        print(f"  tranche decomp: " + "  ".join(
            f"{k}: n={v['n']} r={v['r']:+.1f}" for k, v in
            sorted(r["tranche_reason_decomp_r"].items())))


if __name__ == "__main__":
    main()
