# Gold Quantamental Bot

A quantamental XAUUSD research and trading project. **Gold macro fundamentals define what to
consider; M15 technicals define when to act.** The overseer is a rule-based referee, not a trade
originator. Exits are first-class, especially SELL decisions.

This repository currently implements **M1: the scope manifest and headless validator**. Strategy,
execution, dashboard, and wizard layers are intentionally not built yet.

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

## Non-goals for M1

- No strategy logic.
- No order execution.
- No dashboard.
- No wizard.
- No real-money trading.
