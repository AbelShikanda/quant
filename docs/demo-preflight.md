# Demo preflight (read-only)

`goldbot demo-preflight` checks the demo account, terminal, symbol, real macro
files, the latest closed M15 bar, and the strategy decision. It calls
`order_check` on a would-be market order only when the strategy returns
`ENTER_LONG`. **It never calls `order_send`.** The command is intentionally a
preflight, not an execution path.

## Command

```powershell
.\.venv\Scripts\python.exe -m goldbot.cli demo-preflight `
  --manifest config/scope.gold.v1.yaml `
  --account-profile config/profiles/standard_1k.yaml `
  --macro-dir data/raw/macro `
  --bars 1600 `
  --context-bars 500 `
  --expected-login 106806652 `
  --ledger reports/demo.preflight.ledger.sqlite `
  --report reports/demo.preflight.json
```

`data/raw/macro` is local runtime data and is git-ignored. It contains the free
source files declared in the manifest:

- `DFII10.csv`, `DTWEXBGS.csv`, `T10YIE.csv` from FRED
- `GLD.xlsx` from the SPDR Gold Shares historical archive
- `COT.csv` built from CFTC annual ZIPs, filtered to gold contract `088691`

The preflight refuses to use synthetic macro data: `--macro-dir` is required.

## Observed 2026-11-14 (weekend)

```text
status: BLOCKED
account: 106806652 FBS-Demo equity=1000.0 trade_mode=0
terminal: connected=True trade_allowed=True tradeapi_disabled=False
symbol: XAUUSD trade_mode=4 volume_min=0.01 filling_mode=3
decision: NO_TRADE as_of=2026-11-13T23:45:00+00:00 score=-0.484
```

The API blocker is gone: `trade_allowed=True` and `tradeapi_disabled=False`.
The preflight still blocks for honest reasons:

- `market_open`: the last tick is stale because the market is closed for the
  weekend.
- `macro_data`: `broad_usd` is stale at the decision time. The latest
  `DTWEXBGS` observation is `2026-11-06`, and the manifest's
  `stale_after_days.broad_usd` is `5`.
- `broker_time_calibration`: the broker UTC offset is a human calibration item;
  the manifest requires it before live execution.
- `strategy_decision`: `NO_TRADE`; the fusion layer did not produce an entry
  candidate.

No broker order was sent. There is no forced-trade path: a demo order must wait
for a valid `ENTER_LONG` decision and a separately reviewed execution command.

## Guarded demo order

`goldbot demo-send` is the only command with an `order_send` path. It refuses to
send unless **all** of these are present:

- `--send`
- `--confirm-order "SEND DEMO ORDER"`
- `--confirm-broker-offset` with a human-calibrated `--broker-utc-offset-minutes`
- `--expected-login` matching the connected demo account
- a preflight where every item is `PASS` and the decision is `ENTER_LONG`
- no existing ledger order with the same decision idempotency key

It attaches the strategy stop and target to the market request, records the
broker order/fill/position in SQLite, and reconciles the open position from MT5.
The command has not been run with all guards, and no broker order has been sent.
