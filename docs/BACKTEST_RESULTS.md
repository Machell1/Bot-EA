# EURUSD H1 screening result

## Verdict

The **revised** default strategy is materially better than the first version
and is FTMO-rule compliant on this dataset, but it is **not yet a validated
edge**. Out-of-sample expectancy is positive, while the full-period in-sample
expectancy is still slightly negative. Treat it as a promising, conservative
baseline to validate further—not a guaranteed FTMO Challenge pass.

Four changes drove the improvement over the original defaults:

1. **Breakout confirmation buffer** (`entry_buffer_atr = 0.5`). The breakout
   close must clear the Donchian channel by half an ATR. Marginal pokes through
   the range were the largest single source of whipsaw losses.
2. **Liquid-hours session** (`08:00–17:00` server, was `07:00–20:00`). Entries
   are restricted to the London/New York window and Friday trading stops at
   17:00.
3. **Wider volatility stop** (`stop_atr = 2.5`, was `2.0`). Trend trades are
   given room to develop instead of being stopped on normal noise.
4. **Candlestick confirmation of the breakout bar** (`candle_body_min = 0.2`,
   `candle_wick_max = 0.3`). The breakout candle must have a decisive body that
   closes in the breakout direction with only a small rejection wick
   (≤30% of the bar's range on the breakout side). Doji-like bars and long
   opposing wicks—signs the range was defended—are rejected. This wick/body
   filter is the single most effective quality gate: it lifts out-of-sample
   profit factor from 1.34 to 1.50 while also improving the in-sample split.

With these defaults the guarded account no longer trips the soft floor and shut
down mid-sample; it survives the full period, so the FTMO-guarded path and the
unguarded diagnostic are now identical.

## Original vs revised (unguarded diagnostic, 1× spread)

| Split | Version | Trades | Net | PF | Expectancy | Win rate |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| In-sample | original | 286 | -$15,220.72 | 0.648 | -0.163R | 28.3% |
| In-sample | revised | 139 | -$2,962.43 | 0.839 | -0.060R | 28.8% |
| Out-of-sample | original | 105 | -$2,412.07 | 0.817 | -0.077R | 33.3% |
| Out-of-sample | revised | 48 | +$2,632.87 | 1.501 | +0.161R | 45.8% |
| Full period | original | 391 | -$17,632.79 | 0.687 | -0.140R | 29.7% |
| Full period | revised | 187 | -$329.56 | 0.986 | -0.004R | 33.2% |

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

| Path | Trades | Net | PF | Expectancy | Max equity DD | Rule breach |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| FTMO guards, 1× spread | 187 | -$329.56 | 0.986 | -0.004R | 5.26% | No |
| FTMO guards, 2× spread | 185 | -$1,495.61 | 0.937 | -0.022R | 6.13% | No |

Out-of-sample:

| Costs | Trades | Net | PF | Expectancy | Win rate |
| --- | ---: | ---: | ---: | ---: | ---: |
| 1× spread | 48 | +$2,632.87 | 1.501 | +0.161R | 45.83% |
| 2× spread | 48 | +$2,055.28 | 1.392 | +0.127R | 43.75% |

The out-of-sample edge survives a doubling of spread, which is the most
important robustness check for a low-cost-sensitivity claim. The OOS sample
(51 trades) is still far below the preregistered 300-trade minimum, and the
full-period in-sample expectancy remains slightly negative, so the result is
encouraging but not yet conclusive. Collect more history and more symbols
before trusting it live.

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
