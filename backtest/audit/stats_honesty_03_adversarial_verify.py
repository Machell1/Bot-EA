"""ADVERSARIAL VERIFICATION of the statistics-selection analyst's headline.

Independent re-derivation (written from scratch, not reusing stats_honesty_01/02):

  V1  Unit construction + risk recovery, with TWO independent cross-checks:
      (a) engine-config magnitude check: recovered risk budget should be
          ~ equity * risk_pct/100 / sizing_cost_reserve (0.35%/1.10 ~ 0.318%
          of equity), lot-rounding tolerance;
      (b) reconciliation of reconstructed net pnl to ledger net_profit.
  V2  Pooled 4-symbol per-unit expectancy: n, mean R, sd, naive t,
      entry-day cluster-robust (Liang-Zeger) t.  CLAIM: -0.0166R, t_cl=-0.27,
      n=459.
  V3  Per-symbol means/t's.  CLAIM table incl. USDJPY -0.1396 (t_cl -1.23).
  V4  EURUSD OOS cell: n=28, mean +0.2587R, naive t=1.363, clustered t=1.26,
      OOS net $2,277.68 == ledger oos net_profit.
  V5  Re-split test, MY OWN implementation (random.sample of the OOS set
      rather than shuffle-split), fresh seeds 101..120, 20k draws each:
      p(random OOS mean >= +0.2587) claimed 0.061-0.094 (unit level);
      any IS<0<OOS sign flip claimed ~28%.  XAUUSD mirror flip claimed
      IS +0.076 / OOS -0.057 with OOS beaten by 72-78% of splits.
  V6  Analytic numbers via statistics.NormalDist (independent of the
      analyst's Acklam implementation): Bonferroni z ladder, Bailey/LdP
      expected-max-of-N-nulls ladder, power n for +0.05R.
  V7  Ledger headline expectancy_r (claimed 0.514 tranche artifact) and
      canonical return_pct per symbol.

Stdlib only.
"""

import collections
import datetime as dt
import json
import math
import os
import random
import statistics
from statistics import NormalDist

BASE = r"C:/Users/Sanique Richards/Documents/Homework Heroes/Pokemon/Bot-EA/backtest/results"
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "stats_honesty_03_verify_results.json")

FILES = {
    "EURUSD": "EURUSD.json",
    "GBPUSD": "GBPUSD_relaxedgate.json",
    "USDJPY": "USDJPY.json",
    "XAUUSD": "XAUUSD.json",
}

ND = NormalDist()
report = {}


# ---------------------------------------------------------------- V1: units
def build_units(sym):
    d = json.load(open(os.path.join(BASE, FILES[sym])))
    m = d["measured"]
    tr = m["trades"]
    zero_r = [t for t in tr if t["r"] == 0]
    groups = collections.defaultdict(list)
    for t in tr:
        groups[t["entry_time"]].append(t)
    # collision check: same entry_time, different (entry, side) would merge
    # two distinct units -> verify uniqueness
    collisions = sum(
        1 for ts in groups.values()
        if len({(t["entry"], t["side"]) for t in ts}) > 1
    )
    units = []
    for etime in sorted(groups):
        ts = groups[etime]
        risk = sum(t["pnl"] / t["r"] for t in ts if t["r"] != 0)
        pnl = sum(t["pnl"] for t in ts)
        units.append({
            "sym": sym,
            "t": dt.datetime.fromisoformat(etime),
            "risk": risk,
            "pnl": pnl,
            "r": pnl / risk,
            "oos": ts[0]["oos"],
            "n_tranches": len(ts),
        })
    recon_net = sum(u["pnl"] for u in units)
    return d, units, {
        "n_tranches": len(tr),
        "n_zero_r_tranches": len(zero_r),
        "n_units": len(units),
        "entrytime_collisions": collisions,
        "recon_net_vs_ledger": [recon_net, m["all"]["net_profit"]],
        "recon_ok": abs(recon_net - m["all"]["net_profit"]) < 0.01,
    }


def mean_sd_t(rs):
    n = len(rs)
    m = statistics.fmean(rs)
    sd = statistics.stdev(rs)
    return n, m, sd, m / (sd / math.sqrt(n))


def lz_cluster_t(pairs):
    """pairs = [(cluster_key, r)]; Liang-Zeger robust t for the mean."""
    rs = [r for _, r in pairs]
    n = len(rs)
    mu = statistics.fmean(rs)
    agg = collections.defaultdict(float)
    for k, r in pairs:
        agg[k] += r - mu
    G = len(agg)
    var = (G / (G - 1)) * sum(v * v for v in agg.values()) / (n * n)
    return G, mu / math.sqrt(var)


all_units = []
report["V1_unit_construction"] = {}
report["V7_ledger_headlines"] = {}
for sym in FILES:
    d, units, chk = build_units(sym)
    report["V1_unit_construction"][sym] = chk
    all_units.extend(units)
    report["V7_ledger_headlines"][sym] = {
        "return_pct": d["measured"]["return_pct"],
        "ledger_expectancy_r_all": d["measured"]["all"]["expectancy_r"],
        "ledger_oos_net_profit": d["measured"]["oos"]["net_profit"],
    }

# engine-config magnitude cross-check on EURUSD: risk ~0.318% of running equity.
# We don't track equity per entry; check first unit vs initial 100k and the
# distribution of risk/(0.318% * 100k) stays within the ~[0.9, 1.15] band the
# 1.06% total return + lot rounding allows.
eur_units = [u for u in all_units if u["sym"] == "EURUSD"]
expected0 = 100_000 * 0.0035 / 1.10
ratios = [u["risk"] / expected0 for u in eur_units]
report["V1_unit_construction"]["EURUSD_risk_magnitude_check"] = {
    "expected_risk_at_100k": expected0,
    "first_unit_risk": eur_units[0]["risk"],
    "risk_ratio_min": min(ratios),
    "risk_ratio_max": max(ratios),
    "risk_ratio_mean": statistics.fmean(ratios),
}

# ------------------------------------------------- V2/V3: expectancy tables
def cell(units):
    rs = [u["r"] for u in units]
    n, m, sd, t = mean_sd_t(rs)
    G, tcl = lz_cluster_t([((u["sym"], u["t"].date()) if False else u["t"].date(), u["r"]) for u in units])
    return {"n": n, "mean_r": m, "sd_r": sd, "t_naive": t,
            "clusters_day": G, "t_cluster_day": tcl}


report["V2_pooled"] = cell(all_units)
report["V3_per_symbol"] = {}
for sym in FILES:
    su = [u for u in all_units if u["sym"] == sym]
    report["V3_per_symbol"][sym] = cell(su)
    report["V3_per_symbol"][sym]["IS_mean"] = statistics.fmean(
        [u["r"] for u in su if not u["oos"]])
    report["V3_per_symbol"][sym]["OOS_mean"] = statistics.fmean(
        [u["r"] for u in su if u["oos"]])

# ---------------------------------------------------- V4: EURUSD OOS cell
eur_oos = [u for u in eur_units if u["oos"]]
report["V4_eurusd_oos"] = cell(eur_oos)
report["V4_eurusd_oos"]["oos_net_dollars"] = sum(u["pnl"] for u in eur_oos)
report["V4_eurusd_oos"]["ledger_oos_net"] = \
    report["V7_ledger_headlines"]["EURUSD"]["ledger_oos_net_profit"]
# p-values (normal approx and note df)
t_n = report["V4_eurusd_oos"]["t_naive"]
t_c = report["V4_eurusd_oos"]["t_cluster_day"]
report["V4_eurusd_oos"]["p_naive_normal_approx"] = 2 * (1 - ND.cdf(abs(t_n)))
report["V4_eurusd_oos"]["p_cluster_normal_approx"] = 2 * (1 - ND.cdf(abs(t_c)))


# ------------------------------------------------------- V5: re-split test
def resplit(units, n_draws=20000, seeds=range(101, 121)):
    rs = [u["r"] for u in units]
    n = len(rs)
    n_oos = sum(1 for u in units if u["oos"])
    obs_oos = statistics.fmean([u["r"] for u in units if u["oos"]])
    obs_is = statistics.fmean([u["r"] for u in units if not u["oos"]])
    tot = sum(rs)
    p_ge, p_flip = [], []
    for sd in seeds:
        rng = random.Random(sd)
        ge = flip = 0
        idx = list(range(n))
        for _ in range(n_draws):
            pick = rng.sample(idx, n_oos)
            s = sum(rs[i] for i in pick)
            m_oos = s / n_oos
            m_is = (tot - s) / (n - n_oos)
            if m_oos >= obs_oos:
                ge += 1
            if m_is < 0.0 < m_oos:
                flip += 1
        p_ge.append(ge / n_draws)
        p_flip.append(flip / n_draws)
    return {
        "n": n, "n_oos": n_oos, "obs_is_mean": obs_is, "obs_oos_mean": obs_oos,
        "p_oos_ge_obs_min": min(p_ge), "p_oos_ge_obs_max": max(p_ge),
        "p_oos_ge_obs_mean": statistics.fmean(p_ge),
        "p_any_signflip_mean": statistics.fmean(p_flip),
        "n_draws_per_seed": n_draws, "n_seeds": len(list(seeds)),
    }


report["V5_resplit_EURUSD"] = resplit(eur_units)
xau_units = [u for u in all_units if u["sym"] == "XAUUSD"]
report["V5_resplit_XAUUSD"] = resplit(xau_units)

# --------------------------------------------- V6: analytic verifications
gamma = 0.5772156649015329
def emax(N):
    return (1 - gamma) * ND.inv_cdf(1 - 1 / N) + gamma * ND.inv_cdf(1 - 1 / (N * math.e))

report["V6_analytic"] = {
    "bonferroni_z": {N: ND.inv_cdf(1 - 0.025 / N) for N in (4, 8, 9, 13, 36, 100)},
    "expected_max_null_t": {N: emax(N) for N in (4, 8, 9, 36, 100)},
}
za, z80, z95 = ND.inv_cdf(0.975), ND.inv_cdf(0.80), ND.inv_cdf(0.95)
for label, sd in [("EURUSD", report["V3_per_symbol"]["EURUSD"]["sd_r"]),
                  ("pooled", report["V2_pooled"]["sd_r"])]:
    report["V6_analytic"]["power_n_+0.05R_" + label] = {
        "sd": sd,
        "n_80pct": ((za + z80) * sd / 0.05) ** 2,
        "n_95pct": ((za + z95) * sd / 0.05) ** 2,
    }
span_y = (max(u["t"] for u in eur_units) - min(u["t"] for u in eur_units)).days / 365.25
report["V6_analytic"]["eurusd_units_per_year"] = len(eur_units) / span_y
span_all = (max(u["t"] for u in all_units) - min(u["t"] for u in all_units)).days / 365.25
report["V6_analytic"]["pooled_units_per_year"] = len(all_units) / span_all

with open(OUT, "w") as f:
    json.dump(report, f, indent=1, default=str)

print(json.dumps(report, indent=1, default=str))
