"""Executable specification for the account guard used by FTMOQuantEA.

These tests mirror the simple monetary formulas in the MQL5 implementation.
They do not test strategy profitability or replace MetaTrader tick backtests.
"""

import unittest


def daily_floor(day_start_balance: float, initial_balance: float, limit_pct: float) -> float:
    return day_start_balance - initial_balance * limit_pct / 100.0


def total_floor(initial_balance: float, limit_pct: float) -> float:
    return initial_balance * (1.0 - limit_pct / 100.0)


def active_floor(
    initial_balance: float,
    day_start_balance: float,
    official_daily_pct: float = 5.0,
    official_total_pct: float = 10.0,
    soft_daily_pct: float = 4.0,
    soft_total_pct: float = 8.0,
) -> float:
    return max(
        daily_floor(day_start_balance, initial_balance, official_daily_pct),
        total_floor(initial_balance, official_total_pct),
        daily_floor(day_start_balance, initial_balance, soft_daily_pct),
        total_floor(initial_balance, soft_total_pct),
    )


def projected_risk_allowed(equity: float, risk_money: float, floor: float) -> bool:
    # The EA reserves another 15% for slippage and transaction costs.
    return equity - risk_money * 1.15 > floor


def reconstruct_reset_balance(current_balance: float, deal_deltas: list[float]) -> float:
    return current_balance - sum(deal_deltas)


class FtmoRiskModelTests(unittest.TestCase):
    def test_official_floors_for_100k_account(self) -> None:
        self.assertEqual(daily_floor(100_000, 100_000, 5), 95_000)
        self.assertEqual(total_floor(100_000, 10), 90_000)

    def test_soft_daily_guard_is_used_at_challenge_start(self) -> None:
        self.assertEqual(active_floor(100_000, 100_000), 96_000)

    def test_daily_floor_rises_after_profitable_day(self) -> None:
        self.assertEqual(active_floor(100_000, 103_000), 99_000)

    def test_daily_guard_remains_stricter_after_losing_day(self) -> None:
        self.assertEqual(active_floor(100_000, 97_000), 93_000)

    def test_daily_and_total_soft_guards_meet_at_96k_day_start(self) -> None:
        self.assertEqual(active_floor(100_000, 96_000), 92_000)

    def test_projected_trade_rejected_near_floor(self) -> None:
        self.assertFalse(projected_risk_allowed(96_300, 300, 96_000))

    def test_projected_trade_allowed_with_headroom(self) -> None:
        self.assertTrue(projected_risk_allowed(100_000, 350, 96_000))

    def test_equity_exactly_on_floor_is_not_safe(self) -> None:
        self.assertFalse(projected_risk_allowed(96_000, 0, 96_000))

    def test_late_first_tick_reconstructs_midnight_balance(self) -> None:
        self.assertEqual(
            reconstruct_reset_balance(98_250, [-2_000, 300, -50]),
            100_000,
        )


if __name__ == "__main__":
    unittest.main()
