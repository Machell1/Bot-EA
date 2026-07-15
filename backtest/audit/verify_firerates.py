#!/usr/bin/env python3
"""VERIFICATION (independent re-derivation): real signal per-regime fire rates,
unconditional vs conditioned on in-session decision bars.

The engine only consults signal() on in-session flat bars, so the fair random
control should match the fire rate where the signal is actually sampled. This
script measures both rates to quantify how much the prior analyst's
unconditional-rate control under-fires in session.
"""
import json
import math
import sys
from pathlib import Path

REPO = Path(r"C:/Users/Sanique Richards/Documents/Homework Heroes/Pokemon/Bot-EA")
sys.path.insert(0, str(REPO))

import backtest.ftmo_quant_backtest as eng

FALLBACK_PTS = {"EURUSD": 10.0, "GBPUSD": 15.0, "USDJPY": 28.0, "XAUUSD": 16.0}


def main() -> None:
    out = {}
    for symbol, fb in FALLBACK_PTS.items():
        broker = json.loads((REPO / "backtest/deriv_broker_meta.json").read_text(encoding="utf-8"))
        meta = broker["symbols"][symbol]
        bars = eng.aggregate_h1(
            eng.load_m15(REPO / f"backtest/data/derivM15/{symbol}.csv", fb * float(meta["point"]))
        )
        cfg = eng.Config()
        closes = [b.close for b in bars]
        fast = eng.ema(closes, cfg.ema_fast)
        slow = eng.ema(closes, cfg.ema_slow)
        atr = eng.atr_sma_of_tr(bars, cfg.atr_period)
        warmup = max(cfg.ema_slow + 10, cfg.donchian + 2)

        c = dict(
            bull=0, bear=0, lf=0, sf=0,
            bull_sess=0, bear_sess=0, lf_sess=0, sf_sess=0,
        )
        for i in range(warmup, len(bars)):
            if not math.isfinite(atr[i - 1]):
                continue
            s = eng.signal(i, bars, fast, slow, cfg, atr)
            sess = eng.in_session(bars[i].time, cfg)
            if fast[i - 1] > slow[i - 1]:
                c["bull"] += 1
                c["lf"] += s == 1
                if sess:
                    c["bull_sess"] += 1
                    c["lf_sess"] += s == 1
            elif fast[i - 1] < slow[i - 1]:
                c["bear"] += 1
                c["sf"] += s == -1
                if sess:
                    c["bear_sess"] += 1
                    c["sf_sess"] += s == -1
        out[symbol] = {
            "bull_bars": c["bull"], "bear_bars": c["bear"],
            "long_fires": c["lf"], "short_fires": c["sf"],
            "p_long_uncond": c["lf"] / c["bull"] if c["bull"] else 0.0,
            "p_short_uncond": c["sf"] / c["bear"] if c["bear"] else 0.0,
            "bull_bars_insess": c["bull_sess"], "bear_bars_insess": c["bear_sess"],
            "long_fires_insess": c["lf_sess"], "short_fires_insess": c["sf_sess"],
            "p_long_insess": c["lf_sess"] / c["bull_sess"] if c["bull_sess"] else 0.0,
            "p_short_insess": c["sf_sess"] / c["bear_sess"] if c["bear_sess"] else 0.0,
        }
        r = out[symbol]
        print(
            f"{symbol}: uncond p_long {r['p_long_uncond']:.5f} ({r['long_fires']}/{r['bull_bars']}) "
            f"p_short {r['p_short_uncond']:.5f} ({r['short_fires']}/{r['bear_bars']}) | "
            f"in-sess p_long {r['p_long_insess']:.5f} ({r['long_fires_insess']}/{r['bull_bars_insess']}) "
            f"p_short {r['p_short_insess']:.5f} ({r['short_fires_insess']}/{r['bear_bars_insess']})"
        )
    (REPO / "backtest/audit/verify_firerates_results.json").write_text(
        json.dumps(out, indent=2), encoding="utf-8"
    )


if __name__ == "__main__":
    main()
