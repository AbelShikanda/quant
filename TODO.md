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
- [~] Post-trade reconciliation against MT5 deals and positions (`goldbot reconcile` compares ledger vs broker positions, exit code non-zero on mismatch; verified MATCH on empty book; deals reconciliation still pending)
- [ ] Kill switch drill: flatten and halt (`goldbot kill-switch` built: demo-only, exact phrase `FLATTEN AND HALT`, writes `kill-switch.halt` next to ledger, agent loop checks the flag and exits, incident recorded; guard refusal tested; real flatten drill pending)
- [x] Kill switch guard-chain drill recorded (`goldbot kill-switch --drill`, 2026-11-17, demo account 106806652 verified; no orders sent; halt flag NOT armed; incident `kill_switch_drill` in ledger; promotion gate still wants a flatten drill under an open position)
- [x] Reconnect drill recorded (`goldbot reconnect-drill`, 2026-11-17, verdict PASS; incident + event in ledger)
- [ ] No-chart-assumption tests

### Logging and storage
- [x] SQLite WAL single-writer ledger
- [x] Snapshots table
- [x] Decisions / orders / fills / positions tables
- [x] Logs table with structured payload
- [ ] Parquet/DuckDB analytical exports
- [ ] Retention and backup policy

### Analytics
- [ ] Baseline definitions and stored results
- [ ] Live vs backtest drift monitor
- [ ] Per-source staleness dashboard data
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
- [ ] Inline refresh button for download-driven items (broad USD)
- [ ] Failure independence from MT5 (dashboard keeps working if MT5 is down)
- [ ] Authentication / remote access (not needed while loopback-only)

---

## M4 — Demo operation and promotion

- [ ] 3 months demo on `standard_1k`
- [ ] 100+ trades recorded
- [ ] Positive OOS after costs
- [ ] Drawdown within 0.25%/trade, 1.5% daily, 8% max drawdown
- [ ] Kill switch drill passed
- [ ] Reconnect drill passed
- [ ] Incident log / reviews complete
- [ ] Human sign-off recorded
- [ ] Only then can we consider real-money operation

---

## Execution-quality and trust gaps (needed for live trading, not just demo)

### P0 — must exist before any live trading
- [ ] Economic calendar with blackout windows (FOMC, CPI, PCE, NFP, Treasury auctions, Fed speakers, holidays)
- [~] Fill quality: slippage now measured on demo fills (`slippage_points` recorded in ledger fills); latency and fill probability at size not yet measured
- [ ] Tick/depth backtest to verify trades would have filled where expected
- [ ] Stop quality under news stress (gap-through-stop simulations)
- [~] Always-on supervisor/health-check for MT5 connection and bot process (agent loop exists with stop file + halt flag; no heartbeat monitor yet)

### P1 — strong edge needed for an honest demo record
- [~] Freshness automation: `goldbot refresh-macro` re-downloads FRED + existing ALFRED vintage files atomically and validates them before touching `data/raw/macro`; wired as a command the owner/agent can call (scheduler/Task Scheduler next step). As of 2026-11-17: DFII10/DTWEXBGS 11-13 (4d, fresh), T10YIE 11-16 (1d), ALFRED_DTWEXBGS 11-16 vintage (4d) — manifest stale limits untouched
- [ ] Risk-correlation checks (e.g. correlated USD long/short vs gold positions)
- [ ] Economic surprise factor (consensus vs actual)
- [ ] Order-book/depth or spread-history proxy for fill probability at size
- [ ] Traceability: at least one full human reconstruction proof from ledger logs
- [ ] Regulatory/compliance memo for the broker terms (leverage, confirmation, permitted instruments)

### P2 — enhancements
- [ ] Options/positioning diagnostic (futures already captured; no options-open-interest yet)
- [ ] Live vs backtest drift monitoring with alerting
- [ ] SELL-first-class review report for exits

---

## Recommended route — execution infrastructure progress (updated 2026-11-17)

Order: 1 broker specs → 2 pre/post-trade checks → 3 idempotency/reconciliation → 4 kill switch + reconnect drill → 5 controlled demo fills → 6 promotion gates.

- [x] **Step 1 — Broker spec capture.** `goldbot broker-specs --symbol XAUUSD` → `reports/broker_specs.json` (verified live: tick 0.01, contract 100, min lot 0.01, stops_level 0, spread float).
- [ ] Step 1b — Broker terms review. Human item; owner must read the FBS client agreement for EA/Python automation on XAUUSD before any non-demo use. Web search surfaced only generic MetaTrader algotrading docs, not FBS-specific terms — must be done by the owner.
- [~] **Step 2 — Pre- and post-trade checks.** Pre-trade checks live in the demo preflight (spread, stop distance, notional cap, risk %, symbol trade mode, offset confirmation). Post-trade: `goldbot reconcile` (positions, read-only, verified MATCH). Deals-level reconciliation still pending.
- [x] **Step 3 — Idempotency.** Demo order path enforces `decision_id:ENTRY` idempotency keys; reconcile command detects ledger-only/broker-only positions.
- [~] **Step 4 — Kill switch + reconnect drill.** Reconnect drill PASS and recorded (incident in ledger). Kill-switch guard-chain drill PASS and recorded (no orders, halt flag not armed). A flatten drill under an open position still needs the owner's go-ahead and an open demo position.
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
- [!] Pilot gate, kill-switch, reconnect drills not performed in live MT5.
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
