#!/usr/bin/env python3
"""Trend-beta / regime-luck audit, part 2: trivial baselines under EA costs.

Benchmarks the EA's canonical result against strategies with no entry logic
worth the name, charged the SAME costs (1x fallback spread on every fill,
$2.5/side/lot commission, MT5-style swap points with Wednesday triple) and the
SAME sizing (0.35% of equity / 1.10 cost reserve against a 2.5-ATR stop
distance, lot-floored to broker volume constraints):

  A. ema_cross_long_short          - long while EMA50>EMA200 (H1), short
                                     otherwise; always in market; flip at cross.
  B. ema_cross_long_short_stop     - same, plus a fixed 2.5-ATR stop; after a
                                     stop-out stays flat until the cross flips.
  C. ema_cross_long_flat           - long while EMA50>EMA200, else flat.
  D. ema_cross_long_flat_stop      - C plus the 2.5-ATR stop / flat-until-flip.
  E. bh_risk_scaled                - buy at the first tradable bar with the
                                     same 0.35%-risk sizing, hold to the end.

Timing matches the engine: decisions use bar (i-1) indicator values and fill
at bar i's open (long at ask=open+spread, short at bid=open); stops fill
pessimistically (gap fills at the worse of stop and open). Positions are held
over weekends (the EA flattens Fridays; the baselines are deliberately
simpler) and swap books once per calendar-date change, the engine's own
convention. No look-ahead anywhere.

Stdlib only, fully deterministic (no randomness).
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

REPO = Path(r"C:/Users/Sanique Richards/Documents/Homework Heroes/Pokemon/Bot-EA")
sys.path.insert(0, str(REPO))

from backtest.ftmo_quant_backtest import (  # noqa: E402
    aggregate_h1,
    atr_sma_of_tr,
    ema,
    floor_volume,
    load_m15,
)

SYMBOLS = {
    # symbol: (fallback spread points, canonical split_time from the ledger)
    "EURUSD": (10.0, "2025-08-26 02:00:00"),
    "GBPUSD": (15.0, "2025-08-24 23:00:00"),
    "USDJPY": (28.0, "2025-08-24 23:00:00"),
    "XAUUSD": (16.0, "2025-08-08 05:00:00"),
}
INITIAL = 100_000.0
RISK_PCT = 0.35
COST_RESERVE = 1.10
STOP_ATR = 2.5
EMA_FAST, EMA_SLOW, ATR_PERIOD = 50, 200, 14
WARMUP = EMA_SLOW + 10  # first index allowed to trade, matching signal()


def run_baseline(bars, meta, *, allow_short: bool, use_stop: bool, buy_and_hold: bool, split_time: str) -> dict:
    point = float(meta["point"])
    spread = None  # per-bar (constant fallback in practice)
    dpppl = float(meta["trade_tick_value_loss"]) / float(meta["trade_tick_size"])
    comm = float(meta["commission"]["per_side_usd_per_lot"])
    vol_min, vol_max, vol_step = (float(meta[k]) for k in ("volume_min", "volume_max", "volume_step"))
    swap_cfg = meta.get("swap") or {}
    swap_long = float(swap_cfg.get("long_points", 0.0)) * point * dpppl
    swap_short = float(swap_cfg.get("short_points", 0.0)) * point * dpppl
    triple_wd = int(swap_cfg.get("triple_rollover_weekday", 3))

    closes = [b.close for b in bars]
    fast = ema(closes, EMA_FAST)
    slow = ema(closes, EMA_SLOW)
    atr = atr_sma_of_tr(bars, ATR_PERIOD)

    balance = INITIAL
    pos = None  # dict(side, entry, stop, lots, entry_time)
    stopped_regime = 0  # regime side under which the last stop-out happened
    positions: list[dict] = []
    current_day = None
    equity_at_split = None
    year_equity: dict[str, float] = {}
    peak = INITIAL
    max_dd_pct = 0.0
    swap_paid = 0.0

    def close_pos(price: float, time_str: str, reason: str) -> None:
        nonlocal balance, pos
        gross = pos["side"] * (price - pos["entry"]) * pos["lots"] * dpppl
        pnl = gross - 2.0 * comm * pos["lots"]
        balance += pnl
        positions.append(
            {"entry_time": pos["entry_time"], "exit_time": time_str, "side": pos["side"], "pnl": pnl, "reason": reason}
        )
        pos = None

    for i in range(WARMUP, len(bars)):
        bar = bars[i]
        spread = bar.spread
        ask_open = bar.open + spread
        tstr = bar.time.isoformat(sep=" ")

        # swap at calendar-date rollover (engine convention)
        day = bar.time.date()
        if current_day is None:
            current_day = day
        elif day != current_day:
            if pos is not None:
                mult = 3.0 if bar.time.weekday() == triple_wd else 1.0
                charge = (swap_long if pos["side"] > 0 else swap_short) * pos["lots"] * mult
                balance += charge
                swap_paid += charge
            current_day = day

        if equity_at_split is None and tstr >= split_time:
            mark = bar.open if (pos and pos["side"] > 0) else (ask_open if pos else bar.open)
            open_pnl = pos["side"] * (mark - pos["entry"]) * pos["lots"] * dpppl - comm * pos["lots"] if pos else 0.0
            equity_at_split = balance + open_pnl

        if buy_and_hold:
            desired = 1
        else:
            desired = 1 if fast[i - 1] > slow[i - 1] else (-1 if allow_short else 0)

        # regime change clears the stop latch
        if stopped_regime and desired != stopped_regime:
            stopped_regime = 0

        # exit on regime flip (never for buy-and-hold)
        if pos is not None and not buy_and_hold and desired != pos["side"]:
            close_pos(bar.open if pos["side"] > 0 else ask_open, tstr, "flip")

        # entry
        if pos is None and desired != 0 and not stopped_regime and math.isfinite(atr[i - 1]):
            entry = ask_open if desired > 0 else bar.open
            dist = STOP_ATR * atr[i - 1]
            risk_money = balance * RISK_PCT / 100.0
            lots = floor_volume(risk_money / COST_RESERVE / (dist * dpppl), vol_min, vol_max, vol_step)
            if lots:
                stop = (bar.open - dist) if desired > 0 else (ask_open + dist)
                pos = {"side": desired, "entry": entry, "stop": stop, "lots": lots, "entry_time": tstr}
                if buy_and_hold:
                    use_stop_here = False  # explicit: E never stops out
            # if lots floor to zero, simply no trade this bar

        # intrabar stop (entry bar included, matching the engine's ordering)
        if pos is not None and use_stop and not buy_and_hold:
            if pos["side"] > 0:
                if bar.low <= pos["stop"]:
                    fill = min(pos["stop"], bar.open)
                    stopped_regime = desired
                    close_pos(fill, tstr, "stop")
            else:
                if bar.high + spread >= pos["stop"]:
                    fill = max(pos["stop"], ask_open)
                    stopped_regime = desired
                    close_pos(fill, tstr, "stop")

        # close-mark equity, drawdown, yearly bookkeeping
        equity = balance
        if pos is not None:
            mark = bar.close if pos["side"] > 0 else bar.close + spread
            equity += pos["side"] * (mark - pos["entry"]) * pos["lots"] * dpppl - comm * pos["lots"]
        peak = max(peak, equity)
        max_dd_pct = max(max_dd_pct, 100.0 * (peak - equity) / peak)
        year_equity[str(bar.time.year)] = equity

    if pos is not None:
        last = bars[-1]
        close_pos(last.close if pos["side"] > 0 else last.close + last.spread, last.time.isoformat(sep=" "), "end")

    if equity_at_split is None:
        equity_at_split = balance

    wins = [p["pnl"] for p in positions if p["pnl"] > 0]
    losses = [-p["pnl"] for p in positions if p["pnl"] < 0]
    years = sorted(year_equity)
    yearly = {}
    prev = INITIAL
    for y in years:
        yearly[y] = round(year_equity[y] - prev, 2)
        prev = year_equity[y]

    return {
        "net": round(balance - INITIAL, 2),
        "return_pct": round(100.0 * (balance / INITIAL - 1.0), 3),
        "is_net": round(equity_at_split - INITIAL, 2),
        "oos_net": round(balance - equity_at_split, 2),
        "positions": len(positions),
        "wins": len(wins),
        "profit_factor": round(sum(wins) / sum(losses), 3) if losses else None,
        "max_dd_pct": round(max_dd_pct, 2),
        "swap_paid": round(swap_paid, 2),
        "yearly_net": yearly,
    }


def main() -> None:
    meta_all = json.loads((REPO / "backtest" / "deriv_broker_meta.json").read_text(encoding="utf-8"))["symbols"]
    out: dict = {}
    for symbol, (fallback_pts, split_time) in SYMBOLS.items():
        meta = meta_all[symbol]
        bars = aggregate_h1(
            load_m15(REPO / "backtest" / "data" / "derivM15" / f"{symbol}.csv", fallback_pts * float(meta["point"]))
        )
        drift_pct = round(100.0 * (bars[-1].close / bars[WARMUP].close - 1.0), 2)
        out[symbol] = {
            "bars": len(bars),
            "window": {"from": bars[WARMUP].time.isoformat(sep=" "), "to": bars[-1].time.isoformat(sep=" ")},
            "price_drift_pct_from_warmup": drift_pct,
            "baselines": {
                "ema_cross_long_short": run_baseline(
                    bars, meta, allow_short=True, use_stop=False, buy_and_hold=False, split_time=split_time
                ),
                "ema_cross_long_short_stop": run_baseline(
                    bars, meta, allow_short=True, use_stop=True, buy_and_hold=False, split_time=split_time
                ),
                "ema_cross_long_flat": run_baseline(
                    bars, meta, allow_short=False, use_stop=False, buy_and_hold=False, split_time=split_time
                ),
                "ema_cross_long_flat_stop": run_baseline(
                    bars, meta, allow_short=False, use_stop=True, buy_and_hold=False, split_time=split_time
                ),
                "bh_risk_scaled": run_baseline(
                    bars, meta, allow_short=False, use_stop=False, buy_and_hold=True, split_time=split_time
                ),
            },
        }
        print(symbol, "done", flush=True)

    dest = REPO / "backtest" / "audit" / "beta_regime_02_baselines_results.json"
    dest.write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
