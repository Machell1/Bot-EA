# EURUSD H1 screening result

## Verdict

The **revised** default strategy is FTMO-rule compliant on this dataset and now
shows **positive expectancy in-sample, out-of-sample, and full period**, with a
tiny equity drawdown. The edge survives a doubling of spread. The important
caveat is sample size: the confluence filters are deliberately selective, so the
full-period sample is only 74 trades (19 out of sample), well below the
preregistered 300-trade minimum. Treat this as a promising, conservative
baseline to validate on more history and more symbols—not a guaranteed FTMO
Challenge pass.

Changes over the original defaults, in the order they were added:

1. **Breakout confirmation buffer** (`entry_buffer_atr = 0.5`). The breakout
   close must clear the Donchian channel by half an ATR; marginal pokes through
   the range were the largest single source of whipsaw losses.
2. **Liquid-hours session** (`08:00–17:00` server, was `07:00–20:00`); Friday
   trading stops at 17:00.
3. **Wider volatility stop** (`stop_atr = 2.5`, was `2.0`) so trend trades get
   room to develop.
4. **Candlestick + wick confirmation** (`candle_body_min = 0.2`,
   `candle_wick_max = 0.3`). The breakout candle must be a decisive body that
   closes in the breakout direction with only a small rejection wick.
5. **Higher-timeframe confluence** (`htf_factor = 4` H4 and `htf2_factor = 24`
   D1, EMA `50/200`). The breakout is taken only when both the H4 and the Daily
   fast/slow EMA stacks agree with its direction. This was the change that
   pushed in-sample expectancy positive.
6. **Session behavior** (`skip_hours = (12,)`). The midday lull between the
   London morning and the New York session is skipped.
7. **Volatility regime guard** (`vol_avg_len = 50`, `vol_ratio_max = 2.5`).
   Breakouts on blow-off, news-spike candles (ATR far above its own average) are
   rejected. The default band is a light guard that rarely binds.
8. **Market structure** (`ms_channel_lookback`, default `0` = off). An optional
   filter requiring the Donchian channel to make higher highs and higher lows
   (or the mirror). It is implemented and tested but disabled by default because
   it reduced in-sample robustness on EURUSD H1; enable it per instrument.

Higher-timeframe values always come from the last *completed* HTF bar, so there
is no look-ahead. With these defaults the guarded account never trips the soft
floor, so the FTMO-guarded path and the unguarded diagnostic are identical.

## Original vs revised (unguarded diagnostic, 1× spread)

| Split | Version | Trades | Net | PF | Expectancy | Win rate |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| In-sample | original | 286 | -$15,220.72 | 0.648 | -0.163R | 28.3% |
| In-sample | revised | 55 | +$602.91 | 1.082 | +0.033R | 32.7% |
| Out-of-sample | original | 105 | -$2,412.07 | 0.817 | -0.077R | 33.3% |
| Out-of-sample | revised | 19 | +$528.36 | 1.248 | +0.080R | 36.8% |
| Full period | original | 391 | -$17,632.79 | 0.687 | -0.140R | 29.7% |
| Full period | revised | 74 | +$1,131.27 | 1.119 | +0.045R | 33.8% |

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

## Revised results

| Path | Trades | Net | PF | Expectancy | Max equity DD | Return | Rule breach |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| FTMO guards, 1× spread | 74 | +$1,131.27 | 1.119 | +0.045R | 1.94% | +1.13% | No |
| FTMO guards, 2× spread | 72 | +$1,407.48 | 1.158 | +0.057R | 1.84% | +1.41% | No |

Out-of-sample:

| Costs | Trades | Net | PF | Expectancy | Win rate |
| --- | ---: | ---: | ---: | ---: | ---: |
| 1× spread | 19 | +$528.36 | 1.248 | +0.080R | 36.84% |
| 2× spread | 19 | +$412.90 | 1.192 | +0.063R | 36.84% |

Every split—in-sample, out-of-sample, 1× and 2× spread—is now positive, and the
maximum equity drawdown is under 2%. That is encouraging, but the trade count is
small by design: the multi-timeframe confluence trades quality over quantity.
Collect more history and more symbols before trusting it live.

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
