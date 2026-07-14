import math
import unittest
from datetime import datetime, timedelta

from backtest.ftmo_quant_backtest import (
    Bar,
    Config,
    active_floor,
    aggregate_h1,
    atr_sma_of_tr,
    ema,
    floor_volume,
    htf_ema_aligned,
    in_session,
    run_backtest,
    signal,
    sma,
)

_TEST_META = {
    "point": 0.01,
    "trade_tick_size": 0.01,
    "trade_tick_value_loss": 1.0,
    "volume_min": 0.01,
    "volume_max": 100.0,
    "volume_step": 0.01,
}


def _uptrend_bars(count: int) -> list:
    """A steady uptrend with periodic shallow pullbacks, for exercising the
    scale-out and pullback-pyramiding engine deterministically."""
    start = datetime(2024, 1, 1, 0)  # Monday
    bars = []
    price = 100.0
    for i in range(count):
        step = -0.18 if i % 12 in (6, 7) else 0.15
        open_ = price
        close = price + step
        bars.append(
            Bar(
                start + timedelta(hours=i),
                open_,
                max(open_, close) + 0.05,
                min(open_, close) - 0.05,
                close,
                0.001,
            )
        )
        price = close
    return bars


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

    def test_candlestick_rejects_weak_breakout_bar(self) -> None:
        start = datetime(2026, 1, 1)
        bars = [
            Bar(start + timedelta(hours=i), float(i), i + 0.2, i - 0.2, i + 0.1, 0.01)
            for i in range(20)
        ]
        fast = [float(i) for i in range(20)]
        slow = [float(i) - 1.0 for i in range(20)]
        config = Config(donchian=3, ema_fast=2, ema_slow=5)
        # A decisive momentum candle passes the confirmation filter.
        self.assertEqual(signal(19, bars, fast, slow, config), 1)
        # Same breakout close but a long upper rejection wick and a tiny body:
        # the range was defended, so the trade is skipped.
        bars[18] = Bar(start + timedelta(hours=18), 18.0, 19.0, 17.95, 18.05, 0.01)
        self.assertEqual(signal(19, bars, fast, slow, config), 0)

    def test_atr_matches_sma_of_true_range(self) -> None:
        # MT5's iATR is an SMA of True Range (not Wilder), so the engine's ATR
        # must be a plain moving average of TR with the first TR ignored.
        start = datetime(2026, 1, 5, 0)
        highs_lows = [(10.0, 9.0), (11.0, 9.5), (10.5, 9.5), (12.0, 10.0), (11.5, 10.5)]
        bars = [
            Bar(start + timedelta(hours=i), lo, hi, lo, hi, 0.0)
            for i, (hi, lo) in enumerate(highs_lows)
        ]
        atr = atr_sma_of_tr(bars, 2)
        tr = [0.0]
        for i in range(1, len(bars)):
            pc = bars[i - 1].close
            tr.append(max(bars[i].high, pc) - min(bars[i].low, pc))
        self.assertTrue(math.isnan(atr[1]))
        self.assertAlmostEqual(atr[2], (tr[1] + tr[2]) / 2)
        self.assertAlmostEqual(atr[3], (tr[2] + tr[3]) / 2)
        self.assertAlmostEqual(atr[4], (tr[3] + tr[4]) / 2)

    def test_sma_uses_trailing_window(self) -> None:
        result = sma([1.0, 2.0, 3.0, 4.0], 2)
        self.assertTrue(math.isnan(result[0]))
        self.assertEqual(result[1:], [1.5, 2.5, 3.5])

    def test_htf_ema_aligned_uses_previous_completed_bar(self) -> None:
        start = datetime(2026, 1, 5, 0)
        bars = [
            Bar(start + timedelta(hours=i), 1.0 + i, 1.0 + i, 1.0 + i, 1.0 + i, 0.01)
            for i in range(6)
        ]
        fast_aligned, _ = htf_ema_aligned(bars, 1, 2, 3)
        # The first bar has no completed higher-timeframe bar yet.
        self.assertTrue(math.isnan(fast_aligned[0]))
        # Later bars must reference the EMA through the *previous* bar (no peek).
        full_fast = ema([bar.close for bar in bars], 2)
        self.assertAlmostEqual(fast_aligned[3], full_fast[2])

    def test_session_skips_configured_hours(self) -> None:
        config = Config()  # skip_hours defaults to (12,), window 08:00-17:00
        monday_10 = datetime(2026, 1, 5, 10)
        monday_12 = datetime(2026, 1, 5, 12)
        monday_07 = datetime(2026, 1, 5, 7)
        self.assertTrue(in_session(monday_10, config))
        self.assertFalse(in_session(monday_12, config))
        self.assertFalse(in_session(monday_07, config))

    def test_scale_out_books_tp1_and_tp2_partials(self) -> None:
        bars = _uptrend_bars(400)
        config = Config(
            ema_fast=5, ema_slow=20, donchian=10, stop_atr=2.0, tp1_r=1.0, reward_risk=2.0,
            entry_buffer_atr=0.0, candle_body_min=0.0, candle_wick_max=1.0,
            htf_factor=0, htf2_factor=0, vol_avg_len=0, skip_hours=(),
            session_start=0, session_end=0, friday_close=24,
            daily_profit_lock_pct=100.0, max_trades_day=99, max_losses_day=99,
        )
        result = run_backtest(
            bars, _TEST_META, config, initial_balance=100_000.0, spread_multiplier=1.0,
            split_fraction=0.7, max_spread_points=1e9, enforce_ftmo_guards=False,
        )
        reasons = {trade["reason"] for trade in result["trades"]}
        self.assertIn("tp1", reasons)
        self.assertIn("tp2", reasons)

    def test_pyramiding_opens_more_units_than_single_shot(self) -> None:
        bars = _uptrend_bars(400)
        base = dict(
            ema_fast=5, ema_slow=20, donchian=10, stop_atr=2.0, tp1_r=1.0, reward_risk=2.0,
            entry_buffer_atr=0.0, candle_body_min=0.0, candle_wick_max=1.0,
            htf_factor=0, htf2_factor=0, vol_avg_len=0, skip_hours=(),
            session_start=0, session_end=0, friday_close=24,
            daily_profit_lock_pct=100.0, max_trades_day=99, max_losses_day=99,
            pullback_atr=0.3,
        )
        run_kw = dict(
            initial_balance=100_000.0, spread_multiplier=1.0, split_fraction=0.7,
            max_spread_points=1e9, enforce_ftmo_guards=False,
        )
        with_pyramid = run_backtest(bars, _TEST_META, Config(**base, pyramid_enabled=True, max_units=3), **run_kw)
        no_pyramid = run_backtest(bars, _TEST_META, Config(**base, pyramid_enabled=False, max_units=1), **run_kw)

        def units(result):
            return len({(t["entry_time"], t["entry"]) for t in result["trades"]})

        self.assertGreater(units(with_pyramid), units(no_pyramid))

    def test_swap_reconciles_ledger_with_balance_and_costs_longs(self) -> None:
        bars = _uptrend_bars(400)
        config = Config(
            ema_fast=5, ema_slow=20, donchian=10, stop_atr=2.0, tp1_r=1.0, reward_risk=2.0,
            entry_buffer_atr=0.0, candle_body_min=0.0, candle_wick_max=1.0,
            htf_factor=0, htf2_factor=0, vol_avg_len=0, skip_hours=(),
            session_start=0, session_end=0, friday_close=24,
            daily_profit_lock_pct=100.0, max_trades_day=99, max_losses_day=99,
        )
        run_kw = dict(
            initial_balance=100_000.0, spread_multiplier=1.0, split_fraction=0.7,
            max_spread_points=1e9, enforce_ftmo_guards=False,
        )
        no_swap = run_backtest(bars, _TEST_META, config, **run_kw)
        meta = dict(_TEST_META)
        meta["swap"] = {"long_points": -50.0, "short_points": 10.0}
        with_swap = run_backtest(bars, meta, config, **run_kw)
        # Ledger must reconcile with the account for both runs.
        for result in (no_swap, with_swap):
            ledger = sum(t["pnl"] for t in result["trades"])
            self.assertAlmostEqual(
                ledger, result["ending_balance"] - 100_000.0, places=6
            )
        # Long-only uptrend with negative long swap: financing is a real cost.
        self.assertLess(
            with_swap["all"]["net_profit"], no_swap["all"]["net_profit"]
        )

    def test_daily_profit_lock_blocks_later_entries_that_day(self) -> None:
        # Once the day's closed profit reaches the lock, no further entries
        # are taken that day (the lock is latched, mirroring the EA; with the
        # break-even floor after TP1 a give-back below the threshold requires
        # a surviving pre-TP1 unit, so the latch and the per-bar check differ
        # only on multi-unit give-back days on real data).
        start = datetime(2024, 1, 1, 0)  # Monday
        closes = []
        price = 100.0
        for _ in range(30):            # establish the channel/EMAs
            price += 0.05
            closes.append(price)
        for _ in range(6):             # strong rally: entry + TP1/TP2 profits
            price += 1.2
            closes.append(price)
        for _ in range(6):             # sharp give-back: stops out, day pnl ~ flat
            price -= 1.4
            closes.append(price)
        for _ in range(8):             # fresh breakout, still the same day
            price += 1.3
            closes.append(price)
        bars = []
        prev = 100.0
        for i, close in enumerate(closes):
            bars.append(
                Bar(
                    start + timedelta(minutes=90 * i),  # 16 bars/day
                    prev,
                    max(prev, close) + 0.02,
                    min(prev, close) - 0.02,
                    close,
                    0.001,
                )
            )
            prev = close
        base = dict(
            ema_fast=3, ema_slow=8, donchian=5, stop_atr=1.5, tp1_r=0.5,
            # The runner must SURVIVE the lock (far TP2, no break-even, no
            # trail) so the crash can realize a loss that drags the day's pnl
            # back below the lock threshold while a later signal is available.
            reward_risk=5.0, break_even_r=1e9, trail_start_r=1e9,
            entry_buffer_atr=0.0, candle_body_min=0.0, candle_wick_max=1.0,
            htf_factor=0, htf2_factor=0, vol_avg_len=0, skip_hours=(),
            session_start=0, session_end=0, friday_close=24,
            max_trades_day=99, max_losses_day=99, pyramid_enabled=False,
        )
        run_kw = dict(
            initial_balance=100_000.0, spread_multiplier=1.0, split_fraction=0.7,
            max_spread_points=1e9, enforce_ftmo_guards=False,
        )
        locked = run_backtest(
            bars, _TEST_META, Config(**base, daily_profit_lock_pct=0.05), **run_kw
        )
        unlocked = run_backtest(
            bars, _TEST_META, Config(**base, daily_profit_lock_pct=10_000.0), **run_kw
        )
        def entries_by_day(result):
            days = {}
            for t in result["trades"]:
                days.setdefault(t["entry_time"][:10], set()).add(t["entry_time"])
            return {d: len(s) for d, s in days.items()}
        locked_days = entries_by_day(locked)
        unlocked_days = entries_by_day(unlocked)
        # The give-back day: the locked run books TP1 past the lock, the
        # runner stops out (day pnl falls back below the lock), and the fresh
        # breakout later the same day must still be refused (latched), while
        # the unlocked run takes it.
        giveback_day = "2024-01-03"
        self.assertEqual(locked_days.get(giveback_day), 1)
        self.assertGreater(unlocked_days.get(giveback_day, 0), 1)

    def test_profit_lock_suppresses_trend_change_flatten(self) -> None:
        # The EA gates the EMA-flip flatten behind accountSafe, which the
        # daily profit lock latches false for the rest of the day - so on a
        # locked day units ride their stops through a trend flip. Scenario
        # (from the parity verification): flat warmup -> rally whose TP1/TP2
        # bank far past the lock the same day (runner survives, BE stop well
        # below) -> same-day gentle decline flips the EMA stack without
        # touching the runner's stop.
        start = datetime(2024, 1, 1, 0)  # Monday, 16 bars/day at 90 min
        bars = []

        def add(open_, high, low, close):
            bars.append(
                Bar(start + timedelta(minutes=90 * len(bars)), open_, high, low, close, 0.001)
            )

        price = 100.0
        for i in range(30):  # flat warmup: no breakout, small ATR
            close = 100.04 if i % 2 == 0 else 100.00
            add(price, max(price, close) + 0.02, min(price, close) - 0.02, close)
            price = close
        for _ in range(5):   # rally; lows never dip below opens
            close = price + 1.2
            add(price, close + 0.02, price, close)
            price = close
        for _ in range(8):   # same-day decline: flips EMA3<EMA8 above BE stop
            close = price - 0.8
            add(price, price + 0.02, close - 0.02, close)
            price = close
        for _ in range(6):   # tail
            close = price - 0.02
            add(price, price + 0.02, close - 0.02, close)
            price = close

        base = dict(
            ema_fast=3, ema_slow=8, donchian=5, stop_atr=1.5,
            tp1_r=15.0, reward_risk=20.0, break_even_r=1e9, trail_start_r=1e9,
            entry_buffer_atr=0.0, candle_body_min=0.0, candle_wick_max=1.0,
            htf_factor=0, htf2_factor=0, vol_avg_len=0, skip_hours=(),
            session_start=0, session_end=0, friday_close=24,
            max_trades_day=99, max_losses_day=99, pyramid_enabled=False,
        )
        run_kw = dict(
            initial_balance=100_000.0, spread_multiplier=1.0, split_fraction=0.7,
            max_spread_points=1e9, enforce_ftmo_guards=False,
        )
        locked = run_backtest(
            bars, _TEST_META, Config(**base, daily_profit_lock_pct=0.05), **run_kw
        )
        unlocked = run_backtest(
            bars, _TEST_META, Config(**base, daily_profit_lock_pct=10_000.0), **run_kw
        )
        unlocked_tc = [t for t in unlocked["trades"] if t["reason"] == "trend_change"]
        self.assertTrue(unlocked_tc)  # the flip does fire when not locked
        flip_day = unlocked_tc[0]["exit_time"][:10]
        locked_tc_that_day = [
            t for t in locked["trades"]
            if t["reason"] == "trend_change" and t["exit_time"][:10] == flip_day
        ]
        self.assertEqual(locked_tc_that_day, [])  # locked day: no flip flatten

    def test_soft_floor_matches_ea_defaults(self) -> None:
        self.assertEqual(active_floor(100_000, 103_000, Config()), 99_000)

    def test_volume_is_floored_to_broker_step(self) -> None:
        self.assertAlmostEqual(floor_volume(1.237, 0.01, 50, 0.01), 1.23)
        self.assertEqual(floor_volume(0.009, 0.01, 50, 0.01), 0.0)


if __name__ == "__main__":
    unittest.main()
