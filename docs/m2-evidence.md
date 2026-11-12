# M2 Strategy Core Evidence

This file records the M2 verification commands and the observed results. It is not a profitability
claim. The synthetic fixture is deterministic test data; the MT5 runs are demo-account data pulls.
The `micro_100` profile is an explicit high-risk override, not a promotion candidate.

## 1. Test suite

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

Observed:

```text
66 passed
```

Key coverage:

- `tests/test_point_in_time.py` — a revised macro print is invisible before its knowledge time.
- `tests/test_fundamental.py` — sign conventions, missing/stale handling, zero-weight diagnostics.
- `tests/test_technical.py` — synthetic bars, insufficient warm-up, forming H4 bar exclusion, a
  manifest-driven spread filter, and an enabled session filter that blocks weekends and allows a
  weekday session.
- `tests/test_fusion.py` — both-layer requirement, conflict policy, score minimums, and
  manifest-declared required triggers.
- `tests/test_overseer.py` — no origination, risk veto, hold behavior, exit precedence.
- `tests/test_exits.py` — both disagreement cases and risk veto immunity.
- `tests/test_strategy.py` — end-to-end decisions/trades, standard 1k sizing, $100 standard-profile
  refusal, micro-100 minimum-lot sizing, profile overlay immutability, Sharpe/Sortino/exposure
  metrics, and manifest change.
- `tests/test_dashboard.py` — high-risk banner, read-only notice, saved-report rendering, and CLI.
- `tests/test_data_loaders.py` — FRED, ALFRED `realtime_start`, ALFRED-over-FRED precedence, CBOE
  GVZ, Treasury, COT, and H4 aggregation.
- `tests/test_stress.py` — gap-through-stop fills at the gap open, gap-through-take-profit fills at
  the gap open, and missing/stale macro data is reported rather than forward-filled.

## 2. Manifest-parameter change without a code edit

```powershell
.\.venv\Scripts\python.exe -m goldbot.cli demo-param `
  --manifest config/scope.gold.v1.yaml `
  --bars 1200 `
  --equity 10000
```

Observed:

```text
account_profile: base
baseline entry_threshold=0.2
  entries=4 trades=4 final_equity=10186.72
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
  --report reports/m2.run.leveraged.json `
  --dashboard reports/m2.dashboard.html
```

Observed summary:

```text
account_profile: base (high_risk=False, promotion_eligible=True)
bars: M15=1200 H4=75
initial_equity: 10000.00
final_equity: 10186.72
trade_count: 4
entry_decisions: 4
exit_decisions: 0
```

All four trades exited through the protective profit-take path with explicit `exit_reason` values.
Discretionary exit decisions are also recorded when the exit engine wins; the fixture happens to
hit protective exits first.

## 4. Standard $1,000 profile on MT5

The MT5 run does not depend on any chart being open or focused.

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

Observed:

```text
mt5 account: 106806652 FBS-Demo equity=1000.0
account_profile: standard_1k (high_risk=False, promotion_eligible=True)
bars: M15=500 H4=500
initial_equity: 1000.00
final_equity: 1002.67
trade_count: 1
entry_decisions: 1
exit_decisions: 1
trade pos-0001: LONG 0.01 4114.26 -> 4116.94 pnl=2.67
reason=trailing stop hit: price 4117.05 <= trail 4118.00
```

The entry decision records the actual lot-rounded risk:

```text
volume 0.01 lots, risk 9.37 (0.9366% of equity), notional 4114.27 <= 5000.00
sizing: sized at broker minimum lot 0.01; actual stop risk 0.9366% <= min-lot cap 1.2500%
```

The 5:1 notional cap is $5,000; the minimum-lot notional is $4,114.27, so the trade fits. The
minimum-lot risk guard is 1.25%; the observed risk is 0.9366%, so the $1,000 account can trade
without changing the 0.25% base risk for larger accounts.

## 5. High-risk $100 micro profile on MT5

This profile exists only because the user explicitly asked to allow the broker minimum lot on a
$100 account. It is **not promotion-eligible**.

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

Observed:

```text
WARNING: account profile micro_100 is a HIGH RISK OVERRIDE; it is not promotion-eligible.
mt5 account: 106806652 FBS-Demo equity=1000.0
account_profile: micro_100 (high_risk=True, promotion_eligible=False)
bars: M15=500 H4=500
initial_equity: 100.00
final_equity: 102.67
trade_count: 1
entry_decisions: 1
exit_decisions: 1
trade pos-0001: LONG 0.01 4114.26 -> 4116.94 pnl=2.67
reason=trailing stop hit: price 4117.05 <= trail 4118.00
```

The entry decision records:

```text
volume 0.01 lots, risk 9.37 (9.3663% of equity), notional 4114.27 <= 5000.00
sizing: sized
```

The 50:1 notional cap is $5,000; the minimum-lot notional is $4,114.27, so the minimum lot fits.
The actual stop risk is 9.3663% of the $100 account. The profile caps per-trade risk at 11%, the
daily halt at 12%, and the max drawdown at 25%. A single normal stop is a double-digit loss. The
dashboard shows a red high-risk banner and the profile is marked not promotion-eligible. The
risk-preserving route for a $100 account is a micro-lot broker, not this override.

## 6. Read-only dashboard

The dashboard is a static HTML renderer of a saved JSON report. It does not import MetaTrader5, does
not connect to a broker, does not place orders, and cannot change scope.

```powershell
.\.venv\Scripts\python.exe -m goldbot.cli dashboard `
  --report reports/m2.mt5.run.1000.json `
  --output reports/m2.dashboard.standalone.html `
  --max-decisions 200
```

The `run` command can also emit it directly with `--dashboard <path>`. The generated page contains:

- the account profile, manifest hash, run id, and scope id,
- a red/green risk banner,
- equity, return, drawdown, trade, win-rate, profit-factor, expectancy, Sharpe, Sortino, and
  max-entry-exposure cards,
- the account model, leverage cap, notional cap, per-trade risk, min-lot risk cap, daily halt, and
  max drawdown,
- data-quality counts,
- the equity curve from closed trades,
- the trades table and the last N explained decisions,
- an explicit read-only/no-MT5 footer.

`reports/` is git-ignored; the tracked evidence is this file and the code/tests.

## 7. Metrics, exposure, and stress tests

The report now includes:

- `sharpe_daily_annualized` and `sortino_daily_annualized`, computed from daily realized trade PnL
  and annualized with `sqrt(252)`. They stay `null` until at least 20 trading days have closed
  trades, so a four-trade fixture cannot produce a misleading annualized ratio.
- `daily_return_count`, `average_entry_notional`, `max_entry_notional`,
  `average_entry_exposure_pct`, and `max_entry_exposure_pct`.

Observed exposure on the current MT5 contract:

```text
standard $1,000: entry notional 4,114.27, max entry exposure 411.43% (4.11:1), cap 500%
micro_100 $100:  entry notional 4,114.27, max entry exposure 4,114.27% (41.14:1), cap 5,000%
```

Both are inside the configured notional caps. The `micro_100` exposure is large in percentage terms
because a $100 account is holding the same 1 oz minimum lot as the $1,000 account.

Stress tests:

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_stress.py
```

- A bar that opens below the long stop exits at the gap open, not the stop price.
- A bar that opens above the long take-profit exits at the gap open, not the target price.
- An empty macro store makes the fundamental view invalid and reports missing inputs; it does not
  forward-fill.
- A stale observation is reported in `stale_inputs` and makes the view invalid.

## 8. Point-in-time no-lookahead

The test creates one `DFII10` observation with value `1.0` known on 2026-01-02 and a revised value
`2.0` known on 2026-02-01. At an as-of of 2026-01-15, the store returns only `1.0`; at 2026-02-02 it
returns the revision. The strategy therefore cannot use a future revision in a backtest.

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_point_in_time.py
```

## 9. Exit precedence evidence

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

## 10. Validator remains green after the leverage/profile extension

```powershell
.\.venv\Scripts\python.exe -m goldbot.cli validate `
  --manifest config/scope.gold.v1.yaml `
  --report reports/gold.v1.validation.json `
  --markdown reports/gold.v1.validation.md
```

Observed:

```text
manifest_hash: d531a2492322e8aba241246ea8c5b883144075d31cfb3d34f99c0039d6d1eec8
overall: PASS
m1: PASS
runtime: PASS
counts: PASS=35, FAIL=0, HUMAN_JUDGMENT=2, SKIP=0, BLOCKED=0
```

The validator now accepts the approved leveraged account model with a 5:1 cap and checks the
standard profile's 1.25% minimum-lot risk guard. The `micro_100` overlay is intentionally outside
the M1 safe scope: its 11% per-trade risk, 12% daily halt, 25% max drawdown, and 50:1 cap would fail
the safe-scope risk checks if validated as the base manifest. It is run only as an explicit,
warning-labeled high-risk override.

Remaining HUMAN_JUDGMENT items: the absent manual WGC/IMF XLSX (weight 0, not consumed by the
overseer in v1) and human sign-off.
