# GoldBot launcher (one click)

Double-click `goldbot-launch.bat` to open the interactive launcher.

```
GoldBot launcher
1) Open read-only dashboard (http://127.0.0.1:8010/)
2) Run demo preflight (read-only; no order is sent)
3) Request demo order (explicit SEND DEMO ORDER required)
4) Exit
```

Option 1 serves the read-only dashboard on port `8010` if it is not already running, then opens the browser. The dashboard never connects to MT5 directly and cannot change scope.

Option 2 runs `demo-preflight` for the selected profile. Nothing is sent.

Option 3 first shows the same read-only preflight. If it is blocked, the order is **not** sent. If it is clean, the launcher asks you to type:

```text
SEND DEMO ORDER
```

Only then does it call `demo-send` with `--send` and the explicit confirmation.

## No `.mq5` file is required

The bot is Python and talks to MT5 through the `MetaTrader5` package. MT5 is the data source and the execution venue; it is not an MQL5 Expert Advisor and no chart has to be open, selected, or focused. The required MT5 settings are:

- Algo Trading toolbar button enabled (`terminal.trade_allowed=True`)
- Python trade API enabled (`terminal.tradeapi_disabled=False`)
- logged into a demo account (`account.trade_mode=0`)

The launcher is a convenience wrapper around the CLI; it is not a control surface for the dashboard and does not override risk exits.

## Dashboard

The dashboard is still read-only. It shows the ledger, not a live MT5 query.
