"""Timestamped backups of the execution ledger.

Uses SQLite's online backup API (safe on WAL databases that may be mid-write)
and verifies the copy with ``PRAGMA integrity_check`` afterward.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .contracts import utc_now

DEFAULT_KEEP = 30


@dataclass
class BackupResult:
    source: str
    destination: str
    ok: bool
    integrity: str
    size_bytes: int
    kept: int
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": "backup",
            "source": self.source,
            "destination": self.destination,
            "ok": self.ok,
            "integrity": self.integrity,
            "size_bytes": self.size_bytes,
            "kept": self.kept,
            "detail": self.detail,
        }


def run_backup(
    *,
    ledger_path: str | Path,
    out_dir: str | Path = "reports/backups",
    keep: int = DEFAULT_KEEP,
) -> BackupResult:
    source = Path(ledger_path)
    if not source.exists():
        return BackupResult(
            source=str(source),
            destination="",
            ok=False,
            integrity="n/a",
            size_bytes=0,
            kept=0,
            detail=f"ledger not found: {source}",
        )

    destination_dir = Path(out_dir)
    destination_dir.mkdir(parents=True, exist_ok=True)
    stamp = utc_now().strftime("%Y%m%dT%H%M%S")
    destination = destination_dir / f"{source.stem}-{stamp}.sqlite"

    src = sqlite3.connect(str(source))
    try:
        dst = sqlite3.connect(str(destination))
        try:
            with dst:
                src.backup(dst)
            integrity_row = dst.execute("PRAGMA integrity_check").fetchone()
            integrity = str(integrity_row[0]) if integrity_row else "unknown"
        finally:
            dst.close()
    finally:
        src.close()

    size = destination.stat().st_size

    backups = sorted(destination_dir.glob(f"{source.stem}-*.sqlite"))
    pruned = 0
    if keep > 0 and len(backups) > keep:
        for old in backups[:-keep]:
            old.unlink(missing_ok=True)
            pruned += 1

    ok = integrity == "ok"
    return BackupResult(
        source=str(source),
        destination=str(destination),
        ok=ok,
        integrity=integrity,
        size_bytes=size,
        kept=keep,
        detail=(
            f"backup integrity ok; pruned {pruned} older backup(s)"
            if ok
            else f"integrity_check failed: {integrity}"
        ),
    )
