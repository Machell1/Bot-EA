# Validation protocol

The EA contains a plausible trend-following edge hypothesis. It does not yet
contain evidence that the edge survives the costs and data for a particular
broker. Use this protocol before a challenge.

## 1. Compile and smoke test

Compile `experts/FTMOQuantEA.mq5` in a current MetaEditor 5 with zero errors and
warnings. In Strategy Tester, confirm that:

- every entry has a stop loss and take profit;
- lot size decreases when ATR stop distance increases;
- the EA does not open more than two trades per configured trading day;
- it moves a stop only toward the market, never away;
- Friday positions close at the configured server hour;
- removing and reattaching the EA does not reset the initial balance or the
  current daily balance baseline.

Run `python -m unittest discover -s tests -v` to check representative floor and
projected-risk calculations.

## 2. Historical test design

Recommended first market: EURUSD H1. Use MT5's "Every tick based on real ticks"
mode with the challenge account's commission, leverage, and realistic variable
spread. Include at least five years and several market regimes.

Split data chronologically:

1. development segment: choose only broad parameter ranges;
2. validation segment: select one stable parameter region, not its best point;
3. untouched out-of-sample segment: make the final go/no-go decision.

Keep the EMA pair fixed initially. If optimizing, limit the search to Donchian
lookback, ATR stop multiple, and reward/risk ratio. Reject isolated parameter
peaks; neighboring values should have similar results.

## 3. Minimum evidence gates

These are screening gates, not guarantees:

- at least 300 completed out-of-sample trades;
- out-of-sample profit factor above 1.15 after all costs;
- positive expectancy and positive return in most rolling 12-month windows;
- no FTMO rule breach in any historical test;
- maximum simulated daily equity drawdown below 4%;
- maximum simulated total drawdown below 8%;
- no single trade contributes more than 10% of total net profit;
- results remain positive with spread and slippage doubled;
- results remain acceptable when each trade is randomly worsened by 0.1R;
- walk-forward parameter choices remain stable.

If these gates fail, do not increase risk to force the profit target. A failed
robustness test means the strategy or market selection needs revision.

## 4. FTMO-specific scenario tests

Use a fresh tester run for each case:

| Scenario | Expected result |
| --- | --- |
| Initial balance 100,000; day starts 100,000 | Soft daily floor is 96,000 |
| Day starts after balance rises to 103,000 | Soft daily floor is 99,000 |
| Day starts after balance falls to 97,000 | Active soft daily floor is 93,000 |
| Day starts after balance falls to 96,000 | Both soft floors are 92,000 |
| Equity 96,300 and proposed risk 300 | Entry rejected after 15% reserve |
| Closed daily P/L reaches 1,000 | No more entries that day |
| Two losing strategy exits occur | No more entries that day |

Also test an open trade across the reset boundary. The daily floor is based on
the account balance at reset, while equity still includes floating P/L.

## 5. Forward validation

Run on an FTMO Free Trial or equivalent demo with the same symbol, server-time
settings, and VPS intended for the challenge. Compare actual fills against the
tester and verify the daily reset around daylight-saving transitions.

Only proceed if the live behavior matches the tested behavior. Begin with the
default 0.35% risk; lowering risk is the correct response to uncertainty.
