# M2 Strategy Core Evidence

This file records the M2 verification commands and the observed results. It is not a profitability
claim. The synthetic fixture is deterministic test data; the MT5 run is a demo-account data pull.

## 1. Test suite

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

Observed:

```text
52 passed
```

Key coverage:

- `tests/test_point_in_time.py` — a revised macro print is invisible before its knowledge time.
- `tests/test_fundamental.py` — sign conventions, missing/stale handling, zero-weight diagnostics.
- `tests/test_technical.py` — synthetic bars, insufficient warm-up, forming H4 bar exclusion, and
  a manifest-driven spread filter.
- `tests/test_fusion.py` — both-layer requirement, conflict policy, score minimums, and
  manifest-declared required triggers.
- `tests/test_overseer.py` — no origination, risk veto, hold behavior, exit precedence.
- `tests/test_exits.py` — both disagreement cases and risk veto immunity.
- `tests/test_strategy.py` — end-to-end decisions/trades, no-leverage sizing cap, manifest change.
- `tests/test_data_loaders.py` — FRED, Treasury, COT, and H4 aggregation.

## 2. Manifest-parameter change without a code edit

```powershell
.\.venv\Scripts\python.exe -m goldbot.cli demo-param `
  --manifest config/scope.gold.v1.yaml
```

Observed:

```text
baseline entry_threshold=0.2
  entries=4 trades=4 final_equity=10067.90
changed entry_threshold=0.99
  entries=0 trades=0 final_equity=10000.00
manifest parameter change altered behavior without a code edit
```

The only change is `strategy.fusion.entry_threshold` in a deep-copied manifest. No source file is
edited.

## 3. Synthetic end-to-end run

```powershell
.\.venv\Scripts\python.exe -m goldbot.cli run `
  --manifest config/scope.gold.v1.yaml `
  --source synthetic `
  --bars 1200 `
  --equity 10000 `
  --report reports/m2.run.json
```

Observed summary:

```text
bars: M15=1200 H4=75
initial_equity: 10000.00
final_equity: 10067.90
trade_count: 4
entry_decisions: 4
exit_decisions: 0
```

All four trades exited through the protective profit-take path with explicit `exit_reason` values.
Discretionary exit decisions are also recorded when the exit engine wins; the fixture happens to
hit protective exits first.

## 4. MT5 headless pull and cash-equivalent cap

The MT5 run does not depend on any chart being open or focused.

```powershell
.\.venv\Scripts\python.exe -m goldbot.cli run `
  --manifest config/scope.gold.v1.yaml `
  --source mt5 `
  --bars 500 `
  --context-bars 500 `
  --equity 1000 `
  --report reports/m2.mt5.1000.json
```

Observed:

```text
mt5 account: 106806652 FBS-Demo equity=1000.0
bars: M15=500 H4=500
initial_equity: 1000.00
final_equity: 1000.00
trade_count: 0
entry_decisions: 0
```

The report contains three decisions where fusion/overseer approved an entry but sizing refused it:

```text
no trade: risk-sized volume is below the broker minimum lot (0.01);
cash-equivalent/no-leverage account cannot hold the minimum notional
```

This is the correct no-leverage behavior: at ~$4,114 gold, the minimum 0.01 lot is about 1 oz, or
~$4,114 notional, which exceeds $1,000 equity.

For comparison, the same MT5 pull with a $10,000 simulated equity produces one trade:

```text
initial_equity: 10000.00
final_equity: 10005.34
trade_count: 1
entry_decisions: 1
exit_decisions: 1
trade pos-0001: LONG 0.02 4114.26 -> 4116.94 pnl=5.34
reason=trailing stop hit: price 4117.05 <= trail 4118.00
```

The $10,000 figure is a backtest input, not the live demo account equity.

## 5. Point-in-time no-lookahead

The test creates one `DFII10` observation with value `1.0` known on 2026-01-02 and a revised value
`2.0` known on 2026-02-01. At an as-of of 2026-01-15, the store returns only `1.0`; at 2026-02-02 it
returns the revision. The strategy therefore cannot use a future revision in a backtest.

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_point_in_time.py
```

## 6. Exit precedence evidence

The exit tests construct the two disagreement cases explicitly:

- Fundamentals deteriorate (`score = -0.50`) while technicals are healthy (`+0.50`):
  `thesis_invalidated` priority 80 wins.
- Technicals deteriorate (`score = -0.50`) while fundamentals are healthy (`+0.50`):
  `technical_or_profit_trail` priority 50 wins.
- A risk halt is present: `emergency_risk` priority 100 wins and `risk_veto_immune` is true.
- A hard stop and thesis invalidation are both present: `hard_or_trailing_stop` priority 90 wins.

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_exits.py tests/test_overseer.py
```

## 7. Validator remains green after the manifest extension

The M2 `strategy` section and `series_id` fields were added to the gold manifest. The M1 validator
still passes because it validates its declared items and does not invent strategy checks.

```powershell
.\.venv\Scripts\python.exe -m goldbot.cli validate `
  --manifest config/scope.gold.v1.yaml `
  --report reports/gold.v1.validation.json `
  --markdown reports/gold.v1.validation.md
```

Observed:

```text
overall: PASS
m1: PASS
runtime: PASS
counts: PASS=35, FAIL=0, HUMAN_JUDGMENT=2, SKIP=0, BLOCKED=0
```

Remaining HUMAN_JUDGMENT items: the absent manual WGC/IMF XLSX (weight 0, not consumed by the
overseer in v1) and human sign-off.
