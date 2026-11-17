"""Refresh FRED macro inputs from the free public endpoints.

Reads the manifest (the source of truth for URLs), downloads the FRED CSVs,
validates them before touching the macro directory, and writes atomically.

This command exists because stale macro data blocks the bot per the manifest's
``stale_after_days`` rules. It NEVER edits the manifest, NEVER widens a
staleness limit, and NEVER invents data: if FRED has not published a newer
print, the file keeps its last observation and the command reports exactly
that.
"""

from __future__ import annotations

import csv
import io
import os
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from .contracts import utc_now
from .manifest import load_manifest

FRED_SERIES = {
    "DFII10": "fred_dfii10",
    "DTWEXBGS": "fred_dtwexbgs",
    "T10YIE": "fred_t10yie",
}

ALFRED_SERIES = {
    "DFII10": ("alfred_dfii10", "ALFRED_DFII10.csv"),
    "DTWEXBGS": ("alfred_dtwexbgs", "ALFRED_DTWEXBGS.csv"),
    "T10YIE": ("alfred_t10yie", "ALFRED_T10YIE.csv"),
}


@dataclass
class RefreshResult:
    generated_at_utc: datetime
    macro_dir: str
    series: list[dict[str, Any]] = field(default_factory=list)
    ok: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": "refresh_macro",
            "generated_at_utc": self.generated_at_utc.isoformat(),
            "macro_dir": self.macro_dir,
            "series": self.series,
            "ok": self.ok,
        }


def _value_column(fields: list[str], series_id: str) -> str | None:
    for name in fields:
        if name == series_id:
            return name
    for name in fields:
        if name.startswith(series_id + "_"):
            return name
    return None


def _latest_observation(text: str, value_column: str) -> date | None:
    reader = csv.DictReader(io.StringIO(text.lstrip("\ufeff")))
    latest: date | None = None
    for row in reader:
        raw = str(row.get("observation_date") or "").strip()
        if not raw:
            continue
        try:
            observed = datetime.strptime(raw, "%Y-%m-%d").date()
        except ValueError:
            continue
        value = str(row.get(value_column) or "").strip()
        if value in {"", "."}:
            continue
        if latest is None or observed > latest:
            latest = observed
    return latest


def _validate_fred_csv(text: str, series_id: str, min_rows: int) -> tuple[list[str], str | None]:
    errors: list[str] = []
    reader = csv.DictReader(io.StringIO(text.lstrip("\ufeff")))
    fieldnames = reader.fieldnames or []
    if "observation_date" not in fieldnames:
        errors.append("missing column: observation_date")
    value_column = _value_column(fieldnames, series_id)
    if value_column is None:
        errors.append(f"missing value column for series {series_id}")
    rows = list(reader)
    if len(rows) < min_rows:
        errors.append(f"only {len(rows)} rows; expected at least {min_rows}")
    if value_column is not None and _latest_observation(text, value_column) is None:
        errors.append("no parseable observation dates")
    return errors, value_column


def refresh_series(
    *,
    series_id: str,
    url: str,
    macro_dir: Path,
    min_rows: int = 1000,
    timeout_seconds: float = 30.0,
    http_client: httpx.Client | None = None,
    filename: str | None = None,
    series_label: str | None = None,
) -> dict[str, Any]:
    dest = macro_dir / (filename or f"{series_id}.csv")
    result: dict[str, Any] = {
        "series_id": series_label or series_id,
        "url": url,
        "path": str(dest),
        "status": "FAILED",
        "rows": 0,
        "latest_date": None,
        "age_days": None,
        "changed": False,
        "errors": [],
    }

    client = http_client or httpx.Client(
        timeout=timeout_seconds, follow_redirects=True,
        headers={"User-Agent": "Mozilla/5.0 (compatible; goldbot/refresh-macro)"},
    )
    try:
        response = client.get(url)
        if response.status_code != 200:
            result["errors"] = [f"HTTP {response.status_code}"]
            return result
        text = response.text
        errors, value_column = _validate_fred_csv(text, series_id, min_rows)
        if errors:
            result["errors"] = errors
            return result
        assert value_column is not None
        latest = _latest_observation(text, value_column)
        rows = sum(1 for _ in csv.DictReader(io.StringIO(text.lstrip("\ufeff"))))

        macro_dir.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(dest.suffix + ".tmp")
        tmp.write_text(text, encoding="utf-8", newline="")

        changed = True
        if dest.exists():
            existing = dest.read_text(encoding="utf-8")
            _, existing_value_column = _validate_fred_csv(existing, series_id, 0)
            existing_latest = _latest_observation(
                existing, existing_value_column
            ) if existing_value_column else None
            changed = existing_latest != latest
        os.replace(tmp, dest)

        now = utc_now().date()
        result.update(
            status="OK",
            rows=rows,
            latest_date=latest.isoformat() if latest else None,
            age_days=(now - latest).days if latest else None,
            changed=changed,
            errors=[],
        )
        return result
    except httpx.HTTPError as exc:
        result["errors"] = [f"HTTP error: {exc}"]
        return result
    finally:
        if http_client is None:
            client.close()


def _min_rows(source: dict[str, Any]) -> int:
    min_rows = 0
    for check in source.get("checks", []) or []:
        if isinstance(check, dict) and "min_rows" in check:
            min_rows = max(min_rows, int(check["min_rows"]))
    return min_rows


def refresh_macro(
    *,
    manifest_path: str | Path,
    macro_dir: str | Path,
    http_client: httpx.Client | None = None,
) -> RefreshResult:
    generated_at = utc_now()
    manifest = load_manifest(manifest_path)
    sources = {
        str(source.get("id")): source
        for source in manifest.get("sources", [])
        if isinstance(source, dict)
    }
    results: list[dict[str, Any]] = []
    ok = True
    for series_id, source_id in FRED_SERIES.items():
        source = sources.get(source_id)
        url = source.get("url") if source else None
        if not url:
            results.append(
                {
                    "series_id": series_id,
                    "status": "FAILED",
                    "errors": [f"source {source_id} missing or has no URL in manifest"],
                }
            )
            ok = False
            continue
        min_rows = _min_rows(source)
        series_result = refresh_series(
            series_id=series_id,
            url=str(url),
            macro_dir=Path(macro_dir),
            min_rows=min_rows or 1,
            http_client=http_client,
        )
        if series_result["status"] != "OK":
            ok = False
        results.append(series_result)

    # ALFRED vintage files: refresh only files that already exist locally so a
    # refresh never silently changes the data basis of a series.
    for series_id, (source_id, filename) in ALFRED_SERIES.items():
        dest = Path(macro_dir) / filename
        if not dest.exists():
            results.append(
                {
                    "series_id": f"ALFRED_{series_id}",
                    "status": "SKIPPED",
                    "path": str(dest),
                    "errors": ["no local ALFRED vintage file; leaving basis unchanged"],
                }
            )
            continue
        source = sources.get(source_id)
        url = source.get("url") if source else None
        if not url:
            results.append(
                {
                    "series_id": f"ALFRED_{series_id}",
                    "status": "FAILED",
                    "errors": [f"source {source_id} missing or has no URL in manifest"],
                }
            )
            ok = False
            continue
        series_result = refresh_series(
            series_id=series_id,
            url=str(url),
            macro_dir=Path(macro_dir),
            min_rows=_min_rows(source) or 1,
            http_client=http_client,
            filename=filename,
            series_label=f"ALFRED_{series_id}",
        )
        if series_result["status"] != "OK":
            ok = False
        results.append(series_result)

    return RefreshResult(
        generated_at_utc=generated_at,
        macro_dir=str(macro_dir),
        series=results,
        ok=ok,
    )


def stale_limits(manifest: dict[str, Any]) -> dict[str, int]:
    fundamental = manifest.get("strategy", {}).get("fundamental", {}) if isinstance(manifest, dict) else {}
    raw = fundamental.get("stale_after_days") if isinstance(fundamental, dict) else None
    if not isinstance(raw, dict):
        return {}
    return {str(key): int(value) for key, value in raw.items()}
