"""WGC central-bank gold holdings — mechanical reconfirm only.

The file is downloaded by the human owner (weight in the entry score = 0, NOT
consumed by the overseer). This module finds the most recently downloaded
XLSX in the manual directory, runs the same *mechanical* checks the validator
applies, and records evidence in the ledger so the dashboard can prove the
item was actually reconfirmed — never by clicking a button alone.

It does NOT judge the content (HUMAN_JUDGMENT stays human) and it does NOT
alter the score, the overseer, or the manifest.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .contracts import utc_now
from .ledger import Ledger

WGC_DIR = Path("data/manual/wgc")
MAX_AGE_DAYS = 90
REMINDER_CADENCE_DAYS = 30


def newest_wgc_xlsx(wgc_dir: str | Path = WGC_DIR) -> Path | None:
    directory = Path(wgc_dir)
    if not directory.is_dir():
        return None
    candidates = [p for p in directory.glob("*.xlsx") if p.is_file()]
    candidates = [p for p in candidates if not p.name.startswith("~$")]
    if not candidates:
        return None
    return max(candidates, key=lambda p: (p.stat().st_mtime, p.name))


def _latest_observation_date(values: list[tuple[Any, ...]]) -> str | None:
    latest: datetime | None = None
    for row in values:
        for cell in row:
            if isinstance(cell, datetime):
                if latest is None or cell > latest:
                    latest = cell
    return latest.date().isoformat() if latest else None


def inspect_wgc_file(path: Path) -> dict[str, Any]:
    import io

    import openpyxl

    result: dict[str, Any] = {
        "path": str(path),
        "name": path.name,
        "size_bytes": path.stat().st_size,
        "mtime": datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat(),
        "sha256": None,
        "parse_ok": False,
        "sheets": [],
        "sheet": None,
        "rows": 0,
        "headers": [],
        "latest_date": None,
        "error": None,
    }
    try:
        content = path.read_bytes()
        result["sha256"] = hashlib.sha256(content).hexdigest()
        workbook = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
        try:
            sheets = list(workbook.sheetnames)
            worksheet = workbook[sheets[0]]
            non_empty = [
                tuple(row)
                for row in worksheet.iter_rows(values_only=True)
                if any(value is not None and str(value).strip() for value in row)
            ]
            headers = [str(value).strip() for value in (non_empty[0] if non_empty else [])]
            result.update(
                parse_ok=True,
                sheets=sheets,
                sheet=worksheet.title,
                rows=len(non_empty),
                headers=headers,
                latest_date=_latest_observation_date(non_empty[1:]),
            )
        finally:
            workbook.close()
    except Exception as exc:  # noqa: BLE001 - report, never raise into the UI
        result["error"] = f"{type(exc).__name__}: {exc}"
    return result


def _refresh_event(wgc_dir: Path) -> dict[str, Any]:
    event_path = wgc_dir / "refresh_event.json"
    try:
        if event_path.exists():
            return json.loads(event_path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - best-effort context only
        pass
    return {}


def _age_days(iso_timestamp: str | None) -> int | None:
    if not iso_timestamp:
        return None
    try:
        text = iso_timestamp.strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        stamp = datetime.fromisoformat(text)
    except ValueError:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return (utc_now() - stamp.astimezone(timezone.utc)).days


def run_wgc_check(
    *,
    wgc_dir: str | Path = WGC_DIR,
    ledger_path: str | Path | None = None,
) -> dict[str, Any]:
    generated_at = utc_now()
    directory = Path(wgc_dir)
    newest = newest_wgc_xlsx(directory)
    report: dict[str, Any] = {
        "mode": "wgc_check",
        "generated_at_utc": generated_at.isoformat(),
        "wgc_dir": str(directory),
        "status": "waiting",
        "file": None,
        "inspection": None,
        "refresh_event": None,
        "age_days": None,
        "sha_matches_refresh_event": None,
        "detail": None,
    }

    if newest is None:
        report["detail"] = (
            "no XLSX found in data/manual/wgc/; the owner downloads the monthly WGC/IMF "
            "file and the reconfirm will pass once it is present and parseable"
        )
    else:
        inspection = inspect_wgc_file(newest)
        event = _refresh_event(directory)
        event_sha = str(event.get("sha256") or "").strip().lower()
        file_sha = str(inspection.get("sha256") or "").lower()
        sha_match = bool(event_sha) and event_sha == file_sha
        age = _age_days(event.get("last_refresh_utc")) if event else None
        if age is None:
            age = _age_days(inspection.get("mtime"))

        report["file"] = str(newest)
        report["inspection"] = inspection
        report["refresh_event"] = {
            "last_refresh_utc": event.get("last_refresh_utc"),
            "publication_month": event.get("publication_month"),
            "downloaded_by": event.get("downloaded_by"),
            "present": bool(event),
        }
        report["sha_matches_refresh_event"] = sha_match if event_sha else None
        report["age_days"] = age

        if not inspection.get("parse_ok"):
            report["status"] = "fail"
            report["detail"] = f"file does not parse: {inspection.get('error')}"
        elif int(inspection.get("rows") or 0) < 1:
            report["status"] = "fail"
            report["detail"] = "file parses but contains no non-empty rows"
        elif event_sha and not sha_match:
            report["status"] = "fail"
            report["detail"] = (
                "file sha256 does not match refresh_event.json; re-download or re-record the event"
            )
        elif age is not None and age > MAX_AGE_DAYS:
            report["status"] = "fail"
            report["detail"] = f"file is {age} days old; max {MAX_AGE_DAYS} days for the monthly manual refresh"
        else:
            report["status"] = "pass"
            report["detail"] = (
                f"mechanical checks passed: {inspection.get('rows')} rows, "
                f"sha256={file_sha[:12]}…, age_days={age}"
            )

    if ledger_path is not None:
        try:
            with Ledger(ledger_path) as ledger:
                ledger.log_event(
                    level="INFO" if report["status"] == "pass" else "WARNING",
                    component="wgc_check",
                    event_type="wgc_check",
                    message=f"WGC reconfirm status={report['status']}",
                    correlation_id=None,
                    payload=report,
                    logged_at_utc=generated_at,
                )
        except Exception as exc:  # noqa: BLE001 - never fail the check over ledger logging
            report["ledger_warning"] = f"could not log wgc_check to ledger: {exc}"

    return report
