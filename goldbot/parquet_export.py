"""Export the execution ledger to Parquet for analytics (DuckDB).

Read-only against the ledger. The JSON-valued columns are written as text;
no schema is changed, no writes are issued against the ledger.
"""

from __future__ import annotations

import csv
import io
import sqlite3
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_TABLES = (
    "strategy_runs",
    "strategy_decisions",
    "snapshots",
    "decisions",
    "orders",
    "fills",
    "positions",
    "logs",
    "incidents",
    "macro_observations",
    "chart_bars",
)


@dataclass
class ExportResult:
    ledger_path: str
    out_dir: str
    written: dict[str, int]
    ok: bool
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": "export_parquet",
            "ledger_path": self.ledger_path,
            "out_dir": self.out_dir,
            "written": self.written,
            "ok": self.ok,
            "detail": self.detail,
        }


def _table_rows(connection: sqlite3.Connection, table: str) -> tuple[list[str], list[tuple]]:
    try:
        cursor = connection.execute(f"SELECT * FROM {table}")
    except sqlite3.Error:
        return [], []
    columns = [description[0] for description in (cursor.description or [])]
    rows = cursor.fetchall()
    return columns, rows


def run_export(
    *,
    ledger_path: str | Path,
    out_dir: str | Path = "reports/parquet",
    tables: tuple[str, ...] = DEFAULT_TABLES,
) -> ExportResult:
    import duckdb

    source = Path(ledger_path)
    if not source.exists():
        return ExportResult(
            ledger_path=str(source),
            out_dir=str(out_dir),
            written={},
            ok=False,
            detail=f"ledger not found: {source}",
        )

    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    written: dict[str, int] = {}
    connection = sqlite3.connect(f"file:{source.resolve().as_posix()}?mode=ro", uri=True, timeout=1.0)
    try:
        connection.row_factory = sqlite3.Row
        for table in tables:
            columns, rows = _table_rows(connection, table)
            if not columns:
                continue
            csv_buffer = io.StringIO()
            writer = csv.writer(csv_buffer, lineterminator="\n")
            writer.writerow(columns)
            for row in rows:
                writer.writerow([row[col] for col in columns])
            parquet_path = out_path / f"{table}.parquet"
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".csv", delete=False, encoding="utf-8"
            ) as tmp:
                tmp.write(csv_buffer.getvalue())
                tmp_path = Path(tmp.name)
            try:
                duckdb.execute(
                    "COPY (SELECT * FROM read_csv(?, auto_detect=true, header=true, all_varchar=true)) "
                    f"TO '{parquet_path.as_posix()}' (FORMAT PARQUET)",
                    [str(tmp_path)],
                )
            finally:
                tmp_path.unlink(missing_ok=True)
            written[table] = len(rows)
    finally:
        connection.close()

    return ExportResult(
        ledger_path=str(source),
        out_dir=str(out_path),
        written=written,
        ok=bool(written),
        detail=f"exported {len(written)} table(s) to {out_path}",
    )
