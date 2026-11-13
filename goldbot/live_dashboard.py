"""Read-only live dashboard for the goldbot SQLite ledger.

The server never imports MetaTrader5 and never writes to the ledger.  It opens a
fresh SQLite connection in ``mode=ro`` plus ``PRAGMA query_only=ON`` for each
request.  The browser only sees a local, read-only HTML/JSON view.

This is deliberately built on the Python standard library so the dashboard does
not add a web-framework dependency to the trading process.
"""

from __future__ import annotations

import html
import json
import sqlite3
import threading
import webbrowser
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse

DEFAULT_LEDGER = Path("reports/goldbot.ledger.sqlite")
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000
DEFAULT_REFRESH_SECONDS = 2.0
DEFAULT_MAX_ROWS = 25
DEFAULT_STALE_AFTER_SECONDS = 60.0


# ---------------------------------------------------------------------------
# State loading
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


def load_state(
    ledger_path: str | Path,
    *,
    max_rows: int = DEFAULT_MAX_ROWS,
    now: datetime | None = None,
    stale_after_seconds: float = DEFAULT_STALE_AFTER_SECONDS,
) -> dict[str, Any]:
    """Read the ledger and return a JSON-safe dashboard state.

    The ledger is opened read-only.  Missing files and missing tables are
    reported in the state rather than crashing the dashboard server.
    """
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
    }

    if not path.exists():
        state["message"] = "Ledger not found. Start a paper or demo run, or check --ledger."
        return state

    try:
        stat = path.stat()
        state["ledger_size_bytes"] = stat.st_size
        state["ledger_mtime_utc"] = datetime.fromtimestamp(
            stat.st_mtime, tz=timezone.utc
        ).isoformat()
    except OSError as exc:
        state["message"] = f"Could not stat ledger: {exc}"
        return state

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
        ):
            count, error = _count(connection, table)
            counts[table] = count
            _add_error(errors, error)
    finally:
        connection.close()

    run_summary = _json_dict(latest_run.get("summary_json")) if latest_run else {}
    snapshot_market = _json_dict(latest_snapshot.get("market_json")) if latest_snapshot else {}
    risk_state = _json_dict(snapshot_market.get("risk_state"))
    data_quality = (
        _json_dict(latest_snapshot.get("data_quality_json")) if latest_snapshot else {}
    )
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
                    "fundamentals": _json_dict(latest_snapshot.get("fundamentals_json")),
                    "technicals": _json_dict(latest_snapshot.get("technicals_json")),
                    "fusion": _json_dict(latest_snapshot.get("fusion_json")),
                    "overseer": _json_dict(latest_snapshot.get("overseer_json")),
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
        }
    )
    return state


# ---------------------------------------------------------------------------
# HTML rendering
# ---------------------------------------------------------------------------
def _e(value: Any) -> str:
    return html.escape("" if value is None else str(value))


def _fmt_num(value: Any, digits: int = 2) -> str:
    number = _as_float(value)
    if number is None:
        return "n/a"
    return f"{number:,.{digits}f}"


def _fmt_signed(value: Any, digits: int = 3) -> str:
    number = _as_float(value)
    if number is None:
        return "n/a"
    return f"{number:+,.{digits}f}"


def _fmt_pct(value: Any) -> str:
    number = _as_float(value)
    if number is None:
        return "n/a"
    return f"{number:.2f}%"


def _fmt_dt(value: Any) -> str:
    parsed = _parse_dt(value)
    if parsed is None:
        return "n/a" if value in (None, "") else str(value)
    return parsed.strftime("%Y-%m-%d %H:%M:%S UTC")


def _fmt_age(seconds: Any) -> str:
    number = _as_float(seconds)
    if number is None:
        return "n/a"
    total = max(0, int(number))
    if total < 60:
        return f"{total}s ago"
    if total < 3600:
        return f"{total // 60}m {total % 60}s ago"
    if total < 86400:
        return f"{total // 3600}h {(total % 3600) // 60}m ago"
    return f"{total // 86400}d ago"


def _short(value: Any, limit: int = 220) -> str:
    text = "" if value is None else str(value)
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def _badge(value: Any, css: str = "") -> str:
    classes = "badge" + (f" {css}" if css else "")
    return f'<span class="{classes}">{_e(value)}</span>'


def _table(headers: list[str], rows: list[list[str]], empty: str) -> str:
    if not rows:
        return f'<p class="muted">{_e(empty)}</p>'
    head = "".join(f"<th>{_e(value)}</th>" for value in headers)
    body = "\n".join(
        "<tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>"
        for row in rows
    )
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


def _mode_class(mode: str) -> str:
    if "paper" in mode:
        return "paper"
    if "demo" in mode or "live" in mode:
        return "live"
    return "unknown"


def _banners(state: dict[str, Any]) -> str:
    banners: list[tuple[str, str]] = []
    mode = str(state.get("mode") or "unknown")
    if not state.get("ledger_exists"):
        banners.append(("warn", "Waiting for ledger — no file exists at the configured path."))
    elif not state.get("ok"):
        banners.append(("danger", f"Ledger read failed: {state.get('message')}"))
    if state.get("stale"):
        banners.append(
            (
                "danger",
                "STALE DATA — last activity was "
                f"{_fmt_age(state.get('last_activity_age_seconds'))}.",
            )
        )
    if state.get("is_finished"):
        banners.append(
            (
                "info",
                f"Run finished at {_fmt_dt(state.get('finished_at_utc'))}. "
                "This is a completed run, not a live execution loop.",
            )
        )
    if "paper" in mode:
        banners.append(("info", "PAPER REPLAY — no broker orders were sent."))
    elif "demo" in mode or "live" in mode:
        banners.append(
            (
                "ok",
                "DEMO LIVE — broker orders may be present. This view is read-only.",
            )
        )
    if state.get("high_risk_override"):
        banners.append(
            (
                "danger",
                "HIGH RISK OVERRIDE — not promotion-eligible. One normal stop can be a "
                "double-digit account loss.",
            )
        )
    if state.get("promotion_eligible") is False and not state.get("high_risk_override"):
        banners.append(("warn", "This profile is not promotion-eligible."))
    errors = state.get("errors") or []
    if errors:
        banners.append(("warn", "Ledger warnings: " + "; ".join(errors[:3])))
    banners.append(
        (
            "ok",
            "Read-only dashboard. It does not import MetaTrader5, connect to the broker, "
            "place orders, or change scope.",
        )
    )
    return "\n".join(
        f'<div class="banner {css}">{_e(text)}</div>' for css, text in banners
    )


def _cards(state: dict[str, Any]) -> str:
    counts = state.get("counts") or {}
    open_positions = state.get("open_positions") or []
    latest_decision = state.get("latest_decision") or {}
    last_activity = (
        "run finished"
        if state.get("is_finished")
        else _fmt_age(state.get("last_activity_age_seconds"))
    )
    cards = [
        ("Equity", _fmt_num(state.get("equity"), 2)),
        ("Daily PnL", _fmt_num(state.get("daily_pnl"), 2)),
        ("Drawdown", _fmt_pct(state.get("current_drawdown_pct"))),
        ("Open positions", _fmt_num(len(open_positions), 0)),
        ("Orders", _fmt_num(counts.get("orders"), 0)),
        ("Fills", _fmt_num(counts.get("fills"), 0)),
        ("Trades", _fmt_num(counts.get("positions"), 0)),
        ("Decisions", _fmt_num(counts.get("strategy_decisions"), 0)),
        (
            "Last decision",
            _e(latest_decision.get("decision_type") or "n/a"),
        ),
        ("Last activity", _e(last_activity)),
    ]
    return "\n".join(
        f'<div class="card"><div class="label">{_e(label)}</div>'
        f'<div class="value">{value}</div></div>'
        for label, value in cards
    )


def _risk_table(state: dict[str, Any]) -> str:
    risk = state.get("risk_state") or {}
    if not risk:
        return '<p class="muted">No risk state in the latest snapshot.</p>'
    rows = [
        ["Equity", _fmt_num(risk.get("equity"), 2)],
        ["Starting equity", _fmt_num(risk.get("starting_equity"), 2)],
        ["Daily start equity", _fmt_num(risk.get("daily_start_equity"), 2)],
        ["Daily PnL", _fmt_num(risk.get("daily_pnl"), 2)],
        ["Current drawdown", _fmt_pct(risk.get("current_drawdown_pct"))],
        ["Daily halt", _e("YES" if risk.get("daily_halt") else "no")],
        ["Max-drawdown halt", _e("YES" if risk.get("max_drawdown_halt") else "no")],
        ["Kill switch", _e("YES" if risk.get("kill_switch") else "no")],
    ]
    return _table(["Field", "Value"], rows, "No risk state.")


def _open_position_table(state: dict[str, Any]) -> str:
    position = state.get("open_position")
    if not position:
        return '<p class="muted">No open position.</p>'
    rows = [
        [
            f"<code>{_e(position.get('position_id'))}</code>",
            _e(position.get("side")),
            _fmt_num(position.get("volume"), 2),
            _fmt_num(position.get("open_price"), 2),
            _fmt_num(position.get("stop_loss"), 2),
            _fmt_num(position.get("take_profit"), 2),
            _fmt_dt(position.get("open_time_utc")),
            _e(position.get("broker_position_ticket") or "none"),
            _badge(position.get("state") or "n/a"),
        ]
    ]
    return _table(
        [
            "Position",
            "Side",
            "Lots",
            "Entry",
            "Stop",
            "Target",
            "Opened",
            "Broker ticket",
            "State",
        ],
        rows,
        "No open position.",
    )


def _orders_table(state: dict[str, Any]) -> str:
    rows = []
    for order in state.get("orders") or []:
        state_value = str(order.get("state") or "")
        css = "paper" if state_value.startswith("PAPER_") else "live"
        rows.append(
            [
                f"<code>{_e(order.get('order_id'))}</code>",
                _fmt_dt(order.get("submitted_at_utc")),
                _e(order.get("side")),
                _e(order.get("order_type")),
                _fmt_num(order.get("volume"), 2),
                _fmt_num(order.get("price"), 2),
                _fmt_num(order.get("stop_loss"), 2),
                _fmt_num(order.get("take_profit"), 2),
                _badge(state_value or "n/a", css),
                _e(order.get("broker_order_ticket") or "none"),
            ]
        )
    return _table(
        [
            "Order",
            "Submitted",
            "Side",
            "Type",
            "Lots",
            "Price",
            "Stop",
            "Target",
            "State",
            "Broker ticket",
        ],
        rows,
        "No orders recorded.",
    )


def _fills_table(state: dict[str, Any]) -> str:
    rows = []
    for fill in state.get("fills") or []:
        rows.append(
            [
                f"<code>{_e(fill.get('fill_id'))}</code>",
                f"<code>{_e(fill.get('order_id'))}</code>",
                _fmt_dt(fill.get("filled_at_utc")),
                _e(fill.get("side")),
                _fmt_num(fill.get("volume"), 2),
                _fmt_num(fill.get("price"), 2),
                _fmt_num(fill.get("commission"), 2),
                _e(fill.get("broker_deal_ticket") or "none"),
            ]
        )
    return _table(
        ["Fill", "Order", "Filled", "Side", "Lots", "Price", "Commission", "Broker deal"],
        rows,
        "No fills recorded.",
    )


def _positions_table(state: dict[str, Any]) -> str:
    rows = []
    for position in state.get("positions") or []:
        rows.append(
            [
                f"<code>{_e(position.get('position_id'))}</code>",
                _e(position.get("side")),
                _fmt_num(position.get("volume"), 2),
                _fmt_num(position.get("open_price"), 2),
                _fmt_num(position.get("close_price"), 2),
                _fmt_num(position.get("realized_pnl"), 2),
                _e(position.get("close_reason") or "open"),
                _badge(position.get("state") or "n/a"),
            ]
        )
    return _table(
        ["Position", "Side", "Lots", "Open", "Close", "PnL", "Reason", "State"],
        rows,
        "No positions recorded.",
    )


def _decisions_table(state: dict[str, Any]) -> str:
    rows = []
    for decision in state.get("decisions") or []:
        reasons = decision.get("reasons") or []
        rows.append(
            [
                _fmt_dt(decision.get("as_of_utc")),
                _e(decision.get("decision_type")),
                _e(decision.get("side")),
                _fmt_signed(decision.get("combined_score"), 3),
                _e(decision.get("overseer_verdict") or "n/a"),
                _e(_short("; ".join(str(value) for value in reasons), 240)),
            ]
        )
    return _table(
        ["As of", "Type", "Side", "Score", "Verdict", "Reasons"],
        rows,
        "No decisions recorded.",
    )


def _logs_table(state: dict[str, Any]) -> str:
    rows = []
    for log in state.get("logs") or []:
        rows.append(
            [
                _fmt_dt(log.get("logged_at_utc")),
                _e(log.get("level")),
                _e(log.get("component")),
                _e(log.get("event_type")),
                _e(_short(log.get("message"), 260)),
            ]
        )
    return _table(
        ["Logged", "Level", "Component", "Event", "Message"],
        rows,
        "No logs recorded.",
    )


def _incidents_table(state: dict[str, Any]) -> str:
    rows = []
    for incident in state.get("incidents") or []:
        rows.append(
            [
                _fmt_dt(incident.get("opened_at_utc")),
                _e(incident.get("severity")),
                _e(incident.get("category")),
                _e(_short(incident.get("summary"), 220)),
                _e(incident.get("action_taken")),
                _fmt_dt(incident.get("closed_at_utc")),
            ]
        )
    return _table(
        ["Opened", "Severity", "Category", "Summary", "Action", "Closed"],
        rows,
        "No incidents recorded.",
    )


def _data_quality_table(state: dict[str, Any]) -> str:
    data_quality = state.get("data_quality") or {}
    rows = [
        [_e(key.replace("_", " ").title()), _e(value)]
        for key, value in data_quality.items()
    ]
    return _table(["Field", "Value"], rows, "No data-quality fields.")


def render_page(state: dict[str, Any], refresh_seconds: float = DEFAULT_REFRESH_SECONDS) -> str:
    refresh = max(1, int(round(float(refresh_seconds))))
    mode = str(state.get("mode") or "unknown")
    mode_css = _mode_class(mode)
    profile = state.get("account_profile") or "n/a"
    generated = _fmt_dt(state.get("generated_at_utc"))
    ledger_path = state.get("ledger_path") or "n/a"
    latest = state.get("latest_decision") or {}
    latest_line = (
        f"{_e(latest.get('decision_type'))} · score "
        f"{_fmt_signed(latest.get('combined_score'), 3)} · "
        f"{_e(latest.get('overseer_verdict') or 'n/a')}"
        if latest
        else "n/a"
    )
    doc = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="{refresh}">
<title>Gold Quantamental — Live Read-Only Dashboard</title>
<style>
:root {{ color-scheme: dark; }}
* {{ box-sizing: border-box; }}
body {{ margin: 0; padding: 24px; background: #020617; color: #e2e8f0;
       font: 14px/1.45 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }}
h1 {{ margin: 0 0 4px; font-size: 26px; }}
h2 {{ margin: 28px 0 10px; font-size: 17px; color: #bae6fd; }}
.muted {{ color: #94a3b8; }}
.meta {{ color: #94a3b8; }}
.meta code {{ color: #cbd5e1; }}
.banner {{ margin: 12px 0; padding: 12px 14px; border-radius: 10px; font-weight: 600; }}
.banner.ok {{ background: #052e16; border: 1px solid #16a34a; color: #bbf7d0; }}
.banner.info {{ background: #0c4a6e; border: 1px solid #0284c7; color: #e0f2fe; }}
.banner.warn {{ background: #422006; border: 1px solid #d97706; color: #fef3c7; }}
.banner.danger {{ background: #450a0a; border: 1px solid #dc2626; color: #fecaca; }}
.grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
         gap: 10px; margin-top: 14px; }}
.card {{ background: #0f172a; border: 1px solid #1e293b; border-radius: 10px; padding: 12px; }}
.card .label {{ color: #94a3b8; font-size: 12px; text-transform: uppercase; letter-spacing: .04em; }}
.card .value {{ font-size: 19px; font-weight: 700; margin-top: 4px; }}
table {{ width: 100%; border-collapse: collapse; background: #0f172a; border-radius: 10px;
         overflow: hidden; }}
th, td {{ padding: 8px 10px; text-align: left; border-bottom: 1px solid #1e293b;
          vertical-align: top; }}
thead th {{ background: #111c33; color: #bae6fd; font-size: 12px; text-transform: uppercase; }}
tbody tr:last-child td {{ border-bottom: none; }}
td {{ font-size: 13px; }}
.two {{ display: grid; grid-template-columns: 1fr 1fr; gap: 18px; }}
@media (max-width: 900px) {{ .two {{ grid-template-columns: 1fr; }} }}
.footer {{ margin-top: 28px; padding-top: 14px; border-top: 1px solid #1e293b;
           color: #94a3b8; font-size: 12px; }}
.badge {{ display: inline-block; padding: 2px 8px; border-radius: 999px; font-size: 12px;
          background: #1e293b; color: #bae6fd; }}
.badge.paper {{ background: #1e3a5f; color: #bae6fd; }}
.badge.live {{ background: #14532d; color: #bbf7d0; }}
.badge.unknown {{ background: #3f3f46; color: #e4e4e7; }}
code {{ color: #cbd5e1; }}
a {{ color: #7dd3fc; }}
</style>
</head>
<body>
<h1>Gold Quantamental — Live Read-Only Dashboard</h1>
<div class="meta">
  mode {_badge(mode, mode_css)} ·
  run <strong>{_e(state.get('run_id') or 'n/a')}</strong> ·
  profile <span class="badge">{_e(profile)}</span> ·
  scope <strong>{_e(state.get('scope_id') or 'n/a')}</strong>
</div>
<div class="meta">
  ledger <code>{_e(ledger_path)}</code> ·
  updated <strong>{_e(generated)}</strong> ·
  auto-refresh every {refresh}s ·
  <a href="/">refresh now</a> ·
  <a href="/api/state">raw JSON</a>
</div>
{_banners(state)}
<p class="muted">Latest decision: {latest_line}</p>
<div class="grid">{_cards(state)}</div>

<div class="two">
  <section>
    <h2>Risk state</h2>
    {_risk_table(state)}
  </section>
  <section>
    <h2>Data quality</h2>
    {_data_quality_table(state)}
  </section>
</div>

<h2>Open position</h2>
{_open_position_table(state)}

<h2>Recent orders</h2>
{_orders_table(state)}

<h2>Recent fills</h2>
{_fills_table(state)}

<h2>Recent positions</h2>
{_positions_table(state)}

<h2>Recent decisions</h2>
{_decisions_table(state)}

<div class="two">
  <section>
    <h2>Recent logs</h2>
    {_logs_table(state)}
  </section>
  <section>
    <h2>Incidents</h2>
    {_incidents_table(state)}
  </section>
</div>

<div class="footer">
  Read-only local dashboard. It reads the SQLite ledger in read-only/query-only mode.
  It does not import MetaTrader5, does not connect to the broker, does not place orders,
  and cannot change scope. The bot process remains the only ledger writer and the only
  component allowed to talk to MT5.
</div>
</body>
</html>
"""
    return doc


# ---------------------------------------------------------------------------
# HTTP server
# ---------------------------------------------------------------------------
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
    ) -> None:
        super().__init__(server_address, handler_class)
        self.ledger_path = Path(ledger_path)
        self.refresh_seconds = max(1.0, float(refresh_seconds))
        self.max_rows = max(1, int(max_rows))


class LiveDashboardHandler(BaseHTTPRequestHandler):
    server_version = "GoldbotLiveDashboard/0.1"

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            try:
                state = load_state(
                    self.server.ledger_path,
                    max_rows=self.server.max_rows,
                    stale_after_seconds=max(30.0, self.server.refresh_seconds * 3.0),
                )
                state["server"] = {
                    "host": self.server.server_address[0],
                    "port": self.server.server_address[1],
                }
                body = render_page(state, self.server.refresh_seconds).encode("utf-8")
                self._send(HTTPStatus.OK, "text/html; charset=utf-8", body)
            except Exception as exc:  # noqa: BLE001 - server must report, not crash
                self._send(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    "text/plain; charset=utf-8",
                    f"dashboard-live error: {type(exc).__name__}: {exc}".encode("utf-8"),
                )
            return
        if path == "/api/state":
            try:
                state = load_state(
                    self.server.ledger_path,
                    max_rows=self.server.max_rows,
                    stale_after_seconds=max(30.0, self.server.refresh_seconds * 3.0),
                )
                state["server"] = {
                    "host": self.server.server_address[0],
                    "port": self.server.server_address[1],
                }
                body = json.dumps(
                    state, ensure_ascii=False, indent=2, default=str
                ).encode("utf-8")
                self._send(HTTPStatus.OK, "application/json; charset=utf-8", body)
            except Exception as exc:  # noqa: BLE001
                self._send(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    "application/json; charset=utf-8",
                    json.dumps(
                        {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
                    ).encode("utf-8"),
                )
            return
        if path == "/health":
            body = json.dumps(
                {"ok": True, "read_only": True, "no_mt5_connection": True}
            ).encode("utf-8")
            self._send(HTTPStatus.OK, "application/json; charset=utf-8", body)
            return
        if path == "/favicon.ico":
            self.send_response(HTTPStatus.NO_CONTENT)
            self.end_headers()
            return
        self._send(
            HTTPStatus.NOT_FOUND,
            "text/plain; charset=utf-8",
            b"not found",
        )

    def do_HEAD(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        self.do_GET()

    def _send(self, status: HTTPStatus, content_type: str, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'none'; style-src 'unsafe-inline'; img-src data:; "
            "frame-ancestors 'none'",
        )
        self.end_headers()
        if status >= HTTPStatus.BAD_REQUEST:
            print(
                f"[dashboard-live] {self.address_string()} {int(status)} {self.path}"
            )
        if self.command != "HEAD":
            self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        # Suppress per-request access logs; the dashboard auto-refreshes every few
        # seconds and would otherwise flood the terminal.  _send() logs errors.
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
    )


def serve_live_dashboard(
    *,
    ledger_path: str | Path = DEFAULT_LEDGER,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    refresh_seconds: float = DEFAULT_REFRESH_SECONDS,
    max_rows: int = DEFAULT_MAX_ROWS,
    open_browser: bool = False,
) -> None:
    server = make_server(
        ledger_path=ledger_path,
        host=host,
        port=port,
        refresh_seconds=refresh_seconds,
        max_rows=max_rows,
    )
    bound_host, bound_port = server.server_address[:2]
    url = f"http://{bound_host}:{bound_port}/"
    print(f"[dashboard-live] serving read-only dashboard at {url}")
    print(f"[dashboard-live] ledger: {Path(ledger_path).resolve()}")
    print("[dashboard-live] press Ctrl+C to stop")
    if open_browser:
        threading.Timer(0.5, webbrowser.open, args=(url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[dashboard-live] stopped")
    finally:
        server.server_close()
