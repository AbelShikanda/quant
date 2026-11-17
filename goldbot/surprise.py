"""Economic surprise factor (consensus vs actual), owner-provided.

There is no free, ToS-clean consensus feed for this bot. The owner
maintains ``data/manual/econ/surprises.csv`` with columns::

    release_date,event,forecast,actual

and ``goldbot surprise`` reports the surprise profile for the latest
releases. A missing/empty file is reported honestly as absent data —
never fabricated.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

CSV_PATH = Path("data/manual/econ/surprises.csv")
HEADER = ["release_date", "event", "forecast", "actual"]


def load_surprises(path: Path = CSV_PATH) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        rows = [row for row in csv.DictReader(handle) if any((v or "").strip() for v in row.values())]
    return rows


def report(path: Path = CSV_PATH) -> dict[str, Any]:
    rows = load_surprises(path)
    items: list[dict[str, Any]] = []
    for row in rows:
        try:
            forecast = float(row.get("forecast") or "")
            actual = float(row.get("actual") or "")
        except (TypeError, ValueError):
            continue
        surprise = actual - forecast
        surprise_pct = (surprise / abs(forecast) * 100.0) if forecast else None
        items.append(
            {
                "release_date": (row.get("release_date") or "").strip(),
                "event": (row.get("event") or "").strip(),
                "forecast": forecast,
                "actual": actual,
                "surprise": round(surprise, 4),
                "surprise_pct": round(surprise_pct, 2) if surprise_pct is not None else None,
            }
        )

    summary: dict[str, Any] = {"count": len(items)}
    if items:
        direction = sum(1 for i in items if (i["surprise"] or 0) > 0) - sum(
            1 for i in items if (i["surprise"] or 0) < 0
        )
        summary["net_beat"] = direction
        summary["last"] = items[-1]

    return {
        "mode": "surprise",
        "csv_path": str(path),
        "csv_exists": path.exists(),
        "count": len(items),
        "observations": items,
        "summary": summary,
        "note": (
            "owner-maintained data; gold historically reacts inversely to USD strength. "
            "No free, ToS-clean consensus feed is available; file must be maintained by the owner."
        ),
    }
