# FTMO Quant EA

An MT5 Expert Advisor built around a testable trend-following hypothesis and
conservative FTMO 2-Step account controls.

> **Research status: not a validated edge.** After an adversarial audit
> (`backtest/results/engine_audit_findings.json`) the screening engine was
> corrected to stop under-detecting risk and to match the EA as shipped. On the
> corrected engine EURUSD returns only **+1.28%** (was a flattered +4.03%) with
> a losing in-sample half, and the strategy is negative on GBPUSD and USDJPY and
> in-sample-only on XAUUSD. Do not use it for a funded/challenge decision; the
> only credible next step is MetaTrader 5 real-tick validation. See
> [the full result](docs/BACKTEST_RESULTS.md) and [the data brief](docs/DATA.md).

The strategy buys or sells a 20-bar Donchian breakout only when a stack of
confluences agrees:

- The 50 EMA is on the correct side of the 200 EMA and moving in the breakout
  direction.
- The breakout close clears the channel by 0.5 ATR (filters marginal pokes).
- The breakout candle is a decisive body that closes in the breakout direction
  with only a small rejection wick (wick/body confirmation that discards doji
  and long-opposing-wick bars).
- Higher timeframes agree: the H4 **and** Daily 50/200 EMA stacks both point in
  the breakout direction.
- Volatility is not in a blow-off news spike (ATR within its normal range).

Entries are restricted to the 08:00–17:00 server (London/New York) window and
skip the midday lull. Stops are volatility-scaled with ATR (2.5×) and position
size is calculated from the stop distance. Each unit **scales out at two
targets**: TP1 (1.0R) books the first partial and moves the original order to
break-even, TP2 (2.2R) books a second partial, and the remaining runner trails
by ATR. Once TP1 is booked the EA **buys the pullback**—a retrace of 0.5 ATR
followed by a momentum resume adds another unit in the trend direction (up to
three concurrent units)—and rides the trend, adding on each pullback until the
EMA stack flips and every unit is flattened, or the units are stopped out. An
optional market-structure filter (higher highs/higher lows) is available but off
by default. This is a classic, explainable source of potential trend premium—not
a claim of guaranteed profits.

## Safety defaults

- 0.35% account-equity risk per trade (per unit)
- Up to three concurrent trend units, added only on pullbacks after TP1
- At most two new trend entries and two losing exits per trading day
- The pre-trade gate bounds *aggregate* open risk, so pyramiding can never risk
  more than the FTMO floor allows
- New entries stop at 4% daily or 8% total drawdown
- Account positions are flattened at the soft protection floor
- A 15% cost/slippage reserve is included in the pre-trade risk gate
- Trading stops after 1% closed profit in a day
- Spread, session, and weekend filters
- Adds are made only to winning trends (each with its own stop); no martingale,
  averaging down, or recovery sizing

The official FTMO 2-Step defaults encoded by the EA are 5% Maximum Daily Loss
and 10% Maximum Loss. Confirm the current objectives for your specific account
before use.

## Install

1. Copy `experts/FTMOQuantEA.mq5` into the terminal's
   `MQL5/Experts/FTMOQuantEA/` directory.
2. Open the file in MetaEditor and compile it.
3. In MT5, attach the EA to one liquid symbol chart. Start with EURUSD H1.
   Use a **hedging** account: the pullback pyramiding holds several units on the
   same symbol at once, which a netting account cannot represent.
4. Set `InpChallengeInitialBalance` to the challenge's original balance.
5. Convert FTMO's 00:00 CE(S)T reset to the broker server clock and set
   `InpDailyResetHourServer` and `InpDailyResetMinuteServer`.
   Recheck this mapping at European and broker daylight-saving transitions.
6. Use a dedicated challenge account. The default emergency action closes
   every account position because FTMO limits apply account-wide.

If `InpChallengeInitialBalance` is zero, the EA stores the balance seen on its
first launch. That is convenient for a fresh account but unsafe if the EA is
first attached after trading has begun. State is scoped by account login,
broker server, and `InpStateId`. Assign a new ID (up to eight characters) to
every challenge so an old baseline cannot be reused.

## Validate before use

Backtest with real ticks, variable spreads, commissions, and the FTMO account's
leverage. Do not optimize all parameters on one date range. Use the procedure
and acceptance gates in [docs/VALIDATION.md](docs/VALIDATION.md).

Run the dependency-free risk formula tests with:

```bash
python3 -m unittest discover -s tests -v
```

The Python tests are an executable specification for the monetary guard. The
EA itself must still be compiled and tested in MetaTrader 5.

## Reproduce the screening backtest

`backtest/ftmo_quant_backtest.py` mirrors the default EA on complete H1 bars
aggregated from M15 candles. It uses pessimistic stop-first intrabar ordering
and requires no third-party Python packages.

```bash
python3 backtest/ftmo_quant_backtest.py \
  --data /path/to/Scalp-trader-/backtest/data/derivM15_diverse/EURUSD.csv \
  --broker-meta /path/to/Scalp-trader-/backtest/h1_universe_broker_meta.json \
  --symbol EURUSD \
  --fallback-spread-points 10 \
  --output backtest/results/eurusd_h1.json
```

The source repository stores candles in Git LFS. Run `git lfs pull` there
before the backtest. The command tests both measured and doubled spread with a
chronological 70/30 split and records the input SHA-256 hash.

**Update 2026-07-13:** the historical data is now bundled directly in this
repo — real Deriv M15 candles for EURUSD, GBPUSD, USDJPY, and XAUUSD (~2.8–3
years each) at `backtest/data/derivM15/`, plus the symbol specs at
`backtest/deriv_broker_meta.json`. No LFS or external clone needed. See
[docs/DATA.md](docs/DATA.md) for provenance, exact run commands per symbol,
the four-symbol results summary, and the engine audit findings
(`backtest/results/engine_audit_findings.json`) that anyone modifying the
engine or EA should read first.

**Update 2026-07-14:** a post-fill lower-timeframe monitor (M5/M15 RSI
divergence, EMA alignment, variance/stop-touch projection with an `act` mode)
was prototyped and event-tested on 70 real XAUUSD units over real M5 data. It
failed its controls — the alert reduces to stop proximity, and acting on it
timed exits worse than random — so it was **removed** from the EA and the
codebase. The evidence is kept: [docs/MONITOR_EVENT_STUDY.md](docs/MONITOR_EVENT_STUDY.md),
`backtest/results/XAUUSD_monitor_event_study.json`, and the harness-audit
ledger `backtest/results/monitor_event_study_audit.json`. The module, EA code,
and study harness are recoverable from git history (commit `0d9495d`).

## Important limitations

No EA can guarantee an FTMO Challenge pass. Market regimes change, fills can
gap beyond stops, terminal/VPS outages can prevent the EA from enforcing a
limit, and a backtest can overfit. FTMO evaluates account equity including
open P/L, swaps, and commissions. Keep FTMO's own risk dashboard and hard
broker-side stops as independent controls.

The Python backtest is a screening model, not MT5 execution parity. M15-derived
H1 bars cannot establish tick order, the EURUSD source has no historical
spread column, and source timestamps are assumed to match EA server time.
