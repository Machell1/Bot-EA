# AGENTS.md

## Cursor Cloud specific instructions

This repo is a pure Python 3 standard-library project (no third-party
dependencies, no lockfile) plus one MetaTrader 5 Expert Advisor. Python 3.12 is
preinstalled; the update script does not need to install anything.

### Components

- `experts/FTMOQuantEA.mq5` — the MT5 Expert Advisor. It can only be compiled
  and run inside MetaTrader 5 / MetaEditor (Windows). It **cannot be built or
  run in this Linux environment**; treat it as source-only here.
- `backtest/ftmo_quant_backtest.py` — standalone screening backtest, stdlib
  only. See `README.md` for the canonical invocation.
- `tests/` — dependency-free `unittest` suite.

### Test / lint / run (see `README.md` for canonical commands)

- Tests: `python3 -m unittest discover -s tests -v` — run from the repo root.
  `tests/test_backtest.py` imports `backtest.ftmo_quant_backtest`, so the repo
  root must be on `sys.path`; `unittest discover` from the root handles this
  (there is no `__init__.py`, and none is needed).
- Lint: no linter/formatter is configured. Use `python3 -m py_compile` on the
  Python files as a syntax check.

### Running the backtest without the external dataset

The backtest requires an M15 candle CSV and a broker-meta JSON that live in the
external Git LFS repo `Machell1/Scalp-trader-` (not vendored here). If that data
is unavailable, generate a synthetic dataset to exercise the engine end-to-end:

- CSV columns: `time,open,high,low,close` (optional `spread_price`; when absent
  the `--fallback-spread-points` value is used). `time` is ISO-8601.
- Rows must be complete, consecutive M15 quarters (`:00,:15,:30,:45`); an hour
  is only aggregated to an H1 bar when all four quarters are present.
- Provide at least ~215 resulting H1 bars (~860 M15 rows) to clear the
  200-EMA + Donchian warmup, or the run raises `not enough H1 bars`.
- broker-meta JSON: `{"symbols": {"<SYMBOL>": {"point", "trade_tick_size",
  "trade_tick_value_loss", "volume_min", "volume_max", "volume_step",
  "commission": {"kind": "usd_per_lot", "per_side_usd_per_lot"}}}}`.

Synthetic data proves the engine runs; it says nothing about strategy edge.
