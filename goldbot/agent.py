"""Guarded live-agent loop for the demo account.

The agent connects to MT5, evaluates the strategy on every closed M15 bar,
writes decisions and OHLCV bars to the ledger, and prints status lines. It
never sends orders. Execution happens only through the guarded ``demo-send``
command, which is what the dashboard's Accept & Execute button invokes.
"""

from __future__ import annotations

import signal
import sys
import time
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .contracts import RiskState, StrategyDecision, ensure_utc, utc_now
from .data import BarSeries, MT5DataFeed, load_macro_directory
from .demo import _latest_closed_bar, run_demo_order
from .econ_calendar import blackout_at
from .ledger import Ledger
from .manifest import as_dict, manifest_hash
from .strategy import StrategyEngine


def _log(message: str) -> None:
    print(message, flush=True)


def _sync_open_positions(
    feed: MT5DataFeed,
    ledger: Ledger,
    scope_id: str,
    execution_minutes: int,
    broker_utc_offset_minutes: int = 120,
) -> None:
    """Reconcile MT5 open positions into the ledger.

    Positions still open in MT5 but missing from the ledger are inserted;
    positions present in the ledger but no longer open in MT5 are closed.
    """
    # MT5 `time` is broker-local wall time expressed as epoch — subtract offset.
    open_offset = timedelta(minutes=broker_utc_offset_minutes)
    rows = ledger.connection.execute(
        """
        SELECT position_id, side, volume, open_price, stop_loss, take_profit
        FROM positions
        WHERE close_time_utc IS NULL AND state = 'DEMO_OPEN'
        """
    ).fetchall()
    open_rows = {str(row["position_id"]): dict(row) for row in rows}
    open_tickets = {}
    for position in feed.open_positions():
        ticket = str(position.get("ticket"))
        if ticket:
            open_tickets[ticket] = position

        position_id = f"broker-{ticket}" if ticket else None
        if position_id and position_id not in open_rows:
            side = "LONG" if int(position.get("type", 0)) == 0 else "SELL"
            open_price = float(position.get("price_open") or 0.0)
            volume = float(position.get("volume") or 0.0)
            stop_loss = float(position.get("sl") or 0.0) or None
            take_profit = float(position.get("tp") or 0.0) or None
            ledger.connection.execute(
                """
                INSERT OR IGNORE INTO positions
                    (position_id, symbol, side, volume, open_price,
                     open_time_utc, stop_loss, take_profit, state,
                     broker_position_ticket, raw_position_json)
                VALUES (?, 'XAUUSD', ?, ?, ?, ?, ?, ?, 'DEMO_OPEN', ?, ?)
                """,
                (
                    position_id,
                    side,
                    volume,
                    open_price,
                    datetime.fromtimestamp(int(position.get("time", 0)), tz=timezone.utc) - open_offset,
                    stop_loss,
                    take_profit,
                    int(position.get("ticket") or 0),
                    json.dumps(position, ensure_ascii=False, default=str),
                ),
            )
            ledger.log_event(
                level="INFO",
                component="agent",
                event_type="position_synced",
                message=f"synced MT5 position ticket={ticket} into ledger",
                correlation_id=scope_id,
                payload={"ticket": ticket, "side": side},
            )

    broker_tickets = set(open_tickets.keys())
    for position_id, row in list(open_rows.items()):
        ticket = str((row["position_id"] or "")).replace("broker-", "", 1)
        if ticket not in broker_tickets:
            tick = feed.symbol_tick()
            price = float(tick.get("bid") or row["open_price"])
            side = str(row["side"]).upper()
            direction = 1 if side == "LONG" else -1
            realized_pnl = (price - float(row["open_price"])) * float(row["volume"]) * 100.0 * direction
            ledger.connection.execute(
                """
                UPDATE positions
                SET close_price = ?, close_time_utc = ?, close_reason = 'closed_by_broker',
                    state = 'DEMO_CLOSED', realized_pnl = ?
                WHERE position_id = ?
                """,
                (price, utc_now().isoformat(), realized_pnl, position_id),
            )
    ledger.connection.commit()


def run_agent_loop(
    manifest: dict[str, Any],
    macro_dir: str | Path,
    ledger_path: str | Path,
    broker_utc_offset_minutes: int = 120,
    bars: int = 1600,
    context_bars: int = 500,
    interval_seconds: float = 15.0,
    expected_login: int | None = None,
    stop_file: str | Path | None = None,
    max_iterations: int | None = None,
    feed: MT5DataFeed | None = None,
    auto_execute: bool = False,
) -> int:
    """Run the guarded agent loop. Returns 0 on clean stop, 1 on error.

    ``auto_execute=True`` means the owner has given standing consent: on every
    closed bar the strategy decides, and when the decision is ``ENTER_LONG``
    the exact same preflight that guards ``demo-send`` is run before any
    order is submitted. The order is attempted exactly once per decision and
    is skipped on any doubt. Every attempt is logged to the ledger.
    """
    _log(f"[agent] starting; expected_login={expected_login}; auto_execute={auto_execute}")
    if auto_execute:
        _log("[agent] AUTO EXECUTION mode armed (owner standing directive)")
    market = as_dict(manifest.get("market"))
    symbol_name = str(market.get("symbol", "XAUUSD"))
    execution_minutes = 15 if str(market.get("execution_bar", "M15")).upper() == "M15" else 60

    active_feed = feed or MT5DataFeed(
        symbol=symbol_name, broker_utc_offset_minutes=broker_utc_offset_minutes
    )
    owns_feed = feed is None
    try:
        try:
            active_feed.connect()
        except Exception as exc:  # noqa: BLE001 - report MT5 blocker and exit
            _log(f"[agent] ERROR MT5 connect failed: {type(exc).__name__}: {exc}")
            return 1

        try:
            account = active_feed.account_info()
        except Exception as exc:  # noqa: BLE001
            _log(f"[agent] ERROR account_info failed: {type(exc).__name__}: {exc}")
            return 1

        if expected_login is not None and account.get("login") != expected_login:
            _log(
                f"[agent] ERROR connected account {account.get('login')} "
                f"!= expected login {expected_login}; refusing to run"
            )
            return 2
        if account.get("trade_mode") != 0:
            _log(
                f"[agent] ERROR account trade_mode={account.get('trade_mode')}; "
                "expected demo mode 0; refusing to run"
            )
            return 2

        try:
            terminal = active_feed.terminal_info()
        except Exception as exc:  # noqa: BLE001
            terminal = {}
            _log(f"[agent] WARN terminal_info failed: {type(exc).__name__}: {exc}")

        if terminal and not terminal.get("trade_allowed", False):
            _log("[agent] ERROR terminal trade_allowed=False; enable Algo Trading")
            return 2
        if terminal and terminal.get("tradeapi_disabled", True):
            _log("[agent] ERROR tradeapi_disabled=True; uncheck the Python API disable option")
            return 2

        _log(
            f"[agent] account login={account.get('login')} server={account.get('server')} "
            f"equity={account.get('equity')}"
        )
        _log(
            f"[agent] terminal connected={terminal.get('connected')} "
            f"trade_allowed={terminal.get('trade_allowed')} "
            f"tradeapi_disabled={terminal.get('tradeapi_disabled')}"
        )

        digest = manifest_hash(manifest)
        scope_id = str(manifest.get("scope_id", "unknown"))
        run_id = digest[:16] + ":" + utc_now().strftime("%Y%m%d%H%M%S")
        strategy = StrategyEngine(manifest)
        risk_state = RiskState(
            equity=float(account.get("equity") or 0.0),
            starting_equity=float(account.get("equity") or 0.0),
            daily_start_equity=float(account.get("equity") or 0.0),
            peak_equity=float(account.get("equity") or 0.0),
        )
        last_peak_date: str | None = None

        with Ledger(ledger_path) as ledger:
            ledger.start_run(
                run_id=run_id,
                scope_id=scope_id,
                manifest_hash=digest,
                started_at_utc=utc_now(),
                summary={
                    "mode": "agent",
                    "scope_id": scope_id,
                    "equity": account.get("equity"),
                    "account_login": account.get("login"),
                    "account_trade_mode": account.get("trade_mode"),
                    "terminal_connected": bool(terminal.get("connected", False)),
                    "trade_allowed": bool(terminal.get("trade_allowed", False)),
                    "tradeapi_disabled": bool(terminal.get("tradeapi_disabled", True)),
                },
                allow_multiple_runs=True,
            )

            iteration = 0
            stop_path = Path(stop_file) if stop_file else None
            halt_path = Path(ledger_path).parent / "kill-switch.halt"
            try:
                while True:
                    iteration += 1
                    if stop_path and stop_path.exists():
                        _log("[agent] stop file detected; exiting")
                        break
                    if halt_path.exists():
                        _log("[agent] KILL SWITCH halt flag detected; flattening must be verified, exiting")
                        break
                    if max_iterations is not None and iteration > max_iterations:
                        _log(f"[agent] max_iterations={max_iterations} reached; exiting")
                        break
                    try:
                        iteration_start = time.monotonic()
                        account = active_feed.account_info()
                        equity = float(account.get("equity") or 0.0)
                        peak_date = ensure_utc(utc_now()).date().isoformat()
                        if last_peak_date != peak_date:
                            risk_state.start_new_day()
                            last_peak_date = peak_date
                        risk_state.update(equity)
                        risk_state.peak_equity = max(risk_state.peak_equity, equity)

                        m15 = BarSeries(active_feed.fetch_bars("M15", bars), "M15")
                        h4 = BarSeries(active_feed.fetch_bars("H4", context_bars), "H4")
                        macro_store = load_macro_directory(macro_dir)
                        tick = active_feed.symbol_tick()
                        _sync_open_positions(active_feed, ledger, scope_id, execution_minutes, broker_utc_offset_minutes)
                        latest_bar = _latest_closed_bar(
                            m15,
                            tick=tick,
                            broker_utc_offset_minutes=broker_utc_offset_minutes,
                            execution_minutes=execution_minutes,
                        )
                        if latest_bar is None:
                            _log("[agent] WARN no closed execution bar; skipping")
                        else:
                            as_of = latest_bar.time_utc + timedelta(minutes=execution_minutes)
                            blocked, blackout_event, _ = blackout_at(as_of)
                            if blocked and blackout_event is not None:
                                _log(
                                    f"[agent] NEWS BLACKOUT: {blackout_event.label}; "
                                    f"entries blocked until {blackout_event.blackout_end_utc.isoformat()}"
                                )
                            decision = strategy.decide(
                                execution_bars=m15,
                                context_bars=h4,
                                macro_store=macro_store,
                                as_of_utc=as_of,
                                equity=equity,
                                position=None,
                                risk_state=risk_state,
                                news_blackout=blocked,
                            )
                            ledger.record_chart_bars(
                                scope_id=scope_id,
                                timeframe="M15",
                                bars=m15.bars[-400:],
                                recorded_at_utc=utc_now(),
                            )
                            ledger.record_decision(
                                decision,
                                scope_id=scope_id,
                                symbol=symbol_name,
                                execution_bar=str(market.get("execution_bar", "M15")),
                                context_bar=str(market.get("context_bar", "H4")),
                                market={
                                    "mode": "agent",
                                    "account": account,
                                    "terminal": terminal,
                                    "symbol_tick": tick,
                                },
                            )
                            _log(
                                f"[agent] as_of={as_of.isoformat()} "
                                f"decision={decision.decision_type} "
                                f"score={decision.combined_score:+.3f} "
                                f"reasons={' | '.join(decision.reasons)}"
                            )
                            if auto_execute and decision.decision_type == "ENTER_LONG":
                                positions = active_feed.positions()
                                if positions:
                                    _log("[agent] AUTO EXECUTION skipped: open position exists")
                                elif halt_path.exists():
                                    _log("[agent] AUTO EXECUTION skipped: kill-switch halt flag appeared mid-loop")
                                elif getattr(risk_state, "max_drawdown_halt", False) or getattr(risk_state, "daily_halt", False):
                                    _log("[agent] AUTO EXECUTION skipped: risk state halted")
                                else:
                                    _log("[agent] AUTO EXECUTION: running guarded order send (standing)")
                                    try:
                                        auto_result = run_demo_order(
                                            manifest=manifest,
                                            macro_dir=macro_dir,
                                            ledger_path=ledger_path,
                                            expected_login=int(expected_login if expected_login is not None else account.get("login") or 0),
                                            broker_utc_offset_minutes=broker_utc_offset_minutes,
                                            broker_offset_confirmed=True,
                                            send=True,
                                            confirm_phrase=None,
                                            authorization_mode="standing",
                                        )
                                        if auto_result.sent:
                                            _log(f"[agent] AUTO EXECUTION placed order: {auto_result.send_result}")
                                        else:
                                            _log(
                                                "[agent] AUTO EXECUTION not sent: "
                                                f"{auto_result.send_result or auto_result.preflight.status}"
                                            )
                                    except Exception as exc:  # noqa: BLE001 - keep loop alive
                                        _log(f"[agent] AUTO EXECUTION error: {type(exc).__name__}: {exc}")
                    except Exception as exc:  # noqa: BLE001 - keep the agent alive
                        _log(f"[agent] ERROR iteration {iteration}: {type(exc).__name__}: {exc}")

                    elapsed = time.monotonic() - iteration_start if 'iteration_start' in locals() else 0.0
                    sleep_for = max(0.0, interval_seconds - elapsed)
                    if sleep_for > 0:
                        time.sleep(sleep_for)
            except KeyboardInterrupt:
                _log("[agent] interrupted")
            except Exception as exc:  # noqa: BLE001 - keep agent exiting cleanly
                _log(f"[agent] ERROR fatal: {type(exc).__name__}: {exc}")
                return 1
            finally:
                ledger.log_event(
                    level="INFO",
                    component="agent",
                    event_type="agent_stop",
                    message="agent loop exited",
                    correlation_id=run_id,
                    payload={"mode": "agent", "iterations": iteration},
                )
    finally:
        if owns_feed:
            active_feed.close()
    return 0
