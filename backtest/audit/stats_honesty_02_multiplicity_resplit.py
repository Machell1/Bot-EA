"""STATISTICAL HONESTY AUDIT - part 2: multiple-testing discount + OOS paradox.

(A) Multiple-testing discount.
    Documented selection history (docs/BACKTEST_RESULTS.md "Changes over the
    original defaults, in the order they were added", README, DATA.md):
      1 breakout buffer, 2 session window, 3 wider stop, 4 candle/wick,
      5 HTF confluence, 6 skip-hour, 7 vol regime guard, 8 market structure
      (tried, default off = a tested-and-rejected trial), 9 scale-out+pyramid
    = 9 sequential accepted design decisions (each is >=1 trial; rejected
    parameter values along the way are NOT recorded, so 9 is a hard floor).
    x 4 symbols screened, x 2 cost levels reported, + 3 engine re-screens
    (+4.03% -> +1.28% -> +1.06%) + at least 1 gate variant (GBPUSD relaxedgate).
    We compute the Bonferroni-required z and the Bailey/Lopez-de-Prado expected
    maximum of N null t-stats, E[max_N] ~ (1-g)*Phi^-1(1-1/N) + g*Phi^-1(1-1/(N e)),
    for N in a ladder {9, 13, 24, 36, 50, 100}, and compare with the observed
    best split (EURUSD OOS) t after day-clustering.

(B) OOS paradox: EURUSD IS mean R is negative, OOS positive. Under the null
    that unit R's are exchangeable over time (no time-localized edge), how
    often does a random 70/30 re-split of the unit sequence look at least as
    good OOS? Two designs, both seeded:
      B1 unit-level permutation (10,000 perms): shuffle units, first n_IS ->
         "IS", rest -> "OOS"; p = frac(perm OOS mean >= observed OOS mean).
      B2 week-block permutation (10,000 perms): shuffle ISO-week blocks
         (preserves within-week correlation), split at the same OOS unit count.
    Also: fraction of re-splits with the full sign-flip pattern
    (IS mean <= observed IS mean AND OOS mean >= observed OOS mean), and the
    unconditional frequency of ANY sign flip (IS<0<OOS), which shows how
    unremarkable the pattern is. Stability across 100 seeds is recorded.

Stdlib + optional scipy for exact t tail; numpy not required.
"""

import collections
import json
import math
import os
import random
import statistics
import datetime as dt

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = r"C:/Users/Sanique Richards/Documents/Homework Heroes/Pokemon/Bot-EA/backtest/results"
OUT = os.path.join(HERE, "stats_honesty_02_results.json")

FILES = {
    "EURUSD": "EURUSD.json",
    "GBPUSD": "GBPUSD_relaxedgate.json",
    "USDJPY": "USDJPY.json",
    "XAUUSD": "XAUUSD.json",
}


def load_units(symbol):
    d = json.load(open(os.path.join(BASE, FILES[symbol])))
    groups = collections.defaultdict(list)
    for t in d["measured"]["trades"]:
        groups[(t["entry_time"], t["entry"], t["side"])].append(t)
    units = []
    for (etime, _e, _s), ts in sorted(groups.items()):
        risk = sum(t["pnl"] / t["r"] for t in ts)
        units.append({
            "r": sum(t["pnl"] for t in ts) / risk,
            "entry_time": dt.datetime.fromisoformat(etime),
            "oos": ts[0]["oos"],
        })
    units.sort(key=lambda u: u["entry_time"])
    return units


def inv_norm(p):
    """Acklam/Beasley-Springer-Moro inverse normal CDF (stdlib)."""
    try:
        from scipy import stats as st
        return float(st.norm.ppf(p))
    except Exception:
        pass
    a = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00]
    b = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
         3.754408661907416e+00]
    plow, phigh = 0.02425, 1 - 0.02425
    if p < plow:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / \
               ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    if p > phigh:
        q = math.sqrt(-2 * math.log(1 - p))
        return -(((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / \
                ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    q = p - 0.5
    r = q * q
    return (((((a[0]*r+a[1])*r+a[2])*r+a[3])*r+a[4])*r+a[5])*q / \
           (((((b[0]*r+b[1])*r+b[2])*r+b[3])*r+b[4])*r+1)


EULER_GAMMA = 0.5772156649015329


def expected_max_null_t(N):
    """Bailey & Lopez de Prado approximation to E[max of N iid N(0,1)]."""
    if N < 2:
        return 0.0
    return (1 - EULER_GAMMA) * inv_norm(1 - 1.0 / N) + \
        EULER_GAMMA * inv_norm(1 - 1.0 / (N * math.e))


def multiplicity_table():
    # 4 = symbols; 8 = symbol x split cells; 9 = documented design decisions;
    # 13 = 9 decisions + 4 symbols; 24/36 = decisions x symbols (partial/full
    # interaction); 50/100 = allowance for unlogged rejected parameter values
    ladder = [4, 8, 9, 13, 24, 36, 50, 100]
    rows = {}
    for N in ladder:
        z_bonf = inv_norm(1 - 0.025 / N)   # two-sided 5% family
        rows[str(N)] = {
            "bonferroni_z_required_5pct_two_sided": z_bonf,
            "expected_max_null_t": expected_max_null_t(N),
        }
    return rows


def mean(xs):
    return statistics.fmean(xs)


def resplit_tests(units, n_perm=10000, seed=7, n_seed_check=100):
    rs = [u["r"] for u in units]
    obs_is = mean([u["r"] for u in units if not u["oos"]])
    obs_oos = mean([u["r"] for u in units if u["oos"]])
    n_oos = sum(1 for u in units if u["oos"])
    n = len(units)
    n_is = n - n_oos

    def one_run(seed_, np_):
        rng = random.Random(seed_)
        ge_oos = 0          # perm OOS mean >= observed OOS mean
        joint = 0           # perm IS <= obs IS AND perm OOS >= obs OOS
        signflip = 0        # perm IS < 0 < perm OOS (pattern per se)
        idx = list(range(n))
        for _ in range(np_):
            rng.shuffle(idx)
            s_oos = sum(rs[i] for i in idx[n_is:])
            m_oos = s_oos / n_oos
            m_is = (sum(rs) - s_oos) / n_is
            if m_oos >= obs_oos:
                ge_oos += 1
            if m_is <= obs_is and m_oos >= obs_oos:
                joint += 1
            if m_is < 0.0 < m_oos:
                signflip += 1
        return ge_oos / np_, joint / np_, signflip / np_

    p_ge, p_joint, p_flip = one_run(seed, n_perm)

    # week-block permutation: shuffle week blocks, allocate to OOS from the
    # end until >= n_oos units are covered (keeps split size comparable)
    blocks = collections.defaultdict(list)
    for u in units:
        iso = u["entry_time"].isocalendar()
        blocks[(iso[0], iso[1])].append(u["r"])
    bkeys = sorted(blocks.keys())

    def block_run(seed_, np_):
        rng = random.Random(seed_)
        ge_oos = joint = signflip = 0
        keys = list(bkeys)
        for _ in range(np_):
            rng.shuffle(keys)
            oos_vals, i = [], len(keys) - 1
            while len(oos_vals) < n_oos and i >= 0:
                oos_vals.extend(blocks[keys[i]])
                i -= 1
            is_vals = []
            for j in range(i + 1):
                is_vals.extend(blocks[keys[j]])
            m_oos, m_is = mean(oos_vals), mean(is_vals)
            if m_oos >= obs_oos:
                ge_oos += 1
            if m_is <= obs_is and m_oos >= obs_oos:
                joint += 1
            if m_is < 0.0 < m_oos:
                signflip += 1
        return ge_oos / np_, joint / np_, signflip / np_

    pb_ge, pb_joint, pb_flip = block_run(seed, n_perm)

    # stability across 100 seeds (1000 perms each)
    seeds_ge = [one_run(1000 + s, 1000)[0] for s in range(n_seed_check)]
    seeds_bge = [block_run(2000 + s, 1000)[0] for s in range(n_seed_check)]

    return {
        "n_units": n, "n_is": n_is, "n_oos": n_oos,
        "obs_is_mean_r": obs_is, "obs_oos_mean_r": obs_oos,
        "perm_unit": {"p_oos_ge_obs": p_ge, "p_joint_pattern": p_joint,
                       "p_any_signflip": p_flip, "n_perm": n_perm, "seed": seed,
                       "p_oos_ge_obs_seed_range": [min(seeds_ge), max(seeds_ge)]},
        "perm_weekblock": {"p_oos_ge_obs": pb_ge, "p_joint_pattern": pb_joint,
                            "p_any_signflip": pb_flip, "n_perm": n_perm, "seed": seed,
                            "n_blocks": len(bkeys),
                            "p_oos_ge_obs_seed_range": [min(seeds_bge), max(seeds_bge)]},
    }


def main():
    out = {"multiplicity_ladder": multiplicity_table(),
           "documented_trials_floor": {
               "design_decisions_sequential": 9,
               "symbols_screened": 4,
               "cost_levels_reported": 2,
               "engine_rescreens": 3,
               "gate_variants": "GBPUSD relaxedgate exists alongside GBPUSD.json",
               "note": "9 decisions x 4 symbols = 36 implicit trials is a "
                       "conservative mid estimate; rejected parameter values "
                       "were not logged so the true count is unbounded above."}}
    for sym in ["EURUSD", "XAUUSD"]:
        units = load_units(sym)
        res = resplit_tests(units)
        out["resplit_" + sym] = res
        print(sym, json.dumps({k: v for k, v in res.items() if not isinstance(v, dict)}))
        print("  unit-perm:", res["perm_unit"])
        print("  week-perm:", res["perm_weekblock"])
    print(json.dumps(out["multiplicity_ladder"], indent=1))
    with open(OUT, "w") as f:
        json.dump(out, f, indent=1)
    print("saved", OUT)


if __name__ == "__main__":
    main()
