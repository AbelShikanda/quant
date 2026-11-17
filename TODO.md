# TODO — Gold Quantamental XAUUSD Demo & Promotion Path

Status legend: `[ ]` todo, `[~]` in progress, `[x]` done, `[!]` blocked, `[-]` deferred.

The contract is `AGENTS.md`. The authoritative scope is `notes.txt` (the specification brief).
The authoritative build order is `TASKS.md`. This file syncs the two and adds the demo/promotion path.

Done correctly means: safe, gated, and evidence-oriented — not merely "the code exists."

---

## M1 — Manifest, validator, environment

- [x] Initialize repo, `.venv`, pinned dependencies, `.gitignore`
- [x] `AGENTS.md` guardrails
- [x] `README.md` with validator usage
- [x] `TASKS.md` checklist
- [ ] CI workflow for tests + validator smoke run (deferred until repo hosting chosen)
- [x] Scope manifest `config/scope.gold.v1.yaml`
- [x] XAUUSD spot-gold CFD, long-only, M15/H4, demo venue
- [x] Risk defaults: 0.25% per trade, 1.25% minimum-lot risk guard, 5:1 leverage cap, 1.5% daily halt, 8% max drawdown
- [x] Account profiles: `standard_1k` (promotion-eligible) and `micro_100` (high-risk, not promotion-eligible)
- [x] Source registry, legal/ToS risk, point-in-time behavior, coverage, cadence, gaps
- [x] Broken negative-test scope `config/scope.broken.sec_edgar.yaml`
- [x] Validator implementation
- [x] MT5 preflight / demo authorization / fresh numbers
- [x] M1 gate decision: PASS for machine-checkable items
- [x] M1 human sign-off recorded in `reports/m1.signoff.json`
- [~] Human review artifact honest? Pending formal review, not the same as operational sign-off; not a promotion gate by itself.

---

## M2 — Strategy core (data, fundamentals, technicals, fusion, overseer, exits, backtest)

### Data ingestion
- [x] MT5 OHLCV M15/H4 (headless)
- [~] Broker time to UTC calibration; DST handling not wired into the runner; offset is manually encoded
- [x] Symbol specs captured via `goldbot broker-specs` → `reports/broker_specs.json` (tick_value=1.0, contract=100, volume_min/step=0.01, stops_level=0, freeze_level=0, filling mode 3, spread ~23-25, swap_long=-69.95)
- [x] FRED inputs and GPD (real yields, broad USD, T10YIE, Treasury)
- [~] ALFRED vintage support exists in code (`realtime_start` handling and `ALFRED_*.csv` preference) but automated replay scheduling is M3
- [x] Treasury curve ingestion
- [x] CFTC COT ingestion/mapping
- [x] GLD holdings ingestion
- [x] CBOE GVZ ingestion
- [ ] iShares IAU ingestion
- [~] Manual WGC/IMF XLSX present and mechanically accepted (weight 0, diagnostic, HUMAN_JUDGMENT); not consumed by strategy/overseer
- [~] Per-source gap/revision logging not fully persisted
- [ ] Raw snapshot retention with source hash and retrieval time

### Fundamental engine
- [x] Real yield signal
- [x] Broad USD signal
- [x] Gold ETF flow signal
- [x] COT signal
- [x] Central-bank gold = 0.00 diagnostic; never used by overseer
- [x] Breakeven 10y = 0.00 diagnostic
- [x] Missing-data policy: no forward-fill
- [x] Point-in-time replay in backtests
- [x] Unit tests passed

### Technical engine
- [x] EMA stack, RSI, ATR, volatility
- [x] Entry rules for long-only
- [x] No-trade filters: spread, session (disabled), news, volatility extremes, stale data vix
- [~] Swing-break structure: implemented; failed-break/pivot confirmation not implemented
- [x] H4 trend context implemented; range/volatility-regime classification not implemented
- [x] Unit tests passed

### Fusion and overseer
- [x] Fusion requires both layers
- [x] Overseer never originates trades
- [x] Risk exits veto-immune
- [~] Overseer calibration artifact: manifest hash recorded; no fitted parameters yet
- [x] Decision record includes inputs, weights, vetoes
- [x] Unit tests passed

### Exits
- [x] Priority 100 emergency risk
- [x] Priority 90 hard/trailing stop
- [x] Priority 80 thesis invalidated
- [x] Priority 70 thesis weakened
- [x] Priority 60 time stop
- [x] Priority 50 technical/profit trail
- [x] Priority 40 profit take
- [x] Priority 30 advisory
- [~] Partial-fill tests not done
- [~] Dedicated SELL-review report not done
- [~] “M2 gate decision: PASS for machine-checkable items” in TASKS.md, but not promotion-ready

### Backtest and validation
- [x] Event-driven backtester M15/H4
- [x] Costs: spread, commission, slippage; swap not modeled
- [x] Stop-first intrabar
- [~] No lookahead enforced through point-in-time store + closed-bar windows (vintage replay automation is M3)
- [x] Walk-forward / out-of-sample splits exist
- [x] Baselines: buy-and-hold, simple trend, random-entry control recorded
- [x] Daily Sharpe/Sortino in report (null until ≥20 days)
- [x] Stress tests (gap risk, spread spike, stale print) exist
- [x] Standard $1,000 profile sizing verified
- [x] High-risk `micro_100` profile design exists (not promotion-eligible)
- [x] Read-only static dashboard renderer

---

## M3 — Execution, logging, analytics, dashboard

### Execution
- [x] Paper execution replay with deterministic order IDs and idempotency keys
- [x] Paper ledger, orders, fills, positions, logs
- [x] Daily loss halt enforced in paper risk state
- [x] Max drawdown flatten/halt enforced in paper risk state
- [~] Headless MT5 order router with idempotency keys (guarded `demo-send` path has idempotency; no autonomous router yet — agent never sends)
- [~] Pre-trade checks: scope, risk, margin, spread, session, symbol state (built into demo preflight; not yet shared with an autonomous router)
- [x] Post-trade reconciliation: positions + deals (`goldbot reconcile`, read-only). Live 2026-11-17: positions MATCH (0/0); deals surface 3 broker-only deals with no ledger fills — honest known gap; to backfill in the redo
- [x] Kill switch drill: flatten and halt (built, drilled under live positions 2026-11-17; incident `killswitch-70b65f13baf44ec0`; halt flag armed)
- [x] Kill-switch UI placement: the owner fires the kill switch from the dashboard (red button, phrase-confirmed modal, spawns the guarded CLI; endpoint never auto-fires). Verified 2026-11-17 via `POST /api/agent/kill-switch`
- [~] Kill-switch flatten-under-position drill (2026-11-17, via dashboard `POST /api/agent/kill-switch`): 2 open demo positions flattened at market, CRITICAL incident recorded, ledger closed with `close_reason=kill_switch` (P&L -2.71/-3.26), MT5 flat, `reconcile` MATCH (0/0), agent refuses to start while `reports/kill-switch.halt` exists. **PARTIAL / redo later**: 2 broker fills were never tracked in the ledger (FK crash, pre-fix); orders/fills rows for them are missing; redo a clean drill after the intent-first order flow lands.
- [x] Kill switch guard-chain drill recorded (`goldbot kill-switch --drill`, 2026-11-17, demo account 106806652 verified; no orders sent; halt flag NOT armed)
- [x] Reconnect drill recorded (`goldbot reconnect-drill`, 2026-11-17, verdict PASS; incident + event in ledger)
- [x] No-chart-assumption tests (`tests/test_no_chart_assumption.py` AST scan of `goldbot/*.py`; zero forbidden chart API calls)

### Logging and storage
- [x] SQLite WAL single-writer ledger
- [x] Snapshots table
- [x] Decisions / orders / fills / positions tables
- [x] Logs table with structured payload
- [x] Parquet/DuckDB analytical exports (`goldbot export-parquet`; DuckDB `COPY ... FORMAT PARQUET`; verified 2026-11-17: chart_bars=464, strategy_runs=5, decisions=11, logs=15)
- [x] Retention and backup policy (`goldbot backup`; sqlite online backup + `PRAGMA integrity_check`; keeps N newest backups (`--keep`); verified: backup integrity ok, 434176B)

### Analytics
- [ ] Baseline definitions and stored results
- [ ] Live vs backtest drift monitor
- [x] Per-source staleness dashboard data (per-fundamental `STALE/ok` rows + `kvFundStale` chip; macro freshness verified from binding files)
- [ ] Trade review: entry quality, exit quality, MAE/MFE
- [ ] SELL-decision review report
- [ ] Weekly/monthly performance summaries
- [ ] Reconstruction test: human explains one decision from logs alone

### Dashboard
- [x] Read-only live dashboard (SQLite-based, loopback-only)
- [x] Preserves blockers; shows straddle values and gate blocker
- [x] Agent steps / architecture visualisation
- [x] Live positions monitor for MT5 positions
- [x] Start/stop agent from UI; terminal output piped per bar
- [x] Accept & Execute + close path (with DEMO OVERRIDE for testing)
- [x] Notifications panel with stale macro + WGC manual link
- [x] Notification bell + dropdown, persisted until completed
- [x] Notifications pruned to pending-only: only items needing consistent reconciliation are shown (macro freshness gate, WGC monthly, any pending drill/signoff); proven items disappear; "All clear" when empty
- [x] WGC reconfirm flow: bell item (link + Reconfirm button) spawns `goldbot wgc-check` — newest XLSX must byte-match the manifest/review file (`world_official_gold_holdings.xlsx`, sha256-tallied, else status `review`); mechanical checks (parse, rows, sha256 vs refresh_event.json, age ≤ 90d); logs `wgc_check` evidence; clears until the next monthly file lands; waiting/fail/stale/review/pass states verified; M1 review evidence and bell tally (same file, same sha)
- [x] Inline refresh button for download-driven items (macro freshness): bell item spawns `goldbot refresh-macro`; WGC reconfirm button live when monthly file is stale/missing; verified 2026-11-17
- [x] Failure independence from MT5 (dashboard never imports MT5; `live_dashboard.py` has zero MT5 imports (tests), reads only SQLite read-only; verified in test suite)
- [ ] Authentication / remote access (not needed while loopback-only)

---

## M4 — Demo operation and promotion

- [ ] 3 months demo on `standard_1k`
- [ ] 100+ trades recorded
- [ ] Positive OOS after costs
- [ ] Drawdown within 0.25%/trade, 1.5% daily, 8% max drawdown
- [~] Kill switch drill passed (demo 2026-11-17; done but intentionally redo later after intent-first order flow; 2 orphan fills untracked)
- [ ] Reconnect drill passed
- [ ] Incident log / reviews complete
- [ ] Human sign-off recorded
- [ ] Only then can we consider real-money operation

---

## Execution-quality and trust gaps (needed for live trading, not just demo)

### P0 — must exist before any live trading
- [x] Economic calendar with blackout windows: `goldbot/econ_calendar.py`, `goldbot calendar` � FOMC/CPI/NFP/PCE +/-30 min, Treasury auction +/-60 min (owner-approved 2026-11-17); wired into agent + demo-preflight gates; `news_blackout` preflight item; dates verified against FOMC/BLS/BEA/Treasury sources; 7 new tests
- [~] Fill quality: slippage recorded on fills; latency/fill-probability not measured; spread-history sampler built (spread_history.csv; p50=28 p99=32 pts during hour 16; needs more samples)
- [~] Tick/depth backtest: not done; MT5 provides ticks; FBS provides no market depth (probed) � needs its own effort; spread-history proxy built (see spread_history.csv; `goldbot spread-sample`/`spread-report`)
- [~] Stop quality under news stress: `goldbot news-stress` built; real MT5 M15 bars: 4 of 10 events (4000 bars, Oct-Nov 2026) would have blown through a 1.5x-ATR stop (40%); blackout justified; full multi-year sweep still pending
- [~] Supervisor/health-check (agent loop has stop file + halt flag; `goldbot health` probes MT5 + ledger activity age + halt flag, exit codes; verified HALTED with armed flag. Not a daemon/auto-respawn yet � a real supervisor is still open)

### P1 — strong edge needed for an honest demo record
- [~] Freshness automation: `goldbot refresh-macro` re-downloads FRED + existing ALFRED vintage files atomically and validates them before touching `data/raw/macro`; wired as a command the owner/agent can call (scheduler/Task Scheduler next step). As of 2026-11-17: DFII10/DTWEXBGS 11-13 (4d, fresh), T10YIE 11-16 (1d), ALFRED_DTWEXBGS 11-16 vintage (4d) — manifest stale limits untouched
- [ ] Risk-correlation checks (e.g. correlated USD long/short vs gold positions)
- [ ] Economic surprise factor (consensus vs actual)
- [ ] Order-book/depth or spread-history proxy for fill probability at size
- [~] Traceability: `goldbot explain <decision_id>` built for mechanical reconstruction (Markdown from the ledger, never re-runs the strategy). Human reconstruction proof still pending, by design
- [ ] Regulatory/compliance memo for the broker terms (leverage, confirmation, permitted instruments)

### P2 — enhancements
- [ ] Options/positioning diagnostic (futures already captured; no options-open-interest data on FBS/free sources)
- [ ] Live vs backtest drift monitoring with alerting
- [ ] SELL-first-class review report for exits

---

## Recommended route — execution infrastructure progress (updated 2026-11-17)

Order: 1 broker specs → 2 pre/post-trade checks → 3 idempotency/reconciliation → 4 kill switch + reconnect drill → 5 controlled demo fills → 6 promotion gates.

- [x] **Step 1 — Broker spec capture.** `goldbot broker-specs --symbol XAUUSD` → `reports/broker_specs.json` (verified live: tick 0.01, contract 100, min lot 0.01, stops_level 0, spread float).
- [ ] Step 1b — Broker terms review. Human item; owner must read the FBS client agreement for EA/Python automation on XAUUSD before any non-demo use. Web search surfaced only generic MetaTrader algotrading docs, not FBS-specific terms — must be done by the owner.
- [x] **Step 2 — Pre- and post-trade checks.** Pre-trade checks live in the demo preflight (spread, stop distance, notional cap, risk %, symbol trade mode, offset confirmation). Post-trade: `goldbot reconcile` verified — positions MATCH (0/0) 2026-11-17; deals-level reconciliation live and flags 3 broker-only deals honestly (known gap; backfill in the redo).
- [x] **Step 3 — Idempotency.** Demo order path enforces `decision_id:ENTRY` idempotency keys; reconcile command detects ledger-only/broker-only positions.
- [~] **Step 4 — Kill switch + reconnect drill.** PARTIAL — redo after intent-first order flow. Reconnect drill PASS (incident in ledger). Kill-switch guard-chain drill PASS. Kill-switch flatten drill PASS under live positions (2026-11-17): 2 positions closed, halt armed, MT5 flat, reconcile MATCH. Caveats to redo: the 2 fills were not tracked as orders/fills in the ledger (pre-fix FK crash); redo a clean drill once intent-first landing.
- [~] **Step 4c — Freshness automation (unblocked the demo).** `goldbot refresh-macro` (FRED + existing ALFRED vintages, atomic, validated) cleared the `broad_usd` gate on 2026-11-17; manifest staleness limits untouched; strategy still reports NO_TRADE on its own signal.
- [ ] Step 5 — Controlled demo fills → measure slippage/latency.
- [ ] Step 6 — Promotion gates 1–7 (standard_1k only).

## Dependencies / current gaps that block the demo

- [x] Broad USD FRED broad index exists at `data/raw/macro/DTWEXBGS.csv` and `data/raw/macro/ALFRED_DTWEXBGS.csv` (2026-11-13, 4 days old).
- [x] Fresh macro ingestion is built and wired (`goldbot refresh-macro`).
- [x] FRED published new prints; refresh verified — all weight-bearing macro inputs present and fresh (preflight `[PASS] macro_data` 2026-11-17).
- [x] `micro_50` profile exists; high risk override; not promotion-eligible; not substitute for a micro-lot broker.
- [x] M1 gate decision recorded; sign-off file exists.
- [ ] (Resolved 2026-11-17) Fresh `broad_usd` gap — closed via refresh; ALFRED vintage was the binding file.
- [!] No `standard_1k` demo run outside the booked crash-recovered tests yet.
- [!] No demo trade has been taken with the strategy's own signal (only manual override _testing_). As of 2026-11-17 the strategy reports NO_TRADE (score −0.585) — correctly no entry.
- [!] Pilot gate, reconnect drill not performed in live MT5 (reconnect-drill + kill-switch flatten drills both done 2026-11-17; MT5 demo).
- [x] Intent-first order flow: `demo-send` records the order intent (`PENDING_INTENT`) BEFORE the broker send; crash between send and ledger write recovers from broker state on the next run (recover by match, or refuse-to-resend on ambiguity); 4 new tests; duplicate resend impossible by construction
- [!] OOS-after-costs not demonstrated on real fills.

---

## Human decisions / confirmations pending

- [ ] Fresh `broad_usd` accepted (not an opportunity to widen the 5-day window without intent)
- [ ] Fresh WGC XLSX downloaded and retained monthly
- [ ] `micro_100` / `micro_50` overrides acknowledged as non-promotional
- [ ] Dashboard display is acceptable for current loopback use
- [ ] Repo hosting / CI choice (optional for M4+)
- [ ] Broker terms reviewed (automation, margin, instruments)
- [ ] Human sign-off for each gate, not just M1
