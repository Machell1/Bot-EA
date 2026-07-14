# EURUSD H1 screening result

## Verdict

The default strategy does **not** demonstrate an edge on this dataset and
should not be used for an FTMO Challenge.

The guarded account lost 7.75% before the 70%/30% split. At that point,
projected risk on another trade would cross the configured 8% total soft
floor, so the EA correctly stopped opening positions. An unguarded diagnostic
was therefore run to measure later strategy expectancy without pretending
that the FTMO account could continue.

## Data and method

- Source: `Machell1/Scalp-trader-`
- Source revision: `61f42c99811bd45c64954f2ba01d1ed21684f2d2`
- File: `backtest/data/derivM15_diverse/EURUSD.csv`
- File SHA-256:
  `af36bd1495021d0cd364653f5b396710cec67bf7db38d6aeb5c4ed9ee259b747`
- Manifest verification: 46 files OK, 0 missing, 0 mismatched
- Range: 2023-09-05 through 2026-06-30
- Sample: 17,499 complete H1 bars aggregated from 69,999 M15 rows
- Split: first 70% development, final 30% out of sample from 2025-08-26
- Costs: 10-point fallback spread and FTMO snapshot commission of $2.50
  per lot per side; a second run doubled spread
- Execution: pessimistic stop-first H1 OHLC model

The source file has no historical spread column. The 10-point EURUSD spread
is an explicit assumption, and source timestamps are treated as EA server
time. These limitations prevent a tick-parity claim.

## Results

| Path | Trades | Net | PF | Expectancy | Max equity DD | Rule breach |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| FTMO guards, 1× spread | 111 | -$7,746.84 | 0.575 | -0.206R | 8.53% | No |
| FTMO guards, 2× spread | 108 | -$7,850.15 | 0.565 | -0.215R | 8.74% | No |
| Diagnostic continuation, 1× | 391 | -$17,632.79 | 0.687 | -0.140R | 19.35% | Yes |
| Diagnostic continuation, 2× | 385 | -$19,075.90 | 0.664 | -0.156R | 21.01% | Yes |

Out-of-sample diagnostic:

| Costs | Trades | Net | PF | Expectancy | Win rate |
| --- | ---: | ---: | ---: | ---: | ---: |
| 1× spread | 105 | -$2,412.07 | 0.817 | -0.077R | 33.33% |
| 2× spread | 104 | -$2,975.32 | 0.785 | -0.098R | 33.65% |

The OOS sample also misses the preregistered minimum of 300 trades. More data
would improve precision, but both in-sample and OOS estimates are negative by
a wide enough margin that the current defaults fail the screening stage.

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
