# Post-audit plan (2026-07-14)

Response to `docs/WHY_NO_EDGE.md`. Ground rule: of the audit's six issue
groups, only the engineering layer was fixable and it is already fixed (all 20
engine findings + second-pass parity are shipped; the EA compiles 0/0 and the
screen is EA-faithful). The remaining five findings are statements about the
market, not the code:

| Audit finding | Fixable in this EA? | Why |
| --- | --- | --- |
| Breakout premise dead on FX, inverted on USDJPY | **No** | there is no pre-cost drift to harvest; no parameter reaches it |
| Entries ≈ random (except GBPUSD) | **No** | GBPUSD's real timing needs ≤2.4-pt all-in spread — no venue offers it |
| Filters are in-sample dressing | Only by deletion | raw signal is IS-negative on all 4 symbols; nothing to purify |
| Gross expectancy ≈ 0 before costs | **No** | cost engineering can't fix a zero-gross strategy |
| Positives = regime luck, < trivial beta | Only by *becoming* the baseline | a 2-line EMA cross beat it by ~$38k under identical costs |
| Statistically unprovable at ~36 trades/yr | **No** | validating +0.05R needs 3,000–5,000 units = 84–139 years |

Anything sold as a "fix plan" that keeps the H1-Donchian-FX design intact is
another lap of the loop that produced the 10.6:1 in-sample:OOS tuning
signature. The plan is therefore a decision between three branches.

---

## Branch A — Retire the EA (recommended, effort ≈ 0)

Freeze the repo as research evidence (it is now a fully documented case study
with a clean engine and reproducible audits). No further screens on this data:
the three bundled years are exhausted — every additional look raises the
multiple-testing hurdle that already sits at ~2× the best observed statistic.

**Do:** mark README status "retired / research archive". Nothing else.

## Branch B — Pivot to what the data actually paid (bounded experiment)

The only robust money in this dataset was trend beta: the audited two-line
EMA50/200 long/flat baseline (+$35,060 vs the EA's −$2,654, ~9× better
return-per-drawdown on gold). If the goal is "an EA that would have made
money," this is that EA. Honest framing: it is **regime-riding beta**, not a
proven edge — it must clear real gates before any funded use.

Pre-registered gates (write these down BEFORE coding; any failure = stop):

1. **Spec freeze first:** EMA50/200 long/flat (start XAUUSD + one index),
   2.5-ATR stop, 0.35% equity risk, FTMO guards carried over from this EA
   unchanged. No optimization of the two EMA periods — they are the
   pre-registration.
2. **New data, not this repo's 3 years:** ≥10 years per instrument (or as deep
   as the feed allows), walk-forward evaluation, costs at 1× and 2× measured
   spread + commission + swap.
3. **Statistical gates:** day-clustered t ≥ 2.5 on OOS walk-forward folds AND
   DSR ≥ 0.95 (this program's standard; note the 2026-06 Turtle study failed
   at DSR 0.91 on a similar design — the honest prior is ~coin-flip).
4. **Venue gates:** MT5 real-tick backtest, then FTMO free trial with the
   spread/swap check before any paid challenge. Flag: a long-only gold EA may
   trip FTMO's "one-sided betting" review — check their current rules first.
5. **Kill criteria:** any gate failed → archive, do not iterate parameters.

## Branch C — Attempt to salvage this EA anyway (not recommended)

The audit left exactly two real assets. If work continues, it is ONLY these,
under the discipline below, with the stated expectation that both likely die:

- **C1. GBPUSD entry timing** (97.5–99th pctile vs matched random — real).
  Salvage question: does the effect survive at horizons long enough to clear
  realistic costs? Test the timing signal at 24h–120h holds on NEW GBPUSD data
  (other venues/years); it currently needs ≤2.4-pt all-in spread, cheaper than
  futures after fees, so the honest prior is: dies.
- **C2. XAUUSD multi-day selection skill** (+2.07 ATR at 72h, unmonetized).
  Note the engine-level result: random entries through the same exits made
  MORE — so the value is exposure, not the breakout. Any redesign here
  converges to Branch B. Treat C2 as closed unless B fails narrowly.

Discipline if C is pursued: one pre-registered hypothesis per iteration with
its kill threshold written first; evaluation only on data never screened
before (new years/venues — never this repo's dataset again); no new filters;
no exit re-tuning (the exit-leak claim failed null calibration — see
WHY_NO_EDGE §7); final gate = MT5 real ticks. Hard budget cap agreed upfront.

## Explicitly out of scope for any branch

- Cost/venue engineering for EURUSD/USDJPY/XAUUSD (gross ≈ 0 or negative).
- Adding/re-weighting confluence filters (multiplicity debt, IS dressing).
- Exit-stack re-tuning driven by random-exit benchmarks (retracted method).
- Re-screening the bundled 2023–2026 data in any form.

## Recommendation

**A + B**: retire this EA as designed, and run Branch B as a bounded,
gate-driven experiment — it tests the one hypothesis this dataset actually
supports, with the failure criteria written before the first line of code.
