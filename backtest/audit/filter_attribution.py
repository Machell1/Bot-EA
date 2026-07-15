#!/usr/bin/env python3
"""Confluence-filter attribution ladder for FTMOQuantEA.

Question: do the confluence filters (entry buffer, candle confirmation,
H4+D1 EMA stacks, vol regime) earn their keep, or do they just shrink the
sample while shuffling expectancy?

Method: run the corrected screening engine with cumulative filter sets in the
documented order of addition (buffer -> candle -> HTF -> vol), holding the
session, exits, scale-out/pyramid, sizing, costs, and FTMO account layer
constant. Rung 4 equals the canonical config and must reproduce the published
numbers exactly (validation gate).

Notes on what "raw breakout" (rung 0) still contains, because it is baked into
signal() and cannot be disabled by config: the H1 EMA50/200 stack + rising-EMA
condition, and the requirement that the breakout bar closes in the breakout
direction (with candle_body_min=0 and candle_wick_max=1 the candle test
degenerates to close>open / close<open). A separate monkeypatched variant
(R0_dirfree) removes the direction residue to size it.

Deterministic: no randomness anywhere, so no seeds are required.
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

REPO = Path(r"C:/Users/Sanique Richards/Documents/Homework Heroes/Pokemon/Bot-EA")
sys.path.insert(0, str(REPO))

import backtest.ftmo_quant_backtest as eng  # noqa: E402
from backtest.ftmo_quant_backtest import Config, aggregate_h1, load_m15  # noqa: E402

SYMBOLS = {
    # exact args from docs/DATA.md "Reproduce the 2026-07-13 runs"
    "EURUSD": dict(csv="EURUSD.csv", fallback_pts=10.0, max_spread_points=25.0),
    "GBPUSD": dict(csv="GBPUSD.csv", fallback_pts=15.0, max_spread_points=60.0),
    "USDJPY": dict(csv="USDJPY.csv", fallback_pts=28.0, max_spread_points=60.0),
    "XAUUSD": dict(csv="XAUUSD.csv", fallback_pts=16.0, max_spread_points=40.0),
}

OFF = dict(
    entry_buffer_atr=0.0,
    candle_body_min=0.0,
    candle_wick_max=1.0,
    htf_factor=0,
    htf2_factor=0,
    vol_avg_len=0,
)

RUNGS = [
    ("R0_raw_breakout", dict(OFF)),
    ("R1_plus_buffer", {**OFF, "entry_buffer_atr": 0.5}),
    (
        "R2_plus_candle",
        {**OFF, "entry_buffer_atr": 0.5, "candle_body_min": 0.2, "candle_wick_max": 0.3},
    ),
    (
        "R3_plus_htf",
        {
            **OFF,
            "entry_buffer_atr": 0.5,
            "candle_body_min": 0.2,
            "candle_wick_max": 0.3,
            "htf_factor": 4,
            "htf2_factor": 24,
        },
    ),
    ("R4_plus_vol_FULL", {}),  # engine defaults == canonical published config
]

CANONICAL = {
    # measured (FTMO-guarded, 1x spread) published 2026-07-14
    "EURUSD": dict(return_pct=1.0624885714287435, tranches=172),
    "GBPUSD": dict(return_pct=-1.2327542857146767, tranches=171),
    "USDJPY": dict(return_pct=-4.213073807413292, tranches=142),
    "XAUUSD": dict(return_pct=1.7288980000000231, tranches=253),
}


def signal_dirfree(index, bars, fast, slow, config, atr=None):
    """R0 signal with the residual candle-direction requirement removed:
    pure Donchian close-through + the H1 EMA stack (which config cannot
    disable). No buffer, no candle test at all."""
    if index < max(config.ema_slow + 10, config.donchian + 2):
        return 0
    prior = bars[index - config.donchian - 1 : index - 1]
    upper = max(bar.high for bar in prior)
    lower = min(bar.low for bar in prior)
    close = bars[index - 1].close
    if (
        close > upper
        and fast[index - 1] > slow[index - 1]
        and fast[index - 1] > fast[index - 2]
    ):
        return 1
    if (
        close < lower
        and fast[index - 1] < slow[index - 1]
        and fast[index - 1] < fast[index - 2]
    ):
        return -1
    return 0


def tranche_split_stats(trades: list[dict]) -> dict:
    out = {}
    for name, rows in (
        ("all", trades),
        ("is", [t for t in trades if not t["oos"]]),
        ("oos", [t for t in trades if t["oos"]]),
    ):
        pos = sum(t["pnl"] for t in rows if t["pnl"] > 0)
        neg = sum(-t["pnl"] for t in rows if t["pnl"] < 0)
        out[name] = dict(
            tranches=len(rows),
            net=round(sum(t["pnl"] for t in rows), 2),
            pf=round(pos / neg, 3) if neg else None,
        )
    return out


def unit_stats(trades: list[dict]) -> dict:
    """Group scale-out tranches into distinct trade units (one try_open each;
    entry_time is unique per unit because at most one unit opens per bar)."""
    units: dict[str, dict] = {}
    for t in trades:
        u = units.setdefault(t["entry_time"], dict(pnl=0.0, oos=t["oos"], n=0))
        u["pnl"] += t["pnl"]
        u["n"] += 1
    rows = list(units.values())

    def block(sel):
        pnls = [u["pnl"] for u in sel]
        n = len(pnls)
        if not n:
            return dict(units=0, net=0.0, pf=None, mean=None, win_pct=None, t=None)
        net = sum(pnls)
        pos = sum(p for p in pnls if p > 0)
        neg = sum(-p for p in pnls if p < 0)
        mean = net / n
        if n > 1:
            var = sum((p - mean) ** 2 for p in pnls) / (n - 1)
            t_stat = mean / math.sqrt(var / n) if var > 0 else None
        else:
            t_stat = None
        return dict(
            units=n,
            net=round(net, 2),
            pf=round(pos / neg, 3) if neg else None,
            mean=round(mean, 2),
            win_pct=round(100.0 * sum(1 for p in pnls if p > 0) / n, 1),
            t=round(t_stat, 2) if t_stat is not None else None,
        )

    return dict(
        all=block(rows),
        is_=block([u for u in rows if not u["oos"]]),
        oos=block([u for u in rows if u["oos"]]),
    )


def main() -> None:
    broker = json.loads((REPO / "backtest/deriv_broker_meta.json").read_text(encoding="utf-8"))
    results: dict = {}
    for symbol, spec in SYMBOLS.items():
        meta = broker["symbols"][symbol]
        fallback_spread = spec["fallback_pts"] * float(meta["point"])
        bars = aggregate_h1(
            load_m15(REPO / "backtest/data/derivM15" / spec["csv"], fallback_spread)
        )
        sym_out: dict = {}
        for rung_name, overrides in RUNGS + [("R0_dirfree", dict(OFF))]:
            config = Config(**overrides)
            if rung_name == "R0_dirfree":
                eng.signal = signal_dirfree
            else:
                eng.signal = ORIGINAL_SIGNAL
            rung_out = {}
            for mode, guards in (("guarded", True), ("diag_unguarded", False)):
                res = eng.run_backtest(
                    bars,
                    meta,
                    config,
                    initial_balance=100_000.0,
                    spread_multiplier=1.0,
                    split_fraction=0.70,
                    max_spread_points=spec["max_spread_points"],
                    enforce_ftmo_guards=guards,
                )
                rung_out[mode] = dict(
                    return_pct=round(res["return_pct"], 4),
                    tranche=tranche_split_stats(res["trades"]),
                    unit=unit_stats(res["trades"]),
                    max_dd_pct=round(res["max_equity_drawdown_pct"], 2),
                    breach=res["official_rule_breach"],
                )
            sym_out[rung_name] = rung_out
            g = rung_out["guarded"]
            print(
                f"{symbol} {rung_name:18s} ret {g['return_pct']:+8.3f}%  "
                f"units {g['unit']['all']['units']:4d}  "
                f"IS net {g['unit']['is_']['net']:+10.2f} (PF {g['unit']['is_']['pf']})  "
                f"OOS net {g['unit']['oos']['net']:+10.2f} (PF {g['unit']['oos']['pf']}, "
                f"t {g['unit']['oos']['t']})",
                flush=True,
            )
        eng.signal = ORIGINAL_SIGNAL

        # validation gate: rung 4 must reproduce the canonical measured run
        got = sym_out["R4_plus_vol_FULL"]["guarded"]
        want = CANONICAL[symbol]
        ok_ret = abs(got["return_pct"] - want["return_pct"]) < 1e-3
        ok_n = got["tranche"]["all"]["tranches"] == want["tranches"]
        sym_out["_canonical_reproduced"] = bool(ok_ret and ok_n)
        print(
            f"{symbol} canonical check: return {got['return_pct']:+.4f} vs "
            f"{want['return_pct']:+.4f}, tranches "
            f"{got['tranche']['all']['tranches']} vs {want['tranches']} -> "
            f"{'OK' if ok_ret and ok_n else 'MISMATCH'}",
            flush=True,
        )
        results[symbol] = sym_out

    out_path = REPO / "backtest/audit/filter_attribution_results.json"
    out_path.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"wrote {out_path}")


ORIGINAL_SIGNAL = eng.signal

if __name__ == "__main__":
    main()
