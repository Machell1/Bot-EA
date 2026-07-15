# Why this EA has no edge — full mechanism audit (2026-07-14)

A 14-agent adversarial audit (7 analysis dimensions, each independently
re-derived by a refuter with fresh seeds; every canonical run reproduced
trade-for-trade before any counterfactual). Analysis scripts and raw results
live in `backtest/audit/`. Data: the four bundled real-Deriv M15 sets
(~2.8–3y), corrected engine, canonical costs, 459 units total.

**One-line verdict: the EA trades a premise that barely exists on these
instruments, with a near-zero gross expectancy that costs push negative, a
filter stack that is in-sample dressing, and headline positives that are two
short-EUR windows plus gold path luck — statistically indistinguishable from
zero on a sample ~30–100× too small to prove the size of edge it would need.**

## 1. The premise (H1 breakout continuation) is dead where the EA needs it

Raw 20-bar Donchian breakout, spread-free, ~1,900 events/symbol:

| Symbol | Drift at +24 bars | Round-trip cost | Verdict |
| --- | --- | --- | --- |
| EURUSD | +0.09 ATR (CI −0.23…+0.43) | 0.11 ATR | zero; drift ≈ one cost |
| GBPUSD | +0.17 ATR (p=0.085) | 0.12 ATR | zero-ish; drift ≈ one cost |
| USDJPY | +0.74 (2023) → **−0.37 (2026, CI excludes 0)** | 0.16 ATR | premise decayed then INVERTED |
| XAUUSD | +0.53 ATR (p=0.001), +0.76 at 48 bars | 0.02 ATR | real — but long-side only, ~⅔ is gold-bull drift |

There is nothing on EURUSD/GBPUSD for any machinery to harvest; USDJPY is
trading a dead regime (hence −4.21%); gold's drift is mostly beta a long-only
filter captures without breakout logic.

## 2. The entry signal adds no deployable timing skill

Full-engine reruns with a seeded random signal matched to the real signal's
per-regime fire rate and side mix, identical costs/gates/exits (200 seeds/symbol,
verified with independent seed streams and a session-conditioned variant):

- **EURUSD** +1.06% ≈ 88–91st percentile of random — an above-average draw,
  not significant.
- **GBPUSD** −1.23% ≈ 97.5–99th percentile (count-matched control) — the one
  symbol with real timing skill, **which still loses money** (see §4).
- **USDJPY** ≈ 44–62nd percentile — pure noise; the rule also buys into yen
  reversals (11/96 entries within 24h of a >5 ATR adverse move).
- **XAUUSD** +1.73% ≈ 25–33rd percentile — significantly **worse than
  random**: 79–85% of random-entry runs on gold are positive (mean +3.9–5.5%).
  Gold's profit is regime drift that random timing harvests better.

Event-level, every symbol shows a real but tiny +4h burst (+0.22…+0.41 ATR)
that decays within a day on FX and inverts by +72h on EURUSD (pyramid adds
−1.96 ATR — adds are bought near trend exhaustion). XAUUSD alone shows
multi-day selection skill (+2.07 ATR at 72h) — which the exit stack fails to
monetize (70% of the position is booked by 2.2R).

## 3. The confluence stack is selection dressing

Attribution ladder (raw breakout → +buffer → +candle → +HTF → +vol), per symbol:

- The raw signal has **negative in-sample expectancy on all four symbols**
  (unit PF 0.67–0.95) — the filters had nothing to purify.
- The vol-regime guard vetoes **3 of 2,141** candidates (0.14%) — a filter in
  name only. The candle filter is noise-level and sign-inconsistent.
- Buffer + HTF do all the sample reduction (~4× fewer units) and on the
  development symbol added **+$12,533 in-sample vs +$1,182 OOS (10.6:1)** —
  the signature of in-sample tuning. The same filters flip sign elsewhere
  (buffer destroys gold's only OOS-positive cell; HTF craters USDJPY).
- **No rung on any symbol has a credible OOS edge** (best t = 1.39).

## 4. It is not a cost problem — gross expectancy is ~zero

Frictionless (no spread/commission/swap) reruns, per-unit gross expectancy:
EURUSD **+0.057R**, GBPUSD **−0.030R**, USDJPY **−0.199R** (negative before
any costs), XAUUSD **+0.002R**. All costs combined are only 0.03–0.07R/unit.

- EURUSD: break-even spread ≈ 19.5–20 pts vs 10 modeled — costs are not the
  binding problem; the problem is that all gross profit lives in one 28-unit
  OOS window (IS gross ≈ $0).
- GBPUSD: break-even spread 2.4 pts — not available anywhere; this is where
  the real timing skill of §2 dies.
- USDJPY: break-even spread is negative (−28.9 pts) — unfixable; swap is
  actually a +$1,275 carry credit and it still loses 6.1% frictionless.
- XAUUSD: positive net at 16-pt spread is path luck — the strategy is
  **negative at 0–8 pt spreads** on the same data.

A strategy with ~zero gross edge cannot be rescued by better fills or a
cheaper broker.

## 5. The positives are regime luck, and worse than trivial beta

Under identical costs and sizing, across all four symbols the EA nets
**−$2,654** while a two-line EMA50/200 long/flat cross nets **+$35,060**.

- EURUSD's profit is 100%+ short-sided (long −$2,742 / short +$3,804),
  concentrated in two EUR-down windows; **83% of the celebrated OOS PF 1.84
  comes from 5 short units**.
- XAUUSD longs net PF 1.04 (≈ zero) through a +107% gold bull; the EA lost
  money in 2025 while gold rose 65%, and captures 5.6% of what the trivial
  cross earns at ~9× worse return-per-drawdown.
- Top-3 units carry 170% (EURUSD) / 118% (XAUUSD) of full-period net;
  bootstrap p(net ≤ 0) = 0.33–0.93 on every symbol.

## 6. Statistically there is nothing there — and there never could have been

- Pooled expectancy: **−0.017R/unit** (n=459, day-clustered t = −0.27).
- The best cell in the whole grid (EURUSD OOS +0.259R) has clustered
  t = 1.26 (p = 0.22) — *below* the expected maximum of 8 pure-noise t-stats
  (1.46) from picking the best of 4 symbols × 2 splits, and less than half
  the ≥2.77–3.48 hurdle implied by the ≥9 documented design iterations × 4
  symbols × repeated re-screens.
- The IS-negative/OOS-positive "paradox" on EURUSD is a 7–11% base-rate event
  under random re-splits, and XAUUSD shows the mirror-image flip in the same
  results grid.
- Detecting a true +0.05R edge at this R-volatility needs **~3,000–5,000
  units**; the strategy produces ~36/year/symbol. That is 84–139 years of
  trading. The sample can never validate the edge size this design could
  plausibly have.

## 7. Exit-engine correction (supersedes the monitor study's leak claim)

The earlier finding that the H1 exits "leak ~16R vs random exits" (M5 monitor
event study) and this audit's initial ~75R figure **do not survive null
calibration**: running the identical engine + statistic on permuted,
zero-information data produces a *larger* apparent "leak" (+0.189R/unit vs
+0.163 measured) and lands at ≤1st percentile of random-exit portfolios in
79% of null runs. ~44% of the gap is pure fill-model asymmetry (stop-family
tranches fill intra-bar; the benchmark fills at bar closes) and the rest is
the hindsight-bounded exit window. Against the calibrated null the actual
exits are at-or-better than signal-free on EURUSD/GBPUSD/XAUUSD and only
suggestively worse on USDJPY (p≈0.40 family-wise). The BE-at-TP1 ablation
(+25R full-sample, GBPUSD flips positive) is in-sample-driven (+4.1R OOS,
negative on EUR/GBP OOS) — not a shippable fix. **There is no pot of exit
money; the failure is entry/premise/cost, which no exit engineering can
rescue.**

## What would have to be true for this EA to work

1. A venue where GBPUSD trades at ≤2.4 pts all-in (does not exist), or
2. a return of the 2023 USDJPY breakout regime (unknowable, currently
   inverted), or
3. gold exposure — which a one-line long filter captures better with 9×
   better drawdown efficiency.

None of these is the EA. The honest conclusion after three engine-correction
passes, ~40 verified audit findings, and this 7-dimension study is that the
design occupies the well-documented dead zone of retail H1 breakout systems
on major FX: no pre-cost drift to harvest, filters that fit the past, and
profits that are indistinguishable from the regime they happened to ride.
