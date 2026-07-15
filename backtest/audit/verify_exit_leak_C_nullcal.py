#!/usr/bin/env python3
"""NULL CALIBRATION of the exit-leak statistic (adversarial verification).

Question: under a process where NO exit policy can have information (i.i.d.
permuted, de-meaned H1 increments -> exchangeable, driftless), what does the
"leak vs random-in-window exits" statistic measure on the full engine?

If the null distribution of leak-per-unit centers near 0, the measured
+0.163 R/unit is real information loss. If it centers near the measured
value, the statistic is construction bias (intra-bar stop fills vs close
fills + hindsight-bounded windows), not exit alpha.

Two permutation schemes per symbol (robustness):
  iid   : permute individual H1 bar-increment tuples
  block : permute contiguous 24-bar blocks (preserves vol clustering)

Bars are rebuilt on the ORIGINAL timestamps (sessions/weekends/rollovers
intact). De-meaning the close-to-close increment makes each permuted series a
bridge back to the starting price (driftless by construction). The FULL
engine (canonical spreads, FTMO guards on) runs on each series; the leak
statistic is computed with the same pipeline as verify_exit_leak_A.

Per run we record: units, leak total, leak/unit, leak_fill/unit,
leak_timing/unit, stop-final share, and the percentile of the actual total
within 200 seeded random-exit portfolios (calibration of the 0th-pctile
claim: under the null this percentile should be ~Uniform[0,100]).
"""
import json
import random
import sys
from pathlib import Path

REPO = Path(r"C:/Users/Sanique Richards/Documents/Homework Heroes/Pokemon/Bot-EA")
sys.path.insert(0, str(REPO))

from backtest.ftmo_quant_backtest import (  # noqa: E402
    Bar, Config, aggregate_h1, load_m15, atr_sma_of_tr, run_backtest,
)

CFG = Config()
PARAMS = {
    "EURUSD": (10.0, 25.0),
    "GBPUSD": (15.0, 25.0),
    "USDJPY": (28.0, 30.0),
    "XAUUSD": (16.0, 25.0),
}
N_PER_SCHEME = 60  # x2 schemes = 120 null runs per symbol
BLOCK = 24
OUT = REPO / "backtest/audit/verify_exit_leak_C_runs.jsonl"


def build_null_bars(bars, spread, rng, scheme):
    incs = []
    for j in range(1, len(bars)):
        pc = bars[j - 1].close
        b = bars[j]
        incs.append((b.open - pc, b.high - pc, b.low - pc, b.close - pc))
    m = sum(t[3] for t in incs) / len(incs)
    incs = [(a - m, h - m, l - m, c - m) for a, h, l, c in incs]
    if scheme == "iid":
        rng.shuffle(incs)
    else:
        blocks = [incs[i:i + BLOCK] for i in range(0, len(incs), BLOCK)]
        rng.shuffle(blocks)
        incs = [t for blk in blocks for t in blk]
    out = [bars[0]]
    pc = bars[0].close
    for j, (do, dh, dl, dc) in enumerate(incs, start=1):
        out.append(Bar(bars[j].time, pc + do, pc + dh, pc + dl, pc + dc, spread))
        pc = pc + dc
    return out


def leak_stats(trades, bars, atr, spread, tag):
    """Same statistic pipeline as verify_exit_leak_A (actual vs exact
    random-in-window mean, close-repriced split, 200-portfolio percentile)."""
    t2i = {b.time.isoformat(sep=" "): i for i, b in enumerate(bars)}
    groups = {}
    for t in trades:
        groups.setdefault(t["entry_time"], []).append(t)
    units = []
    skipped = 0
    for et, rows in groups.items():
        if any(t["r"] == 0.0 for t in rows):
            skipped += 1
            continue
        side, entry = rows[0]["side"], rows[0]["entry"]
        e = t2i[et]
        ir = CFG.stop_atr * atr[e - 1] + spread
        shares = [t["pnl"] / t["r"] for t in rows]
        rb = sum(shares)
        ws = [s / rb for s in shares]
        x = max(t2i[t["exit_time"]] for t in rows)

        def cpx(j):
            return bars[j].close if side > 0 else bars[j].close + spread

        r_act = sum(w * side * (t["exit"] - entry) / ir for t, w in zip(rows, ws))
        r_cl = sum(w * side * (cpx(t2i[t["exit_time"]]) - entry) / ir
                   for t, w in zip(rows, ws))
        vals = [side * (cpx(j) - entry) / ir for j in range(e, x + 1)]
        fin = max(rows, key=lambda t: t["exit_time"])["reason"]
        units.append((r_act, r_cl, sum(vals) / len(vals), vals, fin))
    if not units:
        return None
    n = len(units)
    act = sum(u[0] for u in units)
    cl = sum(u[1] for u in units)
    rnd = sum(u[2] for u in units)
    totals = []
    for k in range(200):
        prng = random.Random(f"nullport-{tag}-{k}")
        totals.append(sum(u[3][prng.randrange(len(u[3]))] for u in units))
    pct = 100.0 * sum(1 for v in totals if v < act) / len(totals)
    nstop = sum(1 for u in units if u[4] == "stop")
    return {"units": n, "skipped": skipped, "leak": rnd - act,
            "leak_pu": (rnd - act) / n, "leak_fill_pu": (cl - act) / n,
            "leak_timing_pu": (rnd - cl) / n, "pct": pct,
            "stop_share": nstop / n, "r_act": act}


def main():
    broker = json.loads((REPO / "backtest/deriv_broker_meta.json").read_text())
    done = set()
    if OUT.exists():
        for line in OUT.read_text().splitlines():
            r = json.loads(line)
            done.add((r["sym"], r["scheme"], r["k"]))
    with OUT.open("a", encoding="utf-8") as fh:
        for sym, (spts, maxsp) in PARAMS.items():
            meta = broker["symbols"][sym]
            spread = spts * float(meta["point"])
            real = aggregate_h1(
                load_m15(REPO / "backtest/data/derivM15" / f"{sym}.csv", spread))
            for scheme in ("iid", "block"):
                for k in range(N_PER_SCHEME):
                    if (sym, scheme, k) in done:
                        continue
                    rng = random.Random(f"null-{sym}-{scheme}-{k}")
                    nb = build_null_bars(real, spread, rng, scheme)
                    if min(b.low for b in nb) <= 0:
                        row = {"sym": sym, "scheme": scheme, "k": k, "neg_price": True}
                        fh.write(json.dumps(row) + "\n"); fh.flush()
                        continue
                    atr = atr_sma_of_tr(nb, CFG.atr_period)
                    res = run_backtest(nb, meta, CFG, initial_balance=100_000.0,
                                       spread_multiplier=1.0, split_fraction=0.70,
                                       max_spread_points=maxsp)
                    st = leak_stats(res["trades"], nb, atr, spread,
                                    f"{sym}-{scheme}-{k}")
                    row = {"sym": sym, "scheme": scheme, "k": k,
                           "trades": res["all"]["trades"]}
                    if st:
                        row.update(st)
                    fh.write(json.dumps(row) + "\n"); fh.flush()
            print(f"{sym} done", flush=True)
    print("all done")


if __name__ == "__main__":
    main()
