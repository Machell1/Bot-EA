#!/usr/bin/env python3
"""Event study for the post-fill lower-timeframe monitor.

The H1 screening backtest is bar-close driven and has no M5 data, so the
monitor (``backtest/lower_tf_monitor.py``) cannot be exercised inside it. This
harness replays every H1 unit from an existing screening ledger on real M5
bars and calls ``assess()`` at each completed M5 bar, recording the OK /
WARNING / ACTION timeline per unit. It then reports:

1. Lead time: for units that ended in a losing stop, whether WARNING/ACTION
   fired before the M5 bar that touched the stop, and by how many M5 bars.
2. Precision: of ACTION bars, the fraction followed within the ~10-minute
   horizon by (a) a stop touch and (b) a breach of the monitor's own projected
   adverse band - each compared against the base rate over all monitored bars,
   because an alert that fires as often as chance is not a signal.
3. Counterfactuals: "on first ACTION move the stop to break-even" (or flatten
   when price is already beyond break-even) and "on first ACTION flatten
   everything", replayed on the M5 path, compared with the ledger baseline in
   gross R-multiples per unit.

Approximations (deliberate, all disclosed in the output):

- current_stop is the unit's initial 2.5x SMA-ATR stop, recomputed from the
  bundled M15 data with the engine's own functions, and moved to break-even
  once the M5 path first touches TP1 (+1R). The screening engine's post-TP2
  ATR trail is NOT modelled, so late-trade stops here sit wider than the
  engine's; assessments after TP2 use an approximate stop.
- M5 highs/lows are bid candles; the ask side is approximated as bid + a
  constant spread (matching the screening run's fallback spread).
- Ledger tranche exits are H1-bar-stamped. When a counterfactual M5 exit and a
  ledger exit fall in the same hour, the counterfactual (adverse) exit wins,
  mirroring the engine's stop-first pessimism.
- Counterfactual P&L is gross R (no commission/swap): tranche weights are the
  config fractions (TP1 0.4, TP2 0.3, remainder 0.3).

Dependency-free; standard library only.
"""

from __future__ import annotations

import argparse
import csv
import json
import statistics
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from backtest.ftmo_quant_backtest import Config, aggregate_h1, atr_sma_of_tr, load_m15
from backtest.lower_tf_monitor import MonitorConfig, TFSeries, assess


@dataclass
class M5Bar:
    time: datetime
    open: float
    high: float
    low: float
    close: float


@dataclass
class Unit:
    entry_time: datetime
    side: int
    entry: float
    initial_stop: float
    tp1_price: float
    tranches: list[dict]          # ledger rows, ordered by exit_time
    weights: list[float]
    oos: bool

    @property
    def initial_risk(self) -> float:
        return abs(self.entry - self.initial_stop)

    @property
    def final_exit_time(self) -> datetime:
        return max(datetime.fromisoformat(t["exit_time"]) for t in self.tranches)

    def baseline_r(self) -> float:
        return sum(
            w * self.side * (t["exit"] - self.entry) / self.initial_risk
            for w, t in zip(self.weights, self.tranches)
        )


def load_m5(path: Path) -> list[M5Bar]:
    bars = []
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            bars.append(
                M5Bar(
                    datetime.fromisoformat(row["time"]),
                    float(row["open"]),
                    float(row["high"]),
                    float(row["low"]),
                    float(row["close"]),
                )
            )
    return bars


def build_units(ledger: dict, h1_bars, atr, spread: float, config: Config) -> list[Unit]:
    """Group ledger tranches into units and recompute each unit's initial stop
    with the engine's own entry conventions (long entry=ask, stop off bid)."""
    index_by_time = {bar.time: i for i, bar in enumerate(h1_bars)}
    groups: dict[tuple, list[dict]] = {}
    for trade in ledger["measured"]["trades"]:
        groups.setdefault((trade["entry_time"], trade["entry"], trade["side"]), []).append(trade)

    units = []
    for (entry_time, entry, side), tranches in groups.items():
        when = datetime.fromisoformat(entry_time)
        i = index_by_time.get(when)
        if i is None or i < 1:
            continue
        bid = h1_bars[i].open
        ask = bid + spread
        dist = atr[i - 1] * config.stop_atr
        stop = bid - dist if side > 0 else ask + dist
        risk = abs(entry - stop)
        tp1 = entry + side * risk * config.tp1_r
        tranches = sorted(tranches, key=lambda t: t["exit_time"])
        reasons = [t["reason"] for t in tranches]
        weights = []
        residual = 1.0
        for reason in reasons:
            if reason == "tp1":
                weights.append(config.tp1_fraction)
                residual -= config.tp1_fraction
            elif reason == "tp2":
                weights.append(config.tp2_fraction)
                residual -= config.tp2_fraction
            else:
                weights.append(0.0)  # placeholder, gets the residual
        finals = [k for k, w in enumerate(weights) if w == 0.0]
        for k in finals:
            weights[k] = residual / len(finals)
        units.append(Unit(when, side, entry, stop, tp1, tranches, weights, tranches[0]["oos"]))
    return sorted(units, key=lambda u: u.entry_time)


def study_unit(
    unit: Unit,
    m5: list[M5Bar],
    m5_index: dict[datetime, int],
    m15_closes: list[float],
    m15_highs: list[float],
    m15_lows: list[float],
    m15_times: list[datetime],
    spread: float,
    mon_cfg: MonitorConfig,
    window: int,
) -> dict | None:
    start = None
    for probe in range(12):
        start = m5_index.get(unit.entry_time + timedelta(minutes=5 * probe))
        if start is not None:
            break
    if start is None or start < window:
        return None
    # Position death bound: the replay must not assess a closed position. The
    # engine executes weekend/trend-flip flattens at the H1 bar OPEN; price-
    # triggered exits (stop/tp/trail) happen inside their stamped hour.
    final_reason = unit.tranches[-1]["reason"]
    if final_reason in ("weekend", "trend_change", "end_of_data"):
        end_time = unit.final_exit_time
    else:
        end_time = unit.final_exit_time + timedelta(hours=1)

    # M15 pointer: number of COMPLETED M15 bars strictly before a given time.
    import bisect

    timeline = []
    stop = unit.initial_stop
    tp1_done = False
    stop_touch_bar = None
    first_warning = None
    first_action = None
    actions = []

    i = start
    while i < len(m5) and m5[i].time < end_time:
        bar = m5[i]
        # Order inside the bar mirrors the engine: stop first, then TP1/BE.
        # A stop touch kills the position; the touch bar itself is NOT
        # assessed (its close is post-mortem).
        touched = (bar.low <= stop) if unit.side > 0 else (bar.high + spread >= stop)
        if touched:
            stop_touch_bar = i
            break
        if not tp1_done:
            hit = (bar.high >= unit.tp1_price) if unit.side > 0 else (bar.low + spread <= unit.tp1_price)
            if hit:
                tp1_done = True
                stop = unit.entry  # engine moves the original order to break-even
        bar_close_time = bar.time + timedelta(minutes=5)
        n15 = bisect.bisect_right(m15_times, bar_close_time - timedelta(minutes=15))
        if n15 >= window:
            m5_win = TFSeries(
                [b.high for b in m5[i - window + 1 : i + 1]],
                [b.low for b in m5[i - window + 1 : i + 1]],
                [b.close for b in m5[i - window + 1 : i + 1]],
            )
            m15_win = TFSeries(
                m15_highs[n15 - window : n15],
                m15_lows[n15 - window : n15],
                m15_closes[n15 - window : n15],
            )
            result = assess(unit.side, stop, m5_win, m15_win, mon_cfg)
            timeline.append(
                {
                    "i": i,
                    "time": bar.time.isoformat(sep=" "),
                    "status": result.status,
                    "touch_prob": result.touch_probability,
                    "projected_adverse": result.projected_adverse,
                    "close": bar.close,
                    "stop": stop,
                    "tp1_done": tp1_done,
                }
            )
            if result.status == "warning" and first_warning is None:
                first_warning = len(timeline) - 1
            if result.status == "action":
                if first_action is None:
                    first_action = len(timeline) - 1
                actions.append(len(timeline) - 1)
        i += 1

    return {
        "timeline": timeline,
        "stop_touch_bar": stop_touch_bar,
        "first_warning": first_warning,
        "first_action": first_action,
        "actions": actions,
    }


def forward_extreme(m5: list[M5Bar], i: int, side: int, spread: float, steps: int) -> float:
    """Adverse extreme over the next ``steps`` M5 bars after bar i."""
    window = m5[i + 1 : i + 1 + steps]
    if not window:
        return m5[i].close
    if side > 0:
        return min(b.low for b in window)
    return max(b.high + spread for b in window)


def counterfactual_r(
    unit: Unit,
    m5: list[M5Bar],
    trigger_i: int | None,
    mode: str,
    spread: float,
) -> float:
    """Gross R of the unit if, at the close of M5 bar ``trigger_i``, we either
    flatten everything (mode='flatten') or move the stop to break-even
    (mode='be'; flattens instead when price is already through break-even).
    Tranches whose ledger exit hour precedes the trigger keep their ledger
    exit. mode='baseline' or trigger None returns the ledger R."""
    if trigger_i is None or mode == "baseline":
        return unit.baseline_r()

    trigger_time = m5[trigger_i].time
    trigger_close = m5[trigger_i].close
    risk = unit.initial_risk

    if mode == "flatten":
        cf_time, cf_price = trigger_time, trigger_close
    else:  # 'be'
        beyond = trigger_close > unit.entry if unit.side > 0 else trigger_close < unit.entry
        if not beyond:
            cf_time, cf_price = trigger_time, trigger_close
        else:
            cf_time = cf_price = None
            j = trigger_i + 1
            horizon = unit.final_exit_time + timedelta(hours=1)
            while j < len(m5) and m5[j].time <= horizon:
                hit = (
                    m5[j].low <= unit.entry
                    if unit.side > 0
                    else m5[j].high + spread >= unit.entry
                )
                if hit:
                    cf_time, cf_price = m5[j].time, unit.entry
                    break
                j += 1
            if cf_time is None:
                return unit.baseline_r()  # BE never revisited: ledger path stands

    total = 0.0
    for w, t in zip(unit.weights, unit.tranches):
        ledger_exit_hour = datetime.fromisoformat(t["exit_time"])
        # Ledger exits are H1-stamped; an exit in hour H happened in
        # [H, H+1h). Counterfactual (adverse) exit wins ties, stop-first style.
        if ledger_exit_hour + timedelta(hours=1) <= cf_time:
            exit_price = t["exit"]
        else:
            exit_price = cf_price
        total += w * unit.side * (exit_price - unit.entry) / risk
    return total


def max_drawdown(series: list[float]) -> float:
    peak = 0.0
    dd = 0.0
    total = 0.0
    for value in series:
        total += value
        peak = max(peak, total)
        dd = max(dd, peak - total)
    return dd


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--m15", type=Path, required=True)
    parser.add_argument("--m5", type=Path, required=True)
    parser.add_argument("--spread-price", type=float, required=True,
                        help="constant spread in price units (e.g. 0.16 for XAUUSD at 16 points)")
    parser.add_argument("--window", type=int, default=60,
                        help="trailing bars per timeframe fed to assess()")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    config = Config()
    mon_cfg = MonitorConfig()
    horizon = mon_cfg.horizon_minutes // mon_cfg.m5_minutes

    ledger = json.loads(args.ledger.read_text(encoding="utf-8"))
    h1 = aggregate_h1(load_m15(args.m15, args.spread_price))
    atr = atr_sma_of_tr(h1, config.atr_period)
    m5 = load_m5(args.m5)
    m5_index = {bar.time: i for i, bar in enumerate(m5)}

    m15_all = load_m15(args.m15, args.spread_price)
    m15_times = [bar.time for bar in m15_all]
    m15_highs = [bar.high for bar in m15_all]
    m15_lows = [bar.low for bar in m15_all]
    m15_closes = [bar.close for bar in m15_all]

    units = [
        unit
        for unit in build_units(ledger, h1, atr, args.spread_price, config)
        if unit.entry_time >= m5[0].time + timedelta(hours=6)
        and unit.final_exit_time <= m5[-1].time
    ]

    per_unit = []
    for unit in units:
        record = study_unit(
            unit, m5, m5_index, m15_closes, m15_highs, m15_lows, m15_times,
            args.spread_price, mon_cfg, args.window,
        )
        if record is None:
            continue
        timeline = record["timeline"]
        statuses = [t["status"] for t in timeline]
        baseline = unit.baseline_r()
        first_action_i = (
            timeline[record["first_action"]]["i"] if record["first_action"] is not None else None
        )
        per_unit.append(
            {
                "unit": {
                    "entry_time": unit.entry_time.isoformat(sep=" "),
                    "side": unit.side,
                    "entry": unit.entry,
                    "initial_stop": unit.initial_stop,
                    "oos": unit.oos,
                    "reasons": [t["reason"] for t in unit.tranches],
                    "ledger_pnl": sum(t["pnl"] for t in unit.tranches),
                },
                "n_bars": len(timeline),
                "n_ok": statuses.count("ok"),
                "n_warning": statuses.count("warning"),
                "n_action": statuses.count("action"),
                "first_warning": record["first_warning"],
                "first_action": record["first_action"],
                "stop_touch_bar": record["stop_touch_bar"],
                "baseline_r": baseline,
                "cf_be_r": counterfactual_r(unit, m5, first_action_i, "be", args.spread_price),
                "cf_flatten_r": counterfactual_r(unit, m5, first_action_i, "flatten", args.spread_price),
                "_timeline": timeline,
                "_record": record,
            }
        )

    # ---- Metric 1: lead time on losing stop units -------------------------
    lead = {"warning": [], "action": []}
    losing_stop_units = 0
    alerted_losing = {"warning": 0, "action": 0}
    for row in per_unit:
        is_losing_stop = row["unit"]["ledger_pnl"] < 0 and "stop" in row["unit"]["reasons"]
        if not is_losing_stop or row["stop_touch_bar"] is None:
            continue
        losing_stop_units += 1
        for kind, first in (("warning", row["first_warning"]), ("action", row["first_action"])):
            if first is None:
                continue
            alert_i = row["_timeline"][first]["i"]
            if alert_i < row["stop_touch_bar"]:
                alerted_losing[kind] += 1
                lead[kind].append(row["stop_touch_bar"] - alert_i)

    # ---- Metric 2: ACTION precision vs base rate --------------------------
    # All timeline bars are LIVE (the replay terminates at position death), so
    # the precision comparison cannot be contaminated by post-mortem bars.
    # Skill is additionally tested by matching on the monitor's own MC touch
    # probability: if ACTION adds no lift within touch_prob bins, the alert is
    # a stop-proximity detector, not incremental signal.
    action_stop, action_band, action_band_close, action_n = 0, 0, 0, 0
    base_stop, base_band, base_band_close, base_n = 0, 0, 0, 0
    bins = [(0.0, 0.05), (0.05, 0.15), (0.15, 0.25), (0.25, 0.5), (0.5, 1.01)]
    matched = {b: {"action": [0, 0], "other": [0, 0]} for b in bins}
    action_units = [r for r in per_unit if r["n_action"] > 0]
    recovered_action_units = [
        r for r in action_units if r["unit"]["ledger_pnl"] > 0
    ]
    for row in per_unit:
        side = row["unit"]["side"]
        for t in row["_timeline"]:
            i = t["i"]
            ext = forward_extreme(m5, i, side, 0.0 if side > 0 else args.spread_price, horizon)
            closes = [b.close for b in m5[i + 1 : i + 1 + horizon]]
            ext_close = (min(closes) if side > 0 else max(closes)) if closes else m5[i].close
            hit_stop = ext <= t["stop"] if side > 0 else ext >= t["stop"]
            hit_band = ext <= t["projected_adverse"] if side > 0 else ext >= t["projected_adverse"]
            hit_band_close = (
                ext_close <= t["projected_adverse"] if side > 0 else ext_close >= t["projected_adverse"]
            )
            base_n += 1
            base_stop += hit_stop
            base_band += hit_band
            base_band_close += hit_band_close
            for b in bins:
                if b[0] <= t["touch_prob"] < b[1]:
                    cell = matched[b]["action" if t["status"] == "action" else "other"]
                    cell[0] += 1
                    cell[1] += hit_stop
                    break
            if t["status"] == "action":
                action_n += 1
                action_stop += hit_stop
                action_band += hit_band
                action_band_close += hit_band_close

    # ---- Controls: does acting on ACTION beat feasible signal-free rules? --
    # (a) Signal-free feasible policy: flatten EVERY unit at a uniformly random
    #     live bar (no cohort knowledge, executable in real time). If this
    #     beats act-on-ACTION, the monitor's "improvement" over baseline is
    #     exposure reduction, not signal.
    # (b) Information-matched timing control: within action units only, a
    #     random live bar AT/AFTER the first ACTION. This isolates whether
    #     flattening exactly at the alert beats waiting a random while after
    #     it (pure timing skill given the same information).
    import random as _random

    unit_by_key = {u.entry_time.isoformat(sep=" "): u for u in units}
    control_all_sums = []
    control_matched_sums = []
    for seed in range(500):
        rng = _random.Random(1_000_000 + seed)
        total_all = 0.0
        total_matched = 0.0
        for row in per_unit:
            unit = unit_by_key[row["unit"]["entry_time"]]
            if row["_timeline"]:
                pick = rng.choice(row["_timeline"])["i"]
                total_all += counterfactual_r(unit, m5, pick, "flatten", args.spread_price)
            else:
                total_all += row["baseline_r"]
            if row["n_action"] > 0:
                tail = row["_timeline"][row["first_action"]:]
                pick = rng.choice(tail)["i"]
                total_matched += counterfactual_r(unit, m5, pick, "flatten", args.spread_price)
            else:
                total_matched += row["baseline_r"]
        control_all_sums.append(total_all)
        control_matched_sums.append(total_matched)
    control_all_sums.sort()
    control_matched_sums.sort()

    # ---- Timing percentile: ACTION's exit vs every available exit ---------
    # Per ACTION unit, flatten at EVERY monitored bar and find the percentile
    # of the actual first-ACTION flatten inside that unit's own outcome
    # distribution. Neutral timing = 0.5; adverse selection < 0.5. Immune to
    # trade-length asymmetry between units.
    percentiles = []
    for row in per_unit:
        if row["n_action"] == 0:
            continue
        unit = unit_by_key[row["unit"]["entry_time"]]
        outcomes = [
            counterfactual_r(unit, m5, t["i"], "flatten", args.spread_price)
            for t in row["_timeline"]
        ]
        actual = row["cf_flatten_r"]
        below = sum(1 for o in outcomes if o < actual - 1e-12)
        ties = sum(1 for o in outcomes if abs(o - actual) <= 1e-12)
        percentiles.append((below + 0.5 * ties) / len(outcomes))

    # ---- Metric 3: counterfactuals ----------------------------------------
    def bucket(rows):
        base = [r["baseline_r"] for r in rows]
        be = [r["cf_be_r"] for r in rows]
        flat = [r["cf_flatten_r"] for r in rows]
        return {
            "units": len(rows),
            "baseline": {"sum_r": sum(base), "mean_r": statistics.mean(base) if base else None,
                         "max_dd_r": max_drawdown(base)},
            "act_break_even": {"sum_r": sum(be), "mean_r": statistics.mean(be) if be else None,
                               "max_dd_r": max_drawdown(be)},
            "act_flatten": {"sum_r": sum(flat), "mean_r": statistics.mean(flat) if flat else None,
                            "max_dd_r": max_drawdown(flat)},
        }

    summary = {
        "population": {
            "units_studied": len(per_unit),
            "oos_units": sum(1 for r in per_unit if r["unit"]["oos"]),
            "monitored_m5_bars": base_n,
            "m5_range": [m5[0].time.isoformat(sep=" "), m5[-1].time.isoformat(sep=" ")],
        },
        "status_distribution": {
            "ok": sum(r["n_ok"] for r in per_unit),
            "warning": sum(r["n_warning"] for r in per_unit),
            "action": sum(r["n_action"] for r in per_unit),
        },
        "lead_time_losing_stops": {
            "losing_stop_units": losing_stop_units,
            "with_warning_before_stop": alerted_losing["warning"],
            "with_action_before_stop": alerted_losing["action"],
            "warning_lead_bars": {
                "median": statistics.median(lead["warning"]) if lead["warning"] else None,
                "mean": statistics.mean(lead["warning"]) if lead["warning"] else None,
            },
            "action_lead_bars": {
                "median": statistics.median(lead["action"]) if lead["action"] else None,
                "mean": statistics.mean(lead["action"]) if lead["action"] else None,
            },
        },
        "action_precision_10m": {
            "action_bars": action_n,
            "p_stop_touch_next10m_given_action": action_stop / action_n if action_n else None,
            "p_stop_touch_next10m_base_rate": base_stop / base_n if base_n else None,
            "p_band_breach_next10m_given_action": action_band / action_n if action_n else None,
            "p_band_breach_next10m_base_rate": base_band / base_n if base_n else None,
            "p_band_breach_close_basis_given_action": action_band_close / action_n if action_n else None,
            "p_band_breach_close_basis_base_rate": base_band_close / base_n if base_n else None,
            "units_with_any_action": len(action_units),
            "action_units_that_still_won": len(recovered_action_units),
            "stop_touch_matched_on_touch_prob": {
                f"{lo:.2f}-{hi:.2f}": {
                    "action": {
                        "n": matched[(lo, hi)]["action"][0],
                        "p_touch": (
                            matched[(lo, hi)]["action"][1] / matched[(lo, hi)]["action"][0]
                            if matched[(lo, hi)]["action"][0]
                            else None
                        ),
                    },
                    "non_action": {
                        "n": matched[(lo, hi)]["other"][0],
                        "p_touch": (
                            matched[(lo, hi)]["other"][1] / matched[(lo, hi)]["other"][0]
                            if matched[(lo, hi)]["other"][0]
                            else None
                        ),
                    },
                }
                for lo, hi in bins
            },
        },
        "counterfactual_gross_r": {
            "all": bucket(per_unit),
            "in_sample": bucket([r for r in per_unit if not r["unit"]["oos"]]),
            "out_of_sample": bucket([r for r in per_unit if r["unit"]["oos"]]),
        },
        "per_unit_deltas_flatten": {
            "changed_units": sum(
                1 for r in per_unit if abs(r["cf_flatten_r"] - r["baseline_r"]) > 1e-9
            ),
            "improved": sum(
                1 for r in per_unit if r["cf_flatten_r"] - r["baseline_r"] > 1e-9
            ),
            "hurt": sum(
                1 for r in per_unit if r["cf_flatten_r"] - r["baseline_r"] < -1e-9
            ),
            "median_delta_r_action_units": statistics.median(
                [r["cf_flatten_r"] - r["baseline_r"] for r in action_units]
            ) if action_units else None,
            "mean_delta_r": statistics.mean(
                [r["cf_flatten_r"] - r["baseline_r"] for r in per_unit]
            ),
            "stdev_delta_r": statistics.stdev(
                [r["cf_flatten_r"] - r["baseline_r"] for r in per_unit]
            ),
        },
        "action_timing_percentile_within_unit": {
            "units": len(percentiles),
            "mean": statistics.mean(percentiles) if percentiles else None,
            "median": statistics.median(percentiles) if percentiles else None,
            "note": "0.5 = neutral timing; <0.5 = ACTION exits at worse prices than the unit's average available exit",
        },
        "control_signal_free_flatten_all_units": {
            "seeds": len(control_all_sums),
            "control_sum_r_mean": statistics.mean(control_all_sums),
            "control_sum_r_p5": control_all_sums[int(0.05 * len(control_all_sums))],
            "control_sum_r_p95": control_all_sums[int(0.95 * len(control_all_sums))],
            "actual_flatten_sum_r": sum(r["cf_flatten_r"] for r in per_unit),
            "actual_percentile_in_control": sum(
                1 for c in control_all_sums if c < sum(r["cf_flatten_r"] for r in per_unit)
            ) / len(control_all_sums),
            "note": "feasible signal-free policy: flatten every unit at a random live bar",
        },
        "control_random_after_first_action": {
            "seeds": len(control_matched_sums),
            "control_sum_r_mean": statistics.mean(control_matched_sums),
            "control_sum_r_p5": control_matched_sums[int(0.05 * len(control_matched_sums))],
            "control_sum_r_p95": control_matched_sums[int(0.95 * len(control_matched_sums))],
            "actual_flatten_sum_r": sum(r["cf_flatten_r"] for r in per_unit),
            "actual_percentile_in_control": sum(
                1 for c in control_matched_sums if c < sum(r["cf_flatten_r"] for r in per_unit)
            ) / len(control_matched_sums),
            "note": "same information set: random live bar at/after the first ACTION in action units",
        },
        "approximations": [
            "current_stop = initial 2.5x SMA-ATR stop, to break-even after M5 first touches TP1; engine's post-TP2 ATR trail not modelled",
            "constant spread (bid candles + fixed spread on the ask side); no ticks inside M5 bars",
            "counterfactual P&L is gross R with config tranche weights (0.4/0.3/residual); commission, swap and volume flooring excluded",
            "ledger exits are H1-stamped; counterfactual exits win same-hour ties (stop-first pessimism)",
            "EMA/RSI computed on trailing windows seeded at the window start, not full history",
        ],
    }

    for row in per_unit:
        row.pop("_timeline")
        row.pop("_record")
    output = {"summary": summary, "units": per_unit}
    rendered = json.dumps(output, indent=2, sort_keys=True)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
