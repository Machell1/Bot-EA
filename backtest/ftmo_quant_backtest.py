#!/usr/bin/env python3
"""Bar-level screening backtest for experts/FTMOQuantEA.mq5.

This intentionally uses only Python's standard library. It mirrors the EA's
default signal, sizing, session, exit, and FTMO guard logic on H1 bars
aggregated from complete M15 hours. Intrabar ambiguity is resolved against the
strategy (stop before target; triggered trailing stops may fill in the same
bar), so results are deliberately conservative.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True)
class Bar:
    time: datetime
    open: float
    high: float
    low: float
    close: float
    spread: float


@dataclass
class Position:
    """One trend unit. Lots are scaled out at TP1 and TP2; the remaining runner
    trails until it is stopped or the trend flips."""

    side: int
    entry_time: datetime
    entry_index: int
    entry: float
    stop: float
    initial_risk: float
    dollars_per_price_per_lot: float
    lots: float
    original_lots: float
    risk_budget: float
    tp1_price: float
    tp2_price: float
    tp1_lots: float
    tp2_lots: float
    tp1_done: bool = False
    tp2_done: bool = False

    @property
    def dollars_per_price(self) -> float:
        return self.lots * self.dollars_per_price_per_lot


@dataclass(frozen=True)
class Trade:
    entry_time: str
    exit_time: str
    side: int
    entry: float
    exit: float
    pnl: float
    r: float
    reason: str
    oos: bool


@dataclass(frozen=True)
class Config:
    donchian: int = 20
    ema_fast: int = 50
    ema_slow: int = 200
    atr_period: int = 14
    stop_atr: float = 2.5
    reward_risk: float = 2.2
    # Scale-out and trend pyramiding. TP1 books the first partial and moves the
    # stop to break-even; TP2 (reward_risk) books the second partial; the runner
    # trails. After TP1 the EA re-enters on pullbacks and rides the trend until
    # the EMA stack flips, capped at max_units concurrent adds.
    tp1_r: float = 1.0
    tp1_fraction: float = 0.4
    tp2_fraction: float = 0.3
    pyramid_enabled: bool = True
    max_units: int = 3
    pullback_atr: float = 0.5
    entry_buffer_atr: float = 0.5
    candle_body_min: float = 0.2
    candle_wick_max: float = 0.3
    risk_pct: float = 0.35
    sizing_cost_reserve: float = 1.10
    break_even_r: float = 1.0
    trail_start_r: float = 1.5
    trail_atr: float = 2.0
    max_trades_day: int = 2
    max_losses_day: int = 2
    session_start: int = 8
    session_end: int = 17
    friday_close: int = 17
    # Session behavior: hours (server time) to skip inside the window; the
    # midday lull between the London morning and the New York session is the
    # weakest hour for continuation breakouts.
    skip_hours: tuple[int, ...] = (12,)
    # Higher-timeframe confluence: the breakout is only taken when the primary
    # (H4) and secondary (D1) fast/slow EMA stacks agree with its direction.
    htf_factor: int = 4
    htf_fast: int = 50
    htf_slow: int = 200
    htf2_factor: int = 24
    htf2_fast: int = 50
    htf2_slow: int = 200
    # Volatility regime: current ATR relative to its own average must stay in
    # [min, max]. The default only trims blow-off news-spike breakouts.
    vol_avg_len: int = 50
    vol_ratio_min: float = 0.0
    vol_ratio_max: float = 2.5
    # Market structure: when > 0, require the Donchian channel to be making
    # higher highs and higher lows (or the mirror) over this lookback. Off by
    # default because it reduced in-sample robustness on EURUSD H1.
    ms_channel_lookback: int = 0
    daily_profit_lock_pct: float = 1.0
    official_daily_loss_pct: float = 5.0
    official_total_loss_pct: float = 10.0
    soft_daily_loss_pct: float = 4.0
    soft_total_loss_pct: float = 8.0


def load_m15(path: Path, fallback_spread: float) -> list[Bar]:
    bars: list[Bar] = []
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            spread = (
                float(row["spread_price"])
                if row.get("spread_price") not in (None, "")
                else fallback_spread
            )
            bars.append(
                Bar(
                    datetime.fromisoformat(row["time"]),
                    float(row["open"]),
                    float(row["high"]),
                    float(row["low"]),
                    float(row["close"]),
                    spread,
                )
            )
    return bars


def aggregate_h1(m15: Iterable[Bar]) -> list[Bar]:
    groups: dict[datetime, list[Bar]] = {}
    for bar in m15:
        hour = bar.time.replace(minute=0, second=0, microsecond=0)
        groups.setdefault(hour, []).append(bar)

    output: list[Bar] = []
    for hour in sorted(groups):
        group = sorted(groups[hour], key=lambda bar: bar.time)
        expected = [hour.replace(minute=minute) for minute in (0, 15, 30, 45)]
        if len(group) != 4 or [bar.time for bar in group] != expected:
            continue
        output.append(
            Bar(
                hour,
                group[0].open,
                max(bar.high for bar in group),
                min(bar.low for bar in group),
                group[-1].close,
                max(bar.spread for bar in group),
            )
        )
    return output


def ema(values: list[float], period: int) -> list[float]:
    if not values:
        return []
    alpha = 2.0 / (period + 1.0)
    output = [values[0]]
    for value in values[1:]:
        output.append(alpha * value + (1.0 - alpha) * output[-1])
    return output


def wilder_atr(bars: list[Bar], period: int) -> list[float]:
    output = [math.nan] * len(bars)
    trs: list[float] = []
    for index, bar in enumerate(bars):
        previous_close = bars[index - 1].close if index else bar.close
        tr = max(
            bar.high - bar.low,
            abs(bar.high - previous_close),
            abs(bar.low - previous_close),
        )
        trs.append(tr)
        if index == period - 1:
            output[index] = sum(trs) / period
        elif index >= period:
            output[index] = ((period - 1) * output[index - 1] + tr) / period
    return output


def sma(values: list[float], period: int) -> list[float]:
    output = [math.nan] * len(values)
    if period <= 0:
        return output
    running = 0.0
    for index, value in enumerate(values):
        running += value
        if index >= period:
            running -= values[index - period]
        if index >= period - 1:
            output[index] = running / period
    return output


def htf_ema_aligned(
    bars: list[Bar], factor: int, fast_period: int, slow_period: int
) -> tuple[list[float], list[float]]:
    """Aggregate H1 bars into a higher timeframe (``factor`` hours per bar) and
    return, for every H1 bar, the fast/slow EMA of the last *completed* higher
    timeframe bar. Using the previous HTF bar avoids look-ahead bias."""
    order: list[tuple] = []
    closes_by_key: dict[tuple, list[float]] = {}
    keys: list[tuple] = []
    for bar in bars:
        key = (bar.time.year, bar.time.month, bar.time.day, bar.time.hour // factor)
        if key not in closes_by_key:
            closes_by_key[key] = []
            order.append(key)
        closes_by_key[key].append(bar.close)
        keys.append(key)
    htf_close = [closes_by_key[key][-1] for key in order]
    htf_fast = ema(htf_close, fast_period)
    htf_slow = ema(htf_close, slow_period)
    position = {key: index for index, key in enumerate(order)}
    fast_aligned = [math.nan] * len(bars)
    slow_aligned = [math.nan] * len(bars)
    for index in range(len(bars)):
        bucket = position[keys[index]]
        if bucket - 1 >= 0:
            fast_aligned[index] = htf_fast[bucket - 1]
            slow_aligned[index] = htf_slow[bucket - 1]
    return fast_aligned, slow_aligned


def signal(
    index: int,
    bars: list[Bar],
    fast: list[float],
    slow: list[float],
    config: Config,
    atr: list[float] | None = None,
) -> int:
    if index < max(config.ema_slow + 10, config.donchian + 2):
        return 0
    prior = bars[index - config.donchian - 1 : index - 1]
    upper = max(bar.high for bar in prior)
    lower = min(bar.low for bar in prior)
    close = bars[index - 1].close
    # Require the breakout close to clear the channel by a fraction of ATR so
    # marginal pokes through the range (the main source of whipsaw) are ignored.
    buffer = 0.0
    if atr is not None and config.entry_buffer_atr > 0.0:
        recent_atr = atr[index - 1]
        if not math.isfinite(recent_atr):
            return 0
        buffer = config.entry_buffer_atr * recent_atr
    # Candlestick confirmation on the breakout bar: demand a decisive body that
    # closes in the breakout direction with only a small rejection wick. A doji
    # or a long opposing wick means the range was defended, so skip the trade.
    breakout = bars[index - 1]
    candle_range = breakout.high - breakout.low
    if candle_range <= 0.0:
        return 0
    body = abs(breakout.close - breakout.open)
    upper_wick = breakout.high - max(breakout.open, breakout.close)
    lower_wick = min(breakout.open, breakout.close) - breakout.low
    strong_body = body / candle_range >= config.candle_body_min
    bullish_candle = (
        strong_body
        and breakout.close > breakout.open
        and upper_wick / candle_range <= config.candle_wick_max
    )
    bearish_candle = (
        strong_body
        and breakout.close < breakout.open
        and lower_wick / candle_range <= config.candle_wick_max
    )
    if (
        close > upper + buffer
        and fast[index - 1] > slow[index - 1]
        and fast[index - 1] > fast[index - 2]
        and bullish_candle
    ):
        return 1
    if (
        close < lower - buffer
        and fast[index - 1] < slow[index - 1]
        and fast[index - 1] < fast[index - 2]
        and bearish_candle
    ):
        return -1
    return 0


def in_session(moment: datetime, config: Config) -> bool:
    if moment.weekday() >= 5:
        return False
    if moment.weekday() == 4 and moment.hour >= config.friday_close:
        return False
    if moment.hour in config.skip_hours:
        return False
    if config.session_start == config.session_end:
        return True
    if config.session_start < config.session_end:
        return config.session_start <= moment.hour < config.session_end
    return moment.hour >= config.session_start or moment.hour < config.session_end


def active_floor(initial: float, day_start: float, config: Config) -> float:
    return max(
        day_start - initial * config.official_daily_loss_pct / 100.0,
        initial * (1.0 - config.official_total_loss_pct / 100.0),
        day_start - initial * config.soft_daily_loss_pct / 100.0,
        initial * (1.0 - config.soft_total_loss_pct / 100.0),
    )


def official_floor(initial: float, day_start: float, config: Config) -> float:
    return max(
        day_start - initial * config.official_daily_loss_pct / 100.0,
        initial * (1.0 - config.official_total_loss_pct / 100.0),
    )


def floor_volume(raw_lots: float, minimum: float, maximum: float, step: float) -> float:
    lots = math.floor((raw_lots + 1e-12) / step) * step
    if lots < minimum:
        return 0.0
    return min(lots, maximum)


def summarize(trades: list[Trade]) -> dict[str, float | int | None]:
    profits = [trade.pnl for trade in trades if trade.pnl > 0.0]
    losses = [-trade.pnl for trade in trades if trade.pnl < 0.0]
    total_profit = sum(profits)
    total_loss = sum(losses)
    return {
        "trades": len(trades),
        "wins": len(profits),
        "win_rate_pct": 100.0 * len(profits) / len(trades) if trades else None,
        "net_profit": sum(trade.pnl for trade in trades),
        "profit_factor": total_profit / total_loss if total_loss else None,
        "expectancy_r": sum(trade.r for trade in trades) / len(trades) if trades else None,
        "largest_profit_share_pct": (
            100.0 * max(profits) / total_profit if profits and total_profit else None
        ),
    }


def run_backtest(
    bars: list[Bar],
    meta: dict,
    config: Config,
    *,
    initial_balance: float,
    spread_multiplier: float,
    split_fraction: float,
    max_spread_points: float,
    enforce_ftmo_guards: bool = True,
) -> dict:
    if len(bars) <= config.ema_slow + 10:
        raise ValueError("not enough H1 bars")

    point = float(meta["point"])
    tick_size = float(meta["trade_tick_size"])
    tick_value = float(meta["trade_tick_value_loss"])
    dollars_per_price_per_lot = tick_value / tick_size
    commission = meta.get("commission", {})
    commission_per_side = (
        float(commission.get("per_side_usd_per_lot", 0.0))
        if commission.get("kind") == "usd_per_lot"
        else 0.0
    )
    volume_min = float(meta["volume_min"])
    volume_max = float(meta["volume_max"])
    volume_step = float(meta["volume_step"])

    closes = [bar.close for bar in bars]
    fast = ema(closes, config.ema_fast)
    slow = ema(closes, config.ema_slow)
    atr = wilder_atr(bars, config.atr_period)
    split_index = int(len(bars) * split_fraction)

    # Higher-timeframe confluence stacks and the volatility-regime baseline are
    # precomputed once; each references only completed higher-timeframe data.
    htf_fast_al, htf_slow_al = (
        htf_ema_aligned(bars, config.htf_factor, config.htf_fast, config.htf_slow)
        if config.htf_factor
        else (None, None)
    )
    htf2_fast_al, htf2_slow_al = (
        htf_ema_aligned(bars, config.htf2_factor, config.htf2_fast, config.htf2_slow)
        if config.htf2_factor
        else (None, None)
    )
    atr_sma = (
        sma([value if math.isfinite(value) else 0.0 for value in atr], config.vol_avg_len)
        if config.vol_avg_len
        else None
    )

    def context_ok(index: int, side: int) -> bool:
        prev = index - 1
        if config.htf_factor:
            hf, hs = htf_fast_al[prev], htf_slow_al[prev]
            if not (math.isfinite(hf) and math.isfinite(hs)):
                return False
            if side > 0 and not hf > hs:
                return False
            if side < 0 and not hf < hs:
                return False
        if config.htf2_factor:
            hf2, hs2 = htf2_fast_al[prev], htf2_slow_al[prev]
            if not (math.isfinite(hf2) and math.isfinite(hs2)):
                return False
            if side > 0 and not hf2 > hs2:
                return False
            if side < 0 and not hf2 < hs2:
                return False
        if config.vol_avg_len:
            base = atr_sma[prev]
            if not base or base <= 0.0:
                return False
            ratio = atr[prev] / base
            if not config.vol_ratio_min <= ratio <= config.vol_ratio_max:
                return False
        if config.ms_channel_lookback:
            lookback = config.ms_channel_lookback
            if prev - lookback - config.donchian < 0:
                return False
            now = bars[index - config.donchian - 1 : prev]
            then = bars[index - config.donchian - 1 - lookback : prev - lookback]
            up_now = max(bar.high for bar in now)
            up_then = max(bar.high for bar in then)
            lo_now = min(bar.low for bar in now)
            lo_then = min(bar.low for bar in then)
            if side > 0 and not (up_now > up_then and lo_now > lo_then):
                return False
            if side < 0 and not (up_now < up_then and lo_now < lo_then):
                return False
        return True

    balance = initial_balance
    peak_equity = initial_balance
    max_drawdown_pct = 0.0
    max_daily_loss_pct = 0.0
    day_start_balance = initial_balance
    current_day = None
    entries_today = 0
    losses_today = 0
    units: list[Position] = []
    trend_side = 0
    trend_extreme = 0.0
    pullback_armed = False
    trades: list[Trade] = []
    official_breach = False
    soft_guard_exits = 0
    equity_curve: list[tuple[str, float]] = []

    def book(unit: Position, exit_price: float, close_lots: float, bar: Bar, reason: str) -> None:
        nonlocal balance, losses_today
        if close_lots <= 0.0:
            return
        dollars_per_price = close_lots * dollars_per_price_per_lot
        gross = unit.side * (exit_price - unit.entry) * dollars_per_price
        pnl = gross - 2.0 * commission_per_side * close_lots
        balance += pnl
        if pnl < 0.0:
            losses_today += 1
        unit_risk = (
            unit.risk_budget * (close_lots / unit.original_lots)
            if unit.original_lots
            else unit.risk_budget
        )
        trades.append(
            Trade(
                unit.entry_time.isoformat(sep=" "),
                bar.time.isoformat(sep=" "),
                unit.side,
                unit.entry,
                exit_price,
                pnl,
                pnl / unit_risk if unit_risk else 0.0,
                reason,
                unit.entry_index >= split_index,
            )
        )
        unit.lots -= close_lots

    def open_risk() -> float:
        total = 0.0
        for unit in units:
            risk_price = unit.entry - unit.stop if unit.side > 0 else unit.stop - unit.entry
            total += max(0.0, risk_price) * unit.lots * dollars_per_price_per_lot
        return total

    def flatten(bar: Bar, spread: float, reason: str, adverse: bool = False) -> None:
        nonlocal trend_side, trend_extreme, pullback_armed
        for unit in list(units):
            if adverse:
                price = bar.low if unit.side > 0 else bar.high + spread
            else:
                price = bar.open if unit.side > 0 else bar.open + spread
            book(unit, price, unit.lots, bar, reason)
        units.clear()
        trend_side = 0
        trend_extreme = 0.0
        pullback_armed = False

    def try_open(side: int, index: int, bar: Bar, spread: float) -> bool:
        bid = bar.open
        ask = bar.open + spread
        entry = ask if side > 0 else bid
        stop_distance = atr[index - 1] * config.stop_atr
        stop = bid - stop_distance if side > 0 else ask + stop_distance
        initial_risk = abs(entry - stop)
        risk_money = balance * config.risk_pct / 100.0
        raw_lots = (risk_money / config.sizing_cost_reserve) / (
            initial_risk * dollars_per_price_per_lot
        )
        lots = floor_volume(raw_lots, volume_min, volume_max, volume_step)
        if not lots:
            return False
        # The projected-risk gate covers the aggregate open risk plus this add,
        # so pyramiding can never risk more than the FTMO floor allows.
        projected = balance - (open_risk() + risk_money) * 1.15
        if enforce_ftmo_guards and not (
            projected > active_floor(initial_balance, day_start_balance, config)
        ):
            return False
        tp1_price = entry + side * initial_risk * config.tp1_r
        tp2_price = entry + side * initial_risk * config.reward_risk
        tp1_lots = floor_volume(lots * config.tp1_fraction, 0.0, volume_max, volume_step)
        tp2_lots = floor_volume(lots * config.tp2_fraction, 0.0, volume_max, volume_step)
        if tp1_lots + tp2_lots > lots:
            tp2_lots = max(0.0, lots - tp1_lots)
        units.append(
            Position(
                side,
                bar.time,
                index,
                entry,
                stop,
                initial_risk,
                dollars_per_price_per_lot,
                lots,
                lots,
                risk_money,
                tp1_price,
                tp2_price,
                tp1_lots,
                tp2_lots,
            )
        )
        return True

    for index, raw_bar in enumerate(bars):
        spread = raw_bar.spread * spread_multiplier
        bar = Bar(
            raw_bar.time,
            raw_bar.open,
            raw_bar.high,
            raw_bar.low,
            raw_bar.close,
            spread,
        )
        day = bar.time.date()
        if day != current_day:
            current_day = day
            day_start_balance = balance
            entries_today = 0
            losses_today = 0

        # Trend-change exit: flatten everything when the EMA stack flips against
        # the open trend, then let a fresh signal start the next trend.
        if units and index >= 1:
            flipped = (trend_side > 0 and fast[index - 1] < slow[index - 1]) or (
                trend_side < 0 and fast[index - 1] > slow[index - 1]
            )
            if flipped:
                flatten(bar, spread, "trend_change")

        if units and bar.time.weekday() == 4 and bar.time.hour >= config.friday_close:
            flatten(bar, spread, "weekend")

        # Aggregate FTMO account guard across every open unit.
        if units:
            adverse_equity = balance
            for unit in units:
                adverse_price = bar.low if unit.side > 0 else bar.high + spread
                adverse_equity += unit.side * (
                    adverse_price - unit.entry
                ) * unit.lots * dollars_per_price_per_lot
                adverse_equity -= commission_per_side * unit.lots
            max_daily_loss_pct = max(
                max_daily_loss_pct,
                100.0 * max(0.0, day_start_balance - adverse_equity) / initial_balance,
            )
            if adverse_equity <= official_floor(initial_balance, day_start_balance, config):
                official_breach = True
            if enforce_ftmo_guards and adverse_equity <= active_floor(
                initial_balance, day_start_balance, config
            ):
                flatten(bar, spread, "soft_guard", adverse=True)
                soft_guard_exits += 1

        # Per-unit scale-out and trailing (stop first for pessimism).
        for unit in list(units):
            if unit.side > 0:
                stop_hit = bar.low <= unit.stop
            else:
                stop_hit = bar.high + spread >= unit.stop
            if stop_hit:
                book(unit, unit.stop, unit.lots, bar, "stop")
                units.remove(unit)
                continue

            if not unit.tp1_done:
                tp1_hit = (
                    bar.high >= unit.tp1_price
                    if unit.side > 0
                    else bar.low + spread <= unit.tp1_price
                )
                if tp1_hit:
                    book(unit, unit.tp1_price, min(unit.tp1_lots, unit.lots), bar, "tp1")
                    unit.tp1_done = True
                    unit.stop = unit.entry  # trail original order to break-even
                    if unit.lots <= 0.0:
                        units.remove(unit)
                        continue

            if unit.tp1_done and not unit.tp2_done:
                tp2_hit = (
                    bar.high >= unit.tp2_price
                    if unit.side > 0
                    else bar.low + spread <= unit.tp2_price
                )
                if tp2_hit:
                    book(unit, unit.tp2_price, min(unit.tp2_lots, unit.lots), bar, "tp2")
                    unit.tp2_done = True
                    if unit.lots <= 0.0:
                        units.remove(unit)
                        continue

            favorable = (
                bar.high - unit.entry if unit.side > 0 else unit.entry - (bar.low + spread)
            )
            candidate = unit.stop
            if favorable >= unit.initial_risk * config.break_even_r:
                candidate = (
                    max(candidate, unit.entry)
                    if unit.side > 0
                    else min(candidate, unit.entry)
                )
            if unit.tp2_done and favorable >= unit.initial_risk * config.trail_start_r:
                trail = (
                    bar.high - atr[index] * config.trail_atr
                    if unit.side > 0
                    else bar.low + spread + atr[index] * config.trail_atr
                )
                candidate = (
                    max(candidate, trail) if unit.side > 0 else min(candidate, trail)
                )
            retraced = (
                bar.low <= candidate if unit.side > 0 else bar.high + spread >= candidate
            )
            if retraced and candidate != unit.stop:
                book(unit, candidate, unit.lots, bar, "trail")
                units.remove(unit)
            else:
                unit.stop = candidate

        entry_ready = (
            in_session(bar.time, config)
            and losses_today < config.max_losses_day
            and balance - day_start_balance
            < initial_balance * config.daily_profit_lock_pct / 100.0
            and spread / point <= max_spread_points
            and index >= 2
            and math.isfinite(atr[index - 1])
        )

        # New trend entry when flat.
        if entry_ready and not units and entries_today < config.max_trades_day:
            side = signal(index, bars, fast, slow, config, atr)
            if side and not context_ok(index, side):
                side = 0
            if side and try_open(side, index, bar, spread):
                trend_side = side
                trend_extreme = bar.open
                pullback_armed = False
                entries_today += 1

        # Pyramid add: after TP1 on the trend, buy the pullback resume.
        elif (
            entry_ready
            and config.pyramid_enabled
            and units
            and trend_side != 0
            and len(units) < config.max_units
            and any(unit.tp1_done for unit in units)
            and pullback_armed
        ):
            resume = (
                bars[index - 1].close > bars[index - 2].high
                if trend_side > 0
                else bars[index - 1].close < bars[index - 2].low
            )
            ema_ok = (
                fast[index - 1] > slow[index - 1] and fast[index - 1] > fast[index - 2]
                if trend_side > 0
                else fast[index - 1] < slow[index - 1] and fast[index - 1] < fast[index - 2]
            )
            if resume and ema_ok and try_open(trend_side, index, bar, spread):
                pullback_armed = False
                trend_extreme = bar.open

        # Track the favorable extreme and arm the next pullback re-entry.
        if units and trend_side != 0 and math.isfinite(atr[index]):
            if trend_side > 0:
                trend_extreme = max(trend_extreme, bar.high)
                if trend_extreme - bar.low >= config.pullback_atr * atr[index]:
                    pullback_armed = True
            else:
                trend_extreme = min(trend_extreme, bar.low)
                if bar.high - trend_extreme >= config.pullback_atr * atr[index]:
                    pullback_armed = True
        elif not units:
            trend_side = 0
            pullback_armed = False

        equity = balance
        for unit in units:
            mark = bar.close if unit.side > 0 else bar.close + spread
            equity += unit.side * (mark - unit.entry) * unit.lots * dollars_per_price_per_lot
            equity -= commission_per_side * unit.lots
        peak_equity = max(peak_equity, equity)
        max_drawdown_pct = max(
            max_drawdown_pct,
            100.0 * (peak_equity - equity) / peak_equity,
        )
        max_daily_loss_pct = max(
            max_daily_loss_pct,
            100.0 * max(0.0, day_start_balance - equity) / initial_balance,
        )
        equity_curve.append((bar.time.isoformat(sep=" "), equity))

    if units:
        last = bars[-1]
        flatten(last, last.spread * spread_multiplier, "end_of_data")

    oos = [trade for trade in trades if trade.oos]
    years: dict[str, dict] = {}
    for year in sorted({trade.exit_time[:4] for trade in trades}):
        years[year] = summarize([trade for trade in trades if trade.exit_time.startswith(year)])

    return {
        "bars": len(bars),
        "from": bars[0].time.isoformat(sep=" "),
        "to": bars[-1].time.isoformat(sep=" "),
        "split_time": bars[split_index].time.isoformat(sep=" "),
        "spread_multiplier": spread_multiplier,
        "ftmo_guards_enforced": enforce_ftmo_guards,
        "all": summarize(trades),
        "oos": summarize(oos),
        "ending_balance": balance,
        "return_pct": 100.0 * (balance / initial_balance - 1.0),
        "max_equity_drawdown_pct": max_drawdown_pct,
        "max_daily_equity_loss_pct": max_daily_loss_pct,
        "official_rule_breach": official_breach,
        "soft_guard_exits": soft_guard_exits,
        "yearly": years,
        "trades": [asdict(trade) for trade in trades],
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, required=True, help="M15 candle CSV")
    parser.add_argument("--broker-meta", type=Path, required=True)
    parser.add_argument("--symbol", default="EURUSD")
    parser.add_argument("--fallback-spread-points", type=float, default=10.0)
    parser.add_argument("--max-spread-points", type=float, default=25.0)
    parser.add_argument("--initial-balance", type=float, default=100_000.0)
    parser.add_argument("--split", type=float, default=0.70)
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    broker = json.loads(args.broker_meta.read_text(encoding="utf-8"))
    meta = broker["symbols"][args.symbol]
    fallback_spread = args.fallback_spread_points * float(meta["point"])
    bars = aggregate_h1(load_m15(args.data, fallback_spread))
    config = Config()
    results = {
        "model": "conservative H1 OHLC screening; not MT5 tick parity",
        "source": {
            "path": str(args.data),
            "sha256": hashlib.sha256(args.data.read_bytes()).hexdigest(),
            "symbol": args.symbol,
            "fallback_spread_points": args.fallback_spread_points,
            "timezone_assumption": "source timestamps treated as EA server time",
        },
        "config": asdict(config),
        "measured": run_backtest(
            bars,
            meta,
            config,
            initial_balance=args.initial_balance,
            spread_multiplier=1.0,
            split_fraction=args.split,
            max_spread_points=args.max_spread_points,
        ),
        "double_spread": run_backtest(
            bars,
            meta,
            config,
            initial_balance=args.initial_balance,
            spread_multiplier=2.0,
            split_fraction=args.split,
            max_spread_points=args.max_spread_points,
        ),
        "edge_diagnostic": run_backtest(
            bars,
            meta,
            config,
            initial_balance=args.initial_balance,
            spread_multiplier=1.0,
            split_fraction=args.split,
            max_spread_points=args.max_spread_points,
            enforce_ftmo_guards=False,
        ),
        "edge_diagnostic_double_spread": run_backtest(
            bars,
            meta,
            config,
            initial_balance=args.initial_balance,
            spread_multiplier=2.0,
            split_fraction=args.split,
            max_spread_points=args.max_spread_points,
            enforce_ftmo_guards=False,
        ),
    }
    rendered = json.dumps(results, indent=2, sort_keys=True)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    main()
