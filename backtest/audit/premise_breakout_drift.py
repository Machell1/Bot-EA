#!/usr/bin/env python3
"""AUDIT DIMENSION: is H1 breakout-continuation (the EA's premise) dead on
these four Deriv instruments?

Raw hypothesis test, ignoring the EA machinery: after a 20-bar Donchian
breakout CLOSE on H1 (any session hour), does price drift in the breakout
direction over the next 1/4/12/24/48 bars, spread-free, in ATR units?

Per symbol and pooled:
  * mean signed forward return (dir * (close[t+h]-close[t]) / ATR14[t])
    conditioned on the breakout, with calendar-month block-bootstrap CIs
    (events inside a month stay together; months resampled with replacement,
    so 48h-horizon overlap/serial correlation is respected);
  * the same-horizon UNCONDITIONAL drift and a drift-adjusted continuation
    (continuation minus what the symbol's own drift would hand a random
    direction mix equal to the events' long/short mix) -- separates
    "breakouts predict" from "the symbol trended";
  * continuation by calendar year (decay check);
  * effect of the EA's own confirmation stack, nested variants on the SAME
    event set:  A = plain breakout close;  B = A + 0.5*ATR buffer;
    C = B + decisive-candle body/wick test;  D = exact engine signal()
    (C + EMA50>EMA200 stack + rising fast EMA).  D is computed by calling
    the module-level signal() so it is the EA's literal code path.

No look-ahead: every conditioning quantity (channel, ATR, EMAs, candle) uses
bars <= t; forward returns start at close[t] (the EA would fill at the open of
t+1 ~ close[t]).  All randomness seeded.  Stdlib only.
"""
from __future__ import annotations

import json
import math
import random
import statistics
import sys
from pathlib import Path

REPO = Path(r"C:/Users/Sanique Richards/Documents/Homework Heroes/Pokemon/Bot-EA")
sys.path.insert(0, str(REPO))

import backtest.ftmo_quant_backtest as eng  # noqa: E402
from backtest.ftmo_quant_backtest import (  # noqa: E402
    Config,
    aggregate_h1,
    atr_sma_of_tr,
    ema,
    load_m15,
)

SYMBOLS = ["EURUSD", "GBPUSD", "USDJPY", "XAUUSD"]
HORIZONS = [1, 4, 12, 24, 48]
DONCHIAN = 20
MIN_INDEX = 210  # engine signal() warmup: max(ema_slow+10, donchian+2)
BOOT_N = 1000
SEED = 12345
CFG = Config()


def load_symbol(symbol: str):
    path = REPO / "backtest" / "data" / "derivM15" / f"{symbol}.csv"
    bars = aggregate_h1(load_m15(path, 0.0))  # spread-free study
    closes = [b.close for b in bars]
    fast = ema(closes, CFG.ema_fast)
    slow = ema(closes, CFG.ema_slow)
    atr = atr_sma_of_tr(bars, CFG.atr_period)
    return bars, fast, slow, atr


def build_events(bars, fast, slow, atr):
    """One row per breakout bar b (variant A).  Nested flags B, C, D.
    fwd[h] = signed-by-nothing raw forward return in ATR units."""
    n = len(bars)
    events = []
    hmax = max(HORIZONS)
    for b in range(MIN_INDEX, n - hmax):
        a = atr[b]
        if not (math.isfinite(a) and a > 0.0):
            continue
        window = bars[b - DONCHIAN:b]
        upper = max(w.high for w in window)
        lower = min(w.low for w in window)
        c = bars[b].close
        if c > upper:
            direction = 1
            edge = upper
        elif c < lower:
            direction = -1
            edge = lower
        else:
            continue
        # B: 0.5*ATR buffer beyond the channel edge
        flag_b = (c > upper + CFG.entry_buffer_atr * a) if direction == 1 else (
            c < lower - CFG.entry_buffer_atr * a
        )
        # C: B + decisive candle (engine's exact arithmetic)
        bar = bars[b]
        rng = bar.high - bar.low
        flag_c = False
        if flag_b and rng > 0.0:
            body = abs(bar.close - bar.open)
            upper_wick = bar.high - max(bar.open, bar.close)
            lower_wick = min(bar.open, bar.close) - bar.low
            strong = body / rng >= CFG.candle_body_min
            if direction == 1:
                flag_c = (
                    strong
                    and bar.close > bar.open
                    and upper_wick / rng <= CFG.candle_wick_max
                )
            else:
                flag_c = (
                    strong
                    and bar.close < bar.open
                    and lower_wick / rng <= CFG.candle_wick_max
                )
        # D: the engine's literal signal() (adds the EMA stack)
        sig = eng.signal(b + 1, bars, fast, slow, CFG, atr)
        flag_d = sig == direction
        if sig != 0 and sig != direction:
            raise AssertionError("signal() fired against the breakout direction")
        if flag_d and not flag_c:
            raise AssertionError("nesting broken: D fired without C")
        fwd = {h: (bars[b + h].close - bars[b].close) / a for h in HORIZONS}
        events.append(
            {
                "b": b,
                "time": bars[b].time,
                "month": (bars[b].time.year, bars[b].time.month),
                "year": bars[b].time.year,
                "dir": direction,
                "session": CFG.session_start <= bars[b].time.hour < CFG.session_end
                and bars[b].time.hour not in CFG.skip_hours
                and bars[b].time.weekday() < 5,
                "B": flag_b,
                "C": flag_c,
                "D": flag_d,
                "fwd": fwd,
            }
        )
    return events


def build_uncond(bars, atr):
    """Every valid bar: raw forward returns in ATR units (unconditional)."""
    n = len(bars)
    hmax = max(HORIZONS)
    rows = []
    for b in range(MIN_INDEX, n - hmax):
        a = atr[b]
        if not (math.isfinite(a) and a > 0.0):
            continue
        rows.append(
            {
                "month": (bars[b].time.year, bars[b].time.month),
                "fwd": {h: (bars[b + h].close - bars[b].close) / a for h in HORIZONS},
            }
        )
    return rows


def _pct_ci(xs):
    xs = sorted(xs)
    lo = xs[max(0, int(0.025 * len(xs)))]
    hi = xs[min(len(xs) - 1, int(0.975 * len(xs)))]
    return lo, hi


def month_block_bootstrap(events, uncond, horizons, n_boot=BOOT_N, seed=SEED):
    """Resample calendar months with replacement.  For each resample compute:
      cont[h]  = mean over events of dir*fwd
      drift[h] = mean over ALL bars of fwd  (the symbol's own drift)
      adj[h]   = cont - drift * mean(dir)   (drift-stripped continuation)
    Returns point stats on the real sample + percentile CIs.
    Per-month sufficient statistics are precomputed so each resample is
    O(months), not O(events)."""
    # per-month: [n_ev, sum_dir, {h: sum dir*fwd}, n_un, {h: sum fwd}]
    acc: dict = {}
    for e in events:
        m = acc.setdefault(e["month"], [0, 0.0, {h: 0.0 for h in horizons},
                                        0, {h: 0.0 for h in horizons}])
        m[0] += 1
        m[1] += e["dir"]
        for h in horizons:
            m[2][h] += e["dir"] * e["fwd"][h]
    for u in uncond:
        m = acc.setdefault(u["month"], [0, 0.0, {h: 0.0 for h in horizons},
                                        0, {h: 0.0 for h in horizons}])
        m[3] += 1
        for h in horizons:
            m[4][h] += u["fwd"][h]
    months = sorted(acc)

    def stats_for(month_list):
        n_ev = sum(acc[m][0] for m in month_list)
        n_un = sum(acc[m][3] for m in month_list)
        if n_ev == 0 or n_un == 0:
            return None
        mean_dir = sum(acc[m][1] for m in month_list) / n_ev
        out = {}
        for h in horizons:
            cont = sum(acc[m][2][h] for m in month_list) / n_ev
            drift = sum(acc[m][4][h] for m in month_list) / n_un
            out[h] = (cont, drift, cont - drift * mean_dir)
        return out

    point = stats_for(months)
    rng = random.Random(seed)
    boots = {h: {"cont": [], "drift": [], "adj": []} for h in horizons}
    for _ in range(n_boot):
        sample = [months[rng.randrange(len(months))] for _ in months]
        st = stats_for(sample)
        if st is None:
            continue
        for h in horizons:
            c, d, a = st[h]
            boots[h]["cont"].append(c)
            boots[h]["drift"].append(d)
            boots[h]["adj"].append(a)

    result = {}
    for h in horizons:
        c, d, a = point[h]
        result[h] = {
            "cont": c, "cont_ci": _pct_ci(boots[h]["cont"]),
            "drift": d, "drift_ci": _pct_ci(boots[h]["drift"]),
            "adj": a, "adj_ci": _pct_ci(boots[h]["adj"]),
        }
    return result


def simple_mean_ci(values_by_month, n_boot=BOOT_N, seed=SEED):
    """Month-block bootstrap CI for a plain mean of per-event values."""
    months = sorted(values_by_month)
    sums = {m: (sum(values_by_month[m]), len(values_by_month[m])) for m in months}
    total_n = sum(sums[m][1] for m in months)
    if total_n == 0:
        return None
    point = sum(sums[m][0] for m in months) / total_n
    rng = random.Random(seed)
    boots = []
    for _ in range(n_boot):
        sample = [months[rng.randrange(len(months))] for _ in months]
        s = sum(sums[m][0] for m in sample)
        n = sum(sums[m][1] for m in sample)
        if n:
            boots.append(s / n)
    lo, hi = _pct_ci(boots)
    return point, lo, hi, total_n


def main():
    report = {}
    for symbol in SYMBOLS:
        bars, fast, slow, atr = load_symbol(symbol)
        events = build_events(bars, fast, slow, atr)
        uncond = build_uncond(bars, atr)
        n_up = sum(1 for e in events if e["dir"] == 1)
        n_dn = len(events) - n_up

        # --- headline: plain-breakout continuation vs drift ---------------
        headline = month_block_bootstrap(events, uncond, HORIZONS)

        # --- per-direction raw means (context) ----------------------------
        per_dir = {}
        for h in HORIZONS:
            ups = [e["fwd"][h] for e in events if e["dir"] == 1]
            dns = [-e["fwd"][h] for e in events if e["dir"] == -1]
            per_dir[h] = {
                "up_mean": statistics.fmean(ups) if ups else math.nan,
                "dn_mean": statistics.fmean(dns) if dns else math.nan,
            }

        # --- by-year decay (h=24, signed continuation) ---------------------
        by_year = {}
        for h in (12, 24):
            by_year[h] = {}
            years = sorted({e["year"] for e in events})
            for y in years:
                vbm: dict = {}
                for e in events:
                    if e["year"] == y:
                        vbm.setdefault(e["month"], []).append(e["dir"] * e["fwd"][h])
                r = simple_mean_ci(vbm, n_boot=500, seed=SEED + y)
                if r:
                    by_year[h][y] = {"mean": r[0], "ci": (r[1], r[2]), "n": r[3]}

        # --- confirmation stack: nested variants ---------------------------
        variants = {}
        for name, pred in (
            ("A_plain", lambda e: True),
            ("B_buffer", lambda e: e["B"]),
            ("C_buffer_candle", lambda e: e["C"]),
            ("D_full_signal", lambda e: e["D"]),
            ("A_session_only", lambda e: e["session"]),
        ):
            sel = [e for e in events if pred(e)]
            row = {"n": len(sel), "n_up": sum(1 for e in sel if e["dir"] == 1)}
            for h in HORIZONS:
                vbm: dict = {}
                for e in sel:
                    vbm.setdefault(e["month"], []).append(e["dir"] * e["fwd"][h])
                r = simple_mean_ci(vbm)
                row[f"h{h}"] = {"mean": r[0], "ci": (r[1], r[2])} if r else None
            variants[name] = row

        # --- selection differential: D vs A-without-D (same months) --------
        # paired-by-month difference of means, bootstrap over months
        diff = {}
        for h in HORIZONS:
            # per-month (sum, n) for D events and non-D events
            agg: dict = {}
            for e in events:
                m = agg.setdefault(e["month"], [0.0, 0, 0.0, 0])
                v = e["dir"] * e["fwd"][h]
                if e["D"]:
                    m[0] += v
                    m[1] += 1
                else:
                    m[2] += v
                    m[3] += 1
            months = sorted(agg)
            rng = random.Random(SEED + h)
            boots = []
            for _ in range(BOOT_N):
                sample = [months[rng.randrange(len(months))] for _ in months]
                sd = sum(agg[m][0] for m in sample)
                nd = sum(agg[m][1] for m in sample)
                sr = sum(agg[m][2] for m in sample)
                nr = sum(agg[m][3] for m in sample)
                if nd and nr:
                    boots.append(sd / nd - sr / nr)
            nd = sum(agg[m][1] for m in months)
            nr = sum(agg[m][3] for m in months)
            point = (
                sum(agg[m][0] for m in months) / nd
                - sum(agg[m][2] for m in months) / nr
            )
            diff[h] = {
                "point": point,
                "ci": _pct_ci(boots),
                "n_d": nd,
                "n_rest": nr,
            }

        report[symbol] = {
            "n_bars_h1": len(bars),
            "n_events": len(events),
            "n_up": n_up,
            "n_dn": n_dn,
            "headline": {str(h): headline[h] for h in HORIZONS},
            "per_dir": {str(h): per_dir[h] for h in HORIZONS},
            "by_year": {
                str(h): {str(y): v for y, v in by_year[h].items()} for h in by_year
            },
            "variants": variants,
            "D_minus_rest": {str(h): diff[h] for h in HORIZONS},
        }

        # ---- console table -------------------------------------------------
        print(f"\n=== {symbol}  (H1 bars={len(bars)}, breakout events={len(events)}"
              f" up={n_up} dn={n_dn}) ===")
        print(f"{'h':>4} {'cont':>8} {'ci_lo':>8} {'ci_hi':>8} {'drift':>8}"
              f" {'adj':>8} {'adj_lo':>8} {'adj_hi':>8}")
        for h in HORIZONS:
            r = headline[h]
            print(f"{h:>4} {r['cont']:>8.4f} {r['cont_ci'][0]:>8.4f}"
                  f" {r['cont_ci'][1]:>8.4f} {r['drift']:>8.4f} {r['adj']:>8.4f}"
                  f" {r['adj_ci'][0]:>8.4f} {r['adj_ci'][1]:>8.4f}")
        print("  by-year signed continuation:")
        for h in (12, 24):
            for y, v in by_year[h].items():
                print(f"    h={h:>2} {y}: {v['mean']:+.4f}"
                      f" [{v['ci'][0]:+.4f},{v['ci'][1]:+.4f}] n={v['n']}")
        print("  confirmation variants (signed continuation, mean [CI]):")
        for name, row in variants.items():
            cells = " ".join(
                f"h{h}={row[f'h{h}']['mean']:+.3f}" if row[f"h{h}"] else f"h{h}=NA"
                for h in HORIZONS
            )
            print(f"    {name:<16} n={row['n']:>5} up={row['n_up']:>4}  {cells}")
        print("  D_full_signal minus non-D breakouts (selection differential):")
        for h in HORIZONS:
            d = diff[h]
            print(f"    h={h:>2}: {d['point']:+.4f}"
                  f" [{d['ci'][0]:+.4f},{d['ci'][1]:+.4f}]"
                  f" (nD={d['n_d']}, nRest={d['n_rest']})")

    # ---- pooled across symbols (equal-weight per event) ---------------------
    out_path = REPO / "backtest" / "audit" / "premise_breakout_drift_results.json"
    out_path.write_text(json.dumps(report, indent=1, default=str))
    print(f"\nsaved -> {out_path}")


if __name__ == "__main__":
    main()
