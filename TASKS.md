# TASKS.md — Gold Quantamental Bot

Status legend: `[ ]` todo, `[~]` in progress, `[x]` done, `[!]` blocked, `[-]` deferred.

The build order is deliberate: **manifest → headless validator → strategy core → logging/analytics/
dashboard/wizard**. Do not skip ahead.

---

## M1 — Scope manifest and headless validator

### Repository and environment

- [x] Initialize git repository at project root.
- [x] Create Python 3.12 `.venv`.
- [x] Pin dependencies in `pyproject.toml`.
- [x] Add `.gitignore` for secrets, manual data, runtime data, and reports.
- [x] Add `AGENTS.md` guardrails.
- [x] Add `TASKS.md` milestone checklist.
- [x] Add `README.md` with setup and validator usage.
- [ ] Add CI workflow for tests and validator smoke run. (Deferred until repo hosting is chosen.)

### Scope manifest

- [x] Create `config/scope.gold.v1.yaml`.
- [x] Declare instrument `XAUUSD`, spot-gold CFD, long-only, demo, leveraged with a 5:1 bot notional cap.
- [x] Declare execution bar `M15` and context bar `H4`.
- [x] Declare standard risk defaults: 0.25% per trade, 1.25% minimum-lot risk guard, 5:1 leverage cap, 1.5% daily halt, 8% max drawdown.
- [x] Add explicit account profiles: `standard_1k` (safe) and `micro_100` (high-risk override, not promotion-eligible).
- [x] Declare source registry with legal/ToS risk, point-in-time behavior, coverage, cadence, gaps.
- [x] Declare fundamental inputs and weights:
  - [x] Real yield 10y: 0.55
  - [x] Broad USD: 0.20
  - [x] Gold ETF flows: 0.15
  - [x] COT managed money: 0.10
  - [x] Central-bank gold: 0.00, diagnostic, HUMAN_JUDGMENT
  - [x] Breakeven 10y: 0.00, diagnostic only
- [x] Declare technical context and indicators.
- [x] Declare fusion rule: entry requires both fundamental and technical agreement.
- [x] Declare overseer as rule-based referee, not meta-model, not trade originator.
- [x] Declare exit precedence with risk exits veto-immune.
- [x] Declare SQLite/WAL/single-writer and Parquet/DuckDB storage.
- [x] Declare backtest assumptions and promotion gates.
- [x] Declare runtime process authority boundaries.
- [x] Add a deliberately broken scope for negative testing: `config/scope.broken.sec_edgar.yaml`.

### Validator

- [x] Implement `goldbot` package with CLI.
- [x] Implement manifest loader and deterministic manifest hash.
- [x] Implement check registry and per-item report model.
- [x] Implement structural integrity checks.
- [x] Implement market/scope checks.
- [x] Implement risk-limit checks.
- [x] Implement source registry, legal, point-in-time, and coverage checks.
- [x] Implement fundamental-weight and central-bank-policy checks.
- [x] Implement technical/fusion/overseer/exit checks.
- [x] Implement storage/schema checks.
- [x] Implement backtest/analytics/runtime-boundary checks.
- [x] Implement free-source HTTP checks (CSV, XLSX, ZIP, HTML).
- [x] Implement MT5 preflight check with exact error reporting.
- [x] Implement manual XLSX mechanical checks and HUMAN_JUDGMENT labeling.
- [x] Implement calibration artifact check and manifest-hash verification.
- [x] Implement JSON and Markdown reports.
- [x] Add `--offline` mode for deterministic tests.
- [x] Add `--no-mt5` mode for machines without a terminal.
- [x] Add tests for gold manifest, broken manifest, central-bank handling, and report shape.
- [x] Run validator against gold scope.
- [x] Run validator against broken SEC EDGAR scope.
- [x] Fix MT5 demo-account authorization and rerun full runtime preflight.
  - Terminal is now build 6182 at `C:\AbelDemoMt5`.
  - Demo account `106806652` on `FBS-Demo` is active, USD, equity 1000.00.
  - XAUUSD: digits 2, point 0.01, spread 31 points, 500 M15 bars, 500 H4 bars.
- [ ] Human review of per-item report and confirmation that HUMAN_JUDGMENT labels are honest.
- [x] M1 gate decision: **PASS** for machine-checkable items; human sign-off pending.
  - Design-time manifest validation: **PASS**.
  - Runtime preflight: **PASS**.
  - Gold counts: `PASS=35, FAIL=0, HUMAN_JUDGMENT=2, SKIP=0, BLOCKED=0`.
  - Evidence: `reports/gold.v1.validation.json`, `reports/gold.v1.validation.md`.
  - Negative test: broken SEC EDGAR scope **FAILS** for specific reasons in
    `reports/broken.sec_edgar.validation.json`.
  - Remaining HUMAN_JUDGMENT: manual WGC/IMF XLSX absent (weight 0, not consumed by overseer)
    and human sign-off.

---

## M2 — Strategy core

### Data ingestion

- [x] MT5 OHLCV ingestion for M15 and H4, independent of open charts.
- [~] Broker-time to UTC calibration and DST handling. Offset support exists; DST/automatic
  calibration is not yet wired into the runner.
- [~] Spread, tick value, lot step, stop level, and freeze level capture. Spread and lot step are
  modeled; tick value/stop/freeze levels are not yet captured from the symbol.
- [x] FRED CSV ingestion for real yields, broad USD, breakevens.
- [~] ALFRED vintage ingestion for point-in-time macro revisions. The loader accepts
  `realtime_start`, and `load_macro_directory` prefers `ALFRED_*.csv` over the latest-revision FRED
  file. Automated download/replay scheduling remains M3.
- [x] U.S. Treasury real-yield curve ingestion.
- [x] CFTC COT ZIP ingestion and gold contract mapping.
- [x] SPDR GLD holdings ingestion.
- [ ] iShares IAU holdings ingestion.
- [x] CBOE GVZ ingestion as a risk/vol diagnostic (`GVZ.csv` in the macro directory).
- [~] Manual WGC/IMF XLSX ingestion with mechanical checks and HUMAN_JUDGMENT label. The validator
  checks the manual-file policy; the strategy loader does not yet ingest the XLSX.
- [~] Staleness, gap, and revision logging for every source. Staleness is reported in views; full
  per-source gap/revision logging is not yet persisted.
- [ ] Raw snapshot retention with source hash and retrieval time.

### Fundamental engine

- [x] Build real-yield signal with level and change components.
- [x] Build broad-USD signal.
- [x] Build ETF-flow signal.
- [x] Build COT-positioning signal.
- [x] Build central-bank diagnostic signal with weight 0.
- [x] Build breakeven diagnostic signal with weight 0.
- [x] Implement missing-data policy: no silent forward-fill.
- [x] Implement point-in-time replay for backtests.
- [x] Unit tests for sign conventions and edge cases.

### Technical engine

- [~] M15 structure: swing highs/lows, break-of-structure, failed break. Implemented as rolling
  prior-high/low structure break; pivot confirmation and failed-break state are not yet modeled.
- [x] M15 trend and momentum: EMA stack, RSI, ATR.
- [~] H4 context: trend, range, volatility regime. Trend context is implemented; range/volatility
  regime classification is not.
- [x] Entry timing rules for long-only.
- [x] No-trade filters: spread, session, news, volatility extremes. Spread, ATR, explicit news
  blackout, and the session-filter mechanism are implemented. The session filter is disabled in the
  manifest until the owner confirms the session hours.
- [x] Unit tests with synthetic bars.

### Fusion and overseer

- [x] Fundamental score → directional bias and confidence.
- [x] Technical score → timing trigger and invalidation level.
- [x] Fusion requires both layers for entry.
- [x] Overseer arbitrates conflicts; never originates entries.
- [~] Overseer calibration artifact with manifest hash and fit window. Manifest hash is recorded;
  no parameters are fitted yet.
- [x] Risk exits veto-immune.
- [x] Decision record includes every input, weight, version, and veto.
- [x] Unit tests for arbitration and veto behavior.

### Exits

- [x] Emergency/risk exit priority 100.
- [x] Hard/trailing stop priority 90.
- [x] Thesis invalidated priority 80.
- [x] Thesis weakened priority 70.
- [x] Time stop priority 60.
- [x] Technical/profit trail priority 50.
- [x] Profit take priority 40.
- [x] Advisory exit priority 30.
- [~] SELL decisions are explicit, logged, and reviewed as first-class outcomes. Exits are explicit
  in decisions/trades; a dedicated SELL-review report is not yet built.
- [~] Unit tests for precedence conflicts and partial fills. Precedence tests exist; partial-fill
  tests do not.

### Backtest and validation

- [x] Event-driven backtester using M15 execution and H4 context.
- [x] Costs: spread, commission, slippage, swap if applicable. Spread, commission, and slippage are
  modeled; swap is not.
- [x] Stop-first intrabar assumption.
- [~] No lookahead; vintage macro data only. The point-in-time store and closed-bar windows enforce
  this; automated ALFRED vintage replay remains M3.
- [x] Walk-forward and out-of-sample splits.
- [x] Baseline comparison: buy-and-hold, simple trend, random-entry control.
- [x] Report drawdown, Sharpe, Sortino, hit rate, payoff, exposure, trade count. The report includes
  daily annualized Sharpe/Sortino (null until at least 20 trading days), entry notional, and
  entry-exposure percentages.
- [x] Stress tests: gap risk, spread spikes, missing data, stale macro prints.
- [x] Standard $1,000 profile sizing: 5:1 cap, 0.25% base risk, 1.25% minimum-lot risk guard.
- [x] High-risk $100 micro profile sizing: 50:1 cap, 11% per-trade cap, 12% daily halt, 25% max drawdown, not promotion-eligible.
- [x] Read-only static HTML dashboard renderer for saved run reports; no MT5 connection and no scope/trade mutation.

### M2 gate decision

- [~] M2 core and the first M3 paper/ledger slice are runnable and tested (`79 passed`), but broker
  execution is not promoted. Remaining M2/M3 items above are explicit; do not treat the synthetic
  fixture as profitability evidence. The `micro_100` profile is a high-risk override, not a
  promotion candidate.

---

## M3 — Execution, logging, analytics, dashboard

### Execution

- [x] Paper/dry-run execution replay with deterministic order IDs and idempotency keys (M3 slice).
- [ ] Headless MT5 order router with idempotency keys.
- [ ] Pre-trade checks: scope, risk, margin, spread, symbol state, session.
- [ ] Post-trade reconciliation against MT5 deals and positions.
- [ ] Kill switch: flatten and halt.
- [ ] Reconnect drill and state recovery.
- [x] Daily loss halt enforcement in the paper replay risk state (broker flatten still pending).
- [x] Max drawdown flatten/halt enforcement in the paper replay risk state (broker flatten still
  pending).
- [ ] No-chart-assumption tests.

### Logging and storage

- [x] SQLite schema with WAL and single-writer enforcement.
- [x] Snapshots table: market, fundamentals, technicals, fusion, overseer.
- [x] Decisions table: inputs, scores, vetoes, versions.
- [x] Orders/fills/positions tables.
- [x] Logs table with structured JSON.
- [ ] Parquet/DuckDB analytical exports.
- [ ] Retention and backup policy.

### Analytics

- [ ] Baseline definitions and stored results.
- [ ] Live vs backtest drift monitor.
- [ ] Per-source staleness dashboard data.
- [ ] Trade review: entry quality, exit quality, MAE/MFE.
- [ ] SELL-decision review report.
- [ ] Weekly and monthly performance summaries.

### Dashboard

- [x] Static read-only HTML renderer for saved run reports (M2).
- [x] Never connects to MT5 directly.
- [x] Displays profile, risk limits, decisions, trades, data quality, and alerts.
- [x] Displays paper orders and the `paper_replay` mode badge (M3 slice).
- [x] Cannot change scope or trade.
- [ ] FastAPI read-only backend (M3).
- [ ] Lightweight live HTML/JS frontend (M3).
- [ ] Can request human action, but cannot change scope or trade.
- [ ] Auth and local-only binding.

### Wizard

- [ ] Scope wizard for non-code changes.
- [ ] Validates proposed scope through the headless validator.
- [ ] Requires human confirmation and records an audit event.
- [ ] Supervisor applies approved scope changes; no other component may.

---

## M4 — Demo operation and promotion

- [ ] Run 3 months demo.
- [ ] Collect 100+ trades.
- [ ] Positive out-of-sample after costs.
- [ ] Drawdown within the standard 0.25% per-trade / 1.5% daily / 8% max-drawdown limits. The
  `micro_100` high-risk profile (11% / 12% / 25%) is not promotion-eligible.
- [ ] Kill-switch drill passed.
- [ ] Reconnect drill passed.
- [ ] Incident review log complete.
- [ ] Human sign-off recorded.
- [ ] Only then consider real-money operation.

---

## Open questions / human decisions

- [ ] Provide a valid FBS-Demo account or another demo account for MT5 runtime checks.
- [x] Confirm a demo equity/account model that can hold the XAUUSD minimum lot. The standard
  $1,000 profile now uses 5:1 leverage and a 1.25% minimum-lot risk guard.
- [ ] Confirm whether the `micro_100` high-risk override is acceptable for demo use. It allows
  0.01 lot on $100 at ~9.4%-10.7% stop risk, is not promotion-eligible, and is not a substitute for
  a micro-lot broker.
- [ ] Confirm broker contract specifications: XAUUSD lot step, tick value, stop level, swap.
- [ ] Confirm data-source ToS risk acceptance for each source.
- [ ] Confirm whether GVZ and breakevens remain diagnostic-only in v2.
- [ ] Confirm dashboard authentication approach for the M3 live backend.
- [ ] Confirm repo hosting and CI provider.
