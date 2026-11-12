# AGENTS.md — Gold Quantamental Bot Guardrails

This file is the operating contract for any human or AI agent working in this repository.
`notes.txt` is the authoritative product brief. If this file conflicts with `notes.txt`, stop and ask
the human owner before changing scope.

## 1. Mission

Build a **quantamental XAUUSD** trading bot in which:

- **Gold macro fundamentals define WHAT to consider.**
- **Technicals define WHEN to act.**
- The **overseer** is a rule-based referee. It arbitrates; it does not originate trades.
- **Exits are first-class**, especially SELL decisions. A bot that cannot exit well is not finished.

## 2. Hard scope for v1

- Instrument: **XAUUSD spot gold only**.
- Direction: **long-only**.
- No options, no futures, no equities, no crypto, no cross-instrument relative value.
- Execution timeframe: **M15**.
- Higher-timeframe context: **H4**.
- Account model: **leveraged, with a 5:1 bot notional cap** for the standard profile (approved by
  the human owner on 2026-11-12).
- Notional exposure must never exceed 5x equity for the standard profile.
- Demo first. **No real-money trading** until every promotion gate in §8 is met.

## 3. Risk limits (non-negotiable defaults)

| Limit | Standard profile default |
|---|---|
| Risk per trade | **0.25% of equity** |
| Minimum-lot risk guard | **1.25% of equity** |
| Bot leverage cap | **5:1** |
| Daily loss halt | **1.5% of equity** |
| Maximum drawdown flatten/halt | **8% of equity** |

Risk exits are **veto-immune**. No fundamental, technical, dashboard, or operator action may
override an emergency/risk exit.

### 3a. High-risk `micro_100` profile (explicit owner override)

The human owner also requested that a $100 account be able to hold the broker minimum lot. The
`config/profiles/micro_100.yaml` profile is a separate, warning-labeled override:

| Limit | `micro_100` override |
|---|---|
| Risk per trade | **11% of equity** |
| Daily loss halt | **12% of equity** |
| Maximum drawdown flatten/halt | **25% of equity** |
| Bot leverage cap | **50:1** |

This profile is **not promotion-eligible**, does not alter the standard limits above, and is not a
substitute for a micro-lot broker. One normal stop is a double-digit account loss. The standard
profile remains the only profile eligible for the §8 promotion gates.

## 4. Architecture boundaries

- **MT5 is both the price source and the execution venue.** The bot must not assume any chart is
  open, selected, or focused.
- **The supervisor is the only component allowed to apply scope changes.** The dashboard must never
  connect to MT5 directly and must never change scope.
- The dashboard is read-only with respect to trading and scope. It may display state and request a
  human decision, but it may not write to MT5 or mutate the active scope.
- The bot process must be headless and resilient to terminal focus changes, chart changes, profile
  changes, and reconnects.

## 5. Data rules

- **Zero paid data.** Free sources only.
- Scraping is expected. Every source must declare its license/terms-of-service risk.
- Prefer official CSV/XLSX/ZIP endpoints over fragile HTML scraping.
- Every source must declare:
  - point-in-time / revision behavior,
  - knowledge time,
  - coverage history,
  - cadence,
  - gap notes,
  - ToS/legal risk.
- Do not silently forward-fill missing macro data. Record staleness and coverage gaps.
- Do not use future revisions in backtests. Use vintage data where available.

### Central-bank gold data

Central-bank gold holdings are **manual monthly WGC/IMF XLSX** files in `data/manual/wgc/`.

- Validation class: **HUMAN_JUDGMENT**.
- Weight in v1 entry score: **0.00**.
- **NOT consumed by the overseer (§9) in v1.**
- The validator performs mechanical file checks only: presence, parseability, schema, age, and
  reminder/refresh event.
- The report must carry this exact label:
  **“derived from human-downloaded file; weight in entry score = 0; NOT consumed by the overseer (§9) in v1.”**
- Do not fabricate, synthesize, or commit the XLSX. The human owner downloads it.

## 6. Storage rules

- **SQLite + WAL + single writer** for snapshots, decisions, orders, fills, and logs.
- **Parquet/DuckDB** for OHLCV and analytical tables.
- No PostgreSQL in v1.
- The single-writer rule is a correctness rule, not a performance suggestion.
- Every decision that can lead to an order must be reproducible from persisted inputs and versions.

## 7. Build order

1. Manifest (`config/scope.gold.v1.yaml`).
2. Headless validator.
3. Strategy core.
4. Logging, analytics, dashboard, wizard.

Do not build later layers on an unvalidated manifest. Do not add strategy logic to the validator.
The validator answers: **“Is this scope honest, complete, and mechanically checkable?”**

## 8. Promotion gates

No real-money trading until all of the following are true:

1. v1 is complete.
2. At least **3 months of demo trading**.
3. At least **100+ trades**.
4. Positive out-of-sample performance **after costs**.
5. Drawdown stayed within the §3 limits.
6. Kill-switch and reconnect drills passed.
7. Human sign-off recorded.

## 9. Honesty and reporting rules

Every validation item must be labeled as one of:

- **MECHANICALLY_VALIDATED** — a machine checked it and the evidence is in the report.
- **HUMAN_JUDGMENT** — a human must decide; the validator may only check mechanical proxies.
- **NOT_VALIDATABLE_AT_DESIGN_TIME** — cannot be settled until runtime or out-of-sample data exists.

Never report a human-judgment item as mechanically validated. Never hide a failed runtime preflight
behind a passing manifest check. If a check cannot run, say **BLOCKED** or **HUMAN_JUDGMENT**, not
**PASS**.

## 10. Change discipline

- Do not edit `notes.txt`.
- Keep changes small and reviewable.
- Add or update tests for validator behavior.
- Do not commit secrets, account credentials, broker files, or manual XLSX data.
- Do not change risk defaults, account model, timeframes, or scope without explicit human approval.
- When in doubt, stop and ask.
