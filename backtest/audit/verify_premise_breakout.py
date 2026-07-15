#!/usr/bin/env python3
"""ADVERSARIAL VERIFICATION of the breakout-premise analyst's headline numbers.

Independent implementation (own CSV parse, own H1 aggregation, own ATR,
own event scan, own inference). Verifies, per the analyst's definitions:

  event  : H1 bar b (complete 4xM15 hour) whose close exceeds the max high
           (resp. min low) of the 20 prior H1 bars; b >= 210; b <= n-49.
  cont[h]: mean over events of dir * (close[b+h] - close[b]) / ATR14[b]
           (ATR = SMA of TR, MT5 convention, spread-free closes).
  drift[h]: mean over ALL valid bars of (close[b+h]-close[b])/ATR14[b].
  adj[h] : cont - drift * mean(dir).

Inference re-derived TWO independent ways (neither reuses the analyst's
seed 12345 / 777):
  (1) calendar-month block bootstrap, 2000 resamples, seed 20260714;
  (2) analytic cluster-robust (by calendar month) SE on the event means.
Plus a sign-permutation p (2000 perms, seed 424242) for XAUUSD/USDJPY.

Also re-derives: per-direction XAUUSD split, USDJPY by-year decay + the
2026 inversion (month AND ISO-week block bootstrap - months are too coarse
inside a half year), GBPUSD buffered-vs-plain h24, cost yardstick in ATR
units (full-sample mean ATR and event-time ATR).

Stdlib only. All randomness seeded.
"""
from __future__ import annotations

import csv
import json
import math
import random
from datetime import datetime
from pathlib import Path

REPO = Path(r"C:/Users/Sanique Richards/Documents/Homework Heroes/Pokemon/Bot-EA")
SYMBOLS = ["EURUSD", "GBPUSD", "USDJPY", "XAUUSD"]
HORIZONS = [1, 4, 12, 24, 48]
DON = 20
ATRP = 14
MIN_INDEX = 210
HMAX = 48
BOOT_N = 2000
BOOT_SEED = 20260714
PERM_N = 2000
PERM_SEED = 424242
FALLBACK_SPREAD_PTS = {"EURUSD": 10.0, "GBPUSD": 15.0, "USDJPY": 28.0, "XAUUSD": 16.0}
BUFFER_ATR = 0.5  # engine entry_buffer_atr


# ---------------------------------------------------------------- data layer
def h1_bars(symbol: str):
    """Own parser + aggregation: complete hours only (4 M15 bars at :00/:15/:30/:45)."""
    path = REPO / "backtest" / "data" / "derivM15" / f"{symbol}.csv"
    per_hour: dict = {}
    with path.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            t = datetime.fromisoformat(row["time"])
            key = (t.year, t.month, t.day, t.hour)
            per_hour.setdefault(key, {})[t.minute] = (
                float(row["open"]), float(row["high"]), float(row["low"]), float(row["close"])
            )
    bars = []
    for key in sorted(per_hour):
        q = per_hour[key]
        if sorted(q) != [0, 15, 30, 45]:
            continue
        bars.append(
            (
                datetime(key[0], key[1], key[2], key[3]),
                q[0][0],
                max(q[m][1] for m in q),
                min(q[m][2] for m in q),
                q[45][3],
            )
        )
    return bars


def atr_series(bars):
    """SMA-of-TR ATR, MT5 style: tr[0] treated as 0, value from index ATRP on."""
    n = len(bars)
    atr = [math.nan] * n
    tr = [0.0] * n
    for i in range(1, n):
        pc = bars[i - 1][4]
        tr[i] = max(bars[i][2], pc) - min(bars[i][3], pc)
    s = 0.0
    for i in range(n):
        s += tr[i]
        if i > ATRP:
            s -= tr[i - ATRP]
        if i >= ATRP:
            atr[i] = s / ATRP
    return atr


# ------------------------------------------------------------ event building
def build(symbol: str):
    bars = h1_bars(symbol)
    atr = atr_series(bars)
    n = len(bars)
    events = []   # dict: b, t, dir, buffered, atr, fwd{h}
    uncond = []   # (month_key, {h: fwd})
    for b in range(MIN_INDEX, n - HMAX):
        a = atr[b]
        if not (math.isfinite(a) and a > 0.0):
            continue
        c = bars[b][4]
        fwd = {h: (bars[b + h][4] - c) / a for h in HORIZONS}
        mkey = (bars[b][0].year, bars[b][0].month)
        uncond.append((mkey, fwd))
        hi = max(bars[j][2] for j in range(b - DON, b))
        lo = min(bars[j][3] for j in range(b - DON, b))
        if c > hi:
            d, buffered = 1, c > hi + BUFFER_ATR * a
        elif c < lo:
            d, buffered = -1, c < lo - BUFFER_ATR * a
        else:
            continue
        t = bars[b][0]
        events.append(
            {
                "t": t,
                "month": mkey,
                "week": t.isocalendar()[:2],
                "year": t.year,
                "dir": d,
                "buf": buffered,
                "atr": a,
                "fwd": fwd,
            }
        )
    return bars, atr, events, uncond


# ---------------------------------------------------------------- inference
def mean(xs):
    return sum(xs) / len(xs) if xs else math.nan


def pct_ci(xs, lo=0.025, hi=0.975):
    xs = sorted(xs)
    return xs[max(0, int(lo * len(xs)))], xs[min(len(xs) - 1, int(hi * len(xs)))]


def block_boot_mean(pairs, n_boot=BOOT_N, seed=BOOT_SEED):
    """pairs = [(block_key, value)]; bootstrap the mean by resampling blocks."""
    agg: dict = {}
    for k, v in pairs:
        s, c = agg.get(k, (0.0, 0))
        agg[k] = (s + v, c + 1)
    keys = sorted(agg)
    point = sum(agg[k][0] for k in keys) / sum(agg[k][1] for k in keys)
    rng = random.Random(seed)
    boots = []
    for _ in range(n_boot):
        s = c = 0.0
        for _ in keys:
            k = keys[rng.randrange(len(keys))]
            s += agg[k][0]
            c += agg[k][1]
        if c:
            boots.append(s / c)
    return point, pct_ci(boots)


def cluster_se(pairs):
    """Analytic cluster-robust SE for a mean: SE^2 = sum_g (sum_i (x_gi - xbar))^2 / N^2."""
    n = len(pairs)
    xbar = mean([v for _, v in pairs])
    per: dict = {}
    for k, v in pairs:
        per[k] = per.get(k, 0.0) + (v - xbar)
    var = sum(u * u for u in per.values()) / (n * n)
    g = len(per)
    # small-sample cluster correction g/(g-1)
    var *= g / (g - 1) if g > 1 else 1.0
    return xbar, math.sqrt(var), g


def boot_adjusted(events, uncond, h, n_boot=BOOT_N, seed=BOOT_SEED):
    """Month-block bootstrap of cont, drift, adj = cont - drift*mean(dir)."""
    per: dict = {}
    for e in events:
        m = per.setdefault(e["month"], [0.0, 0, 0.0, 0.0, 0])
        m[0] += e["dir"] * e["fwd"][h]
        m[1] += 1
        m[2] += e["dir"]
    for mk, fwd in uncond:
        m = per.setdefault(mk, [0.0, 0, 0.0, 0.0, 0])
        m[3] += fwd[h]
        m[4] += 1
    keys = sorted(per)

    def stat(sample):
        sc = nc = sd = su = nu = 0.0
        for k in sample:
            m = per[k]
            sc += m[0]
            nc += m[1]
            sd += m[2]
            su += m[3]
            nu += m[4]
        if not nc or not nu:
            return None
        cont = sc / nc
        drift = su / nu
        return cont, drift, cont - drift * (sd / nc)

    point = stat(keys)
    rng = random.Random(seed)
    boots = ([], [], [])
    for _ in range(n_boot):
        st = stat([keys[rng.randrange(len(keys))] for _ in keys])
        if st:
            for i in range(3):
                boots[i].append(st[i])
    return point, tuple(pct_ci(b) for b in boots)


def perm_p(events, h, n_perm=PERM_N, seed=PERM_SEED):
    dirs = [e["dir"] for e in events]
    fwds = [e["fwd"][h] for e in events]
    real = mean([d * f for d, f in zip(dirs, fwds)])
    rng = random.Random(seed)
    stats = []
    pd = list(dirs)
    for _ in range(n_perm):
        rng.shuffle(pd)
        stats.append(mean([d * f for d, f in zip(pd, fwds)]))
    mu = mean(stats)
    extreme = sum(1 for s in stats if abs(s - mu) >= abs(real - mu))
    return real, (extreme + 1) / (n_perm + 1)


# --------------------------------------------------------------------- main
def main():
    meta = json.loads((REPO / "backtest" / "deriv_broker_meta.json").read_text())["symbols"]
    out = {}
    for sym in SYMBOLS:
        bars, atr, events, uncond = build(sym)
        n_up = sum(1 for e in events if e["dir"] == 1)
        rep = {
            "h1_bars": len(bars),
            "span": f"{bars[0][0]} .. {bars[-1][0]}",
            "n_events": len(events),
            "n_up": n_up,
            "n_dn": len(events) - n_up,
        }
        print(f"\n=== {sym}: {rep['h1_bars']} H1 bars {rep['span']} | events={rep['n_events']} "
              f"(up {n_up} / dn {rep['n_dn']})")

        # headline continuation + drift-adjustment at every horizon
        rep["horizons"] = {}
        for h in HORIZONS:
            (cont, drift, adj), (ci_c, ci_d, ci_a) = boot_adjusted(events, uncond, h)
            xbar, se, g = cluster_se([(e["month"], e["dir"] * e["fwd"][h]) for e in events])
            assert abs(xbar - cont) < 1e-12
            rep["horizons"][h] = {
                "cont": cont, "cont_ci_boot": ci_c, "cont_pm2se": (cont - 2 * se, cont + 2 * se),
                "drift": drift, "drift_ci": ci_d,
                "adj": adj, "adj_ci": ci_a, "n_month_clusters": g,
            }
            print(f"  h={h:>2} cont={cont:+.4f} boot[{ci_c[0]:+.3f},{ci_c[1]:+.3f}] "
                  f"±2se[{cont-2*se:+.3f},{cont+2*se:+.3f}] drift={drift:+.4f} "
                  f"adj={adj:+.4f} [{ci_a[0]:+.3f},{ci_a[1]:+.3f}]")

        # permutation p at h24/h48 (and h12 for USDJPY)
        rep["perm"] = {}
        for h in ([12, 24] if sym == "USDJPY" else [24, 48]):
            real, p = perm_p(events, h)
            rep["perm"][h] = {"cont": real, "p_two_sided": p}
            print(f"  perm h={h}: cont={real:+.4f} p={p:.4f}")

        # per-direction split
        rep["per_dir"] = {}
        for h in (24, 48):
            up = mean([e["fwd"][h] for e in events if e["dir"] == 1])
            dn = mean([-e["fwd"][h] for e in events if e["dir"] == -1])
            rep["per_dir"][h] = {"long_cont": up, "short_cont": dn}
            print(f"  per-dir h={h}: long {up:+.4f} short {dn:+.4f}")

        # by-year (h12 and h24) with month-block bootstrap per year
        rep["by_year"] = {}
        for h in (12, 24):
            rep["by_year"][h] = {}
            for y in sorted({e["year"] for e in events}):
                pairs = [(e["month"], e["dir"] * e["fwd"][h]) for e in events if e["year"] == y]
                pt, ci = block_boot_mean(pairs, seed=BOOT_SEED + y + h)
                # week-block variant (finer blocks for partial years)
                pairsw = [(e["week"], e["dir"] * e["fwd"][h]) for e in events if e["year"] == y]
                ptw, ciw = block_boot_mean(pairsw, seed=BOOT_SEED + 1000 + y + h)
                rep["by_year"][h][y] = {
                    "cont": pt, "ci_month": ci, "ci_week": ciw, "n": len(pairs),
                }
                print(f"  {y} h={h:>2}: {pt:+.4f} month[{ci[0]:+.3f},{ci[1]:+.3f}] "
                      f"week[{ciw[0]:+.3f},{ciw[1]:+.3f}] n={len(pairs)}")

        # buffered subset (engine 0.5*ATR) at h24
        for h in (24,):
            plain = [(e["month"], e["dir"] * e["fwd"][h]) for e in events]
            buff = [(e["month"], e["dir"] * e["fwd"][h]) for e in events if e["buf"]]
            pp, pci = block_boot_mean(plain, seed=BOOT_SEED + 7)
            bp, bci = block_boot_mean(buff, seed=BOOT_SEED + 8) if buff else (math.nan, (0, 0))
            rep["buffer_h24"] = {"plain": pp, "buffered": bp, "n_buffered": len(buff)}
            print(f"  buffer h=24: plain {pp:+.4f} -> buffered {bp:+.4f} (n_buf={len(buff)})")

        # cost yardstick
        m = meta[sym]
        spread_price = FALLBACK_SPREAD_PTS[sym] * m["point"]
        dollars_per_price = m["trade_tick_value_loss"] / m["trade_tick_size"]
        comm_price = 5.0 / dollars_per_price
        rt = spread_price + comm_price
        mean_atr_all = mean([a for a in atr if math.isfinite(a)])
        mean_atr_ev = mean([e["atr"] for e in events])
        rep["cost"] = {
            "rt_price": rt,
            "rt_atr_fullsample": rt / mean_atr_all,
            "rt_atr_at_events": rt / mean_atr_ev,
            "mean_atr_fullsample": mean_atr_all,
            "mean_atr_at_events": mean_atr_ev,
        }
        print(f"  cost: rt={rt:.5f} = {rt/mean_atr_all:.4f} ATR(full) "
              f"= {rt/mean_atr_ev:.4f} ATR(at events)")

        out[sym] = rep

    path = REPO / "backtest" / "audit" / "verify_premise_breakout_results.json"
    path.write_text(json.dumps(out, indent=1, default=str))
    print(f"\nsaved -> {path}")


if __name__ == "__main__":
    main()
