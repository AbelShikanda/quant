"""Read-only live dashboard and guarded agent control plane.

The hard boundary is the MT5 rule: the bot/supervisor process connects to MT5.
This dashboard never imports the MT5 Python API, never connects to MT5, and
never executes strategy logic. It observes the SQLite ledger in read-only mode
and can only spawn the guarded CLI processes (agent loop / demo order) as a
remote controller. It remains a remote control; the bot remains the only
ledger writer.
"""

from __future__ import annotations

import csv
import html
import io
import json
import sqlite3
import subprocess
import threading
import time
from collections import deque
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse

import yaml

DEFAULT_LEDGER = Path("reports/demo.execution.ledger.sqlite")
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8010
DEFAULT_REFRESH_SECONDS = 2.0
DEFAULT_MAX_ROWS = 25
DEFAULT_STALE_AFTER_SECONDS = 60.0
DEFAULT_BARS = 1600
DEFAULT_CONTEXT_BARS = 500
DEFAULT_INTERVAL_SECONDS = 15.0
STARTUP_LOG_LINES = 300
PYTHON = "python"
EXECUTION_LEDGER = "reports/demo.execution.ledger.sqlite"
CSP = "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'"

_AGENT_LOCK = threading.Lock()
_AGENT_PROCESS: subprocess.Popen | None = None
_AGENT_STARTED_AT: datetime | None = None
_AGENT_LOG = deque(maxlen=STARTUP_LOG_LINES)
_AGENT_STOP_FILE: Path | None = None


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_dt(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _json_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if value is None or value == "":
        return {}
    try:
        parsed = json.loads(str(value))
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _json_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if value is None or value == "":
        return []
    try:
        parsed = json.loads(str(value))
    except (TypeError, ValueError):
        return []
    return parsed if isinstance(parsed, list) else []


def _as_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _read_only_connection(path: Path) -> sqlite3.Connection:
    resolved = path.resolve()
    uri = f"file:{quote(resolved.as_posix(), safe='/:')}?mode=ro"
    connection = sqlite3.connect(uri, uri=True, timeout=1.0)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only = ON")
    return connection


def _rows(
    connection: sqlite3.Connection,
    sql: str,
    params: tuple[Any, ...] = (),
) -> tuple[list[dict[str, Any]], str | None]:
    try:
        return [dict(row) for row in connection.execute(sql, params).fetchall()], None
    except sqlite3.Error as exc:
        return [], f"{type(exc).__name__}: {exc}"


def _one(
    connection: sqlite3.Connection,
    sql: str,
    params: tuple[Any, ...] = (),
) -> tuple[dict[str, Any] | None, str | None]:
    rows, error = _rows(connection, sql, params)
    return (rows[0] if rows else None), error


def _count(connection: sqlite3.Connection, table: str) -> tuple[int | None, str | None]:
    rows, error = _rows(connection, f"SELECT COUNT(*) AS n FROM {table}")
    if error is not None:
        return None, error
    return int(rows[0]["n"]) if rows else 0, None


def _add_error(errors: list[str], error: str | None) -> None:
    if error is not None and error not in errors:
        errors.append(error)


def _append_log(level: str, message: str) -> None:
    timestamp = _utc_now().strftime("%H:%M:%S")
    text = f"[{timestamp}] [{level}] {message}"
    _AGENT_LOG.append(text)


def _agent_status() -> dict[str, Any]:
    with _AGENT_LOCK:
        running = _AGENT_PROCESS is not None and _AGENT_PROCESS.poll() is None
        uptime = (
            (_utc_now() - _AGENT_STARTED_AT).total_seconds()
            if _AGENT_STARTED_AT and running
            else None
        )
        last_exit_code = (
            _AGENT_PROCESS.poll()
            if _AGENT_PROCESS is not None and not running
            else None
        )
        return {
            "running": running,
            "pid": _AGENT_PROCESS.pid if _AGENT_PROCESS is not None else None,
            "started_at_utc": _AGENT_STARTED_AT.isoformat() if _AGENT_STARTED_AT else None,
            "uptime_seconds": uptime,
            "last_exit_code": last_exit_code,
            "log_lines": len(_AGENT_LOG),
        }


def _agent_log_lines() -> list[str]:
    return list(_AGENT_LOG)


def _decision_summary(payload: dict[str, Any]) -> dict[str, Any]:
    overseer = _json_dict(payload.get("overseer"))
    exit_decision = _json_dict(payload.get("exit_decision"))
    reasons = payload.get("reasons")
    return {
        "decision_id": payload.get("decision_id"),
        "as_of_utc": payload.get("as_of_utc"),
        "decision_type": payload.get("decision_type"),
        "side": payload.get("side"),
        "combined_score": payload.get("combined_score"),
        "overseer_verdict": overseer.get("verdict"),
        "exit_reason": exit_decision.get("reason"),
        "reasons": reasons if isinstance(reasons, list) else [],
    }


def _fundamental_components(fundamentals: dict[str, Any]) -> list[dict[str, Any]]:
    components = _json_dict(fundamentals.get("components"))
    rows: list[dict[str, Any]] = []
    for key, component in components.items():
        if not isinstance(component, dict):
            continue
        rows.append(
            {
                "input_id": component.get("input_id") or key,
                "series_id": component.get("series_id"),
                "value": component.get("value"),
                "signal": component.get("signal"),
                "weight": component.get("weight"),
                "contribution": component.get("contribution"),
                "stale": bool(component.get("stale")),
                "source_id": component.get("source_id"),
                "note": component.get("note") or "",
            }
        )
    return rows


def load_state(
    ledger_path: str | Path,
    *,
    max_rows: int = DEFAULT_MAX_ROWS,
    now: datetime | None = None,
    stale_after_seconds: float = DEFAULT_STALE_AFTER_SECONDS,
    agent_status: dict[str, Any] | None = None,
    log_lines: list[str] | None = None,
) -> dict[str, Any]:
    """Read the ledger read-only and build a JSON-safe UI state."""
    path = Path(ledger_path)
    generated_at = now or _utc_now()
    max_rows = max(1, int(max_rows))
    state: dict[str, Any] = {
        "generated_at_utc": generated_at.isoformat(),
        "ledger_path": str(path),
        "ledger_exists": path.exists(),
        "read_only": True,
        "no_mt5_connection": True,
        "ok": False,
        "mode": "unknown",
        "scope_id": None,
        "run_id": None,
        "manifest_hash": None,
        "account_profile": "n/a",
        "account_profile_description": "",
        "high_risk_override": False,
        "promotion_eligible": None,
        "is_finished": False,
        "finished_at_utc": None,
        "initial_equity": None,
        "final_equity": None,
        "equity": None,
        "daily_pnl": None,
        "current_drawdown_pct": None,
        "daily_halt": None,
        "max_drawdown_halt": None,
        "kill_switch": None,
        "open_position": None,
        "open_positions": [],
        "positions": [],
        "orders": [],
        "fills": [],
        "decisions": [],
        "logs": [],
        "incidents": [],
        "latest_decision": None,
        "latest_snapshot": None,
        "risk_state": {},
        "data_quality": {},
        "counts": {},
        "last_activity_utc": None,
        "last_activity_age_seconds": None,
        "stale": False,
        "errors": [],
        "message": "",
        "chart_bars": [],
        "equity_curve": [],
        "agent": agent_status or {
            "running": False,
            "pid": None,
            "started_at_utc": None,
            "uptime_seconds": None,
            "last_exit_code": None,
            "log_lines": 0,
        },
        "agent_logs": log_lines or [],
        "mt5": {"status": "unknown", "detail": "no terminal state recorded in the ledger"},
        "fundamentals": {"score": None, "components": [], "missing_inputs": [], "stale_inputs": []},
        "technicals": {"score": None, "indicators": {}, "entry_triggers": {}},
        "fusion": {"combined_score": None, "entry_candidate": False, "reasons": []},
        "overseer": {"verdict": None, "decision": None, "side": None, "confidence": None},
        "gate_blocker": "unknown",
    }

    if not path.exists():
        state["message"] = "Ledger not found. Start the agent or run a paper replay."
        return state

    proofs: dict[str, Any] = {
        "reconnect_drill": None,
        "kill_switch_drill": None,
        "refresh_macro": None,
    }
    try:
        connection = _read_only_connection(path)
    except sqlite3.Error as exc:
        state["message"] = f"Could not open ledger read-only: {exc}"
        return state

    errors: list[str] = []
    try:
        latest_run, error = _one(
            connection,
            "SELECT * FROM strategy_runs ORDER BY started_at_utc DESC LIMIT 1",
        )
        _add_error(errors, error)
        latest_snapshot, error = _one(
            connection,
            "SELECT * FROM snapshots ORDER BY captured_at_utc DESC LIMIT 1",
        )
        _add_error(errors, error)
        latest_decision_row, error = _one(
            connection,
            "SELECT payload_json FROM strategy_decisions ORDER BY as_of_utc DESC LIMIT 1",
        )
        _add_error(errors, error)
        decision_rows, error = _rows(
            connection,
            "SELECT payload_json FROM strategy_decisions ORDER BY as_of_utc DESC LIMIT ?",
            (max_rows,),
        )
        _add_error(errors, error)
        orders, error = _rows(
            connection,
            "SELECT * FROM orders ORDER BY submitted_at_utc DESC LIMIT ?",
            (max_rows,),
        )
        _add_error(errors, error)
        fills, error = _rows(
            connection,
            "SELECT * FROM fills ORDER BY filled_at_utc DESC LIMIT ?",
            (max_rows,),
        )
        _add_error(errors, error)
        positions, error = _rows(
            connection,
            "SELECT * FROM positions ORDER BY open_time_utc DESC LIMIT ?",
            (max_rows,),
        )
        _add_error(errors, error)
        open_positions, error = _rows(
            connection,
            "SELECT * FROM positions WHERE close_time_utc IS NULL ORDER BY open_time_utc ASC",
        )
        _add_error(errors, error)
        logs, error = _rows(
            connection,
            "SELECT * FROM logs ORDER BY logged_at_utc DESC LIMIT ?",
            (max_rows,),
        )
        _add_error(errors, error)
        incidents, error = _rows(
            connection,
            "SELECT * FROM incidents ORDER BY opened_at_utc DESC LIMIT ?",
            (max_rows,),
        )
        _add_error(errors, error)

        chart_bars, error = _rows(
            connection,
            """
            SELECT bar_time_utc, open_price, high_price, low_price, close_price, volume
            FROM chart_bars ORDER BY bar_time_utc ASC LIMIT ?
            """,
            (200,),
        )
        _add_error(errors, error)

        counts: dict[str, int | None] = {}
        for table in (
            "strategy_runs",
            "strategy_decisions",
            "snapshots",
            "decisions",
            "orders",
            "fills",
            "positions",
            "logs",
            "incidents",
            "chart_bars",
        ):
            count, error = _count(connection, table)
            counts[table] = count
            _add_error(errors, error)
        proofs = _ledger_proofs(connection)
        proofs["wgc"] = _wgc_status(connection)
    finally:
        connection.close()
    proofs["macro"] = _macro_freshness()
    state["proofs"] = proofs
    try:
        state["exec_quality"] = _exec_quality(connection)
    except Exception:  # noqa: BLE001 - never crash the dashboard on extras
        state["exec_quality"] = {}

    run_summary = _json_dict(latest_run.get("summary_json")) if latest_run else {}
    account_trade_mode = run_summary.get("account_trade_mode")
    account_login = run_summary.get("account_login")
    terminal_connected = run_summary.get("terminal_connected")
    trade_allowed = run_summary.get("trade_allowed")
    tradeapi_disabled = run_summary.get("tradeapi_disabled")
    snapshot_market = _json_dict(latest_snapshot.get("market_json")) if latest_snapshot else {}
    risk_state = _json_dict(snapshot_market.get("risk_state"))
    data_quality = (
        _json_dict(latest_snapshot.get("data_quality_json")) if latest_snapshot else {}
    )
    fundamentals = _json_dict(latest_snapshot.get("fundamentals_json")) if latest_snapshot else {}
    technicals = _json_dict(latest_snapshot.get("technicals_json")) if latest_snapshot else {}
    fusion = _json_dict(latest_snapshot.get("fusion_json")) if latest_snapshot else {}
    overseer = _json_dict(latest_snapshot.get("overseer_json")) if latest_snapshot else {}
    latest_decision_payload = (
        _json_dict(latest_decision_row.get("payload_json")) if latest_decision_row else {}
    )

    mode = str(run_summary.get("mode") or "").strip()
    if not mode:
        order_states = [str(order.get("state") or "") for order in orders]
        if any(value.startswith("PAPER_") for value in order_states):
            mode = "paper_replay"
        elif order_states:
            mode = "demo_live"
        else:
            mode = "unknown"

    is_finished = bool(latest_run and latest_run.get("finished_at_utc"))
    last_activity_candidates: list[datetime] = []

    def add_activity(value: Any) -> None:
        parsed = _parse_dt(value)
        if parsed is not None:
            last_activity_candidates.append(parsed)

    if latest_run:
        add_activity(latest_run.get("finished_at_utc"))
        add_activity(latest_run.get("started_at_utc"))
    if latest_snapshot:
        add_activity(latest_snapshot.get("captured_at_utc"))
    if latest_decision_row:
        add_activity(latest_decision_row.get("as_of_utc"))
    if orders:
        add_activity(orders[0].get("submitted_at_utc"))
    if fills:
        add_activity(fills[0].get("filled_at_utc"))
    if logs:
        add_activity(logs[0].get("logged_at_utc"))
    if positions:
        add_activity(positions[0].get("close_time_utc"))
        add_activity(positions[0].get("open_time_utc"))

    last_activity = max(last_activity_candidates) if last_activity_candidates else None
    last_activity_age = (
        max(0.0, (generated_at - last_activity).total_seconds())
        if last_activity is not None
        else None
    )

    equity = (
        _as_float(risk_state.get("equity"))
        if risk_state
        else None
    )
    if equity is None:
        equity = _as_float(run_summary.get("final_equity"))
    if equity is None:
        equity = _as_float(run_summary.get("initial_equity"))

    missing_inputs = _json_list(fundamentals.get("missing_inputs"))
    stale_inputs = _json_list(fundamentals.get("stale_inputs"))
    fusion_reasons = _json_list(fusion.get("reasons"))
    latest_bars = [
        {
            "time": row.get("bar_time_utc"),
            "open": row.get("open_price"),
            "high": row.get("high_price"),
            "low": row.get("low_price"),
            "close": row.get("close_price"),
            "volume": row.get("volume"),
        }
        for row in chart_bars
    ]

    initial_equity = _as_float(run_summary.get("initial_equity")) or 0.0
    equity_curve = [{"time": None, "equity": initial_equity}]
    running = initial_equity
    for position in reversed(positions):
        if position.get("close_time_utc"):
            pnl = _as_float(position.get("realized_pnl"))
            if pnl is not None:
                running += pnl
                equity_curve.append({"time": position.get("close_time_utc"), "equity": running})

    if missing_inputs:
        gate_blocker = f"missing macro input: {', '.join(missing_inputs)}"
    elif stale_inputs:
        gate_blocker = f"stale macro input: {', '.join(stale_inputs)}"
    elif fusion_reasons:
        gate_blocker = "; ".join(fusion_reasons[:2])
    else:
        gate_blocker = "no gate reason recorded"

    terminal = _json_dict(snapshot_market.get("terminal"))
    mt5_status = "unknown"
    mt5_detail = "no terminal state in the ledger"
    if terminal:
        connected = bool(terminal.get("connected", False))
        mt5_status = "up" if connected else "down"
        mt5_detail = (
            f"connected={connected} trade_allowed={terminal.get('trade_allowed')} "
            f"tradeapi_disabled={terminal.get('tradeapi_disabled')}"
        )

    state.update(
        {
            "ok": True,
            "mode": mode,
            "scope_id": run_summary.get("scope_id")
            or (latest_run.get("scope_id") if latest_run else None),
            "run_id": latest_run.get("run_id") if latest_run else None,
            "manifest_hash": latest_run.get("manifest_hash") if latest_run else None,
            "account_profile": str(run_summary.get("account_profile") or "n/a"),
            "account_profile_description": str(
                run_summary.get("account_profile_description") or ""
            ),
            "high_risk_override": bool(run_summary.get("high_risk_override", False)),
            "promotion_eligible": run_summary.get("promotion_eligible"),
            "is_finished": is_finished,
            "finished_at_utc": latest_run.get("finished_at_utc") if latest_run else None,
            "initial_equity": run_summary.get("initial_equity"),
            "final_equity": run_summary.get("final_equity"),
            "equity": equity,
            "daily_pnl": _as_float(risk_state.get("daily_pnl")),
            "current_drawdown_pct": _as_float(
                risk_state.get("current_drawdown_pct")
            ),
            "daily_halt": risk_state.get("daily_halt"),
            "max_drawdown_halt": risk_state.get("max_drawdown_halt"),
            "kill_switch": risk_state.get("kill_switch"),
            "open_position": open_positions[0] if open_positions else None,
            "open_positions": open_positions,
            "positions": positions,
            "orders": orders,
            "fills": fills,
            "decisions": [
                _decision_summary(_json_dict(row.get("payload_json")))
                for row in decision_rows
            ],
            "logs": logs,
            "incidents": incidents,
            "latest_decision": (
                _decision_summary(latest_decision_payload)
                if latest_decision_payload
                else None
            ),
            "latest_snapshot": (
                {
                    "snapshot_id": latest_snapshot.get("snapshot_id"),
                    "captured_at_utc": latest_snapshot.get("captured_at_utc"),
                    "market": snapshot_market,
                    "fundamentals": fundamentals,
                    "technicals": technicals,
                    "fusion": fusion,
                    "overseer": overseer,
                    "data_quality": data_quality,
                }
                if latest_snapshot
                else None
            ),
            "risk_state": risk_state,
            "data_quality": data_quality,
            "counts": counts,
            "last_activity_utc": last_activity.isoformat() if last_activity else None,
            "last_activity_age_seconds": last_activity_age,
            "stale": bool(
                not is_finished
                and last_activity_age is not None
                and last_activity_age > stale_after_seconds
            ),
            "errors": errors,
            "message": (
                ""
                if latest_run
                else "Ledger is readable but has no strategy runs yet."
            ),
            "chart_bars": latest_bars,
            "equity_curve": equity_curve,
            "mt5": {"status": mt5_status, "detail": mt5_detail},
            "fundamentals": {
                "score": _as_float(fundamentals.get("score")),
                "components": _fundamental_components(fundamentals),
                "missing_inputs": missing_inputs,
                "stale_inputs": stale_inputs,
                "is_valid": fundamentals.get("is_valid"),
            },
            "technicals": {
                "score": _as_float(technicals.get("score")),
                "indicators": _json_dict(technicals.get("indicators")),
                "entry_triggers": _json_dict(technicals.get("entry_triggers")),
            },
            "fusion": {
                "combined_score": _as_float(fusion.get("combined_score")),
                "entry_candidate": bool(fusion.get("entry_candidate")),
                "reasons": fusion_reasons,
            },
            "overseer": {
                "verdict": overseer.get("verdict"),
                "decision": overseer.get("decision"),
                "side": overseer.get("side"),
                "confidence": overseer.get("confidence"),
            },
            "gate_blocker": gate_blocker,
            "account_trade_mode": account_trade_mode,
            "account_login": account_login,
            "agent_terminal_connected": terminal_connected,
            "agent_trade_allowed": trade_allowed,
            "agent_tradeapi_disabled": tradeapi_disabled,
        }
    )
    return state


# ---------------------------------------------------------------------------
# HTTP server and guarded agent control plane
# ---------------------------------------------------------------------------
from .live_dashboard_ui import render_page  # noqa: E402
from .profiles import load_profile  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
WGC_XLSX_URL = "https://www.gold.org/download/file/7739/World_official_gold_holdings_as_of_Nov2026_IFS.xlsx"

_PROFILE_KEYS = {
    "micro_50": "config/profiles/micro_50.yaml",
    "micro_100": "config/profiles/micro_100.yaml",
    "standard_1k": "config/profiles/standard_1k.yaml",
}


def _profile_key(profile_path: str) -> str:
    target = Path(profile_path)
    for key, rel in _PROFILE_KEYS.items():
        candidate = ROOT / rel
        try:
            if target.resolve() == candidate.resolve() or profile_path.replace("\\", "/") == rel:
                return key
        except OSError:
            continue
    return target.stem


def _macro_freshness() -> dict[str, Any]:
    """File-driven freshness for the binding FRED inputs (read-only).

    Reads the manifest limits but NEVER writes them. The binding broad_usd
    file honours the loader's ALFRED-vintage precedence.
    """
    from .refresh import _latest_observation, _value_column, _validate_fred_csv

    limits: dict[str, Any] = {}
    try:
        scope = yaml.safe_load((ROOT / "config" / "scope.gold.v1.yaml").read_text(encoding="utf-8"))
        limits = dict(
            ((scope or {}).get("strategy", {}) or {}).get("fundamental", {}).get("stale_after_days", {}) or {}
        )
    except Exception:  # noqa: BLE001 - fall back to the manifest defaults
        limits = {}

    def limit_for(key: str, default: int) -> int:
        try:
            return int(limits.get(key, default))
        except (TypeError, ValueError):
            return default

    broad_limit = limit_for("broad_usd", 5)
    ry_limit = limit_for("real_yield_10y", 5)
    be_limit = limit_for("breakeven_10y", 5)

    macro_dir = ROOT / "data" / "raw" / "macro"
    broad_file = macro_dir / (
        "ALFRED_DTWEXBGS.csv" if (macro_dir / "ALFRED_DTWEXBGS.csv").exists() else "DTWEXBGS.csv"
    )
    files = [
        ("DFII10", macro_dir / "DFII10.csv", "DFII10", ry_limit),
        ("DTWEXBGS", broad_file, "DTWEXBGS", broad_limit),
        ("T10YIE", macro_dir / "T10YIE.csv", "T10YIE", be_limit),
    ]
    series: list[dict[str, Any]] = []
    all_ok = True
    today = _utc_now().date()
    for series_id, path, column_hint, limit in files:
        entry: dict[str, Any] = {
            "series_id": series_id,
            "path": str(path),
            "limit_days": limit,
            "latest_date": None,
            "age_days": None,
            "ok": False,
            "error": None,
        }
        if not path.exists():
            entry["error"] = "file missing"
            series.append(entry)
            all_ok = False
            continue
        try:
            text = path.read_text(encoding="utf-8")
            fieldnames = list(csv.DictReader(io.StringIO(text.lstrip("\ufeff"))).fieldnames or [])
            value_column = _value_column(fieldnames, column_hint)
            latest = _latest_observation(text, value_column) if value_column else None
            if latest is None:
                entry["error"] = "no parseable observations"
                all_ok = False
            else:
                entry["latest_date"] = latest.isoformat()
                entry["age_days"] = (today - latest).days
                entry["ok"] = entry["age_days"] <= limit
                if not entry["ok"]:
                    all_ok = False
        except Exception as exc:  # noqa: BLE001 - report, don't crash the dashboard
            entry["error"] = f"{type(exc).__name__}: {exc}"
            all_ok = False
        series.append(entry)

    return {
        "ok": all_ok,
        "series": series,
        "checked_at_utc": _utc_now().isoformat(),
    }


_WGC_REPORT_CACHE: dict[tuple, dict[str, Any]] = {}


def _wgc_status(connection) -> dict[str, Any]:
    """Monthly WGC/IMF manual XLSX status (single source of truth: run_wgc_check).

    ``completed`` is true only when (a) the reconfirm passes — parse, rows,
    sha vs refresh_event.json, age ≤ 90d, AND tally with the manifest/review
    file, (b) it is fresh (<= 30 days since download), and (c) a wgc_check
    evidence log exists in the ledger for this exact file sha256.
    """
    from .wgc import MAX_AGE_DAYS, REMINDER_CADENCE_DAYS, newest_wgc_xlsx, run_wgc_check

    wgc_dir = ROOT / "data" / "manual" / "wgc"
    try:
        newest = newest_wgc_xlsx(wgc_dir)
    except Exception:  # noqa: BLE001 - defensive; treated as waiting
        newest = None

    if newest is None:
        return {
            "status": "waiting",
            "completed": False,
            "file_name": None,
            "age_days": None,
            "rows": None,
            "reconfirmed": False,
            "proof": None,
            "tallies_with_review": None,
            "detail": "no XLSX in data/manual/wgc/",
        }

    try:
        stat = newest.stat()
        event_path = wgc_dir / "refresh_event.json"
        event_mtime = event_path.stat().st_mtime if event_path.exists() else 0.0
        cache_key = (str(newest), stat.st_mtime, stat.st_size, event_mtime)
        report = _WGC_REPORT_CACHE.get(cache_key)
        if report is None:
            report = run_wgc_check(wgc_dir=wgc_dir, ledger_path=None)
            _WGC_REPORT_CACHE.clear()
            _WGC_REPORT_CACHE[cache_key] = report
    except Exception as exc:  # noqa: BLE001 - report, never crash the UI
        return {
            "status": "fail",
            "completed": False,
            "file_name": str(newest),
            "age_days": None,
            "rows": None,
            "reconfirmed": False,
            "proof": None,
            "tallies_with_review": None,
            "detail": f"{type(exc).__name__}: {exc}",
        }

    inspection = _json_dict(report.get("inspection"))
    current_sha = str(inspection.get("sha256") or "").lower()
    age = report.get("age_days")
    fresh = age is not None and age <= REMINDER_CADENCE_DAYS

    proof = None
    reconfirmed = False
    try:
        row, error = _one(
            connection,
            "SELECT logged_at_utc, payload_json FROM logs "
            "WHERE component='wgc_check' ORDER BY logged_at_utc DESC LIMIT 1",
        )
        if error is None and row:
            payload = _json_dict(row.get("payload_json"))
            if str(payload.get("status") or "") == "pass":
                payload_inspection = _json_dict(payload.get("inspection"))
                logged_sha = str(payload_inspection.get("sha256") or "").lower()
                if logged_sha and logged_sha == current_sha:
                    reconfirmed = True
                    proof = row.get("logged_at_utc")
    except Exception:  # noqa: BLE001 - evidence is best-effort here; files stay authoritative
        reconfirmed = False

    status = str(report.get("status") or "fail")
    completed = status == "pass" and fresh and reconfirmed

    return {
        "status": status,
        "completed": completed,
        "file_name": newest.name,
        "review_file": report.get("review_file"),
        "tallies_with_review": report.get("tallies_with_review"),
        "latest_date": inspection.get("latest_date"),
        "age_days": age,
        "rows": inspection.get("rows"),
        "sheet": inspection.get("sheet"),
        "reconfirmed": reconfirmed,
        "fresh": fresh,
        "proof": proof,
        "detail": report.get("detail"),
    }


def _ledger_proofs(connection) -> dict[str, Any]:
    """Completion proofs for manual/promotion items, read from the ledger."""
    proofs: dict[str, Any] = {
        "reconnect_drill": None,
        "kill_switch_drill": None,
        "refresh_macro": None,
    }
    try:
        rows = connection.execute(
            "SELECT component, MAX(logged_at_utc) FROM logs "
            "WHERE component IN ('reconnect_drill', 'kill_switch', 'kill_switch_drill', 'refresh_macro') "
            "GROUP BY component"
        ).fetchall()
        by_log = {str(row[0]): row[1] for row in rows}
    except sqlite3.Error:
        by_log = {}
    try:
        rows = connection.execute(
            "SELECT category, MAX(opened_at_utc) FROM incidents "
            "WHERE category IN ('reconnect_drill', 'kill_switch', 'kill_switch_drill') "
            "GROUP BY category"
        ).fetchall()
        by_incident = {str(row[0]): row[1] for row in rows}
    except sqlite3.Error:
        by_incident = {}

    def _latest(*keys: str) -> Any:
        values = [by_log.get(key) for key in keys] + [by_incident.get(key) for key in keys]
        values = [value for value in values if value]
        return max(values) if values else None

    proofs["reconnect_drill"] = _latest("reconnect_drill")
    proofs["kill_switch_drill"] = _latest("kill_switch", "kill_switch_drill")
    proofs["refresh_macro"] = by_log.get("refresh_macro")
    return proofs


def _build_notifications(state: dict[str, Any]) -> list[dict[str, Any]]:
    """Notifications for the bell. Completion is proven, never guessed.

    - macro freshness is verified live from the binding FRED/ALFRED files
      (manifest stale limits are read, never rewritten);
    - drill completion is proven from the ledger (events/incidents);
    - the WGC item is HUMAN_JUDGMENT and can never be auto-cleared.
    """
    notifications: list[dict[str, Any]] = []
    proofs = state.get("proofs") or {}

    # --- macro freshness -------------------------------------------------
    macro = proofs.get("macro") or {}
    macro_ok = bool(macro.get("ok"))
    series_text = ", ".join(
        f"{item.get('series_id')}={item.get('latest_date') or 'n/a'} "
        f"({'ok' if item.get('age_days') is not None and item.get('age_days') <= item.get('limit_days') else 'STALE'})"
        for item in macro.get("series", [])
    )
    notifications.append(
        {
            "id": "macro_data",
            "severity": "info" if macro_ok else "warning",
            "title": "Macro data is fresh" if macro_ok else "Macro data is stale or missing",
            "note": (
                (
                    f"All binding FRED inputs are within their manifest limits and fresh enough for gating "
                    f"({series_text}). Verified from data/raw/macro at {macro.get('checked_at_utc', 'n/a')}."
                )
                if macro_ok
                else (
                    f"The bot stays blocked on stale/missing macro data ({series_text or 'no files readable'}). "
                    "Use the Refresh button to re-download from FRED. The 5-day broad_usd limit applies as written "
                    "in the manifest and is never widened."
                )
            ),
            "action": "refresh",
            "completed": macro_ok,
            "proof": (
                f"macro files verified {macro.get('checked_at_utc')}"
                if macro_ok
                else None
            ),
        }
    )

    # --- WGC central-bank gold (monthly human download; proven via file + ledger) ---
    wgc = proofs.get("wgc") or {}
    wgc_completed = bool(wgc.get("completed"))
    wgc_status = wgc.get("status", "waiting")
    wgc_age = wgc.get("age_days")
    wgc_file = wgc.get("file_name")
    if wgc_completed:
        wgc_note = (
            f"Reconfirmed from the newest file: {wgc_file} "
            f"({wgc.get('rows')} rows, {wgc_age}d since download, sha verified, evidence logged). "
            "Clears until a new file lands."
        )
    elif wgc_status == "waiting":
        wgc_note = (
            "No XLSX found in data/manual/wgc/. Download the monthly WGC/IMF file (link below), "
            "place it there, then hit Reconfirm."
        )
    elif wgc_status == "fail":
        wgc_note = (
            f"WGC file present but failed checks: {wgc.get('detail') or 'parse error'}. "
            "Replace the file, then Reconfirm."
        )
    elif wgc_status == "stale":
        wgc_note = (
            f"File {wgc_file} is {wgc_age} days old (monthly cadence). "
            "Download the new WGC/IMF file, place it in data/manual/wgc/, then Reconfirm."
        )
    elif wgc_status == "review":
        wgc_note = (
            f"Newest file {wgc_file} does NOT tally with the manifest/review file "
            f"({wgc.get('review_file')}). Align the filename (or have the owner update the manifest), "
            "then Reconfirm — the M1 review evidence must reference the same file."
        )
    else:  # present + fresh + parseable but not yet reconfirmed for this exact file
        tally = wgc.get("tallies_with_review")
        tally_text = "tallies with the review file" if tally else "NOT tallied with the review file"
        wgc_note = (
            f"File {wgc_file} parses ({wgc.get('rows')} rows, {wgc_age}d since download, {tally_text}) "
            "but has not been reconfirmed yet. Hit Reconfirm to verify and log it."
        )
    notifications.append(
        {
            "id": "wgc_central_bank_manual",
            "severity": "info" if wgc_completed else "warning",
            "title": "WGC central-bank file reconfirmed" if wgc_completed else "Central-bank gold file (WGC / IMF) — monthly check",
            "note": wgc_note,
            "action": "reconfirm",
            "link": WGC_XLSX_URL,
            "link_text": "Download the Excel file",
            "completed": wgc_completed,
            "proof": wgc.get("proof"),
        }
    )

    try:
        signoff_exists = (ROOT / "reports" / "m1.signoff.json").exists()
    except OSError:
        signoff_exists = False

    if not signoff_exists:
        notifications.append(
            {
                "id": "human.signoff",
                "severity": "warning",
                "title": "M1 sign-off not recorded",
                "note": "reports/m1.signoff.json is missing. Record it after reviewing the M1 validator report.",
                "action": "none",
                "completed": False,
                "proof": None,
            }
        )

    # --- kill switch drill (proven by the ledger) --------------------------
    kill_proof = proofs.get("kill_switch_drill")
    notifications.append(
        {
            "id": "kill_switch_drill",
            "severity": "info" if kill_proof else "warning",
            "title": "Kill-switch drill recorded" if kill_proof else "Kill-switch drill not done",
            "note": (
                f"Kill-switch evidence recorded in the ledger at {kill_proof}."
                if kill_proof
                else (
                    "Required before promotion. Clears automatically once kill-switch evidence "
                    "(component/category 'kill_switch' or 'kill_switch_drill') is recorded in the ledger."
                )
            ),
            "action": "none",
            "completed": bool(kill_proof),
            "proof": kill_proof,
        }
    )

    # --- reconnect drill (proven by the ledger) ----------------------------
    reconnect_proof = proofs.get("reconnect_drill")
    notifications.append(
        {
            "id": "reconnect_drill",
            "severity": "info" if reconnect_proof else "warning",
            "title": "Reconnect drill recorded" if reconnect_proof else "Reconnect drill not done",
            "note": (
                f"Reconnect evidence recorded in the ledger at {reconnect_proof}."
                if reconnect_proof
                else (
                    "Required before promotion. Clears automatically once a reconnect-drill "
                    "evidence record is in the ledger (goldbot reconnect-drill)."
                )
            ),
            "action": "none",
            "completed": bool(reconnect_proof),
            "proof": reconnect_proof,
        }
    )

    notifications.sort(key=lambda item: (bool(item.get("completed")), item.get("id") or ""))
    return notifications


def _profile_spec(profile_path: str) -> dict[str, Any]:
    path = ROOT / Path(profile_path)
    try:
        profile = load_profile(path)
    except Exception as exc:  # noqa: BLE001 - dashboard must report, not crash
        return {
            "key": _profile_key(profile_path),
            "label": _profile_key(profile_path),
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
            "spec_rows": [],
        }
    key = _profile_key(profile_path)
    overrides = _json_dict(profile.get("overrides"))
    market = _json_dict(overrides.get("market"))
    risk = _json_dict(overrides.get("risk"))
    sizing = _json_dict(_json_dict(overrides.get("strategy")).get("sizing"))
    high_risk = bool(profile.get("high_risk_override", False))
    promotion_eligible = bool(profile.get("promotion_eligible", True))
    description = str(profile.get("description", "")).strip()

    if key == "micro_50":
        select_summary = (
            "Selecting: smallest account that can hold the broker minimum; 100:1 leverage; "
            "~19–21% one-stop risk; $50 account."
        )
        forgo_summary = (
            "Forgoing: promotion eligibility, 5:1 discipline, and any drawdown limit inside 20%. "
            "HIGH RISK — not promotion-eligible."
        )
        tone = "red"
    elif key == "micro_100":
        select_summary = (
            "Selecting: $100 account; 50:1 leverage; ~9.7% one-stop risk; broker minimum held."
        )
        forgo_summary = (
            "Forgoing: promotion eligibility, 5:1 discipline, and a stop risk under 10%. "
            "HIGH RISK — not promotion-eligible."
        )
        tone = "red"
    else:
        select_summary = (
            "Selecting: $1,000 promotion-eligible demo; 5:1 leverage; 0.25% risk per trade; "
            "1.25% min-lot guard."
        )
        forgo_summary = (
            "Forgoing: ability to hold 0.01 XAUUSD on a sub-$776 account at 5:1. "
            "The only promotion-eligible profile."
        )
        tone = "green"

    def _row(label, value, tone=""):
        return {"label": label, "value": value, "tone": tone}

    spec_rows = [
        _row("Bot leverage cap", f"{risk.get('leverage_cap', 'n/a')}:1", tone),
        _row("Bot notional cap", f"{market.get('max_notional_exposure_ratio', 'n/a')}x equity", tone),
        _row("Risk per trade", f"{risk.get('per_trade_equity_pct', 'n/a')}%", tone),
        _row("Minimum-lot risk guard", f"{sizing.get('min_lot_risk_cap_pct', 'n/a')}%", tone),
        _row("Daily loss halt", f"{risk.get('daily_loss_halt_pct', 'n/a')}%", "amber"),
        _row("Max drawdown flatten/halt", f"{risk.get('max_drawdown_flatten_pct', 'n/a')}%", "amber"),
        _row("Promotion-eligible", "Yes" if promotion_eligible else "No", "green" if promotion_eligible else "red"),
        _row("High-risk override", "Yes" if high_risk else "No", "red" if high_risk else ""),
    ]
    return {
        "ok": True,
        "key": key,
        "label": key.upper(),
        "description": description,
        "high_risk_override": high_risk,
        "promotion_eligible": promotion_eligible,
        "select_summary": select_summary,
        "forgo_summary": forgo_summary,
        "spec_rows": spec_rows,
    }


def _python_command() -> list[str]:
    candidate = ROOT / ".venv" / "Scripts" / "python.exe"
    if candidate.exists():
        return [str(candidate)]
    return [sys.executable]


def _agent_command(
    *,
    profile_rel: str,
    macro_dir: str,
    ledger_path: str,
    manifest_rel: str,
    broker_utc_offset_minutes: int,
    expected_login: int,
    interval_seconds: float,
    bars: int,
    context_bars: int,
    stop_file: str | None,
) -> list[str]:
    cmd = _python_command() + [
        "-m",
        "goldbot.cli",
        "agent",
        "--manifest",
        str(ROOT / manifest_rel),
        "--account-profile",
        str(ROOT / profile_rel),
        "--macro-dir",
        str(ROOT / macro_dir) if not Path(ROOT / macro_dir).is_absolute() else macro_dir,
        "--broker-utc-offset-minutes",
        str(broker_utc_offset_minutes),
        "--expected-login",
        str(expected_login),
        "--ledger",
        str(ledger_path),
        "--interval-seconds",
        str(interval_seconds),
        "--bars",
        str(bars),
        "--context-bars",
        str(context_bars),
    ]
    if stop_file:
        cmd += ["--stop-file", stop_file]
    return cmd


def _demo_send_command(
    *,
    profile_rel: str,
    macro_dir: str,
    ledger_path: str,
    manifest_rel: str,
    broker_utc_offset_minutes: int,
    expected_login: int,
    override: bool = False,
) -> list[str]:
    cmd = _python_command() + [
        "-m",
        "goldbot.cli",
        "demo-send",
        "--manifest",
        str(ROOT / manifest_rel),
        "--account-profile",
        str(ROOT / profile_rel),
        "--macro-dir",
        str(ROOT / macro_dir) if not Path(ROOT / macro_dir).is_absolute() else macro_dir,
        "--broker-utc-offset-minutes",
        str(broker_utc_offset_minutes),
        "--confirm-broker-offset",
        "--expected-login",
        str(expected_login),
        "--send",
        "--confirm-order",
        "SEND DEMO ORDER",
        "--ledger",
        str(ledger_path),
        "--report",
        str(ROOT / "reports" / "demo.execution.json"),
    ]
    if override:
        cmd.append("--override-blocks")
    return cmd


def _decline_command(
    *,
    ledger_path: str,
    reason: str,
) -> list[str]:
    return _python_command() + [
        "-m",
        "goldbot.cli",
        "decline",
        "--ledger",
        str(ledger_path),
        "--reason",
        reason,
    ]


def _close_position_command(
    *,
    ticket: str,
    reason: str,
) -> list[str]:
    return _python_command() + [
        "-m",
        "goldbot.cli",
        "close-position",
        "--ticket",
        str(ticket),
        "--symbol",
        "XAUUSD",
        "--reason",
        reason,
        "--ledger",
        EXECUTION_LEDGER,
    ]


def _refresh_macro_command() -> list[str]:
    return _python_command() + [
        "-m",
        "goldbot.cli",
        "refresh-macro",
        "--manifest",
        str(ROOT / "config" / "scope.gold.v1.yaml"),
        "--macro-dir",
        str(ROOT / "data" / "raw" / "macro"),
        "--ledger",
        EXECUTION_LEDGER,
    ]


def _exec_quality(connection) -> dict[str, Any]:
    """Execution-quality snapshot for the dashboard: blackout, stress, spread, tick check, exits.

    ``connection`` may be closed already by the caller; a fresh read-only
    handle is opened if so.
    """
    from .econ_calendar import blackout_at, upcoming
    from .spread import report as _spread_report

    now = _utc_now()
    try:
        blocked, event, minutes_until = blackout_at(now)
    except Exception:  # noqa: BLE001 - never crash the dashboard on calendar math
        blocked, event, minutes_until = False, None, None

    halt_path = ROOT / "reports" / "kill-switch.halt"
    out: dict[str, Any] = {
        "blackout_active": bool(blocked),
        "active_event": event.to_dict() if blocked and event else None,
        "minutes_until_blackout": minutes_until,
        "next_event": None,
        "news_stress": None,
        "spread": None,
        "tick_backtest": None,
        "exits": 0,
        "halt_flag": halt_path.exists(),
    }

    try:
        horizon = upcoming(now, 30)
        if horizon:
            nxt = horizon[0]
            out["next_event"] = {
                "label": nxt.label,
                "category": nxt.category,
                "starts_at_utc": nxt.starts_at_utc.isoformat(),
                "blackout_start_utc": nxt.blackout_start_utc.isoformat(),
                "blackout_end_utc": nxt.blackout_end_utc.isoformat(),
            }
    except Exception:  # noqa: BLE001 - best effort
        out["next_event"] = None

    try:
        events_row = connection.execute(
            "SELECT COUNT(*) AS n FROM strategy_decisions WHERE decision_type = 'EXIT'"
        ).fetchone()
        out["exits"] = int(events_row[0]) if events_row else 0
    except Exception:  # noqa: BLE001
        # connection might already be closed; open a fresh one
        try:
            fresh = _read_only_connection(ROOT / "reports" / "demo.execution.ledger.sqlite")
            events_row = fresh.execute(
                "SELECT COUNT(*) AS n FROM strategy_decisions WHERE decision_type = 'EXIT'"
            ).fetchone()
            out["exits"] = int(events_row[0]) if events_row else 0
            fresh.close()
        except Exception:  # noqa: BLE001
            out["exits"] = 0

    spread_csv = ROOT / "reports" / "spread_history.csv"
    if spread_csv.exists():
        try:
            spread = _spread_report(spread_csv)
            overall = spread.get("overall") or {}
            out["spread"] = {
                "samples": spread.get("samples", 0),
                "p50": overall.get("p50"),
                "p90": overall.get("p90"),
                "p99": overall.get("p99"),
                "max": overall.get("max"),
            }
        except Exception:  # noqa: BLE001
            out["spread"] = None

    stress_json = ROOT / "reports" / "news_stress.json"
    if stress_json.exists():
        try:
            stress = json.loads(stress_json.read_text(encoding="utf-8"))
            out["news_stress"] = stress.get("summary")
        except Exception:  # noqa: BLE001
            out["news_stress"] = None

    tick_json = ROOT / "reports" / "tick_backtest.json"
    if tick_json.exists():
        try:
            tick = json.loads(tick_json.read_text(encoding="utf-8"))
            summary = tick.get("summary") or {}
            out["tick_backtest"] = {
                "bars_measured": summary.get("bars_measured"),
                "spread_at_open_p50": (summary.get("spread_at_open") or {}).get("p50"),
                "spread_at_open_p99": (summary.get("spread_at_open") or {}).get("p99"),
                "bars_over_limit_pct": summary.get("bars_over_limit_pct"),
                "window_from": (tick.get("window") or {}).get("from"),
            }
        except Exception:  # noqa: BLE001
            out["tick_backtest"] = None

    return out


def _wgc_check_command() -> list[str]:
    return _python_command() + [
        "-m",
        "goldbot.cli",
        "wgc-check",
        "--wgc-dir",
        str(ROOT / "data" / "manual" / "wgc"),
        "--ledger",
        EXECUTION_LEDGER,
    ]


def _kill_switch_command(*, expected_login: int, broker_utc_offset_minutes: int) -> list[str]:
    return _python_command() + [
        "-m",
        "goldbot.cli",
        "kill-switch",
        "--ledger",
        EXECUTION_LEDGER,
        "--expected-login",
        str(expected_login),
        "--confirm-flatten",
        "FLATTEN AND HALT",
        "--broker-utc-offset-minutes",
        str(broker_utc_offset_minutes),
    ]


def _start_agent(
    server: "LiveDashboardServer",
) -> dict[str, Any]:
    global _AGENT_PROCESS, _AGENT_STARTED_AT, _AGENT_STOP_FILE
    with _AGENT_LOCK:
        if _AGENT_PROCESS is not None and _AGENT_PROCESS.poll() is None:
            return {"started": False, "error": "agent is already running", "pid": _AGENT_PROCESS.pid}

        stop_file = Path(server.ledger_path).parent / f"agent-stop-{_utc_now().strftime('%Y%m%d%H%M%S%f')}.flag"
        _AGENT_STOP_FILE = stop_file
        cmd = _agent_command(
            profile_rel=server.profile,
            macro_dir=server.macro_dir,
            ledger_path=EXECUTION_LEDGER,
            manifest_rel="config/scope.gold.v1.yaml",
            broker_utc_offset_minutes=server.broker_utc_offset_minutes,
            expected_login=server.expected_login,
            interval_seconds=server.interval_seconds,
            bars=server.bars,
            context_bars=server.context_bars,
            stop_file=str(stop_file),
        )
        _AGENT_LOG.clear()
        _append_log("INFO", f"spawning agent: {' '.join(cmd)}")
        try:
            process = subprocess.Popen(
                cmd,
                cwd=str(ROOT),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                bufsize=1,
                text=True,
            )
        except Exception as exc:  # noqa: BLE001
            _append_log("ERROR", f"failed to start agent: {type(exc).__name__}: {exc}")
            return {"started": False, "error": f"{type(exc).__name__}: {exc}"}

        _AGENT_PROCESS = process
        _AGENT_STARTED_AT = _utc_now()

        def _read_output() -> None:
            assert process.stdout is not None
            for line in process.stdout:
                _AGENT_LOG.append(line.rstrip("\n"))
            process.wait()
            exit_code = process.poll()
            _append_log("INFO", f"agent exited with code {exit_code}")

        threading.Thread(target=_read_output, daemon=True).start()
        return {"started": True, "pid": process.pid, "cmd": cmd}


def _stop_agent(server: "LiveDashboardServer") -> dict[str, Any]:
    global _AGENT_PROCESS
    with _AGENT_LOCK:
        process = _AGENT_PROCESS
        if process is None or process.poll() is not None:
            return {"stopped": False, "error": "agent is not running"}
        if _AGENT_STOP_FILE is not None:
            try:
                _AGENT_STOP_FILE.write_text("stop", encoding="utf-8")
            except Exception:
                pass
        try:
            process.wait(timeout=5.0)
        except subprocess.TimeoutExpired:
            try:
                process.terminate()
                process.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        _AGENT_PROCESS = None
        return {"stopped": True}


def _same_origin(origin: str, host_header: str) -> bool:
    if not origin:
        return True
    parsed = urlparse(origin)
    return parsed.netloc.lower() == host_header.lower()


class LiveDashboardServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        server_address: tuple[str, int],
        handler_class: type[BaseHTTPRequestHandler],
        *,
        ledger_path: str | Path,
        refresh_seconds: float,
        max_rows: int,
        profile: str = "config/profiles/micro_50.yaml",
        macro_dir: str = "data/raw/macro",
        manifest_rel: str = "config/scope.gold.v1.yaml",
        broker_utc_offset_minutes: int = 120,
        expected_login: int = 106806652,
        interval_seconds: float = DEFAULT_INTERVAL_SECONDS,
        bars: int = DEFAULT_BARS,
        context_bars: int = DEFAULT_CONTEXT_BARS,
    ) -> None:
        super().__init__(server_address, handler_class)
        self.ledger_path = Path(ledger_path)
        self.refresh_seconds = max(1.0, float(refresh_seconds))
        self.max_rows = max(1, int(max_rows))
        self.profile = profile
        self.macro_dir = macro_dir
        self.manifest_rel = manifest_rel
        self.broker_utc_offset_minutes = int(broker_utc_offset_minutes)
        self.expected_login = int(expected_login)
        self.interval_seconds = float(interval_seconds)
        self.bars = int(bars)
        self.context_bars = int(context_bars)


def _json_response(
    handler: BaseHTTPRequestHandler,
    status: HTTPStatus,
    payload: dict[str, Any],
) -> None:
    body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("X-Content-Type-Options", "nosniff")
    handler.send_header(
        "Content-Security-Policy",
        CSP,
    )
    handler.end_headers()
    handler.wfile.write(body)
    try:
        handler.wfile.flush()
    except OSError:
        pass


class LiveDashboardHandler(BaseHTTPRequestHandler):
    server_version = "GoldbotLiveDashboard/1.0"

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        path = urlparse(self.path).path

        if path in ("/", "/index.html"):
            try:
                state = self._state()
                body = render_page(state, self.server.refresh_seconds).encode("utf-8")
                self._send_html(body)
            except Exception as exc:  # noqa: BLE001 - server must report, not crash
                self._send_html(
                    f"dashboard-live error: {type(exc).__name__}: {exc}".encode("utf-8"),
                    status=HTTPStatus.INTERNAL_SERVER_ERROR,
                )
            return

        if path == "/api/state":
            _json_response(self, HTTPStatus.OK, {"v": 1, "state": self._state()})
            return

        if path == "/api/agent/status":
            _json_response(self, HTTPStatus.OK, {"v": 1, "agent": _agent_status()})
            return

        if path == "/api/stream":
            self._stream_state()
            return

        if path == "/health":
            _json_response(self, HTTPStatus.OK, {"v": 1, "ok": True, "read_only": True, "no_mt5_connection": True})
            return

        if path == "/favicon.ico":
            self.send_response(HTTPStatus.NO_CONTENT)
            self.end_headers()
            return

        self._send_text(b"not found", status=HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        path = urlparse(self.path).path
        content_type = self.headers.get("Content-Type", "").split(";")[0].strip().lower()
        if content_type != "application/json":
            _json_response(self, HTTPStatus.UNSUPPORTED_MEDIA_TYPE, {"v": 1, "ok": False, "error": "Content-Type must be application/json"})
            return
        if not _same_origin(self.headers.get("Origin", ""), self.headers.get("Host", "")):
            _json_response(self, HTTPStatus.FORBIDDEN, {"v": 1, "ok": False, "error": "cross-origin request rejected"})
            return

        length = int(self.headers.get("Content-Length") or 0)
        try:
            body = self.rfile.read(length) if length else b""
            payload = json.loads(body) if body else {}
        except (ValueError, json.JSONDecodeError):
            _json_response(self, HTTPStatus.BAD_REQUEST, {"v": 1, "ok": False, "error": "invalid JSON body"})
            return

        if path == "/api/agent/start":
            result = _start_agent(self.server)
            ok = result.get("started", False)
            _json_response(self, HTTPStatus.OK if ok else HTTPStatus.CONFLICT, {"v": 1, "ok": ok, **result})
            return
        if path == "/api/agent/stop":
            result = _stop_agent(self.server)
            ok = result.get("stopped", False)
            _json_response(self, HTTPStatus.OK if ok else HTTPStatus.CONFLICT, {"v": 1, "ok": ok, **result})
            return
        if path == "/api/agent/execute":
            phrase = str(payload.get("confirm") or "")
            if phrase != "SEND DEMO ORDER":
                _json_response(self, HTTPStatus.BAD_REQUEST, {"v": 1, "ok": False, "error": "confirm must be SEND DEMO ORDER"})
                return
            cmd = _demo_send_command(
                profile_rel=self.server.profile,
                macro_dir=self.server.macro_dir,
                ledger_path=EXECUTION_LEDGER,
                manifest_rel=self.server.manifest_rel,
                broker_utc_offset_minutes=self.server.broker_utc_offset_minutes,
                expected_login=self.server.expected_login,
                override=bool(payload.get("override") or False),
            )
            _AGENT_LOG.clear()
            _AGENT_LOG.append(f"[INFO] spawning guarded demo order: {' '.join(cmd)}")
            try:
                proc = subprocess.run(
                    cmd,
                    cwd=str(ROOT),
                    capture_output=True,
                    text=True,
                    timeout=90,
                )
            except subprocess.TimeoutExpired:
                _append_log("ERROR", "guarded demo order timed out after 90 seconds")
                _json_response(self, HTTPStatus.GATEWAY_TIMEOUT, {"v": 1, "ok": False, "error": "demo order timed out"})
                return
            stdout = proc.stdout or ""
            stderr = proc.stderr or ""
            stdout_tail = stdout.splitlines()[-20:] if stdout else []
            stderr_tail = stderr.splitlines()[-20:] if stderr else []
            for line in stdout.splitlines():
                _AGENT_LOG.append(line)
            for line in stderr.splitlines():
                _AGENT_LOG.append(f"[STDERR] {line}")
            sent = "sent: True" in stdout
            _append_log(
                "INFO",
                f"guarded demo order exited with code {proc.returncode}; sent={sent}",
            )
            _json_response(
                self,
                HTTPStatus.OK,
                {
                    "v": 1,
                    "ok": sent,
                    "sent": sent,
                    "exit_code": proc.returncode,
                    "stdout_tail": stdout_tail,
                    "stderr_tail": stderr_tail,
                },
            )
            return
        if path == "/api/agent/decline":
            reason = str(payload.get("reason") or "").strip()
            if not reason:
                _json_response(self, HTTPStatus.BAD_REQUEST, {"v": 1, "ok": False, "error": "reason is required"})
                return
            ledger = self.server.ledger_path
            cmd = _decline_command(ledger_path=str(ledger), reason=reason)
            try:
                subprocess.run(cmd, cwd=str(ROOT), check=True, timeout=30)
            except Exception as exc:  # noqa: BLE001
                _json_response(self, HTTPStatus.INTERNAL_SERVER_ERROR, {"v": 1, "ok": False, "error": f"{type(exc).__name__}: {exc}"})
                return
            _append_log("INFO", f"decline logged: {reason}")
            _json_response(self, HTTPStatus.OK, {"v": 1, "ok": True})
            return

        if path == "/api/wgc/reconfirm":
            cmd = _wgc_check_command()
            _append_log("INFO", "WGC reconfirm requested from dashboard")
            try:
                proc = subprocess.run(
                    cmd,
                    cwd=str(ROOT),
                    capture_output=True,
                    text=True,
                    timeout=90,
                )
            except subprocess.TimeoutExpired:
                _json_response(
                    self,
                    HTTPStatus.GATEWAY_TIMEOUT,
                    {"v": 1, "ok": False, "error": "wgc-check timed out"},
                )
                return
            output = ((proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")).strip()
            for line in output.splitlines():
                _append_log("INFO", f"[wgc-check] {line}")
            ok = proc.returncode == 0
            _json_response(
                self,
                HTTPStatus.OK if ok else HTTPStatus.INTERNAL_SERVER_ERROR,
                {
                    "v": 1,
                    "ok": ok,
                    "exit_code": proc.returncode,
                    "output": output.splitlines()[-20:],
                },
            )
            return

        if path == "/api/agent/kill-switch":
            phrase = str(payload.get("confirm") or "")
            if phrase != "FLATTEN AND HALT":
                _json_response(self, HTTPStatus.BAD_REQUEST, {"v": 1, "ok": False, "error": "confirm must be FLATTEN AND HALT"})
                return
            cmd = _kill_switch_command(
                expected_login=self.server.expected_login,
                broker_utc_offset_minutes=self.server.broker_utc_offset_minutes,
            )
            _append_log("CRITICAL", f"kill switch requested from dashboard: {' '.join(cmd)}")
            try:
                proc = subprocess.run(
                    cmd,
                    cwd=str(ROOT),
                    capture_output=True,
                    text=True,
                    timeout=90,
                )
            except subprocess.TimeoutExpired:
                _append_log("ERROR", "kill switch timed out after 90s")
                _json_response(self, HTTPStatus.GATEWAY_TIMEOUT, {"v": 1, "ok": False, "error": "kill switch timed out"})
                return
            stdout = proc.stdout or ""
            stderr = proc.stderr or ""
            for line in stdout.splitlines():
                _append_log("CRITICAL", f"[kill-switch] {line}")
            for line in stderr.splitlines():
                _append_log("ERROR", f"[kill-switch] {line}")
            ok = proc.returncode == 0
            _json_response(
                self,
                HTTPStatus.OK if ok else HTTPStatus.INTERNAL_SERVER_ERROR,
                {
                    "v": 1,
                    "ok": ok,
                    "exit_code": proc.returncode,
                    "stdout_tail": stdout.splitlines()[-20:],
                    "stderr_tail": stderr.splitlines()[-20:],
                },
            )
            return

        if path == "/api/refresh-macro":
            cmd = _refresh_macro_command()
            _append_log("INFO", "macro refresh requested from dashboard")
            try:
                proc = subprocess.run(
                    cmd,
                    cwd=str(ROOT),
                    capture_output=True,
                    text=True,
                    timeout=120,
                )
            except subprocess.TimeoutExpired:
                _json_response(
                    self,
                    HTTPStatus.GATEWAY_TIMEOUT,
                    {"v": 1, "ok": False, "error": "macro refresh timed out"},
                )
                return
            output = (
                (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")
            ).strip()
            for line in output.splitlines():
                _append_log("INFO", f"[refresh-macro] {line}")
            ok = proc.returncode == 0
            _json_response(
                self,
                HTTPStatus.OK if ok else HTTPStatus.INTERNAL_SERVER_ERROR,
                {
                    "v": 1,
                    "ok": ok,
                    "exit_code": proc.returncode,
                    "output": output.splitlines()[-20:],
                },
            )
            return

        if path == "/api/agent/close-position":
            confirm = str(payload.get("confirm") or "")
            if confirm != "END TRADE":
                _json_response(self, HTTPStatus.BAD_REQUEST, {"v": 1, "ok": False, "error": "confirm must be END TRADE"})
                return
            ticket = str(payload.get("ticket") or "").strip()
            if not ticket:
                _json_response(self, HTTPStatus.BAD_REQUEST, {"v": 1, "ok": False, "error": "ticket is required"})
                return
            cmd = _close_position_command(ticket=ticket, reason="operator request")
            _AGENT_LOG.clear()
            _append_log("INFO", f"spawning close-position: {' '.join(cmd)}")
            try:
                proc = subprocess.run(
                    cmd,
                    cwd=str(ROOT),
                    capture_output=True,
                    text=True,
                    timeout=60,
                )
            except subprocess.TimeoutExpired:
                _append_log("ERROR", "close-position timed out after 60s")
                _json_response(self, HTTPStatus.GATEWAY_TIMEOUT, {"v": 1, "ok": False, "error": "close-position timed out"})
                return
            stdout = proc.stdout or ""
            stderr = proc.stderr or ""
            stdout_tail = stdout.splitlines()[-20:] if stdout else []
            stderr_tail = stderr.splitlines()[-20:] if stderr else []
            for line in stdout.splitlines():
                _AGENT_LOG.append(line)
            for line in stderr.splitlines():
                _AGENT_LOG.append(f"[STDERR] {line}")
            ok = proc.returncode == 0
            _append_log("INFO", f"close-position exited with code {proc.returncode}; ok={ok}")
            _json_response(
                self,
                HTTPStatus.OK,
                {
                    "v": 1,
                    "ok": ok,
                    "exit_code": proc.returncode,
                    "stdout_tail": stdout_tail,
                    "stderr_tail": stderr_tail,
                },
            )
            return

        if path == "/api/agent/profile":
            key = str(payload.get("profile") or "").strip()
            rel = _PROFILE_KEYS.get(key)
            if rel is None:
                _json_response(
                    self,
                    HTTPStatus.BAD_REQUEST,
                    {"v": 1, "ok": False, "error": f"unknown profile: {key!r}"},
                )
                return
            self.server.profile = rel
            _json_response(
                self,
                HTTPStatus.OK,
                {"v": 1, "ok": True, "profile": _profile_spec(self.server.profile)},
            )
            return

        if path == "/api/server/shutdown":
            if payload.get("confirm") != "SHUT DASHBOARD":
                _json_response(self, HTTPStatus.BAD_REQUEST, {"v": 1, "ok": False, "error": "confirm must be SHUT DASHBOARD"})
                return
            _stop_agent(self.server)
            _append_log("INFO", "dashboard shutdown requested by user")

            def _shutdown() -> None:
                time.sleep(0.2)
                self.server.shutdown()

            threading.Thread(target=_shutdown, daemon=True).start()
            _json_response(self, HTTPStatus.OK, {"v": 1, "ok": True, "shutdown": True, "message": "dashboard is shutting down"})
            return

        _json_response(self, HTTPStatus.NOT_FOUND, {"v": 1, "ok": False, "error": "not found"})

    def do_HEAD(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        self.do_GET()

    def _state(self) -> dict[str, Any]:
        state = load_state(
            self.server.ledger_path,
            max_rows=self.server.max_rows,
            stale_after_seconds=max(30.0, self.server.refresh_seconds * 3.0),
            agent_status=_agent_status(),
            log_lines=_agent_log_lines(),
        )
        state["profile_spec"] = _profile_spec(self.server.profile)
        state["selected_profile"] = _profile_key(self.server.profile)
        state["expected_login"] = self.server.expected_login
        state["notifications"] = _build_notifications(state)
        return state

    def _stream_state(self) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header(
            "Content-Security-Policy",
            CSP,
        )
        self.end_headers()
        try:
            while True:
                state = self._state()
                payload = {"v": 1, "agent": state["agent"], "state": state}
                message = f"event: state\ndata: {json.dumps(payload, default=str, ensure_ascii=False)}\n\n"
                self.wfile.write(message.encode("utf-8"))
                self.wfile.flush()
                threading.Event().wait(self.server.refresh_seconds)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
            return

    def _send_html(self, body: bytes, status: HTTPStatus = HTTPStatus.OK) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header(
            "Content-Security-Policy",
            CSP,
        )
        self.end_headers()
        self.wfile.write(body)
        try:
            self.wfile.flush()
        except OSError:
            pass

    def _send_text(self, body: bytes, status: HTTPStatus = HTTPStatus.OK) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)
        try:
            self.wfile.flush()
        except OSError:
            pass

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
        return


def _validate_host(host: str) -> None:
    if host not in {"127.0.0.1", "localhost"}:
        raise ValueError(
            "dashboard-live binds to loopback only; use 127.0.0.1 or localhost. "
            "Do not expose the trading ledger dashboard to a network."
        )


def make_server(
    *,
    ledger_path: str | Path,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    refresh_seconds: float = DEFAULT_REFRESH_SECONDS,
    max_rows: int = DEFAULT_MAX_ROWS,
    profile: str = "config/profiles/micro_50.yaml",
    macro_dir: str = "data/raw/macro",
    manifest_rel: str = "config/scope.gold.v1.yaml",
    broker_utc_offset_minutes: int = 120,
    expected_login: int = 106806652,
    interval_seconds: float = DEFAULT_INTERVAL_SECONDS,
    bars: int = DEFAULT_BARS,
    context_bars: int = DEFAULT_CONTEXT_BARS,
) -> LiveDashboardServer:
    _validate_host(host)
    if not 0 <= int(port) <= 65535:
        raise ValueError("port must be between 0 and 65535")
    return LiveDashboardServer(
        (host, int(port)),
        LiveDashboardHandler,
        ledger_path=ledger_path,
        refresh_seconds=refresh_seconds,
        max_rows=max_rows,
        profile=profile,
        macro_dir=macro_dir,
        manifest_rel=manifest_rel,
        broker_utc_offset_minutes=broker_utc_offset_minutes,
        expected_login=expected_login,
        interval_seconds=interval_seconds,
        bars=bars,
        context_bars=context_bars,
    )


def serve_live_dashboard(
    *,
    ledger_path: str | Path = DEFAULT_LEDGER,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    refresh_seconds: float = DEFAULT_REFRESH_SECONDS,
    max_rows: int = DEFAULT_MAX_ROWS,
    profile: str = "config/profiles/micro_50.yaml",
    macro_dir: str = "data/raw/macro",
    manifest_rel: str = "config/scope.gold.v1.yaml",
    broker_utc_offset_minutes: int = 120,
    expected_login: int = 106806652,
    interval_seconds: float = DEFAULT_INTERVAL_SECONDS,
    bars: int = DEFAULT_BARS,
    context_bars: int = DEFAULT_CONTEXT_BARS,
    open_browser: bool = False,
) -> None:
    server = make_server(
        ledger_path=ledger_path,
        host=host,
        port=port,
        refresh_seconds=refresh_seconds,
        max_rows=max_rows,
        profile=profile,
        macro_dir=macro_dir,
        manifest_rel=manifest_rel,
        broker_utc_offset_minutes=broker_utc_offset_minutes,
        expected_login=expected_login,
        interval_seconds=interval_seconds,
        bars=bars,
        context_bars=context_bars,
    )
    bound_host, bound_port = server.server_address[:2]
    url = f"http://{bound_host}:{bound_port}/"
    print(f"[dashboard-live] serving read-only dashboard at {url}")
    print(f"[dashboard-live] ledger: {Path(ledger_path).resolve()}")
    print("[dashboard-live] press Ctrl+C to stop")
    if open_browser:
        import threading as _threading
        import webbrowser as _webbrowser
        _threading.Timer(0.5, _webbrowser.open, args=(url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        _append_log("INFO", "dashboard stopped")
        print("\n[dashboard-live] stopped")
    finally:
        _stop_agent(server)
        server.server_close()