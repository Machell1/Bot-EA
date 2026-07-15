# Lower-timeframe monitor — event-study results (XAUUSD)

> **Status: the monitor was REMOVED from the EA and the codebase on
> 2026-07-14 based on this study.** `backtest/lower_tf_monitor.py`, its tests,
> the EA's monitor block, and the study harness
> (`backtest/monitor_event_study.py`) are recoverable from git history at
> commit `0d9495d`. This document and the results JSONs are kept as the
> record of why.
>
> **Correction (2026-07-14, why-no-edge audit):** this study's secondary
> claim that the H1 exits "leak ~16R vs random exits" did NOT survive null
> calibration — the identical random-exit benchmark shows a comparable or
> larger "leak" on permuted, zero-information data (fill-model asymmetry +
> hindsight-bounded exit windows). See `docs/WHY_NO_EDGE.md` §7. The monitor
> verdicts in this document (act-mode kill, alert = stop proximity) are
> unaffected — they rest on the monitor's own controls, not the leak figure.
> Disregard the "redirect effort to the H1 exit engine" recommendation.

**Verdict: do not ship `act` mode; the alert itself is a stop-proximity
detector, not incremental signal.** On 70 real H1 units replayed over real M5
data, acting on ACTION beats the do-nothing baseline in this window, but a
feasible *signal-free* random exit policy beats acting on ACTION in all 500
control seeds, and even given the same alert, flattening immediately at the
ACTION bar is worse than flattening at a random later moment in 97% of seeds
— the alert fires at local lows. The apparent improvement is exposure
reduction in a window where the H1 exits leak badly, not monitor skill.

*This document reflects the harness AFTER an 11-agent adversarial audit (7
confirmed findings, all fixed): the original run assessed positions after
death (56–58% of ACTION bars were post-mortem), which contaminated every
metric. Corrections strengthened the act-mode kill and overturned the earlier
"19× alert lift" reading.*

## Setup

- Harness: `backtest/monitor_event_study.py`
  (`python3 -m backtest.monitor_event_study --ledger backtest/results/XAUUSD.json
  --m15 backtest/data/derivM15/XAUUSD.csv --m5 backtest/data/derivM5/XAUUSD_M5.csv
  --spread-price 0.16 --output backtest/results/XAUUSD_monitor_event_study.json`)
- Ledger: corrected-engine XAUUSD screening run (`measured` path).
- M5 data: `backtest/data/derivM5/XAUUSD_M5.csv` — 100,000 real Deriv M5
  candles, 2025-02-07 → 2026-07-09, same feed/server time as the bundled M15
  (32,724/32,725 overlapping M15 candles reconstruct exactly).
- Population: 70 H1 units (24 in-sample, 46 out-of-sample vs the H1 run's
  70/30 split); 11,554 live monitored M5 bars; default `MonitorConfig`;
  60-bar trailing windows; `assess()` at every completed M5 bar **while the
  position is alive** (replay terminates at the M5 stop-touch bar or at the
  hour-open of weekend/trend-flip flattens).

Approximations: current_stop = initial 2.5×SMA-ATR stop (engine's own
functions), to break-even when M5 first touches TP1; the engine's post-TP2 ATR
trail is not modelled; constant 16-point spread, bid candles, no intrabar
ticks; counterfactual P&L is gross R (no commission/swap) with tranche weights
0.4/0.3/residual; ledger exits are H1-stamped, counterfactual exits win
same-hour ties; EMA/RSI window-seeded.

## 1. Alert density (live bars)

| status | bars | share |
| --- | ---: | ---: |
| OK | 4,631 | 40.1% |
| WARNING | 6,737 | **58.3%** |
| ACTION | 186 | 1.6% |

WARNING is on ~58% of all live bars — as an alert channel it is noise.
32/70 units fired ≥1 live ACTION; 12 of those 32 still ended profitable.

## 2. Lead time (28 losing-stop units)

- 28/28 had a WARNING before the stop touch — meaningless at 58% density.
- 18/28 had an ACTION before the stop touch; median lead 6.5 M5 bars
  (~33 min), mean 96 (skewed by a few very early alerts).

## 3. Is ACTION predictive, or just "price is near the stop"?

Unconditionally, ACTION looks strong: P(stop touch within 10 min | ACTION) =
21.5% vs a 0.73% base rate (~30×). But matched on the monitor's **own**
Monte-Carlo touch probability, the lift disappears where the data is dense:

| touch_prob bin | ACTION bars → P(touch) | non-ACTION bars → P(touch) |
| --- | --- | --- |
| 0.00–0.05 | 81 → 7.4% | 11,339 → 0.31% |
| 0.05–0.15 | 58 → 22.4% | 17 → 17.6% |
| 0.15–0.25 | 23 → 30.4% | 9 → 33.3% |
| 0.25–0.50 | 23 → 56.5% | 2 → 100% |

Wherever both cohorts have support, ACTION adds no lift beyond touch_prob —
i.e. beyond mechanical distance-to-stop. The lowest bin shows a residual lift
(7.4% vs 0.31%) but on only 6 touch events with within-bin confounding
(ACTION bars cluster at the top of the bin) — inconclusive, not a skill claim.
The divergence/EMA components also fail their own 2σ-band test: band-breach
precision is measurement-basis-dependent (extremes basis: 11.3% vs 9.1% base;
closes basis: 8.1% vs 5.1% — both small), and the ~9% extreme-basis base rate
vs the theoretical one-sided 2σ ≈ 2.3% shows the close-vol band understates
gold's true adverse range.

**A simple distance-to-stop threshold would deliver the same alert.**

## 4. Counterfactuals (gross R, 70 units)

| policy | sum R | OOS sum R | max DD (R, unit seq) |
| --- | ---: | ---: | ---: |
| baseline (ledger) | −2.11 | −2.24 | 11.07 |
| act on ACTION → BE / flatten if beyond | +3.02 | +2.58 | 7.53 |
| act on ACTION → flatten | +4.18 | +2.95 | 7.12 |
| **control: flatten EVERY unit at a random live bar** (signal-free, 500 seeds) | **+13.89 mean** (p5 +9.40) | — | — |
| **control: flatten at a random live bar AT/AFTER the first ACTION** (same information, 500 seeds) | **+6.16 mean** (p5 +4.54) | — | — |

- Act-on-ACTION beats baseline (+4.18R vs −2.11R; per-unit mean delta +0.09R,
  sd 0.32, t≈2.3 before clustering adjustment — several stop-outs are
  same-day clustered, so effective significance is lower).
- It sits at the **0th percentile** of the signal-free control: any random
  exposure-cutting rule captured 3× more than acting on the monitor.
- It sits at the **2.8th percentile** of the same-information control:
  waiting a random while *after* the alert beat acting at the alert in 97% of
  seeds.
- Within-trade timing percentile of the ACTION exit: mean 0.23, median 0.15 —
  bottom quartile of each trade's own available exits.

Reading: the studied window is one where the H1 exits gave back heavily
(28 full-stop losers that hovered near entry most of their life). In such a
window *any* early exit helps; the monitor's specific timing — selling exactly
when price presses the stop — is close to the worst feasible response. If
random exits beat the H1 baseline by ~16R gross, the fix belongs in the H1
exit engine (stop placement / give-back control), not in an M5 overlay.

## Recommendation

1. **Do not ship `act` mode** (BE-move or flatten on ACTION).
2. **Do not route WARNING anywhere** at current thresholds (58% duty cycle).
3. If a "stop is in play" ping is wanted, a distance-to-stop/ATR threshold
   reproduces ACTION's alert value without the RSI/EMA/MC machinery.
4. Redirect effort to the H1 exit engine, whose give-back this study
   quantifies (~+16R gross left on the table across 70 units vs random exits).
5. Caveats: one symbol (XAUUSD), 70 units, 17 months, strong-bull gold regime,
   long-only trade population in the window, gross R, bar-level M5
   approximation, single-seed MC inside the monitor. MT5 tick validation
   remains mandatory for any live claim.
