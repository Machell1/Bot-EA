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
    side: int
    entry_time: datetime
    entry_index: int
    entry: float
    stop: float
    target: float
    initial_risk: float
    dollars_per_price: float
    lots: float
    risk_budget: float


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
    entry_buffer_atr: float = 0.5
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
    if (
        close > upper + buffer
        and fast[index - 1] > slow[index - 1]
        and fast[index - 1] > fast[index - 2]
    ):
        return 1
    if (
        close < lower - buffer
        and fast[index - 1] < slow[index - 1]
        and fast[index - 1] < fast[index - 2]
    ):
        return -1
    return 0


def in_session(moment: datetime, config: Config) -> bool:
    if moment.weekday() >= 5:
        return False
    if moment.weekday() == 4 and moment.hour >= config.friday_close:
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

    balance = initial_balance
    peak_equity = initial_balance
    max_drawdown_pct = 0.0
    max_daily_loss_pct = 0.0
    day_start_balance = initial_balance
    current_day = None
    entries_today = 0
    losses_today = 0
    position: Position | None = None
    trades: list[Trade] = []
    official_breach = False
    soft_guard_exits = 0
    equity_curve: list[tuple[str, float]] = []

    def close_position(exit_price: float, bar: Bar, reason: str) -> None:
        nonlocal balance, position, losses_today
        assert position is not None
        gross = position.side * (exit_price - position.entry) * position.dollars_per_price
        costs = 2.0 * commission_per_side * position.lots
        pnl = gross - costs
        balance += pnl
        if pnl < 0.0:
            losses_today += 1
        trades.append(
            Trade(
                position.entry_time.isoformat(sep=" "),
                bar.time.isoformat(sep=" "),
                position.side,
                position.entry,
                exit_price,
                pnl,
                pnl / position.risk_budget,
                reason,
                position.entry_index >= split_index,
            )
        )
        position = None

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

        if position is not None and bar.time.weekday() == 4 and bar.time.hour >= config.friday_close:
            exit_price = bar.open if position.side > 0 else bar.open + spread
            close_position(exit_price, bar, "weekend")

        if (
            position is None
            and in_session(bar.time, config)
            and entries_today < config.max_trades_day
            and losses_today < config.max_losses_day
            and balance - day_start_balance < initial_balance * config.daily_profit_lock_pct / 100.0
            and spread / point <= max_spread_points
            and math.isfinite(atr[index - 1] if index else math.nan)
        ):
            side = signal(index, bars, fast, slow, config, atr)
            if side:
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
                if lots:
                    dollars_per_price = lots * dollars_per_price_per_lot
                    target = entry + side * initial_risk * config.reward_risk
                    projected = balance - risk_money * 1.15
                    if (
                        not enforce_ftmo_guards
                        or projected > active_floor(initial_balance, day_start_balance, config)
                    ):
                        position = Position(
                            side,
                            bar.time,
                            index,
                            entry,
                            stop,
                            target,
                            initial_risk,
                            dollars_per_price,
                            lots,
                            risk_money,
                        )
                        entries_today += 1

        if position is not None:
            adverse_price = bar.low if position.side > 0 else bar.high + spread
            adverse_equity = balance + position.side * (
                adverse_price - position.entry
            ) * position.dollars_per_price - commission_per_side * position.lots
            max_daily_loss_pct = max(
                max_daily_loss_pct,
                100.0 * max(0.0, day_start_balance - adverse_equity) / initial_balance,
            )
            if adverse_equity <= official_floor(initial_balance, day_start_balance, config):
                official_breach = True
            if (
                enforce_ftmo_guards
                and adverse_equity <= active_floor(initial_balance, day_start_balance, config)
            ):
                close_position(adverse_price, bar, "soft_guard")
                soft_guard_exits += 1

        if position is not None:
            if position.side > 0:
                stop_hit = bar.low <= position.stop
                target_hit = bar.high >= position.target
            else:
                stop_hit = bar.high + spread >= position.stop
                target_hit = bar.low + spread <= position.target

            if stop_hit:
                close_position(position.stop, bar, "stop")
            elif target_hit:
                close_position(position.target, bar, "target")
            else:
                assert position is not None
                favorable = (
                    bar.high - position.entry
                    if position.side > 0
                    else position.entry - (bar.low + spread)
                )
                old_stop = position.stop
                candidate = old_stop
                if favorable >= position.initial_risk * config.break_even_r:
                    candidate = (
                        max(candidate, position.entry)
                        if position.side > 0
                        else min(candidate, position.entry)
                    )
                if favorable >= position.initial_risk * config.trail_start_r:
                    trail = (
                        bar.high - atr[index] * config.trail_atr
                        if position.side > 0
                        else bar.low + spread + atr[index] * config.trail_atr
                    )
                    candidate = (
                        max(candidate, trail)
                        if position.side > 0
                        else min(candidate, trail)
                    )
                retraced = (
                    bar.low <= candidate
                    if position.side > 0
                    else bar.high + spread >= candidate
                )
                if retraced and candidate != old_stop:
                    close_position(candidate, bar, "trail")
                else:
                    position.stop = candidate

        equity = balance
        if position is not None:
            mark = bar.close if position.side > 0 else bar.close + spread
            equity += position.side * (mark - position.entry) * position.dollars_per_price
            equity -= commission_per_side * position.lots
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

    if position is not None:
        last = bars[-1]
        close_position(
            last.close if position.side > 0 else last.close + last.spread * spread_multiplier,
            last,
            "end_of_data",
        )

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
