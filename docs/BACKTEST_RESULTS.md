# EURUSD H1 screening result

## Verdict

**Corrected engine (2026-07-14).** After the adversarial audit
(`backtest/results/engine_audit_findings.json`), the screening engine was fixed
so it stops flattering the results and actually measures the EA as shipped (see
"Engine corrections" below). The honest EURUSD number is now **+1.28%** return
over the full sample (was a flattered +4.03%), with a **4.31%** maximum equity
drawdown and no detected rule breach.

The edge is marginal and does not clearly hold: in-sample expectancy is
**negative** (PF 0.89), the out-of-sample split is positive (PF 1.89), and the
full period is only slightly positive. The cross-symbol picture is worse
(GBPUSD −1.01%, USDJPY −5.46%, XAUUSD +2.47% but OOS negative). This is **not a
validated edge** — do not use it for a funded/challenge decision. The only
credible next step is MetaTrader 5 real-tick validation on the target broker's
feed with a hedging account.

## Engine corrections (2026-07-14)

The audit confirmed the earlier numbers were produced by a screen that
under-detected risk and did not match the EA. Fixed in `ftmo_quant_backtest.py`:

- **Entry-bar immunity removed** — entries are now processed first and exposed
  to their own bar's stop/TP and the intrabar FTMO guard.
- **ATR parity** — the screen now uses an SMA of True Range (matching MT5's
  `iATR`), not Wilder smoothing.
- **Gap fills** — stops fill at the worse of the stop and the bar open.
- **Breach-by-flatten flagged** — weekend/trend-flip flattens that realize a
  loss below the FTMO floor now set `official_rule_breach`.
- **Causal trailing** — the trail uses the last completed bar's ATR (index-1),
  matching the EA and removing intrabar look-ahead.
- **Equity-based sizing** — sizing and the projected-risk gate use live equity,
  like the EA's `ACCOUNT_EQUITY`.
- **Break-even within the TP1 bar** and a **true-R denominator** (a full stop
  now books −1R, not −0.91R); **pullback arming is session-gated** to match the
  EA.

Still unmodeled (documented limitations, not fixed): swap/financing, and minor
timing nuances (H4 staleness at boundary hours, per-day counter treatment of
adds). These remain reasons the screen is not tick parity.

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

## Account-level results (FTMO guards, corrected engine)

EURUSD (`backtest/data/derivM15/EURUSD.csv`, SHA-256 `2f308538…`):

| Path | Return | Max equity DD | Max daily loss | Rule breach |
| --- | ---: | ---: | ---: | --- |
| 1× spread | +1.28% | 4.31% | 0.79% | No |
| 2× spread | +0.41% | 4.21% | 0.77% | No |

Cross-symbol (FTMO-guarded, 1× / 2× return; see `docs/DATA.md` for commands):

| Symbol | 1× return | Max equity DD | 2× return | OOS split (1×) |
| --- | ---: | ---: | ---: | --- |
| EURUSD | +1.28% | 4.31% | +0.41% | +$2,362 (PF 1.89) |
| GBPUSD | −1.01% | 4.21% | −2.59% | +$723 (PF 1.22) |
| USDJPY | −5.46% | 6.45% | −3.37% | −$1,426 (PF 0.72) |
| XAUUSD | +2.47% | 4.00% | +3.45% | −$770 (PF 0.88) |

Only EURUSD is positive at both cost levels, and even there the in-sample half
loses money. The edge does not generalize.

## Booked profit by split

Rows count scale-out tranches (TP1, TP2, runner, and stop closes are separate
rows), so trade counts are higher than the number of trades a single-exit model
would report. Profit factor is tranche-level.

EURUSD, rows count scale-out tranches (TP1/TP2/runner/stop are separate rows),
so counts exceed round-turn trades and PF is tranche-level:

| Split | 1× net | 1× PF | 2× net | 2× PF |
| --- | ---: | ---: | ---: | ---: |
| In-sample | -$1,084.37 | 0.894 | -$1,366.37 | 0.869 |
| Out-of-sample | +$2,361.84 | 1.891 | +$1,780.17 | 1.604 |
| Full period | +$1,277.47 | 1.099 | +$413.80 | 1.031 |

In-sample is now negative; the full-period profit comes entirely from the
out-of-sample window. On one symbol and this small a sample that is
encouraging at best, not evidence of an edge.

## Reproduction

The datasets are bundled in the repo (`backtest/data/derivM15/`); see
`docs/DATA.md`. No external repo or Git LFS is needed.

```bash
python3 backtest/ftmo_quant_backtest.py \
  --data backtest/data/derivM15/EURUSD.csv \
  --broker-meta backtest/deriv_broker_meta.json \
  --symbol EURUSD \
  --fallback-spread-points 10 \
  --output backtest/results/EURUSD.json
```

The full configuration, yearly breakdown, and trade ledger are stored in
`backtest/results/EURUSD.json` (and `eurusd_h1.json`, identical).
