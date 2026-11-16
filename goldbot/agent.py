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
from datetime import timedelta
from pathlib import Path
from typing import Any

from .contracts import RiskState, StrategyDecision, ensure_utc, utc_now
from .data import BarSeries, MT5DataFeed, load_macro_directory
from .demo import _latest_closed_bar
from .ledger import Ledger
from .manifest import as_dict, manifest_hash
from .strategy import StrategyEngine


def _log(message: str) -> None:
    print(message, flush=True)


def run_agent_loop(
    *,
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
) -> int:
    """Run the guarded agent loop. Returns 0 on clean stop, 1 on error."""
    _log(f"[agent] starting; expected_login={expected_login}")
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
                },
                allow_multiple_runs=True,
            )

            iteration = 0
            stop_path = Path(stop_file) if stop_file else None
            try:
                while True:
                    iteration += 1
                    if stop_path and stop_path.exists():
                        _log("[agent] stop file detected; exiting")
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
                            decision = strategy.decide(
                                execution_bars=m15,
                                context_bars=h4,
                                macro_store=macro_store,
                                as_of_utc=as_of,
                                equity=equity,
                                position=None,
                                risk_state=risk_state,
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
