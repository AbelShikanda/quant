# M2 Strategy Contracts

These are the fixed interfaces between the M2 data pipeline, fundamental layer, technical layer,
fusion, overseer, exits, and backtest harness. The Python definitions live in
`goldbot/contracts.py`. A module may add diagnostic fields, but it must not silently change the
meaning of a field listed here.

## Global invariants

- All timestamps are timezone-aware **UTC**.
- A bar's `time_utc` is its **open** time.
- A decision made at the close of an M15 bar may use that bar, but a forming H4 bar must be excluded.
- Macro values are keyed by **knowledge time**, not observation date. A revised print is invisible
  until its vintage/release time.
- Missing weight-bearing macro inputs are reported as missing or stale. They are never
  forward-filled.
- The overseer never originates an entry. It can approve, veto, hold, or arbitrate an exit.
- Risk exits are veto-immune.

## Market data

### `Bar`

| Field | Meaning |
|---|---|
| `time_utc` | Bar open time, UTC. |
| `open`, `high`, `low`, `close` | OHLC prices. |
| `tick_volume` | Broker tick volume. |
| `spread_points` | Spread in broker points. |
| `timeframe` | `M15`, `H4`, etc. |

`BarSeries.closed_window(decision_time, bar_minutes)` returns only bars whose close is at or before
the decision time. The technical engine uses this for both M15 and H4.

## Macro data

### `MacroObservation`

| Field | Meaning |
|---|---|
| `series_id` | Stable series key, e.g. `DFII10`, `GLD_TONNES`, `COT_GOLD_NET`. |
| `knowledge_time_utc` | First time this value was knowable. |
| `observation_date` | Reference date of the print, when available. |
| `value` | Numeric value. |
| `source_id` | Source identifier from the manifest. |
| `vintage` | Optional vintage label. |

`PointInTimeStore.latest(series_id, as_of)` and `SQLiteStore.latest_macro(series_id, as_of)` only
return observations with `knowledge_time_utc <= as_of`.

## Fundamental layer

### `FundamentalComponent`

| Field | Meaning |
|---|---|
| `input_id` | Manifest fundamental input ID, e.g. `real_yield_10y`. |
| `series_id` | Series actually read. |
| `value` | Current transformed value, or `None` when unavailable. |
| `signal` | Signed contribution direction in `[-1, 1]`. |
| `weight` | Manifest weight. |
| `contribution` | `weight * signal`. |
| `transform` | Manifest transform name. |
| `sign` | `positive`, `negative`, or `contextual`. |
| `stale` | True when the latest observation is older than the manifest limit. |
| `source_id`, `knowledge_time_utc` | Provenance of the latest observation. |
| `note` | Human-readable transform/status note. |

### `FundamentalView`

| Field | Meaning |
|---|---|
| `as_of_utc` | Decision time. |
| `score` | Sum of component contributions, clipped to `[-1, 1]`. |
| `components` | Per-input components. |
| `diagnostics` | Weight-zero diagnostics, including central bank and breakeven. |
| `missing_inputs` | Weight-bearing inputs with no usable point-in-time value. |
| `stale_inputs` | Weight-bearing inputs older than their limit. |
| `is_valid` | True only when no weight-bearing input is missing or stale. |
| `reasons` | Explainable scoring and failure reasons. |
| `source_versions` | Source/vintage strings for reproducibility. |

The v1 weights are real yield `0.55`, broad USD `0.20`, ETF flows `0.15`, COT `0.10`, central bank
`0.00`, breakeven `0.00`. Central bank is **not consumed by the overseer in v1** and must carry the
required label from `AGENTS.md`.

## Technical layer

### `TechnicalView`

| Field | Meaning |
|---|---|
| `as_of_utc` | Decision time. |
| `score` | Weighted technical score in `[-1, 1]`. |
| `components` | `trend`, `momentum`, `structure`, `volatility` signals. |
| `indicators` | EMA stack, RSI, ATR, ATR%, prior high/low, H4 EMAs, spread. |
| `entry_triggers` | Named booleans: `h4_context`, `m15_trigger`, `atr_filter`, `spread_filter`, `no_news_blackout`. |
| `is_valid` | False when warm-up or data requirements are not met. |
| `reasons` | Explainable component/trigger reasons. |
| `bars_used`, `context_bars_used` | Closed-bar counts. |

The entry trigger list is read from `technical.entry_requires` in the manifest. The fusion layer
checks only the declared triggers.

## Fusion

### `FusionView`

| Field | Meaning |
|---|---|
| `combined_score` | `fundamental_weight * fundamental.score + technical_weight * technical.score`. |
| `fundamental_contribution`, `technical_contribution` | Weighted contributions. |
| `entry_candidate` | True only when both layers are valid, the conflict policy allows it, the score minimums/threshold pass, and the declared triggers pass. |
| `conflict` | True when the layer scores have opposite signs. |
| `reasons` | Threshold, conflict, validity, and trigger explanations. |

`fusion.entry_requires_both`, `fusion.conflict_policy`, `strategy.fusion.entry_threshold`,
`strategy.fusion.fundamental_min`, and `strategy.fusion.technical_min` are all read from the
manifest.

## Overseer

### `OverseerDecision`

| Field | Meaning |
|---|---|
| `decision` | `ENTER_LONG`, `HOLD`, or `NO_TRADE`. |
| `side` | `LONG` or `NONE`. |
| `confidence` | `[0, 1]`; zero for non-entries. |
| `verdict` | `approved`, `hold`, `no_trade`, or `vetoed_risk`. |
| `vetoes` | Risk veto identifiers. |
| `rationale` | Human-readable referee rationale. |
| `source_views` | Scores used for the arbitration record. |

The overseer only returns `ENTER_LONG` when `FusionView.entry_candidate` is true and no risk veto is
active. It cannot originate an entry from raw indicators or fundamentals.

## Exits

### `ExitCandidate`

| Field | Meaning |
|---|---|
| `rule_id` | Manifest exit rule ID. |
| `priority` | Manifest priority. |
| `action` | `close`, `reduce_or_close`, `trail_or_close`, `flatten_and_halt`, or `notify_only`. |
| `reason` | Trigger explanation. |
| `evidence` | Numbers used to trigger the rule. |

### `ExitDecision`

| Field | Meaning |
|---|---|
| `should_exit` | True when the winning action is not `notify_only`. |
| `rule_id`, `priority`, `action`, `reason` | Winning candidate. |
| `candidates` | All candidates, sorted by priority. |
| `risk_veto_immune` | True when an emergency/risk candidate was present. |

### v1 exit precedence

| Priority | Rule |
|---:|---|
| 100 | `emergency_risk` |
| 90 | `hard_or_trailing_stop` |
| 80 | `thesis_invalidated` |
| 70 | `thesis_weakened` |
| 60 | `time_stop` |
| 50 | `technical_or_profit_trail` |
| 40 | `profit_take` |
| 30 | `advisory` |

The M2 backtest runner checks protective stop/take orders intrabar with a stop-first assumption.
Discretionary exits are decided at bar close and filled at the next bar open with modeled costs. The
M3 paper replay fills a discretionary exit at the decision-bar close plus half-spread and slippage;
it uses the same stop-first protective-exit convention.

## Position and risk

### `PositionState`

Tracks `position_id`, `side`, `volume`, `open_price`, `open_time_utc`, `stop_loss`,
`take_profit`, `highest_price`, `bars_in_trade`, `entry_atr`, `entry_score`, and `thesis_valid`.

### `RiskState`

Tracks `equity`, `starting_equity`, `daily_start_equity`, `peak_equity`, `daily_pnl`,
`current_drawdown_pct`, `daily_halt`, `max_drawdown_halt`, and `kill_switch`.

Risk limits are read from the manifest: `0.25%` per trade, `1.5%` daily loss halt, `8%` maximum
drawdown flatten/halt.

## Decision record

### `StrategyDecision`

| Field | Meaning |
|---|---|
| `decision_id` | Deterministic `scope_id:as_of_utc` identifier. |
| `as_of_utc` | Decision time. |
| `decision_type` | `ENTER_LONG`, `HOLD`, `EXIT`, or `NO_TRADE`. |
| `side` | `LONG` or `NONE`. |
| `confidence` | Decision confidence. |
| `combined_score` | Fusion score. |
| `fundamental`, `technical`, `fusion` | Full input views. |
| `overseer` | Referee decision. |
| `exit_decision` | Exit arbitration, when a position exists. |
| `position_size`, `stop_loss`, `take_profit` | Entry plan, when approved. |
| `reasons` | Human-readable reasons. |

`StrategyDecision.to_dict()` is JSON-safe and is the persisted payload. Every order-capable decision
must be reproducible from the persisted views, manifest hash, and source versions.

## Storage

- SQLite is the single-writer transactional store. `config/storage.schema.sql` declares snapshots,
  decisions, orders, fills, positions, logs, incidents, and the M2 macro/run/decision tables.
- WAL mode is enabled by `SQLiteStore`. `goldbot/ledger.py` adds a `.writer.lock` file and refuses to
  open a second writer for the same ledger.
- DuckDB is the analytical OHLCV store. `DuckDBBarStore` writes a `bars` table and can export
  Parquet.
- The strategy process owns the SQLite write connection. No other component may write concurrently.
  Dashboard/report readers use separate read-only connections.

## Paper execution contract (M3 slice)

`goldbot/paper.py` is a deterministic **paper/dry-run replay**, not a broker router.

- It replays closed M15 bars through the same `StrategyEngine` used by the backtest.
- A decision-bar market order fills at that bar's close plus half-spread and configured slippage.
- Protective stops/take-profits use the next bar's high/low with a stop-first assumption; the
  order/fill price is the stop/target or the gap open. A protective exit or an end-of-replay
  force-close creates an explicit synthetic `EXIT` decision, then an order, fill, and position close,
  so the audit chain is complete.
- Orders use deterministic IDs derived from the decision ID and unique idempotency keys. Paper orders
  have `state = PAPER_FILLED` and a null broker ticket.
- One paper run per ledger is the default. The ledger refuses to mix manifest hashes or append a
  second run ID; reusing the same run ID is allowed for an idempotent restart. Use a new ledger for a
  fresh replay.
- Every decision is written to `snapshots`, `decisions`, and `strategy_decisions`. Paper executions
  are written to `orders`, `fills`, and `positions`. Structured events are written to `logs`.
- The report carries `mode = paper_replay`, the ledger path, the fill model, and the protective-exit
  model. The dashboard shows paper orders and states that no broker orders were sent.
- No `order_send` call, no MT5 deal reconciliation, and no live polling loop exist in this slice.
  Those remain the next M3 execution layer.

## Manifest-driven parameters

The M2 code reads these from the manifest rather than hardcoding them:

- fundamental weights, signs, transforms, z-score lookback, minimum observations, stale limits,
- technical indicator periods, component weights, RSI entry band, ATR floor/ceiling, spread limit,
  structure buffer, session-filter hours/weekdays, declared entry triggers,
- fusion weights, entry-requires-both, conflict policy, score minimums, entry threshold,
- exit thresholds, ATR stop/trail multiples, profit-take R, time-stop bars, precedence priorities,
- sizing contract size, lot step, minimum lot, maximum notional ratio, and minimum-lot risk cap,
- backtest point value, commission, and slippage,
- market execution/context timeframes.

## Account profiles and leverage

The base manifest uses a leveraged account model with a **5:1 bot notional cap**. The standard
`config/profiles/standard_1k.yaml` profile keeps the 0.25% base risk and adds a **1.25% minimum-lot
risk guard**. The guard allows the broker minimum lot only when the stop risk implied by that lot is
at or below 1.25% of equity. The strategy always reports the risk implied by the final lot-rounded
volume, not the pre-rounding risk budget.

XAUUSD is normally quoted with a standard lot of 100 oz. On the current FBS-Demo contract, the
minimum `0.01` lot is 1 oz. At ~$4,114 gold and the latest MT5 ATR, that lot risks roughly
$9.37-$10.66, or about **0.94%-1.07% of $1,000**. That fits the 1.25% guard, so the $1,000 account
can trade 0.01 lot without changing the 0.25% base risk for larger accounts.

The same 0.01 lot on a **$100** account risks roughly **9.4%-10.7%** of equity. The standard profile
therefore returns `NO_TRADE` with an explicit min-lot-risk reason. `config/profiles/micro_100.yaml`
is an explicit **high-risk override** that raises the per-trade cap to 11%, the daily halt to 12%,
the max drawdown to 25%, and the bot notional cap to 50:1 so the minimum lot can be held. It is
marked `high_risk_override: true`, `promotion_eligible: false`, prints a warning, and shows a red
banner on the dashboard. One normal stop is a double-digit account loss. The risk-preserving route
for a $100 account is a micro-lot broker (0.001 lot or a 1 oz-per-lot contract), not this override.

## Walk-forward and baseline controls

`goldbot/walkforward.py` splits the execution series into non-overlapping test windows. Each fold has:

- a train window used only as warm-up/context; entries are suppressed there,
- a half-open test window `[test_start, test_end)` where entries are allowed,
- chained equity: each fold starts at the previous fold's final equity,
- per-fold metrics and an aggregate metric set built from all test trades and decisions.

The train window is not used to fit parameters yet. It exists so the strategy has its normal lookback
and so a future fit step can be added without changing the split contract. `goldbot/baselines.py`
computes cost-free, unlevered, 1x-notional controls over the same test windows: buy-and-hold, a simple
EMA trend control (50/200 by default), and random-entry mean/median/p05/p95. The controls are sanity
checks, not strategy results; they do not model spread, commission, or slippage.

## M2 limitations

- ALFRED `realtime_start` vintages and CBOE GVZ are wired into the loader; automated ALFRED
  download/replay scheduling, iShares IAU (deferred to v2), manual WGC/IMF XLSX strategy ingestion
  (still HUMAN_JUDGMENT, weight 0, not consumed by the overseer), and raw source snapshot hashing
  are not yet wired.
- The session-filter mechanism is implemented but disabled in the manifest until the owner confirms
  the session hours; news blackout is an explicit input flag.
- Walk-forward/OOS splitting and baseline controls are implemented, but the train window is warm-up
  only; no parameter fitting or walk-forward optimization is performed yet. The baselines are
  cost-free controls, not after-cost strategy comparisons.
- The dashboard is a read-only static HTML renderer of a saved JSON report. It does not connect to
  MT5, place orders, or change scope. A live FastAPI dashboard is M3 work.
- The demo backtest is a synthetic fixture, not evidence of real-world profitability.

## M3 paper-slice limitations

- There is no MT5 broker order router, no `order_send` path, no live polling loop, no post-trade
  reconciliation against MT5 deals/positions, no kill switch, and no reconnect drill yet.
- Paper orders and fills are model events, not broker fills. `broker_order_ticket` and
  `broker_deal_ticket` are null.
- The paper replay is deterministic over the supplied bars. It is execution/logging evidence, not a
  live demo result and not profitability evidence.
- The static dashboard is still a report renderer. The live read-only backend and the wizard remain
  M3 work.
