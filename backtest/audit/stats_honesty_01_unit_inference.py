"""STATISTICAL HONESTY AUDIT - part 1: unit-level expectancy inference.

Dimension: statistical honesty / selection history of the FTMO Quant EA screen.

Reads the canonical 'measured' ledgers (FTMO-guarded, 1x spread), groups
scale-out tranches into UNITS (one entry = one unit; tranches share
entry_time/entry/side), computes per-unit R = sum(pnl)/risk_budget where
risk_budget is recovered exactly from the ledger as sum(pnl_i/r_i) over the
unit's tranches (the engine writes r_i = pnl_i / (risk_budget*lot_fraction_i),
so the bases sum to the unit's full risk budget). Verified: reconstructed net
pnl reconciles with the ledger net_profit to the cent for all 4 symbols and
no tranche has r == 0.

Inference per symbol, pooled, and per IS/OOS split:
  (a) naive t-stat on mean unit R
  (b) cluster-robust t (clusters = entry calendar day; pooled run clusters by
      day ACROSS symbols since FX majors share regime)
  (c) weekly-block cluster bootstrap CI (resample ISO-week blocks with
      replacement, >=2000 resamples, seeded; stability checked across seeds)
Plus: power analysis - units needed to detect a true +0.05R edge.

Stdlib + numpy (numpy 2.4.2 verified present; math fallback not needed).
"""

import collections
import json
import math
import os
import random
import statistics
import datetime as dt

BASE = r"C:/Users/Sanique Richards/Documents/Homework Heroes/Pokemon/Bot-EA/backtest/results"
FILES = {
    "EURUSD": "EURUSD.json",
    "GBPUSD": "GBPUSD_relaxedgate.json",
    "USDJPY": "USDJPY.json",
    "XAUUSD": "XAUUSD.json",
}
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "stats_honesty_01_results.json")


def load_units(symbol):
    """Return list of dicts: {r, entry_time (datetime), oos} one per unit."""
    d = json.load(open(os.path.join(BASE, FILES[symbol])))
    tranches = d["measured"]["trades"]
    groups = collections.defaultdict(list)
    for t in tranches:
        groups[(t["entry_time"], t["entry"], t["side"])].append(t)
    units = []
    net_check = 0.0
    for (etime, _entry, _side), ts in sorted(groups.items()):
        bases = [t["pnl"] / t["r"] for t in ts]  # exact risk bases (no r==0, verified)
        risk_budget = sum(bases)
        pnl = sum(t["pnl"] for t in ts)
        net_check += pnl
        assert risk_budget > 0, (symbol, etime)
        units.append({
            "r": pnl / risk_budget,
            "entry_time": dt.datetime.fromisoformat(etime),
            "oos": ts[0]["oos"],
        })
    ledger_net = d["measured"]["all"]["net_profit"]
    assert abs(net_check - ledger_net) < 0.01, (symbol, net_check, ledger_net)
    units.sort(key=lambda u: u["entry_time"])
    return units


def naive_t(rs):
    n = len(rs)
    m = statistics.fmean(rs)
    sd = statistics.stdev(rs) if n > 1 else float("nan")
    t = m / (sd / math.sqrt(n)) if n > 1 and sd > 0 else float("nan")
    return n, m, sd, t


def cluster_t(units, key_fn):
    """Liang-Zeger cluster-robust t on mean R with G/(G-1) small-sample factor.

    t = mean / se_cluster,  Var(mean) = G/(G-1) * sum_g u_g^2 / n^2,
    u_g = sum_{i in g} (r_i - mean).  df = G-1.
    """
    rs = [u["r"] for u in units]
    n = len(rs)
    m = statistics.fmean(rs)
    scores = collections.defaultdict(float)
    for u in units:
        scores[key_fn(u)] += u["r"] - m
    G = len(scores)
    if G < 2:
        return G, float("nan"), float("nan")
    var = (G / (G - 1)) * sum(s * s for s in scores.values()) / (n * n)
    se = math.sqrt(var)
    return G, se, m / se if se > 0 else float("nan")


def week_key(u):
    iso = u["entry_time"].isocalendar()
    return (iso[0], iso[1])


def day_key(u):
    return u["entry_time"].date()


def block_bootstrap_ci(units, n_boot=2000, seed=42, block_key=week_key):
    """Cluster bootstrap by week block: resample G observed week-blocks with
    replacement, pool their units, mean R per resample. Percentile 95% CI and
    bootstrap p (fraction of resampled means <= 0, doubled, capped at 1)."""
    blocks = collections.defaultdict(list)
    for u in units:
        blocks[block_key(u)].append(u["r"])
    keys = sorted(blocks.keys())
    G = len(keys)
    rng = random.Random(seed)
    means = []
    for _ in range(n_boot):
        tot, cnt = 0.0, 0
        for _ in range(G):
            k = keys[rng.randrange(G)]
            tot += sum(blocks[k])
            cnt += len(blocks[k])
        means.append(tot / cnt)
    means.sort()
    lo = means[int(0.025 * n_boot)]
    hi = means[int(0.975 * n_boot) - 1]
    frac_le0 = sum(1 for m in means if m <= 0.0) / n_boot
    p_two = min(1.0, 2.0 * min(frac_le0, 1.0 - frac_le0))
    return G, lo, hi, frac_le0, p_two


def norm_cdf(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def t_p_two_sided(t, df):
    try:
        from scipy import stats as st
        return float(2 * st.t.sf(abs(t), df))
    except Exception:
        return 2 * (1 - norm_cdf(abs(t)))  # normal approx fallback


def analyze(label, units, results):
    rs = [u["r"] for u in units]
    n, m, sd, t = naive_t(rs)
    Gd, se_d, t_d = cluster_t(units, day_key)
    Gw, se_w, t_w = cluster_t(units, week_key)
    Gb, lo, hi, frac_le0, p_boot = block_bootstrap_ci(units)
    # seed stability check for the bootstrap (100 seeds, record CI-lo spread)
    los, his = [], []
    for s in range(100):
        _, l2, h2, _, _ = block_bootstrap_ci(units, n_boot=500, seed=1000 + s)
        los.append(l2)
        his.append(h2)
    row = {
        "n_units": n,
        "mean_r": m,
        "sd_r": sd,
        "t_naive": t,
        "p_naive": t_p_two_sided(t, n - 1) if n > 1 else None,
        "clusters_day": Gd,
        "se_cluster_day": se_d,
        "t_cluster_day": t_d,
        "p_cluster_day": t_p_two_sided(t_d, Gd - 1) if Gd > 1 else None,
        "clusters_week": Gw,
        "t_cluster_week": t_w,
        "p_cluster_week": t_p_two_sided(t_w, Gw - 1) if Gw > 1 else None,
        "boot_blocks_week": Gb,
        "boot_ci95": [lo, hi],
        "boot_frac_le0": frac_le0,
        "boot_p_two_sided": p_boot,
        "boot_ci_lo_seed_range": [min(los), max(los)],
        "boot_ci_hi_seed_range": [min(his), max(his)],
    }
    results[label] = row
    print(f"{label:28s} n={n:4d} meanR={m:+.4f} sd={sd:.3f} "
          f"t_naive={t:+.2f} t_clustDay={t_d:+.2f} (G={Gd}) "
          f"bootCI95=[{lo:+.4f},{hi:+.4f}] p_boot={p_boot:.3f}")
    return row


def power_n(sd, effect=0.05, alpha=0.05, power=0.80):
    """Two-sided alpha, given power, one-sample z approx."""
    za = 1.959963984540054  # z_{0.975}
    zb = {0.80: 0.8416212335729143, 0.95: 1.6448536269514722}[power]
    return ((za + zb) * sd / effect) ** 2


def main():
    results = {}
    all_units = []
    per_symbol_units = {}
    for sym in FILES:
        units = load_units(sym)
        per_symbol_units[sym] = units
        all_units.extend(units)
        analyze(sym, units, results)
        analyze(sym + "_IS", [u for u in units if not u["oos"]], results)
        analyze(sym + "_OOS", [u for u in units if u["oos"]], results)
    all_units.sort(key=lambda u: u["entry_time"])
    analyze("POOLED_4SYM", all_units, results)
    analyze("POOLED_4SYM_OOS", [u for u in all_units if u["oos"]], results)
    analyze("POOLED_4SYM_IS", [u for u in all_units if not u["oos"]], results)

    # power / sample adequacy
    power = {}
    for label in ["EURUSD", "POOLED_4SYM"]:
        sd = results[label]["sd_r"]
        n_have = results[label]["n_units"]
        n80 = power_n(sd, power=0.80)
        n95 = power_n(sd, power=0.95)
        # trades per year observed
        units = all_units if label.startswith("POOLED") else per_symbol_units[label]
        span_years = (units[-1]["entry_time"] - units[0]["entry_time"]).days / 365.25
        upy = n_have / span_years
        power[label] = {
            "sd_r": sd, "n_available": n_have,
            "n_needed_alpha5_power80": n80,
            "n_needed_alpha5_power95": n95,
            "units_per_year": upy,
            "years_needed_power80": n80 / upy,
            "years_needed_power95": n95 / upy,
        }
        print(f"POWER {label}: sd={sd:.3f} -> n(80%)={n80:.0f}, n(95%)={n95:.0f} "
              f"vs available {n_have} ({upy:.0f} units/yr -> {n80/upy:.0f} / {n95/upy:.0f} years)")
    results["power_true_edge_+0.05R"] = power

    with open(OUT, "w") as f:
        json.dump(results, f, indent=1)
    print("saved", OUT)


if __name__ == "__main__":
    main()
