# Bundled historical data (for backtesting this EA)

`backtest/data/derivM15/` contains real Deriv MT5 M15 candles, exported from a
live Deriv (SVG) MT5 terminal on 2026-06-30. Columns:
`time,open,high,low,close,volume` (the extra `volume` column is ignored by the
backtest loader). No historical spread column exists, so runs use
`--fallback-spread-points`. Timestamps are Deriv MT5 **server time** and are
treated as EA server time by the session filter — flag, not fact, for any other
broker.

| File | SHA-256 | M15 rows | Range |
| --- | --- | ---: | --- |
| EURUSD.csv | `af36bd1495021d0cd364653f5b396710cec67bf7db38d6aeb5c4ed9ee259b747` | 70,000 | 2023-09-05 → 2026-06-30 |
| GBPUSD.csv | `33e95f524ae3c37601bdfbd77f919231ad4fbc52aa2b1e8167e6375ee7d9dfae` | 70,000 | 2023-09-04 → 2026-06-30 |
| USDJPY.csv | `4a984d48c1a830c09e07471019de1c20ef2f8846adb3a1e7d29981f3a8c05d89` | 70,000 | 2023-09-04 → 2026-06-30 |
| XAUUSD.csv | `d241deb31291cec034e43bea348fc9d1638e641c602772387fe4476c174058bf` | 70,000 | 2023-07-12 → 2026-06-30 |

EURUSD.csv is byte-identical to the file the published result in
[BACKTEST_RESULTS.md](BACKTEST_RESULTS.md) used (same SHA-256), so that run is
directly reproducible from this repo alone.

Integrity (verified 2026-07-13): zero duplicate timestamps, strictly
increasing, all rows quarter-aligned (:00/:15/:30/:45). XAUUSD has 171
incomplete hours at the metal's daily trading break; `aggregate_h1` drops them
by design.

## Broker meta

`backtest/deriv_broker_meta.json` holds the symbol specs. EURUSD matches the
published run; GBPUSD/USDJPY/XAUUSD come from a live Deriv terminal
`symbol_info` snapshot. Assumptions baked in:

- Commission $2.50 per side per lot on **all** symbols (FTMO-style snapshot;
  conservative if metals are commission-free on the target broker).
- USDJPY `trade_tick_value_loss` = 0.6197 USD is a point-in-time JPY→USD
  conversion, held constant across the sample.
- Fallback spreads used for the 2026-07-13 runs: EURUSD 10 pts, GBPUSD 15 pts,
  USDJPY 28 pts, XAUUSD 16 pts (live Deriv snapshot values).

## Reproduce the 2026-07-13 runs

```bash
python3 backtest/ftmo_quant_backtest.py --data backtest/data/derivM15/EURUSD.csv \
  --broker-meta backtest/deriv_broker_meta.json --symbol EURUSD \
  --fallback-spread-points 10 --output backtest/results/EURUSD.json

python3 backtest/ftmo_quant_backtest.py --data backtest/data/derivM15/GBPUSD.csv \
  --broker-meta backtest/deriv_broker_meta.json --symbol GBPUSD \
  --fallback-spread-points 15 --max-spread-points 60 \
  --output backtest/results/GBPUSD_relaxedgate.json

python3 backtest/ftmo_quant_backtest.py --data backtest/data/derivM15/USDJPY.csv \
  --broker-meta backtest/deriv_broker_meta.json --symbol USDJPY \
  --fallback-spread-points 28 --max-spread-points 60 \
  --output backtest/results/USDJPY.json

python3 backtest/ftmo_quant_backtest.py --data backtest/data/derivM15/XAUUSD.csv \
  --broker-meta backtest/deriv_broker_meta.json --symbol XAUUSD \
  --fallback-spread-points 16 --max-spread-points 40 \
  --output backtest/results/XAUUSD.json
```

Gate note: at GBPUSD's 15-pt spread the doubled-spread path (30 pts) exceeds
the default 25-pt `--max-spread-points` entry gate and books **zero trades**;
the relaxed-gate run above is the real cost stress. Run the unit tests with
`python3 -m unittest discover -s tests -v` (20 tests).

## Results summary (2026-07-13, $100k, FTMO-guarded)

| Symbol | 1× return / maxDD | 2× return | OOS split (1×) | Verdict |
| --- | --- | --- | --- | --- |
| EURUSD | +4.03% / 2.86% | +1.42% | +$2,295 (PF 1.74) | reproduces the docs; small sample (105 units) |
| GBPUSD | +1.73% / 3.44% | −0.24% | +$1,546 (PF 1.77) | marginal; dies at 2× spread |
| USDJPY | −0.84% / 3.63% | −2.66% | +$1,333 (PF 1.38) | no edge; Deriv spread too heavy |
| XAUUSD | +8.19% / 3.45% | +6.94% | **−$770 (PF 0.88)** | in-sample only; OOS negative |

No `official_rule_breach` on any run; `trend_change` exits never fired on any
symbol. Cross-symbol conclusion: the edge does **not** generalize — treat the
EURUSD result as screening-positive on one small sample, not a validated edge.

## Read before trusting the screen

`backtest/results/engine_audit_findings.json` — a 25-agent adversarial audit
(2026-07-13) of `ftmo_quant_backtest.py`: 20 confirmed findings, 0 refuted.
Highlights for anyone (including Cursor) working on this engine or EA:

1. **Entry-bar immunity** — stops/TPs/FTMO-guard are evaluated before entries,
   so a new unit is never tested against its own entry bar; FTMO compliance
   metrics (`max_daily_loss_pct`, `official_rule_breach`) are one-sidedly
   understated. Fix first.
2. **ATR parity break** — Python uses Wilder ATR; the EA's `iATR` is an SMA of
   true range. ATR drives entries, stops, TPs, trailing, and pyramid arming,
   so the screen is not measuring the EA as shipped.
3. **Pyramid arming** uses every bar in Python but only session-gated bars in
   the EA; backtested adds include entries the live EA can never take.
4. Gaps fill at the exact stop price; breaches realized by weekend/trend-flip
   flattens are never flagged; break-even-after-TP1 is not enforced within the
   TP1 bar; swap is unmodeled.

The MQL5 EA still requires MT5 real-tick validation on the target broker's
feed (hedging account) before any live or challenge use.
