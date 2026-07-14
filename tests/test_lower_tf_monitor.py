import unittest

from backtest.lower_tf_monitor import (
    MonitorConfig,
    TFSeries,
    assess,
    ema_aligned,
    momentum_divergence,
    projected_adverse,
    rsi,
    simulate_touch_probability,
)


def _series(closes: list[float]) -> TFSeries:
    return TFSeries(
        [c + 0.02 for c in closes],
        [c - 0.02 for c in closes],
        list(closes),
    )


def _steady_up(n: int, step: float, start: float = 100.0) -> list[float]:
    return [start + step * i for i in range(n)]


def _divergent_up() -> list[float]:
    # Strong steady rally, then a choppy tail whose net level is higher than six
    # bars back but whose recent losses pull RSI down => bearish divergence.
    base = _steady_up(50, 0.15)
    closes = list(base)
    price = base[-1]
    for delta in (0.0, -0.6, -0.5, 0.45, 0.42, 0.30, 0.30):
        price += delta
        closes.append(price)
    return closes


class LowerTfMonitorTests(unittest.TestCase):
    CFG = MonitorConfig()

    def test_rsi_bounds_and_extreme(self) -> None:
        values = rsi(_steady_up(40, 0.1), 14)
        self.assertTrue(all(v != v or 0.0 <= v <= 100.0 for v in values))
        # A monotonic rise has no losses -> RSI pinned at 100.
        self.assertAlmostEqual(values[-1], 100.0)

    def test_no_divergence_in_clean_trend(self) -> None:
        self.assertFalse(momentum_divergence(_steady_up(60, 0.05), 1, self.CFG))

    def test_bearish_divergence_detected_for_long(self) -> None:
        self.assertTrue(momentum_divergence(_divergent_up(), 1, self.CFG))

    def test_ema_alignment_flags_counter_trend(self) -> None:
        self.assertTrue(ema_aligned(_steady_up(40, 0.2), 1, self.CFG))
        self.assertFalse(ema_aligned([112 - 0.35 * i for i in range(40)], 1, self.CFG))

    def test_touch_probability_zero_when_stop_is_far(self) -> None:
        self.assertEqual(
            simulate_touch_probability(_steady_up(60, 0.05), 90.0, 1, self.CFG), 0.0
        )

    def test_projected_adverse_is_below_price_for_long(self) -> None:
        closes = _steady_up(60, 0.05)
        self.assertLessEqual(projected_adverse(closes, 1, self.CFG), closes[-1])

    def test_status_ok_when_aligned_and_quiet(self) -> None:
        result = assess(1, 95.0, _series(_steady_up(60, 0.05)), _series(_steady_up(40, 0.2)), self.CFG)
        self.assertEqual(result.status, "ok")

    def test_status_warning_on_m5_divergence_only(self) -> None:
        # M5 diverges, but M15 still aligned with the H1 long -> monitor, not act.
        result = assess(1, 90.0, _series(_divergent_up()), _series(_steady_up(40, 0.2)), self.CFG)
        self.assertEqual(result.status, "warning")

    def test_status_action_when_m15_flips_against_trend(self) -> None:
        # M15 EMA stack against the long AND an M5 threat -> immediate action.
        m15_down = [112 - 0.35 * i for i in range(40)]
        result = assess(1, 90.0, _series(_divergent_up()), _series(m15_down), self.CFG)
        self.assertEqual(result.status, "action")


if __name__ == "__main__":
    unittest.main()
