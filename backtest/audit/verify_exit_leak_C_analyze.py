#!/usr/bin/env python3
"""Analyze the null-calibration runs vs the measured (real-data) leak values."""
import json
import statistics as st
from pathlib import Path

REPO = Path(r"C:/Users/Sanique Richards/Documents/Homework Heroes/Pokemon/Bot-EA")

MEASURED = {  # from verify_exit_leak_A (independently re-derived, matches prior analyst)
    "EURUSD": {"leak_pu": 11.27 / 100, "fill_pu": 13.85 / 100, "timing_pu": -2.58 / 100, "pct": 0.5},
    "GBPUSD": {"leak_pu": 13.87 / 105, "fill_pu": 11.09 / 105, "timing_pu": 2.78 / 105, "pct": 0.25},
    "USDJPY": {"leak_pu": 27.39 / 96, "fill_pu": -0.76 / 96, "timing_pu": 28.14 / 96, "pct": 0.0},
    "XAUUSD": {"leak_pu": 22.29 / 158, "fill_pu": 8.51 / 158, "timing_pu": 13.78 / 158, "pct": 0.0},
}


def describe(vals):
    return {"n": len(vals), "mean": st.mean(vals),
            "sd": st.stdev(vals) if len(vals) > 1 else 0.0,
            "p5": sorted(vals)[int(0.05 * len(vals))],
            "p95": sorted(vals)[int(0.95 * len(vals))]}


def main():
    rows = [json.loads(l) for l in
            (REPO / "backtest/audit/verify_exit_leak_C_runs.jsonl").read_text().splitlines()]
    ok = [r for r in rows if "leak_pu" in r]
    bad = [r for r in rows if "leak_pu" not in r]
    print(f"null runs: {len(rows)} total, {len(ok)} usable, {len(bad)} degenerate/skipped")
    skipped_units = sum(r.get("skipped", 0) for r in ok)
    print(f"zero-r units skipped inside usable runs: {skipped_units}")

    summary = {}
    for sym in MEASURED:
        sub = [r for r in ok if r["sym"] == sym]
        m = MEASURED[sym]
        d = {}
        for scheme in ("iid", "block", "both"):
            s = [r for r in sub if scheme == "both" or r["scheme"] == scheme]
            if not s:
                continue
            leak = describe([r["leak_pu"] for r in s])
            fill = describe([r["leak_fill_pu"] for r in s])
            timing = describe([r["leak_timing_pu"] for r in s])
            pcts = [r["pct"] for r in s]
            frac_low = sum(1 for p in pcts if p <= 1.0) / len(pcts)
            # where does the measured value fall in the null distribution?
            null_leaks = sorted(r["leak_pu"] for r in s)
            pos = sum(1 for v in null_leaks if v < m["leak_pu"]) / len(null_leaks)
            null_tim = sorted(r["leak_timing_pu"] for r in s)
            pos_t = sum(1 for v in null_tim if v < m["timing_pu"]) / len(null_tim)
            d[scheme] = {
                "leak_pu": leak, "fill_pu": fill, "timing_pu": timing,
                "null_pct_mean": st.mean(pcts),
                "null_frac_pct_le1": frac_low,
                "measured_leak_pu": m["leak_pu"],
                "measured_pctile_in_null_leak": 100 * pos,
                "measured_timing_pu": m["timing_pu"],
                "measured_pctile_in_null_timing": 100 * pos_t,
            }
        summary[sym] = d
        b = d["both"]
        print(f"\n=== {sym} (null n={b['leak_pu']['n']}) ===")
        print(f"  null leak/unit   mean {b['leak_pu']['mean']:+.4f} sd {b['leak_pu']['sd']:.4f} "
              f"[p5 {b['leak_pu']['p5']:+.4f}, p95 {b['leak_pu']['p95']:+.4f}]   "
              f"measured {m['leak_pu']:+.4f} -> pctile in null {b['measured_pctile_in_null_leak']:.0f}")
        print(f"  null fill/unit   mean {b['fill_pu']['mean']:+.4f}   measured {m['fill_pu']:+.4f}")
        print(f"  null timing/unit mean {b['timing_pu']['mean']:+.4f} sd {b['timing_pu']['sd']:.4f}  "
              f"measured {m['timing_pu']:+.4f} -> pctile in null {b['measured_pctile_in_null_timing']:.0f}")
        print(f"  null 'pctile vs 400 random portfolios': mean {b['null_pct_mean']:.1f}, "
              f"share <=1st pctile: {100*b['null_frac_pct_le1']:.0f}%   (measured: {m['pct']})")
        for scheme in ("iid", "block"):
            s = d[scheme]
            print(f"    [{scheme:5s}] leak {s['leak_pu']['mean']:+.4f}±{s['leak_pu']['sd']:.4f} "
                  f"timing {s['timing_pu']['mean']:+.4f} pct_mean {s['null_pct_mean']:.1f}")

    (REPO / "backtest/audit/verify_exit_leak_C_summary.json").write_text(
        json.dumps(summary, indent=2))
    print("\nsaved verify_exit_leak_C_summary.json")


if __name__ == "__main__":
    main()
