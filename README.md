# Gold Quantamental Bot

A quantamental XAUUSD research and trading project. **Gold macro fundamentals define what to
consider; M15 technicals define when to act.** The overseer is a rule-based referee, not a trade
originator. Exits are first-class, especially SELL decisions.

This repository currently implements **M1: the scope manifest and headless validator** and the
**M2 strategy core**: point-in-time macro data, fundamental/technical scoring, fusion, the rule-based
overseer, first-class exits, an event-driven M15/H4 backtest harness, and a **read-only static HTML
dashboard renderer**. Order execution, a live dashboard backend, and the wizard are intentionally
not built yet.

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
- Leveraged account model with an explicit **5:1 bot notional cap**. The standard profile keeps
  0.25% base risk and adds a 1.25% minimum-lot risk guard.
- A separate `micro_100` profile allows a $100 account to hold the broker minimum lot by explicitly
  raising risk. It is a **high-risk override**, is not promotion-eligible, and is not a substitute
  for a micro-lot broker.
- Demo first. No real-money trading until all promotion gates in `AGENTS.md` are met.
- Standard risk defaults: 0.25% per trade, 1.5% daily loss halt, 8% max drawdown flatten/halt.

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

### Account profiles: standard $1,000 and high-risk $100

The base manifest uses the approved leveraged account model with a 5:1 bot notional cap. Select an
explicit profile with `--account-profile`; the strategy never infers a profile from equity.

Standard $1,000 profile:

```powershell
.\.venv\Scripts\python.exe -m goldbot.cli run `
  --manifest config/scope.gold.v1.yaml `
  --account-profile config/profiles/standard_1k.yaml `
  --source mt5 `
  --bars 500 `
  --context-bars 500 `
  --equity 1000 `
  --report reports/m2.mt5.run.1000.json `
  --dashboard reports/m2.mt5.dashboard.1000.html
```

- 5:1 notional cap, 0.25% base risk, 1.25% minimum-lot risk guard.
- At ~$4,114 gold and the latest MT5 ATR, the 0.01 lot stop risk was ~$9.37, or 0.9366% of $1,000.
  That fits the guard and the account trades.
- The strategy reports the risk implied by the final lot-rounded volume, not the pre-rounding budget.

High-risk $100 profile:

```powershell
.\.venv\Scripts\python.exe -m goldbot.cli run `
  --manifest config/scope.gold.v1.yaml `
  --account-profile config/profiles/micro_100.yaml `
  --source mt5 `
  --bars 500 `
  --context-bars 500 `
  --equity 100 `
  --report reports/m2.mt5.run.100.json `
  --dashboard reports/m2.mt5.dashboard.100.html
```

- 50:1 notional cap, 11% per-trade cap, 12% daily halt, 25% max drawdown.
- The 0.01 lot stop risk is ~9.37% of a $100 account. One normal stop is a double-digit loss.
- The profile prints a warning, is marked `promotion_eligible: false`, and the dashboard shows a red
  high-risk banner.
- This exists only because the owner explicitly requested the broker minimum lot on $100. The
  risk-preserving route is a micro-lot broker (0.001 lot or a 1 oz-per-lot contract).

### Read-only dashboard

The dashboard renders a saved JSON run report into one self-contained HTML file. It does not import
MetaTrader5, connect to a broker, place orders, or change scope.

```powershell
.\.venv\Scripts\python.exe -m goldbot.cli dashboard `
  --report reports/m2.mt5.run.1000.json `
  --output reports/m2.dashboard.standalone.html `
  --max-decisions 200
```

The `run` command can also emit it directly with `--dashboard <path>`. The page shows the profile,
risk banner, account model, leverage and notional caps, risk limits, equity curve, trades, explained
decisions, and data-quality counts.

This static renderer is an owner-requested, read-only exception to the `notes.txt` "no dashboard"
non-goal. It has no server, no MT5 connection, and no write path. The live dashboard backend and
wizard remain M3 work.

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
overseer vetoes, exit precedence for both disagreement cases, standard-1k sizing, the $100
standard-profile refusal, micro-100 minimum-lot sizing, profile-overlay immutability, the read-only
dashboard, and the manifest-parameter-change demonstration.

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
- technical engine with EMA stack, RSI, ATR, structure, H4 context, spread/ATR/session filters, and
  forming-bar exclusion,
- fusion with both-layer requirement, conflict policy, score minimums, and manifest-declared
  triggers,
- rule-based overseer that cannot originate entries and applies veto-immune risk exits,
- exit engine with the full priority ladder,
- event-driven M15/H4 backtest harness with next-bar-open discretionary fills, stop-first
  protective exits, spread/slippage/commission modeling, and JSON decision records,
- ALFRED `realtime_start` vintage ingestion and ALFRED-over-FRED precedence in the macro directory,
- CBOE GVZ diagnostic ingestion,
- session-filter mechanism (disabled by default pending owner confirmation of session hours),
- Sharpe, Sortino, entry-notional, and exposure reporting; gap/spread/missing/stale stress tests,
- explicit account-profile overlays for the standard $1,000 account and the high-risk $100 account,
- read-only static HTML dashboard renderer for saved run reports.

Evidence:

- `python -m pytest -q` → **66 passed**.
- Synthetic run: 1,200 M15 bars, 75 H4 bars, 10,000 equity → 4 entries/trades, final equity
  10,186.72 on the current fixture.
- Standard $1,000 MT5 run: 1 trade, `LONG 0.01 4114.26 -> 4116.94`, PnL `2.67`, trailing-stop
  exit; entry risk `9.37` = `0.9366%`, within the 1.25% min-lot guard.
- High-risk $100 MT5 run: 1 trade, `LONG 0.01 4114.26 -> 4116.94`, PnL `2.67`, trailing-stop
  exit; entry risk `9.37` = `9.3663%`, under the 11% micro-profile cap but a double-digit loss.
- Manifest-parameter demo: baseline `entry_threshold=0.20` → 4 entries; changed
  `entry_threshold=0.99` → 0 entries, without a code edit.
- Point-in-time test: a revised `DFII10` print is invisible before its knowledge time.
- Exit-precedence tests: fundamentals-only deterioration selects `thesis_invalidated` (80);
  technicals-only deterioration selects `technical_or_profit_trail` (50); risk selects
  `emergency_risk` (100) and is veto-immune.
- Dashboard test: renders the high-risk banner, trades, decisions, and the read-only/no-MT5 notice.
- ALFRED test: `realtime_start` becomes the knowledge time, and an ALFRED file takes precedence over
  the latest-revision FRED file in the macro directory.
- GVZ test: the CBOE diagnostic CSV loads with a next-day knowledge time.
- Session test: an enabled filter blocks a Saturday decision and allows a Friday session decision.
- Stress tests: a gap through the stop fills at the gap open (not the stop), a gap through the
  take-profit fills at the gap open (not the target), and missing/stale macro data is reported, not
  forward-filled.
- Metrics: the report includes `sharpe_daily_annualized`, `sortino_daily_annualized`,
  `daily_return_count`, `average_entry_notional`, `max_entry_notional`, and entry-exposure
  percentages. Sharpe/Sortino stay `null` until at least 20 trading days have closed trades.

Not yet implemented in M2: automated ALFRED download/replay, iShares IAU (deferred to v2 in the
manifest), manual WGC/IMF XLSX strategy ingestion (still HUMAN_JUDGMENT and not consumed by the
overseer), raw source snapshot hashing, walk-forward/OOS splits, and baseline controls. These remain
M3 work. The session-filter mechanism exists but is disabled in the manifest until the owner
confirms the session hours.

## Non-goals for M1/M2

- No order execution.
- No live dashboard backend. The M2 dashboard is a static renderer of a saved report.
- No wizard.
- No real-money trading.
