# M3 Paper Execution and Ledger Evidence

This file records the first M3 slice: a persistent SQLite ledger and a deterministic paper/dry-run
execution replay. It is **not** profitability evidence, and it is **not** a live demo-trading result.
No broker order was sent. The `paper` command has no `order_send` path.

## 1. Test suite

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

Observed:

```text
89 passed
```

New coverage:

- `tests/test_ledger.py` — WAL mode, single-writer lock, idempotent decision/order/fill writes,
  decision/order/fill/position round trips, closed-trade reads, writer-lock release, refusal to mix
  manifest hashes, and refusal to append a second run ID unless explicitly allowed.
- `tests/test_paper.py` — replay-time selection, paper replay persistence, order/fill/position
  counts, no broker tickets, protective-exit stop-first behavior, end-of-replay force-close with an
  explicit synthetic exit, and the `paper` CLI report/dashboard.
- `tests/test_dashboard.py` — paper order rendering, `paper_replay` mode, and the no-broker-order
  notice.
- `tests/test_live_dashboard.py` — read-only SQLite access, no byte changes to the ledger, missing-
  ledger handling, active-versus-finished stale semantics, loopback-only host rejection, HTTP
  `/`, `/api/state`, and `/health` responses, and rendering without JavaScript.

## 2. Synthetic standard $1,000 paper replay

```powershell
.\.venv\Scripts\python.exe -m goldbot.cli paper `
  --manifest config/scope.gold.v1.yaml `
  --account-profile config/profiles/standard_1k.yaml `
  --source synthetic `
  --bars 1600 `
  --replay-bars 200 `
  --equity 1000 `
  --ledger reports/m3.paper.synthetic.1000.ledger.sqlite `
  --report reports/m3.paper.synthetic.1000.json `
  --dashboard reports/m3.paper.synthetic.1000.html
```

Observed:

```text
mode: paper_replay (no broker orders were sent)
account_profile: standard_1k (high_risk=False, promotion_eligible=True)
replay_decisions: 203
orders: 6 fills: 6
initial_equity: 1000.00
final_equity: 1012.70
trade_count: 3
entry_decisions: 3
exit_decisions: 3
```

All three paper trades closed on `profit_take`. The synthetic fixture is deterministic test data,
not a market result.

## 3. Synthetic high-risk $100 paper replay

```powershell
.\.venv\Scripts\python.exe -m goldbot.cli paper `
  --manifest config/scope.gold.v1.yaml `
  --account-profile config/profiles/micro_100.yaml `
  --source synthetic `
  --bars 1600 `
  --replay-bars 200 `
  --equity 100 `
  --ledger reports/m3.paper.synthetic.100.ledger.sqlite `
  --report reports/m3.paper.synthetic.100.json `
  --dashboard reports/m3.paper.synthetic.100.html
```

Observed:

```text
account_profile: micro_100 (high_risk=True, promotion_eligible=False)
replay_decisions: 203
orders: 6 fills: 6
initial_equity: 100.00
final_equity: 125.40
trade_count: 3
```

This is the high-risk 11%-per-trade override, not a promotion candidate. The 25.40% synthetic return
must not be presented as evidence of a safe or repeatable $100 strategy.

## 4. MT5 standard $1,000 paper replay

```powershell
.\.venv\Scripts\python.exe -m goldbot.cli paper `
  --manifest config/scope.gold.v1.yaml `
  --account-profile config/profiles/standard_1k.yaml `
  --source mt5 `
  --bars 1600 `
  --context-bars 500 `
  --replay-bars 1600 `
  --equity 1000 `
  --ledger reports/m3.paper.mt5.1000.ledger.sqlite `
  --report reports/m3.paper.mt5.1000.json `
  --dashboard reports/m3.paper.mt5.1000.html
```

Observed:

```text
mt5 account: 106806652 FBS-Demo equity=1000.0
mode: paper_replay (no broker orders were sent)
account_profile: standard_1k (high_risk=False, promotion_eligible=True)
replay_decisions: 1600
orders: 2 fills: 2
initial_equity: 1000.00
final_equity: 1002.55
trade_count: 1
entry_decisions: 1
exit_decisions: 1
paper trade: LONG 0.01 4114.39 -> 4116.94 pnl=2.55
exit_reason: trailing stop hit
```

The two orders are the paper entry and the strategy exit (a trailing-stop rule). The ledger contains
the full decision/order/fill/position chain. The MT5 sample is short (about 16 days) and produced one
trade; this is execution/logging evidence, not a profitability result.

## 5. MT5 high-risk $100 paper replay

```powershell
.\.venv\Scripts\python.exe -m goldbot.cli paper `
  --manifest config/scope.gold.v1.yaml `
  --account-profile config/profiles/micro_100.yaml `
  --source mt5 `
  --bars 1600 `
  --context-bars 500 `
  --replay-bars 1600 `
  --equity 100 `
  --ledger reports/m3.paper.mt5.100.full.ledger.sqlite `
  --report reports/m3.paper.mt5.100.full.json `
  --dashboard reports/m3.paper.mt5.100.full.html
```

Observed:

```text
account_profile: micro_100 (high_risk=True, promotion_eligible=False)
replay_decisions: 1600
orders: 2 fills: 2
initial_equity: 100.00
final_equity: 102.55
trade_count: 1
```

The same MT5 paper trade is shown under the high-risk $100 profile. It is not promotion-eligible. The
`mt5 account ... equity=1000.0` line is the terminal account; the replay starts at the `--equity 100`
value shown above.

## 6. No-trade control on a short MT5 window

```powershell
.\.venv\Scripts\python.exe -m goldbot.cli paper `
  --manifest config/scope.gold.v1.yaml `
  --account-profile config/profiles/standard_1k.yaml `
  --source mt5 `
  --bars 500 `
  --context-bars 500 `
  --replay-bars 500 `
  --equity 1000 `
  --ledger reports/m3.paper.mt5.1000.short.ledger.sqlite `
  --report reports/m3.paper.mt5.1000.short.json `
  --dashboard reports/m3.paper.mt5.1000.short.html
```

Observed:

```text
replay_decisions: 500
orders: 0 fills: 0
initial_equity: 1000.00
final_equity: 1000.00
trade_count: 0
```

This confirms the paper path does not force a trade when the strategy has no entry candidate.

## 7. Ledger contents

The SQLite ledger uses WAL mode and a `.writer.lock` file. Only one process may write. The paper
replay writes:

- `snapshots` — market, fundamental, technical, fusion, and overseer views,
- `decisions` — indexed decision record with scores and veto reasons,
- `strategy_decisions` — full JSON payload for reproducibility,
- `orders` — deterministic paper orders with `state = PAPER_FILLED`,
- `fills` — paper fills with modeled spread/slippage,
- `positions` — open and closed paper positions with MAE/MFE and realized PnL,
- `logs` — structured run/entry/exit events,
- `strategy_runs` — run start/finish summary.

One paper run per ledger is the default. The ledger refuses to mix manifest hashes or append a
second run ID; reusing the same run ID is allowed for an idempotent restart. Paper orders and fills
have null broker tickets. Protective exits and end-of-replay force-closes create an explicit
synthetic `EXIT` decision, then an order, fill, and position close, so the audit chain is complete.
The daily loss budget resets from the last marked equity, so an open position's overnight PnL is
included in the new day.

## 7a. Live read-only dashboard evidence

A fresh copy of the MT5 standard $1,000 paper replay was generated with the updated run summary so the
ledger records the profile:

```powershell
.\.venv\Scripts\python.exe -m goldbot.cli paper `
  --manifest config/scope.gold.v1.yaml `
  --account-profile config/profiles/standard_1k.yaml `
  --source mt5 `
  --bars 1600 `
  --context-bars 500 `
  --replay-bars 1600 `
  --equity 1000 `
  --ledger reports/live.paper.mt5.1000.ledger.sqlite `
  --report reports/live.paper.mt5.1000.json `
  --dashboard reports/live.paper.mt5.1000.html
```

The live server was started against that ledger:

```powershell
.\.venv\Scripts\python.exe -m goldbot.cli dashboard-live `
  --ledger reports/live.paper.mt5.1000.ledger.sqlite `
  --port 8000 `
  --refresh-seconds 2
```

Observed over local HTTP:

```text
page_status=200 page_bytes=12881
state_status=200 state_bytes=26127
mode=paper_replay profile=standard_1k equity=1002.55 finished=True
```

The state read the same run ID, 1,600 decisions, 2 paper orders, 2 paper fills, and 1 paper position
as the report. The server refused a non-loopback host in the test suite. The dashboard opened the
SQLite ledger with `mode=ro` and `PRAGMA query_only=ON`; the test suite verifies that reading the
ledger does not change its bytes. This is a read-only view of the paper ledger, not a live demo
execution loop.

## 8. What this proves and does not prove

Proves:

- The strategy can run through an execution-shaped path without a chart, dashboard, or broker.
- Every decision and paper execution is persisted with deterministic IDs.
- The dashboard can show paper orders, trades, decisions, and the `paper_replay` mode.
- The local live dashboard can read the ledger without writing it, without importing MetaTrader5,
  and without exposing a network listener.
- The path does not force trades when there is no entry candidate.
- The single-writer ledger contract and one-run-per-file guard are enforced.

Does not prove:

- Broker order routing, fills, slippage, or reconciliation.
- Live polling, reconnect behavior, or kill-switch behavior.
- Demo profitability, after-cost OOS performance, or promotion readiness.
- That the high-risk `micro_100` profile is safe.
- That the live dashboard is connected to a running demo execution loop. It currently displays a
  completed paper replay.

## 9. Next M3 slice

The live read-only dashboard backend is implemented. The next slice is the MT5 execution layer: a
headless demo order router with pre-trade scope/risk/margin/spread/symbol/session checks, post-trade
reconciliation against MT5 deals and positions, kill-switch flatten-and-halt, and reconnect/state
recovery. The paper ledger is the foundation for those checks; it is not a substitute for them.
