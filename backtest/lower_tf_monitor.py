#!/usr/bin/env python3
"""Post-fill lower-timeframe monitor for the FTMO Quant EA.

Once an H1 unit is filled, this engine watches the M5 and M15 timeframes and
grades the open trend as OK / WARNING / ACTION using three inputs:

1. Momentum divergence (price vs RSI) on each lower timeframe, against the
   position side.
2. Alignment: each lower timeframe's fast/slow EMA stack must still agree with
   the H1 trend direction.
3. Near-term variance: from recent M5 return volatility, a projected adverse
   price band ~10 minutes ahead, plus a small Monte-Carlo estimate of the
   probability price touches the current stop within that horizon.

Everything here is dependency-free and deterministic (the Monte-Carlo is
seeded), so it is unit-testable. The MQL5 EA mirrors the same decision logic
live on real M5/M15 data; the H1 screening backtest cannot exercise the M5
layer (no M5 data), so this module is validated in isolation.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

from backtest.ftmo_quant_backtest import ema


@dataclass(frozen=True)
class TFSeries:
    """Recent completed candles for one timeframe, oldest first."""

    high: list[float]
    low: list[float]
    close: list[float]


@dataclass(frozen=True)
class MonitorConfig:
    rsi_period: int = 14
    fast_ema: int = 8
    slow_ema: int = 21
    divergence_lookback: int = 6      # bars back for the price/RSI slope compare
    horizon_minutes: int = 10
    m5_minutes: int = 5
    variance_window: int = 20         # M5 bars for the return-volatility estimate
    variance_z: float = 2.0           # band width in sigmas for the projection
    mc_paths: int = 400
    mc_seed: int = 20260714
    warn_touch_prob: float = 0.25     # P(stop touch in horizon) -> WARNING
    action_touch_prob: float = 0.50   # -> ACTION (with misalignment)


@dataclass(frozen=True)
class Assessment:
    status: str                        # "ok" | "warning" | "action"
    touch_probability: float
    projected_adverse: float
    reasons: list[str] = field(default_factory=list)


def rsi(closes: list[float], period: int) -> list[float]:
    """Wilder's RSI, matching MetaTrader 5's ``iRSI`` (SMMA of gains/losses)."""
    n = len(closes)
    out = [math.nan] * n
    if n <= period:
        return out
    gains = 0.0
    losses = 0.0
    for i in range(1, period + 1):
        change = closes[i] - closes[i - 1]
        gains += max(change, 0.0)
        losses += max(-change, 0.0)
    avg_gain = gains / period
    avg_loss = losses / period
    out[period] = 100.0 if avg_loss == 0.0 else 100.0 - 100.0 / (1.0 + avg_gain / avg_loss)
    for i in range(period + 1, n):
        change = closes[i] - closes[i - 1]
        gain = max(change, 0.0)
        loss = max(-change, 0.0)
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
        out[i] = 100.0 if avg_loss == 0.0 else 100.0 - 100.0 / (1.0 + avg_gain / avg_loss)
    return out


def momentum_divergence(closes: list[float], side: int, config: MonitorConfig) -> bool:
    """Regular divergence against ``side`` measured as opposing price/RSI slope
    over ``divergence_lookback`` bars: for a long, price higher but RSI lower is
    bearish divergence (a warning); mirror for a short."""
    look = config.divergence_lookback
    if len(closes) <= config.rsi_period + look + 1:
        return False
    osc = rsi(closes, config.rsi_period)
    price_now, price_then = closes[-1], closes[-1 - look]
    osc_now, osc_then = osc[-1], osc[-1 - look]
    if not (math.isfinite(osc_now) and math.isfinite(osc_then)):
        return False
    if side > 0:
        return price_now > price_then and osc_now < osc_then
    return price_now < price_then and osc_now > osc_then


def ema_aligned(closes: list[float], side: int, config: MonitorConfig) -> bool:
    """Whether this timeframe's fast/slow EMA stack agrees with ``side``."""
    if len(closes) < config.slow_ema:
        return True  # not enough data: do not raise a false misalignment
    fast = ema(closes, config.fast_ema)[-1]
    slow = ema(closes, config.slow_ema)[-1]
    return fast > slow if side > 0 else fast < slow


def _return_sigma(closes: list[float], window: int) -> float:
    sample = closes[-(window + 1):]
    if len(sample) < 3:
        return 0.0
    rets = [
        (sample[i] - sample[i - 1]) / sample[i - 1]
        for i in range(1, len(sample))
        if sample[i - 1] != 0.0
    ]
    if len(rets) < 2:
        return 0.0
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    return math.sqrt(var)


def horizon_steps(config: MonitorConfig) -> int:
    return max(1, config.horizon_minutes // config.m5_minutes)


def projected_adverse(m5_closes: list[float], side: int, config: MonitorConfig) -> float:
    """Adverse edge of a +/- z-sigma band ``horizon_minutes`` ahead, from recent
    M5 return volatility."""
    price = m5_closes[-1]
    sigma = _return_sigma(m5_closes, config.variance_window)
    step = sigma * math.sqrt(horizon_steps(config))
    move = config.variance_z * step * price
    return price - move if side > 0 else price + move


def simulate_touch_probability(
    m5_closes: list[float], stop: float, side: int, config: MonitorConfig
) -> float:
    """Monte-Carlo estimate of P(price touches ``stop`` within the horizon),
    simulating short random walks from recent M5 return volatility."""
    sigma = _return_sigma(m5_closes, config.variance_window)
    if sigma <= 0.0:
        return 0.0
    steps = horizon_steps(config)
    price0 = m5_closes[-1]
    rng = random.Random(config.mc_seed)
    hits = 0
    for _ in range(config.mc_paths):
        price = price0
        touched = False
        for _ in range(steps):
            price *= 1.0 + rng.gauss(0.0, sigma)
            if (side > 0 and price <= stop) or (side < 0 and price >= stop):
                touched = True
                break
        hits += 1 if touched else 0
    return hits / config.mc_paths


def assess(
    side: int,
    stop: float,
    m5: TFSeries,
    m15: TFSeries,
    config: MonitorConfig | None = None,
) -> Assessment:
    config = config or MonitorConfig()
    reasons: list[str] = []

    div_m5 = momentum_divergence(m5.close, side, config)
    div_m15 = momentum_divergence(m15.close, side, config)
    aligned_m5 = ema_aligned(m5.close, side, config)
    aligned_m15 = ema_aligned(m15.close, side, config)

    adverse = projected_adverse(m5.close, side, config)
    band_breach = adverse <= stop if side > 0 else adverse >= stop
    touch_prob = simulate_touch_probability(m5.close, stop, side, config)

    if div_m5:
        reasons.append("M5 momentum divergence")
    if div_m15:
        reasons.append("M15 momentum divergence")
    if not aligned_m5:
        reasons.append("M5 EMA stack against trend")
    if not aligned_m15:
        reasons.append("M15 EMA stack against trend")
    if band_breach:
        reasons.append("projected 10m variance reaches stop")
    if touch_prob >= config.warn_touch_prob:
        reasons.append(f"P(stop touch <=10m)={touch_prob:.0%}")

    threat = div_m5 or div_m15 or band_breach or touch_prob >= config.action_touch_prob
    if (not aligned_m15) and threat:
        status = "action"
    elif (
        div_m5
        or div_m15
        or band_breach
        or not aligned_m5
        or touch_prob >= config.warn_touch_prob
    ):
        status = "warning"
    else:
        status = "ok"
        reasons.append("aligned with H1; near-term variance within tolerance")

    return Assessment(status, touch_prob, adverse, reasons)
