# FTMO Quant EA

An MT5 Expert Advisor built around a testable trend-following hypothesis and
conservative FTMO 2-Step account controls.

The strategy buys or sells a 20-bar Donchian breakout only when the 50 EMA is
on the correct side of the 200 EMA and is moving in the breakout direction.
Stops are volatility-scaled with ATR, position size is calculated from the
stop distance, and winners use break-even plus ATR trailing logic. This is a
classic, explainable source of potential trend premium—not a claim of
guaranteed profits.

## Safety defaults

- 0.35% account-equity risk per trade
- One open position on the chart symbol
- At most two entries and two losing exits per trading day
- New entries stop at 4% daily or 8% total drawdown
- Account positions are flattened at the soft protection floor
- A 15% cost/slippage reserve is included in the pre-trade risk gate
- Trading stops after 1% closed profit in a day
- Spread, session, and weekend filters
- No grid, martingale, averaging down, or recovery sizing

The official FTMO 2-Step defaults encoded by the EA are 5% Maximum Daily Loss
and 10% Maximum Loss. Confirm the current objectives for your specific account
before use.

## Install

1. Copy `experts/FTMOQuantEA.mq5` into the terminal's
   `MQL5/Experts/FTMOQuantEA/` directory.
2. Open the file in MetaEditor and compile it.
3. In MT5, attach the EA to one liquid symbol chart. Start with EURUSD H1.
4. Set `InpChallengeInitialBalance` to the challenge's original balance.
5. Convert FTMO's 00:00 CE(S)T reset to the broker server clock and set
   `InpDailyResetHourServer` and `InpDailyResetMinuteServer`.
6. Use a dedicated challenge account. The default emergency action closes
   every account position because FTMO limits apply account-wide.

If `InpChallengeInitialBalance` is zero, the EA stores the balance seen on its
first launch. That is convenient for a fresh account but unsafe if the EA is
first attached after trading has begun. State is persisted in MT5 terminal
global variables with the `FTMOQ_<login>_` prefix.

## Validate before use

Backtest with real ticks, variable spreads, commissions, and the FTMO account's
leverage. Do not optimize all parameters on one date range. Use the procedure
and acceptance gates in [docs/VALIDATION.md](docs/VALIDATION.md).

Run the dependency-free risk formula tests with:

```bash
python -m unittest discover -s tests -v
```

The Python tests are an executable specification for the monetary guard. The
EA itself must still be compiled and tested in MetaTrader 5.

## Important limitations

No EA can guarantee an FTMO Challenge pass. Market regimes change, fills can
gap beyond stops, terminal/VPS outages can prevent the EA from enforcing a
limit, and a backtest can overfit. FTMO evaluates account equity including
open P/L, swaps, and commissions. Keep FTMO's own risk dashboard and hard
broker-side stops as independent controls.
