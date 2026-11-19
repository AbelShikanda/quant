"""Feedback store for the local dashboard.

A single home for bugs, improvements, feature requests, tasks, and questions,
written from the dashboard's Feedback page. Each item can be copied out as a
structured Markdown ticket so a human (or an AI agent) can apply the change
directly from the pasted text.

Storage is a dedicated SQLite file (``data/feedback/feedback.sqlite``), NOT the
execution ledger. The ledger is single-writer (bot only) per AGENTS.md §6; this
module deliberately keeps its own database so the dashboard never writes to the
trading ledger. Screenshots live under ``data/feedback/assets/``.

This module never imports MT5, never touches the ledger, and never mutates
scope. It only stores feedback rows.
"""

from __future__ import annotations

import base64
import binascii
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data" / "feedback"
ASSETS_DIR = DATA_DIR / "assets"
DB_PATH = DATA_DIR / "feedback.sqlite"

TYPES: tuple[tuple[str, str], ...] = (
    ("bug", "Bug"),
    ("improvement", "Improvement"),
    ("feature", "Feature request"),
    ("task", "Task / chore"),
    ("question", "Question"),
)
TYPE_LABELS = dict(TYPES)

SEVERITIES: tuple[tuple[str, str], ...] = (
    ("critical", "Critical"),
    ("high", "High"),
    ("medium", "Medium"),
    ("low", "Low"),
)
SEVERITY_LABELS = dict(SEVERITIES)
SEVERITY_ORDER = {key: i for i, (key, _) in enumerate(SEVERITIES)}

STATUSES: tuple[tuple[str, str], ...] = (
    ("new", "New"),
    ("accepted", "Triaged · Accepted (queued)"),
    ("needs_info", "Needs information"),
    ("duplicate", "Duplicate"),
    ("wont_fix", "Won't fix"),
    ("not_a_bug", "Not a bug"),
    ("done", "Done"),
)
STATUS_LABELS = dict(STATUSES)

_TITLE_MAX = 300
_TEXT_MAX = 20_000
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,120}$")

_IMAGE_MIMES = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/gif": ".gif",
    "image/webp": ".webp",
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def connect(db_path: Path | None = None) -> sqlite3.Connection:
    path = Path(db_path) if db_path else DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(path))
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS feedback (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            type TEXT NOT NULL,
            severity TEXT NOT NULL,
            title TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            steps TEXT NOT NULL DEFAULT '',
            expected TEXT NOT NULL DEFAULT '',
            actual TEXT NOT NULL DEFAULT '',
            recommended TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'new',
            attachments TEXT NOT NULL DEFAULT '[]',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    return connection


def _clean_text(value: Any, *, limit: int = _TEXT_MAX) -> str:
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    return text[:limit]


def _clean_title(value: Any) -> str:
    return _clean_text(value, limit=_TITLE_MAX)


def _clean_choice(value: Any, allowed: dict[str, str], default: str) -> str:
    choice = str(value or "").strip().lower()
    return choice if choice in allowed else default


def _clean_attachments(value: Any) -> list[str]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            value = [value]
    if not isinstance(value, list):
        return []
    names: list[str] = []
    for entry in value:
        name = str(entry or "").strip()
        if _NAME_RE.match(name):
            names.append(name)
    return names[:20]


def _row_to_item(row: sqlite3.Row) -> dict[str, Any]:
    item = dict(row)
    try:
        item["attachments"] = json.loads(item.get("attachments") or "[]")
    except ValueError:
        item["attachments"] = []
    item["type_label"] = TYPE_LABELS.get(item["type"], item["type"])
    item["severity_label"] = SEVERITY_LABELS.get(item["severity"], item["severity"])
    item["status_label"] = STATUS_LABELS.get(item["status"], item["status"])
    return item


def create_item(payload: dict[str, Any], *, db_path: Path | None = None) -> dict[str, Any]:
    title = _clean_title(payload.get("title"))
    if not title:
        raise ValueError("title is required")
    item_type = _clean_choice(payload.get("type"), TYPE_LABELS, "bug")
    severity = _clean_choice(payload.get("severity"), SEVERITY_LABELS, "medium")
    now = _utc_now()
    attachments = _clean_attachments(payload.get("attachments"))
    with connect(db_path) as connection:
        cursor = connection.execute(
            """
            INSERT INTO feedback (type, severity, title, description, steps, expected, actual,
                                  recommended, status, attachments, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'new', ?, ?, ?)
            """,
            (
                item_type,
                severity,
                title,
                _clean_text(payload.get("description")),
                _clean_text(payload.get("steps")),
                _clean_text(payload.get("expected")),
                _clean_text(payload.get("actual")),
                _clean_text(payload.get("recommended")),
                json.dumps(attachments),
                now,
                now,
            ),
        )
        new_id = int(cursor.lastrowid)
    return get_item(new_id, db_path=db_path) or {}


def get_item(item_id: int, *, db_path: Path | None = None) -> dict[str, Any] | None:
    with connect(db_path) as connection:
        row = connection.execute("SELECT * FROM feedback WHERE id = ?", (int(item_id),)).fetchone()
    return _row_to_item(row) if row else None


_SORT_KEYS = {
    "created": "created_at",
    "updated": "updated_at",
    "title": "title",
    "type": "type",
    "status": "status",
}


def list_items(
    *,
    sort: str = "created",
    descending: bool = True,
    status: str | None = None,
    item_type: str | None = None,
    db_path: Path | None = None,
) -> list[dict[str, Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if status:
        clauses.append("status = ?")
        params.append(_clean_choice(status, STATUS_LABELS, "new"))
    if item_type:
        clauses.append("type = ?")
        params.append(_clean_choice(item_type, TYPE_LABELS, "bug"))
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    direction = "DESC" if descending else "ASC"
    order_by = _SORT_KEYS.get(sort, "created_at")
    # Completed work always sinks to the bottom, whatever the sort.
    done_last = "CASE WHEN status = 'done' THEN 1 ELSE 0 END ASC"
    # Severity sorts by rank, not alphabetically.
    if sort == "severity":
        order_sql = (
            "CASE severity WHEN 'critical' THEN 0 WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END"
            + f" {direction}, id DESC"
        )
        sql = f"SELECT * FROM feedback{where} ORDER BY {done_last}, {order_sql}"
    else:
        sql = f"SELECT * FROM feedback{where} ORDER BY {done_last}, {order_by} {direction}, id DESC"
    with connect(db_path) as connection:
        rows = connection.execute(sql, params).fetchall()
    return [_row_to_item(row) for row in rows]


_EDITABLE_FIELDS = (
    "type",
    "severity",
    "title",
    "description",
    "steps",
    "expected",
    "actual",
    "recommended",
)


def update_item(item_id: int, payload: dict[str, Any], *, db_path: Path | None = None) -> dict[str, Any] | None:
    existing = get_item(item_id, db_path=db_path)
    if existing is None:
        return None
    updates: dict[str, Any] = {}
    for field in _EDITABLE_FIELDS:
        if field not in payload:
            continue
        if field == "title":
            cleaned = _clean_title(payload[field])
            if not cleaned:
                raise ValueError("title cannot be empty")
            updates[field] = cleaned
        elif field == "type":
            updates[field] = _clean_choice(payload[field], TYPE_LABELS, existing["type"])
        elif field == "severity":
            updates[field] = _clean_choice(payload[field], SEVERITY_LABELS, existing["severity"])
        else:
            updates[field] = _clean_text(payload[field])
    if "status" in payload:
        status = str(payload["status"] or "").strip().lower()
        if status not in STATUS_LABELS:
            raise ValueError(f"unknown status: {status}")
        updates["status"] = status
    if "attachments" in payload:
        updates["attachments"] = json.dumps(_clean_attachments(payload["attachments"]))
    if not updates:
        return existing
    updates["updated_at"] = _utc_now()
    assignments = ", ".join(f"{field} = ?" for field in updates)
    with connect(db_path) as connection:
        connection.execute(
            f"UPDATE feedback SET {assignments} WHERE id = ?",
            (*updates.values(), int(item_id)),
        )
    return get_item(item_id, db_path=db_path)


def delete_item(item_id: int, *, db_path: Path | None = None) -> bool:
    with connect(db_path) as connection:
        cursor = connection.execute("DELETE FROM feedback WHERE id = ?", (int(item_id),))
        deleted = cursor.rowcount > 0
    return deleted


def save_screenshot(
    filename: str,
    data_base64: str,
    mime: str,
    *,
    assets_dir: Path | None = None,
) -> str:
    directory = Path(assets_dir) if assets_dir else ASSETS_DIR
    directory.mkdir(parents=True, exist_ok=True)
    extension = _IMAGE_MIMES.get(str(mime or "").lower())
    if extension is None:
        raise ValueError("unsupported image type (png, jpeg, gif, webp only)")
    try:
        raw = base64.b64decode(data_base64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("invalid base64 image data") from exc
    if len(raw) > 5 * 1024 * 1024:
        raise ValueError("image exceeds 5 MB limit")
    safe_stem = re.sub(r"[^A-Za-z0-9._-]", "_", Path(filename or "screenshot").stem)[:60] or "screenshot"
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    name = f"{stamp}-{safe_stem}{extension}"
    (directory / name).write_bytes(raw)
    return name


def asset_path(name: str, *, assets_dir: Path | None = None) -> Path | None:
    if not _NAME_RE.match(str(name or "")):
        return None
    directory = Path(assets_dir) if assets_dir else ASSETS_DIR
    candidate = (directory / name).resolve()
    try:
        candidate.relative_to(directory.resolve())
    except ValueError:
        return None
    return candidate if candidate.is_file() else None


def to_markdown(item: dict[str, Any]) -> str:
    item_id = item.get("id", "?")
    item_type = item.get("type_label") or TYPE_LABELS.get(item.get("type", ""), "Bug")
    severity = item.get("severity_label") or SEVERITY_LABELS.get(item.get("severity", ""), "?")
    status = item.get("status_label") or STATUS_LABELS.get(item.get("status", ""), "?")
    title = item.get("title", "(untitled)")
    created = item.get("created_at", "?")

    def section(heading: str, body: str) -> list[str]:
        body = (body or "").strip()
        return [f"## {heading}", body if body else "_(none provided)_", ""]

    lines = [
        f"# [{item_type}] #{item_id} {title}",
        "",
        f"**Severity:** {severity} · **Status:** {status} · **Type:** {item_type} · **ID:** #{item_id} · **Filed:** {created}",
        "",
    ]
    lines += section("Description", item.get("description", ""))
    lines += section("Steps to reproduce", item.get("steps", ""))
    lines += section("Expected", item.get("expected", ""))
    lines += section("Actual", item.get("actual", ""))
    lines += section("Recommended fix", item.get("recommended", ""))
    attachments = item.get("attachments") or []
    if attachments:
        lines.append("## Screenshots")
        for name in attachments:
            lines.append(f"- {name}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
