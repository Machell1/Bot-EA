import unittest
from datetime import datetime, timedelta

from backtest.ftmo_quant_backtest import (
    Bar,
    Config,
    active_floor,
    aggregate_h1,
    floor_volume,
    signal,
)


class BacktestEngineTests(unittest.TestCase):
    def test_h1_aggregation_requires_four_complete_quarters(self) -> None:
        start = datetime(2026, 1, 5, 9)
        bars = [
            Bar(start + timedelta(minutes=15 * i), 1 + i, 2 + i, 0 + i, 1.5 + i, 0.1 + i * 0.01)
            for i in range(4)
        ]
        output = aggregate_h1(bars)
        self.assertEqual(len(output), 1)
        self.assertEqual(output[0].open, 1)
        self.assertEqual(output[0].close, 4.5)
        self.assertEqual(output[0].high, 5)
        self.assertEqual(output[0].spread, 0.13)
        self.assertEqual(aggregate_h1(bars[:3]), [])

    def test_signal_uses_prior_bar_breakout(self) -> None:
        start = datetime(2026, 1, 1)
        bars = [
            Bar(start + timedelta(hours=i), float(i), i + 0.2, i - 0.2, i + 0.1, 0.01)
            for i in range(20)
        ]
        fast = [float(i) for i in range(20)]
        slow = [float(i) - 1.0 for i in range(20)]
        config = Config(donchian=3, ema_fast=2, ema_slow=5)
        self.assertEqual(signal(19, bars, fast, slow, config), 1)

    def test_entry_buffer_rejects_marginal_breakout(self) -> None:
        start = datetime(2026, 1, 1)
        bars = [
            Bar(start + timedelta(hours=i), float(i), i + 0.2, i - 0.2, i + 0.1, 0.01)
            for i in range(20)
        ]
        fast = [float(i) for i in range(20)]
        slow = [float(i) - 1.0 for i in range(20)]
        config = Config(donchian=3, ema_fast=2, ema_slow=5)
        # Without an ATR series the buffer is inactive and the breakout signals.
        self.assertEqual(signal(19, bars, fast, slow, config), 1)
        # A wide ATR buffer (0.5 * 2.0) lifts the trigger above the breakout close.
        self.assertEqual(signal(19, bars, fast, slow, config, [2.0] * 20), 0)
        # A small ATR buffer (0.5 * 1.0) still permits the same breakout.
        self.assertEqual(signal(19, bars, fast, slow, config, [1.0] * 20), 1)

    def test_soft_floor_matches_ea_defaults(self) -> None:
        self.assertEqual(active_floor(100_000, 103_000, Config()), 99_000)

    def test_volume_is_floored_to_broker_step(self) -> None:
        self.assertAlmostEqual(floor_volume(1.237, 0.01, 50, 0.01), 1.23)
        self.assertEqual(floor_volume(0.009, 0.01, 50, 0.01), 0.0)


if __name__ == "__main__":
    unittest.main()
