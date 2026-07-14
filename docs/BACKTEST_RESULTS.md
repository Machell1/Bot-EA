# EURUSD H1 screening result

## Verdict

The **revised** default strategy is FTMO-rule compliant on this dataset and
turns a net profit with a small equity drawdown. It scales out at two targets
and rides the trend by pyramiding on pullbacks. The guarded account returns
**+4.03%** over the sample with a **2.86%** maximum equity drawdown and no rule
breach; the edge stays positive and compliant under a doubling of spread.

The important caveat is unchanged: the confluence filters are deliberately
selective, so the sample is small (105 units / 185 scale-out tranches). Treat
this as a promising, conservative baseline to validate on more history and more
symbols—not a guaranteed FTMO Challenge pass. Pyramiding raises exposure, so the
MetaTrader 5 tick backtest matters even more before any live use.

Changes over the original defaults, in the order they were added:

1. **Breakout confirmation buffer** (`entry_buffer_atr = 0.5`).
2. **Liquid-hours session** (`08:00–17:00` server); Friday close at 17:00.
3. **Wider volatility stop** (`stop_atr = 2.5`).
4. **Candlestick + wick confirmation** (`candle_body_min = 0.2`,
   `candle_wick_max = 0.3`).
5. **Higher-timeframe confluence** (H4 and D1 EMA `50/200` must both agree).
6. **Session behavior** (`skip_hours = (12,)`, the midday lull).
7. **Volatility regime guard** (`vol_avg_len = 50`, `vol_ratio_max = 2.5`).
8. **Market structure** (`ms_channel_lookback`, default off).
9. **Two-target scale-out and trend pyramiding** (new):
   - **TP1** at `tp1_r = 1.0` books `tp1_fraction = 0.4` of the unit and trails
     the original order to break-even.
   - **TP2** at `reward_risk = 2.2` books `tp2_fraction = 0.3`; the remaining
     runner trails by ATR.
   - **Buy the pullback:** once a unit's TP1 is booked, a retrace of
     `pullback_atr = 0.5` ATR followed by a momentum resume adds a new unit in
     the trend direction, up to `max_units = 3`. Every add carries its own stop
     and passes the aggregate FTMO projected-risk gate, so this is
     adding-to-winners, not martingale or averaging down.
   - **Trend change:** when the fast/slow EMA stack flips against the open
     trend, all units are flattened. Otherwise units exit on their own stops.

Higher-timeframe values always come from the last *completed* HTF bar, so there
is no look-ahead.

## Data and method

- Source: `Machell1/Scalp-trader-`
- Source revision: `61f42c99811bd45c64954f2ba01d1ed21684f2d2`
- File: `backtest/data/derivM15_diverse/EURUSD.csv`
- File SHA-256:
  `af36bd1495021d0cd364653f5b396710cec67bf7db38d6aeb5c4ed9ee259b747`
- Range: 2023-09-05 through 2026-06-30
- Sample: 17,499 complete H1 bars aggregated from 69,999 M15 rows
- Split: first 70% development, final 30% out of sample from 2025-08-26
- Costs: 10-point fallback spread and FTMO snapshot commission of $2.50
  per lot per side; a second run doubled spread
- Execution: pessimistic stop-first H1 OHLC model

The source file has no historical spread column. The 10-point EURUSD spread
is an explicit assumption, and source timestamps are treated as EA server
time. These limitations prevent a tick-parity claim.

## Account-level results (FTMO guards)

| Path | Return | Max equity DD | Max daily loss | Rule breach |
| --- | ---: | ---: | ---: | --- |
| 1× spread | +4.03% | 2.86% | 1.16% | No |
| 2× spread | +1.42% | 3.24% | 1.13% | No |

## Booked profit by split

Rows count scale-out tranches (TP1, TP2, runner, and stop closes are separate
rows), so trade counts are higher than the number of trades a single-exit model
would report. Profit factor is tranche-level.

| Split | 1× net | 1× PF | 2× net | 2× PF |
| --- | ---: | ---: | ---: | ---: |
| In-sample | +$1,737.39 | 1.165 | -$131.28 | 0.988 |
| Out-of-sample | +$2,294.98 | 1.739 | +$1,554.33 | 1.459 |
| Full period | +$4,032.38 | 1.296 | +$1,423.05 | 1.098 |

The full period is profitable and compliant at both cost levels. At 2× spread
the in-sample split is roughly break-even—pyramiding books more tranches and so
pays more spread—while the out-of-sample split stays clearly positive. The
sample is small by design, so collect more history and more symbols before
trusting it live.

## Reproduction

```bash
git clone https://github.com/Machell1/Scalp-trader-.git
cd Scalp-trader-
git lfs pull
python3 backtest/verify_data.py

cd /path/to/Bot-EA
python3 backtest/ftmo_quant_backtest.py \
  --data /path/to/Scalp-trader-/backtest/data/derivM15_diverse/EURUSD.csv \
  --broker-meta /path/to/Scalp-trader-/backtest/h1_universe_broker_meta.json \
  --symbol EURUSD \
  --fallback-spread-points 10 \
  --output backtest/results/eurusd_h1.json
```

The full configuration, yearly breakdown, and trade ledger are stored in
`backtest/results/eurusd_h1.json`.
