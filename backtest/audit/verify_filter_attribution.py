#!/usr/bin/env python3
"""ADVERSARIAL VERIFICATION of the filter-attribution analyst's headline.

Independently re-derives (own code, not the analyst's):
  V1. Canonical reproduction: default Config, guarded, 1x spread vs the
      published measured ledgers (return_pct, tranche count, AND
      trade-by-trade equality).
  V2. The attribution ladder (5 rungs, guarded + unguarded) with my own
      unit grouping and t-stats. Checks the specific EURUSD / XAUUSD numbers
      the headline leans on, whether R4==R3 trade-for-trade, the EURUSD
      IS-vs-OOS filter-delta ratio, and the max OOS t anywhere in the grid.
  V3. A position-independent census of raw breakout candidates and how many
      the vol-regime filter (ATR ratio bounds) vetoes.
  V4. Guarded raw-breakout account-death claim (EURUSD/GBPUSD ending
      balance, last trade date, OOS trade count).

Deterministic - no randomness, no seeds needed.
"""

from __future__ import annotations

import json
import math
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
    in_session,
    load_m15,
    sma,
    htf_ema_aligned,
)

SYMBOLS = {
    "EURUSD": dict(csv="EURUSD.csv", fallback_pts=10.0, max_spread_points=25.0,
                   ledger="EURUSD.json"),
    "GBPUSD": dict(csv="GBPUSD.csv", fallback_pts=15.0, max_spread_points=60.0,
                   ledger="GBPUSD_relaxedgate.json"),
    "USDJPY": dict(csv="USDJPY.csv", fallback_pts=28.0, max_spread_points=60.0,
                   ledger="USDJPY.json"),
    "XAUUSD": dict(csv="XAUUSD.csv", fallback_pts=16.0, max_spread_points=40.0,
                   ledger="XAUUSD.json"),
}

# Filters OFF baseline (candle degenerates to close-direction; H1 EMA stack is
# baked into signal() and stays on at every rung - same rung spec the analyst
# used and documented in their caveat 1).
OFF = dict(entry_buffer_atr=0.0, candle_body_min=0.0, candle_wick_max=1.0,
           htf_factor=0, htf2_factor=0, vol_avg_len=0)
RUNGS = [
    ("R0", dict(OFF)),
    ("R1", {**OFF, "entry_buffer_atr": 0.5}),
    ("R2", {**OFF, "entry_buffer_atr": 0.5, "candle_body_min": 0.2,
            "candle_wick_max": 0.3}),
    ("R3", {**OFF, "entry_buffer_atr": 0.5, "candle_body_min": 0.2,
            "candle_wick_max": 0.3, "htf_factor": 4, "htf2_factor": 24}),
    ("R4", {}),
]


def units_from_tranches(trades: list[dict]) -> list[dict]:
    """My own grouping: tranches -> units keyed by entry_time (one try_open
    per bar, so entry_time is unique per unit within a run)."""
    acc: dict[str, dict] = {}
    for t in trades:
        u = acc.setdefault(t["entry_time"], dict(pnl=0.0, oos=t["oos"]))
        u["pnl"] += t["pnl"]
        if u["oos"] != t["oos"]:
            raise RuntimeError("unit straddles the IS/OOS flag - grouping invalid")
    return list(acc.values())


def stats(pnls: list[float]) -> dict:
    n = len(pnls)
    if n == 0:
        return dict(n=0, net=0.0, pf=None, mean=None, t=None)
    net = sum(pnls)
    pos = sum(p for p in pnls if p > 0)
    neg = sum(-p for p in pnls if p < 0)
    mean = net / n
    t = None
    if n > 1:
        var = sum((p - mean) ** 2 for p in pnls) / (n - 1)
        if var > 0:
            t = mean / math.sqrt(var / n)
    return dict(n=n, net=round(net, 2), pf=round(pos / neg, 4) if neg else None,
                mean=round(mean, 2), t=round(t, 3) if t is not None else None)


def run(bars, meta, overrides, max_spread, guards):
    return eng.run_backtest(
        bars, meta, Config(**overrides),
        initial_balance=100_000.0, spread_multiplier=1.0, split_fraction=0.70,
        max_spread_points=max_spread, enforce_ftmo_guards=guards,
    )


def census(bars, config_off: Config, full: Config, max_spread_points: float,
           point: float) -> dict:
    """Position-independent filter census. A raw candidate = a bar where the
    engine could evaluate an entry (session/spread/ATR-warmup gates) and the
    OFF-config signal fires. Each candidate is then scored against every
    confluence filter independently of position state."""
    closes = [b.close for b in bars]
    fast = ema(closes, full.ema_fast)
    slow = ema(closes, full.ema_slow)
    atr = atr_sma_of_tr(bars, full.atr_period)
    atr_clean = [v if math.isfinite(v) else 0.0 for v in atr]
    atr_avg = sma(atr_clean, full.vol_avg_len)
    h4f, h4s = htf_ema_aligned(bars, full.htf_factor, full.htf_fast, full.htf_slow)
    d1f, d1s = htf_ema_aligned(bars, full.htf2_factor, full.htf2_fast, full.htf2_slow)

    n_raw = 0
    fail = dict(buffer=0, candle=0, htf=0, vol=0)
    fail_vol_after_others = 0
    pass_all = 0
    for i in range(2, len(bars)):
        bar = bars[i]
        if not in_session(bar.time, full):
            continue
        if bar.spread / point > max_spread_points:
            continue
        if not math.isfinite(atr[i - 1]):
            continue
        side = eng.signal(i, bars, fast, slow, config_off, atr)
        if side == 0:
            continue
        n_raw += 1
        prior = bars[i - full.donchian - 1: i - 1]
        upper = max(b.high for b in prior)
        lower = min(b.low for b in prior)
        close = bars[i - 1].close
        buf = full.entry_buffer_atr * atr[i - 1]
        ok_buffer = close > upper + buf if side > 0 else close < lower - buf
        bo = bars[i - 1]
        rng = bo.high - bo.low
        body = abs(bo.close - bo.open)
        uw = bo.high - max(bo.open, bo.close)
        lw = min(bo.open, bo.close) - bo.low
        strong = rng > 0 and body / rng >= full.candle_body_min
        ok_candle = strong and (
            (bo.close > bo.open and uw / rng <= full.candle_wick_max)
            if side > 0
            else (bo.close < bo.open and lw / rng <= full.candle_wick_max)
        )
        def aligned(f, s):
            if not (math.isfinite(f) and math.isfinite(s)):
                return False
            return f > s if side > 0 else f < s
        ok_htf = aligned(h4f[i], h4s[i]) and aligned(d1f[i], d1s[i])
        base = atr_avg[i - 1]
        ok_vol = bool(base and base > 0.0
                      and full.vol_ratio_min <= atr[i - 1] / base <= full.vol_ratio_max)
        if not ok_buffer:
            fail["buffer"] += 1
        if not ok_candle:
            fail["candle"] += 1
        if not ok_htf:
            fail["htf"] += 1
        if not ok_vol:
            fail["vol"] += 1
        if ok_buffer and ok_candle and ok_htf:
            if ok_vol:
                pass_all += 1
            else:
                fail_vol_after_others += 1
    return dict(raw=n_raw, fail_each_independent=fail,
                vol_binds_after_all_others=fail_vol_after_others,
                pass_all=pass_all)


def main() -> None:
    broker = json.loads((REPO / "backtest/deriv_broker_meta.json").read_text(encoding="utf-8"))
    out: dict = {}
    grid_t_max = []  # (t, symbol, rung, mode)

    for symbol, spec in SYMBOLS.items():
        meta = broker["symbols"][symbol]
        point = float(meta["point"])
        fallback = spec["fallback_pts"] * point
        bars = aggregate_h1(load_m15(REPO / "backtest/data/derivM15" / spec["csv"], fallback))
        sym: dict = {}

        # ---- V1 canonical reproduction vs published ledger -----------------
        ledger = json.loads((REPO / "backtest/results" / spec["ledger"]).read_text(encoding="utf-8"))
        mine = run(bars, meta, {}, spec["max_spread_points"], True)
        pub = ledger["measured"]
        trades_equal = mine["trades"] == pub["trades"]
        sym["V1_canonical"] = dict(
            my_return_pct=mine["return_pct"],
            published_return_pct=pub["return_pct"],
            return_match=abs(mine["return_pct"] - pub["return_pct"]) < 1e-9,
            my_tranches=len(mine["trades"]),
            published_tranches=len(pub["trades"]),
            trades_identical=trades_equal,
        )
        print(f"[V1] {symbol}: my {mine['return_pct']:+.4f}% vs pub {pub['return_pct']:+.4f}% "
              f"tranches {len(mine['trades'])}/{len(pub['trades'])} "
              f"trade-by-trade identical={trades_equal}", flush=True)

        # ---- V2 ladder ------------------------------------------------------
        ladder = {}
        prev_trades = {}
        for rung, ov in RUNGS:
            row = {}
            for mode, g in (("guarded", True), ("unguarded", False)):
                res = run(bars, meta, ov, spec["max_spread_points"], g)
                units = units_from_tranches(res["trades"])
                is_u = stats([u["pnl"] for u in units if not u["oos"]])
                oos_u = stats([u["pnl"] for u in units if u["oos"]])
                row[mode] = dict(
                    units=len(units), is_=is_u, oos=oos_u,
                    return_pct=round(res["return_pct"], 4),
                    ending_balance=round(res["ending_balance"], 2),
                    n_oos_tranches=sum(1 for t in res["trades"] if t["oos"]),
                    last_trade_exit=res["trades"][-1]["exit_time"] if res["trades"] else None,
                    trades_raw=res["trades"],
                )
                if oos_u["t"] is not None:
                    grid_t_max.append((oos_u["t"], symbol, rung, mode))
            ladder[rung] = row
            u = row["unguarded"]
            print(f"[V2] {symbol} {rung} unguarded: units {u['units']:4d} "
                  f"IS {u['is_']['net']:+10.2f}/PF {u['is_']['pf']} | "
                  f"OOS {u['oos']['net']:+10.2f}/PF {u['oos']['pf']} t {u['oos']['t']}",
                  flush=True)
            prev_trades[rung] = row["unguarded"]["trades_raw"]
        sym["V2_r4_equals_r3_unguarded"] = prev_trades["R4"] == prev_trades["R3"]
        sym["V2_r4_equals_r3_guarded"] = (
            ladder["R4"]["guarded"]["trades_raw"] == ladder["R3"]["guarded"]["trades_raw"]
        )
        print(f"[V2] {symbol} R4==R3 trade-for-trade: unguarded "
              f"{sym['V2_r4_equals_r3_unguarded']} guarded {sym['V2_r4_equals_r3_guarded']}",
              flush=True)
        # strip raw trades before persisting
        for rung, row in ladder.items():
            for mode in row:
                row[mode].pop("trades_raw")
        sym["V2_ladder"] = ladder

        # ---- V3 census ------------------------------------------------------
        cen = census(bars, Config(**OFF), Config(), spec["max_spread_points"], point)
        sym["V3_census"] = cen
        print(f"[V3] {symbol} census: raw {cen['raw']}, independent fails {cen['fail_each_independent']}, "
              f"vol-binds-after-others {cen['vol_binds_after_all_others']}, pass_all {cen['pass_all']}",
              flush=True)

        out[symbol] = sym

    # ---- V2 EURUSD IS-vs-OOS filter deltas ---------------------------------
    lad = out["EURUSD"]["V2_ladder"]
    seq = ["R0", "R1", "R2", "R3", "R4"]
    deltas = {}
    for a, b in zip(seq, seq[1:]):
        deltas[f"{a}->{b}"] = dict(
            is_=round(lad[b]["unguarded"]["is_"]["net"] - lad[a]["unguarded"]["is_"]["net"], 2),
            oos=round(lad[b]["unguarded"]["oos"]["net"] - lad[a]["unguarded"]["oos"]["net"], 2),
        )
    tot_is = round(lad["R4"]["unguarded"]["is_"]["net"] - lad["R0"]["unguarded"]["is_"]["net"], 2)
    tot_oos = round(lad["R4"]["unguarded"]["oos"]["net"] - lad["R0"]["unguarded"]["oos"]["net"], 2)
    out["EURUSD_filter_deltas"] = dict(per_filter=deltas, total_is=tot_is, total_oos=tot_oos,
                                       ratio=round(tot_is / tot_oos, 2) if tot_oos else None)
    print(f"[V2] EURUSD deltas {deltas} total IS {tot_is} OOS {tot_oos} "
          f"ratio {out['EURUSD_filter_deltas']['ratio']}", flush=True)

    grid_t_max.sort(reverse=True)
    out["max_oos_unit_t_anywhere"] = grid_t_max[:6]
    print(f"[V2] top OOS unit t across grid (both modes): {grid_t_max[:6]}", flush=True)

    # pooled OOS expectancy per rung across symbols (unguarded)
    pooled = {}
    for rung, _ in RUNGS:
        tot_net = sum(out[s]["V2_ladder"][rung]["unguarded"]["oos"]["net"] for s in SYMBOLS)
        tot_n = sum(out[s]["V2_ladder"][rung]["unguarded"]["oos"]["n"] for s in SYMBOLS)
        pooled[rung] = dict(net=round(tot_net, 2), units=tot_n,
                            mean=round(tot_net / tot_n, 2) if tot_n else None)
    out["pooled_oos_per_rung_unguarded"] = pooled
    print(f"[V2] pooled OOS per rung: {pooled}", flush=True)

    (REPO / "backtest/audit/verify_filter_attribution_results.json").write_text(
        json.dumps(out, indent=2), encoding="utf-8")
    print("wrote verify_filter_attribution_results.json", flush=True)


if __name__ == "__main__":
    main()
