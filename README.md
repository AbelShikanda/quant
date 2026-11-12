# Gold Quantamental Bot

A quantamental XAUUSD research and trading project. **Gold macro fundamentals define what to
consider; M15 technicals define when to act.** The overseer is a rule-based referee, not a trade
originator. Exits are first-class, especially SELL decisions.

This repository currently implements **M1: the scope manifest and headless validator** and the
**M2 strategy core**: point-in-time macro data, fundamental/technical scoring, fusion, the rule-based
overseer, first-class exits, and an event-driven M15/H4 backtest harness. Order execution, the
dashboard, and the wizard are intentionally not built yet.

## Read this first

- `notes.txt` — authoritative product brief. Do not edit.
- `AGENTS.md` — operating guardrails and promotion gates.
- `TASKS.md` — milestone checklist.
- `config/scope.gold.v1.yaml` — the v1 scope manifest.
- `config/scope.broken.sec_edgar.yaml` — deliberately broken scope used to prove the validator fails
  for the right reasons.

## Hard scope

- XAUUSD spot gold only.
- Long-only.
- M15 execution; H4 context.
- Cash-equivalent account, no leverage; notional exposure ≤ equity.
- Demo first. No real-money trading until all promotion gates in `AGENTS.md` are met.
- Risk defaults: 0.25% per trade, 1.5% daily loss halt, 8% max drawdown flatten/halt.

## Setup

Python 3.12 is required.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

The `MetaTrader5` package is Windows-only and requires a running, logged-in MT5 terminal for runtime
preflight checks.

## Run the M2 strategy core

The strategy core is headless and reads every behavior from `config/scope.gold.v1.yaml`. The default
`run` source is a deterministic synthetic fixture so the core can be exercised without network data:

```powershell
.\.venv\Scripts\python.exe -m goldbot.cli run `
  --manifest config/scope.gold.v1.yaml `
  --source synthetic `
  --bars 1200 `
  --equity 10000 `
  --report reports/m2.run.json
```

To pull OHLCV from the running MT5 terminal instead:

```powershell
.\.venv\Scripts\python.exe -m goldbot.cli run `
  --manifest config/scope.gold.v1.yaml `
  --source mt5 `
  --bars 1200 `
  --context-bars 500 `
  --equity 10000 `
  --report reports/m2.mt5.run.json
```

Use `--macro-dir data/macro` to load local `DFII10.csv`, `DTWEXBGS.csv`, `T10YIE.csv`, `GLD.xlsx`,
and `COT.csv`/`COT.zip` files instead of the synthetic macro fixture. Missing files are reported as
missing/stale; they are not forward-filled.

The manifest-parameter demonstration changes only `strategy.fusion.entry_threshold` and shows the
entry count change without a code edit:

```powershell
.\.venv\Scripts\python.exe -m goldbot.cli demo-param `
  --manifest config/scope.gold.v1.yaml
```

The fixed interfaces are documented in `docs/contracts.md`. Verification commands and observed
results are recorded in `docs/m2-evidence.md`.

### Cash-equivalent/no-leverage caveat

A standard XAUUSD lot is normally 100 oz. On a $1,000 cash-equivalent account, the minimum `0.01`
lot is about 1 oz, roughly $2,000 notional at $2,000 gold. That exceeds equity and violates the
no-leverage cap. The strategy returns `NO_TRADE` with an explicit sizing reason instead of silently
taking leverage. Use a larger demo equity for strategy evidence, or obtain a broker contract whose
minimum notional fits the account. The current `$1,000` demo account is therefore expected to show
no entries; this is an honest constraint, not a bug.

## Validate the gold scope

```powershell
.\.venv\Scripts\python.exe -m goldbot.cli validate `
  --manifest config/scope.gold.v1.yaml `
  --report reports/gold.v1.validation.json `
  --markdown reports/gold.v1.validation.md
```

Useful flags:

- `--offline` — skip network checks. Structural and file checks still run. Intended for tests.
- `--no-mt5` — skip the MT5 runtime preflight. Intended for machines without a terminal.
- `--strict` — treat runtime blockers as failures.

Exit codes:

| Code | Meaning |
|---|---|
| 0 | PASS |
| 1 | FAIL |
| 2 | PARTIAL — M1 design checks pass, but a runtime blocker exists |

## Validate the broken scope

```powershell
.\.venv\Scripts\python.exe -m goldbot.cli validate `
  --manifest config/scope.broken.sec_edgar.yaml `
  --report reports/broken.sec_edgar.validation.json
```

This must **FAIL** for specific reasons: unsupported source type, non-gold fundamental input,
missing calibration artifact, missing storage schema, invalid account model, and incomplete exit
precedence. It must not crash.

## Run tests

```powershell
.\.venv\Scripts\python.exe -m pytest
```

The M2 suite covers point-in-time vintage isolation, SQLite/DuckDB round-trips, fundamental sign
conventions and missing/stale handling, technical H4 forming-bar exclusion, fusion conflicts,
overseer vetoes, exit precedence for both disagreement cases, the no-leverage sizing cap, and the
manifest-parameter-change demonstration.

## Manifest hash and calibration artifact

The overseer calibration artifact contains the deterministic SHA-256 of the manifest. If you edit the
gold manifest, regenerate the artifact:

```powershell
.\.venv\Scripts\python.exe -m goldbot.cli hash-manifest `
  --manifest config/scope.gold.v1.yaml
```

Copy the printed hash into `config/calibration/gold.v1.overseer.json` under `manifest_hash`, or use
the `--write-calibration` helper:

```powershell
.\.venv\Scripts\python.exe -m goldbot.cli hash-manifest `
  --manifest config/scope.gold.v1.yaml `
  --write-calibration config/calibration/gold.v1.overseer.json
```

## Central-bank gold data

Central-bank holdings are a **manual monthly WGC/IMF XLSX** in `data/manual/wgc/`. They are:

- `HUMAN_JUDGMENT`,
- weight **0.00** in the v1 entry score,
- **not consumed by the overseer in v1**.

The validator checks only mechanical properties: file presence, parseability, schema, age, and
reminder/refresh event. Missing or stale files are reported as `HUMAN_JUDGMENT`, not as fabricated
data. The required label is:

> derived from human-downloaded file; weight in entry score = 0; NOT consumed by the overseer (§9) in v1.

## Data sources

The gold manifest declares free sources only:

- MT5 XAUUSD M15/H4 (price and execution)
- FRED CSV (`DFII10`, `DTWEXBGS`, `T10YIE`, etc.)
- ALFRED vintage CSV for point-in-time macro revisions
- U.S. Treasury daily real-yield curve CSV
- CFTC COT ZIP (gold contract `088691`)
- SPDR GLD and iShares IAU holdings archives
- CBOE GVZ CSV
- Manual WGC/IMF central-bank XLSX

Each source declares ToS/legal risk, point-in-time behavior, coverage, cadence, and gap notes. Free
does not mean risk-free; review the legal flags before live use.

## Current M1 status

**M1 gate: PASS (machine-checkable items). Human sign-off is still required.**

The manifest and validator are implemented. The gold scope passes design-time checks and the MT5
runtime preflight. Latest gold run:

```text
overall: PASS
m1: PASS
runtime: PASS
counts: PASS=35, FAIL=0, HUMAN_JUDGMENT=2, SKIP=0, BLOCKED=0
```

MT5 evidence:

```text
terminal: MetaTrader 5 build 6182 at C:\AbelDemoMt5
account: 106806652, FBS-Demo, demo trade mode, USD, equity 1000.00
symbol: XAUUSD, digits 2, point 0.01, spread 31 points
bars: 500 M15 + 500 H4
latest: M15 2026-11-11T21:00:00Z, H4 2026-11-11T20:00:00Z
```

The two remaining HUMAN_JUDGMENT items are:

- `source.wgc_central_bank_manual` — the manual WGC/IMF XLSX is absent; weight is 0.00 and it is
  not consumed by the overseer.
- `human.signoff` — a human must review the per-item report and confirm the labels are honest.

Latest broken-scope run (offline, no MT5):

```text
overall: FAIL
m1: FAIL
counts: PASS=3, FAIL=21, HUMAN_JUDGMENT=1, SKIP=0, BLOCKED=0
```

The broken SEC EDGAR scope fails for specific reasons: unsupported source type, non-gold
fundamental inputs, missing central-bank policy, wrong timeframes/indicators, bad fusion and
overseer rules, missing calibration artifact, incomplete exit precedence, wrong storage model,
missing schema/baseline files, invalid dashboard/runtime boundaries, and missing promotion gates.

## Current M2 status

**M2 strategy core: runnable headless implementation, not yet promoted to execution.**

Implemented:

- fixed contracts in `goldbot/contracts.py` and `docs/contracts.md`,
- point-in-time macro store with in-memory and SQLite/WAL persistence,
- DuckDB OHLCV store with Parquet export,
- headless MT5 M15/H4 puller with broker-time offset support,
- synthetic OHLCV and macro fixtures for tests and demos,
- fundamental engine with manifest weights, signs, transforms, z-scores, staleness, and
  zero-weight diagnostics,
- technical engine with EMA stack, RSI, ATR, structure, H4 context, spread/ATR filters, and
  forming-bar exclusion,
- fusion with both-layer requirement, conflict policy, score minimums, and manifest-declared
  triggers,
- rule-based overseer that cannot originate entries and applies veto-immune risk exits,
- exit engine with the full priority ladder,
- event-driven M15/H4 backtest harness with next-bar-open discretionary fills, stop-first
  protective exits, spread/slippage/commission modeling, and JSON decision records.

Evidence:

- `python -m pytest -q` → **52 passed**.
- Synthetic run: 1,200 M15 bars, 75 H4 bars, 10,000 equity → 4 entries/trades, final equity
  10,067.90 on the current fixture.
- Manifest-parameter demo: baseline `entry_threshold=0.20` → 4 entries; changed
  `entry_threshold=0.99` → 0 entries, without a code edit.
- Point-in-time test: a revised `DFII10` print is invisible before its knowledge time.
- Exit-precedence tests: fundamentals-only deterioration selects `thesis_invalidated` (80);
  technicals-only deterioration selects `technical_or_profit_trail` (50); risk selects
  `emergency_risk` (100) and is veto-immune.

Not yet implemented in M2: ALFRED vintage replay, iShares IAU, CBOE GVZ, manual WGC/IMF XLSX
ingestion, raw source snapshot hashing, session filters, walk-forward/OOS splits, and
Sharpe/Sortino/stress reporting. These remain M3 work.

## Non-goals for M1/M2

- No order execution.
- No dashboard.
- No wizard.
- No real-money trading.
