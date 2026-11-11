"""Check implementations for the headless scope validator.

Each check returns a :class:`CheckOutcome`. Checks must be explicit about what they
actually verified. A human-judgment item must never return PASS merely because a
proxy file exists.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import zipfile
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable

import httpx
import openpyxl
import yaml
from dateutil import parser as date_parser
from openpyxl.utils.datetime import from_excel

from .manifest import as_dict, as_list, deep_get, fundamental_index, process_index, source_index
from .models import BLOCKED, FAIL, HUMAN_JUDGMENT, PASS, SKIP, CheckOutcome


ALLOWED_SOURCE_TYPES = {
    "mt5",
    "http_csv",
    "http_xlsx",
    "http_zip",
    "http_html",
    "manual_xlsx",
}
ALLOWED_TOS_RISK = {"low", "medium", "high"}
ALLOWED_SIGNS = {"positive", "negative", "contextual"}
ALLOWED_FUNDAMENTALS = {
    "real_yield_10y",
    "broad_usd",
    "gold_etf_flows",
    "cot_managed_money",
    "central_bank_gold",
    "breakeven_10y",
}
CENTRAL_BANK_LABEL = (
    "derived from human-downloaded file; weight in entry score = 0; "
    "NOT consumed by the overseer (§9) in v1."
)
REQUIRED_TABLES = {
    "snapshots",
    "decisions",
    "orders",
    "fills",
    "positions",
    "logs",
    "scope_versions",
    "incidents",
}
EXPECTED_EXIT_PRECEDENCE = [
    (100, "emergency_risk"),
    (90, "hard_or_trailing_stop"),
    (80, "thesis_invalidated"),
    (70, "thesis_weakened"),
    (60, "time_stop"),
    (50, "technical_or_profit_trail"),
    (40, "profit_take"),
    (30, "advisory"),
]
REQUIRED_PROMOTION_GATES = {
    "v1_complete",
    "three_months_demo",
    "hundred_plus_trades",
    "positive_out_of_sample_after_costs",
    "drawdown_within_limits",
    "kill_switch_drill_passed",
    "reconnect_drill_passed",
    "human_signoff",
}


@dataclass
class CheckContext:
    """Everything a check needs to run."""

    manifest: dict[str, Any]
    manifest_path: Path
    project_root: Path
    manifest_hash: str
    offline: bool = False
    no_mt5: bool = False
    http_timeout: float = 20.0
    http_headers: dict[str, str] = field(
        default_factory=lambda: {
            "User-Agent": "Mozilla/5.0 (compatible; goldbot/0.1; +https://localhost/goldbot)",
            "Accept": "*/*",
        }
    )

    @property
    def now(self) -> datetime:
        return datetime.now(timezone.utc)

    def source(self, source_id: str) -> dict[str, Any]:
        index = source_index(self.manifest)
        if source_id not in index:
            raise KeyError(f"source not found: {source_id}")
        return index[source_id]

    def http_get(self, url: str, timeout: float | None = None) -> httpx.Response:
        return httpx.get(
            url,
            timeout=timeout or self.http_timeout,
            follow_redirects=True,
            headers=self.http_headers,
        )


def _resolve_source_spec(ctx: CheckContext, spec: dict[str, Any]) -> dict[str, Any]:
    """Merge a validation item with its source definition and first check block."""
    source_id = spec.get("source_id")
    if not source_id:
        return dict(spec)
    source = ctx.source(str(source_id))
    merged: dict[str, Any] = dict(source)
    checks = as_list(source.get("checks"))
    if checks and isinstance(checks[0], dict):
        merged.update(checks[0])
    merged.update(spec)
    merged["_source"] = source
    return merged


def _join_errors(errors: list[str], limit: int = 12) -> str:
    if not errors:
        return "all checks passed"
    shown = errors[:limit]
    suffix = "" if len(errors) <= limit else f" (+{len(errors) - limit} more)"
    return "; ".join(shown) + suffix


def _parse_date(value: Any, formats: list[str] | None = None) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, (int, float)):
        try:
            return from_excel(value).date()
        except Exception:
            return None
    text = str(value).strip()
    if not text:
        return None
    for fmt in formats or []:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    try:
        return date_parser.parse(text, fuzzy=False).date()
    except (ValueError, TypeError, OverflowError):
        return None


def _latest_date(rows: list[dict[str, Any]], column: str, formats: list[str] | None) -> date | None:
    latest: date | None = None
    for row in rows:
        parsed = _parse_date(row.get(column), formats)
        if parsed is not None and (latest is None or parsed > latest):
            latest = parsed
    return latest


def _age_days(value: date | None, now: datetime) -> int | None:
    if value is None:
        return None
    return (now.date() - value).days


def _check_columns(
    headers: list[str],
    required_columns: list[str],
    value_column_regex: str | None = None,
) -> list[str]:
    clean_headers = {str(h).strip() for h in headers if h is not None}
    missing = [col for col in required_columns if col not in clean_headers]
    if value_column_regex:
        pattern = re.compile(value_column_regex)
        if not any(pattern.search(h) for h in clean_headers):
            missing.append(f"regex:{value_column_regex}")
    return missing


def _read_csv(text: str) -> tuple[list[str], list[dict[str, Any]]]:
    reader = csv.DictReader(io.StringIO(text.lstrip("\ufeff")))
    rows = [dict(row) for row in reader]
    return list(reader.fieldnames or []), rows


def _read_xlsx(
    content: bytes,
    sheet_name: str | None,
    header_row: int,
) -> tuple[list[str], list[dict[str, Any]], list[str], str]:
    workbook = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    try:
        sheets = list(workbook.sheetnames)
        if sheet_name:
            if sheet_name not in sheets:
                raise ValueError(f"sheet not found: {sheet_name}")
            worksheet = workbook[sheet_name]
        else:
            worksheet = workbook[sheets[0]]
        iterator = worksheet.iter_rows(min_row=header_row, values_only=True)
        try:
            raw_headers = next(iterator)
        except StopIteration:
            return [], [], sheets, worksheet.title
        headers = [str(value).strip() if value is not None else "" for value in raw_headers]
        rows: list[dict[str, Any]] = []
        for raw in iterator:
            row = {headers[index]: value for index, value in enumerate(raw) if index < len(headers)}
            if any(value is not None and str(value).strip() for value in row.values()):
                rows.append(row)
        return headers, rows, sheets, worksheet.title
    finally:
        workbook.close()


def _read_zip_csv(content: bytes, entry: str) -> tuple[list[str], list[dict[str, Any]], list[str]]:
    archive = zipfile.ZipFile(io.BytesIO(content))
    names = archive.namelist()
    if entry not in names:
        raise ValueError(f"zip entry not found: {entry}")
    raw = archive.read(entry)
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("latin-1", errors="replace")
    headers, rows = _read_csv(text)
    return headers, rows, names


def _filter_rows(rows: list[dict[str, Any]], column: str | None, value: str | None) -> list[dict[str, Any]]:
    if not column or value is None:
        return rows
    return [row for row in rows if str(row.get(column, "")).strip() == str(value)]


def check_manifest_integrity(ctx: CheckContext, spec: dict[str, Any]) -> CheckOutcome:
    manifest = ctx.manifest
    errors: list[str] = []
    warnings: list[str] = []

    required_top = [
        "manifest_version",
        "scope_id",
        "scope_version",
        "market",
        "risk",
        "sources",
        "fundamental_inputs",
        "technical",
        "fusion",
        "overseer",
        "exits",
        "storage",
        "backtest",
        "analytics",
        "runtime_processes",
        "validation_items",
    ]
    for key in required_top:
        if key not in manifest:
            errors.append(f"missing top-level section: {key}")

    for section in ("sources", "fundamental_inputs", "validation_items", "runtime_processes"):
        ids = [
            str(item.get("id"))
            for item in as_list(manifest.get(section))
            if isinstance(item, dict) and item.get("id")
        ]
        duplicates = sorted({item_id for item_id in ids if ids.count(item_id) > 1})
        if duplicates:
            errors.append(f"duplicate IDs in {section}: {duplicates}")

    sources = source_index(manifest)
    for item in as_list(manifest.get("fundamental_inputs")):
        if not isinstance(item, dict):
            continue
        for source_id in as_list(item.get("source_ids")):
            if str(source_id) not in sources:
                errors.append(f"fundamental {item.get('id')} references missing source {source_id}")

    weights = [
        float(item.get("weight", 0.0))
        for item in as_list(manifest.get("fundamental_inputs"))
        if isinstance(item, dict)
    ]
    if weights and abs(sum(weights) - 1.0) > 1e-9:
        errors.append(f"fundamental weights sum to {sum(weights):.6f}, expected 1.0")

    market = as_dict(manifest.get("market"))
    if market:
        if market.get("symbol") != "XAUUSD":
            errors.append("market.symbol must be XAUUSD")
        if market.get("instrument_class") != "spot_gold_cfd":
            errors.append("market.instrument_class must be spot_gold_cfd")
        if market.get("direction") != "long_only":
            errors.append("market.direction must be long_only")
        if market.get("execution_bar") != "M15":
            errors.append("market.execution_bar must be M15")
        if market.get("context_bar") != "H4":
            errors.append("market.context_bar must be H4")
        if market.get("account_model") != "cash_equivalent_no_leverage":
            errors.append("market.account_model must be cash_equivalent_no_leverage")
        if float(market.get("max_notional_exposure_ratio", 999)) > 1.0:
            errors.append("market.max_notional_exposure_ratio must be <= 1.0")
        if market.get("chart_assumption") != "none":
            errors.append("market.chart_assumption must be none")
        if market.get("real_money_allowed") is not False:
            errors.append("market.real_money_allowed must be false in v1")

    risk = as_dict(manifest.get("risk"))
    if risk:
        if float(risk.get("per_trade_equity_pct", 999)) > 0.25:
            errors.append("risk.per_trade_equity_pct must be <= 0.25")
        if float(risk.get("daily_loss_halt_pct", 999)) > 1.5:
            errors.append("risk.daily_loss_halt_pct must be <= 1.5")
        if float(risk.get("max_drawdown_flatten_pct", 999)) > 8.0:
            errors.append("risk.max_drawdown_flatten_pct must be <= 8.0")
        if risk.get("risk_exits_veto_immune") is not True:
            errors.append("risk.risk_exits_veto_immune must be true")

    for item in as_list(manifest.get("validation_items")):
        if not isinstance(item, dict):
            errors.append("validation_items contains a non-mapping entry")
            continue
        for key in ("id", "category", "what", "where", "validation_class"):
            if not item.get(key):
                errors.append(f"validation item missing {key}: {item.get('id', '<unknown>')}")
        validation_class = item.get("validation_class")
        if validation_class not in {"MECHANICAL", "HUMAN_JUDGMENT", "NOT_VALIDATABLE_AT_DESIGN_TIME"}:
            errors.append(f"invalid validation_class for {item.get('id')}: {validation_class}")
        if "required_for_m1" not in item:
            errors.append(f"validation item missing required_for_m1: {item.get('id')}")
        if validation_class == "MECHANICAL" and not item.get("check"):
            errors.append(f"mechanical item has no check: {item.get('id')}")
        if item.get("runtime_only") and item.get("required_for_m1") is True:
            warnings.append(f"runtime-only item also marked required_for_m1: {item.get('id')}")

    if errors:
        return CheckOutcome(FAIL, _join_errors(errors), {"errors": errors, "warnings": warnings})
    return CheckOutcome(PASS, "manifest structure and cross-references are valid", {"warnings": warnings})


def check_market_scope(ctx: CheckContext, spec: dict[str, Any]) -> CheckOutcome:
    market = as_dict(ctx.manifest.get("market"))
    errors: list[str] = []
    expected = {
        "symbol": "XAUUSD",
        "instrument_class": "spot_gold_cfd",
        "direction": "long_only",
        "execution_bar": "M15",
        "context_bar": "H4",
        "account_model": "cash_equivalent_no_leverage",
        "chart_assumption": "none",
    }
    for key, value in expected.items():
        if market.get(key) != value:
            errors.append(f"{key}={market.get(key)!r}, expected {value!r}")
    if float(market.get("max_notional_exposure_ratio", 999)) > 1.0:
        errors.append("max_notional_exposure_ratio must be <= 1.0")
    if market.get("broker_time_calibration_required") is not True:
        errors.append("broker_time_calibration_required must be true")
    if market.get("real_money_allowed") is not False:
        errors.append("real_money_allowed must be false")
    if errors:
        return CheckOutcome(FAIL, _join_errors(errors), {"market": market})
    return CheckOutcome(PASS, "market scope is XAUUSD spot-gold, long-only, M15/H4, cash-equivalent", {"market": market})


def check_risk_limits(ctx: CheckContext, spec: dict[str, Any]) -> CheckOutcome:
    risk = as_dict(ctx.manifest.get("risk"))
    errors: list[str] = []
    if float(risk.get("per_trade_equity_pct", 999)) > 0.25:
        errors.append("per_trade_equity_pct must be <= 0.25")
    if float(risk.get("daily_loss_halt_pct", 999)) > 1.5:
        errors.append("daily_loss_halt_pct must be <= 1.5")
    if float(risk.get("max_drawdown_flatten_pct", 999)) > 8.0:
        errors.append("max_drawdown_flatten_pct must be <= 8.0")
    if risk.get("risk_exits_veto_immune") is not True:
        errors.append("risk_exits_veto_immune must be true")
    if risk.get("daily_halt_action") != "flatten_and_halt":
        errors.append("daily_halt_action must be flatten_and_halt")
    if risk.get("max_drawdown_action") != "flatten_and_halt":
        errors.append("max_drawdown_action must be flatten_and_halt")
    if float(risk.get("leverage_cap", 999)) > 1.0:
        errors.append("leverage_cap must be <= 1.0")
    if errors:
        return CheckOutcome(FAIL, _join_errors(errors), {"risk": risk})
    return CheckOutcome(PASS, "risk limits and veto immunity are valid", {"risk": risk})


def check_source_registry(ctx: CheckContext, spec: dict[str, Any]) -> CheckOutcome:
    sources = as_list(ctx.manifest.get("sources"))
    errors: list[str] = []
    if not sources:
        errors.append("no sources declared")
    ids = [str(s.get("id")) for s in sources if isinstance(s, dict) and s.get("id")]
    duplicates = sorted({item_id for item_id in ids if ids.count(item_id) > 1})
    if duplicates:
        errors.append(f"duplicate source IDs: {duplicates}")
    for source in sources:
        if not isinstance(source, dict):
            errors.append("source entry is not a mapping")
            continue
        source_id = source.get("id", "<unknown>")
        if not source.get("name"):
            errors.append(f"{source_id}: missing name")
        source_type = source.get("type")
        if source_type not in ALLOWED_SOURCE_TYPES:
            errors.append(f"{source_id}: unsupported source type {source_type!r}")
        if not source.get("role"):
            errors.append(f"{source_id}: missing role")
        if not isinstance(source.get("legal"), dict):
            errors.append(f"{source_id}: missing legal block")
        if not isinstance(source.get("revision"), dict):
            errors.append(f"{source_id}: missing revision block")
        if not isinstance(source.get("coverage"), dict):
            errors.append(f"{source_id}: missing coverage block")
    source_ids = set(ids)
    for item in as_list(ctx.manifest.get("fundamental_inputs")):
        if not isinstance(item, dict):
            continue
        for source_id in as_list(item.get("source_ids")):
            if str(source_id) not in source_ids:
                errors.append(f"fundamental {item.get('id')} references missing source {source_id}")
    if errors:
        return CheckOutcome(FAIL, _join_errors(errors), {"source_ids": ids})
    return CheckOutcome(PASS, f"{len(sources)} sources declared with supported types", {"source_ids": ids})


def check_source_legal(ctx: CheckContext, spec: dict[str, Any]) -> CheckOutcome:
    errors: list[str] = []
    for source in as_list(ctx.manifest.get("sources")):
        if not isinstance(source, dict):
            continue
        source_id = source.get("id", "<unknown>")
        legal = as_dict(source.get("legal"))
        if not legal.get("license"):
            errors.append(f"{source_id}: missing legal.license")
        if legal.get("tos_risk") not in ALLOWED_TOS_RISK:
            errors.append(f"{source_id}: legal.tos_risk must be one of {sorted(ALLOWED_TOS_RISK)}")
        if not legal.get("notes"):
            errors.append(f"{source_id}: missing legal.notes")
    if errors:
        return CheckOutcome(FAIL, _join_errors(errors), {"errors": errors})
    return CheckOutcome(PASS, "all sources declare license, ToS risk, and legal notes")


def check_source_point_in_time(ctx: CheckContext, spec: dict[str, Any]) -> CheckOutcome:
    errors: list[str] = []
    for source in as_list(ctx.manifest.get("sources")):
        if not isinstance(source, dict):
            continue
        source_id = source.get("id", "<unknown>")
        revision = as_dict(source.get("revision"))
        if not isinstance(revision.get("point_in_time"), bool):
            errors.append(f"{source_id}: revision.point_in_time must be a boolean")
        if not revision.get("knowledge_time"):
            errors.append(f"{source_id}: missing revision.knowledge_time")
        if not revision.get("notes"):
            errors.append(f"{source_id}: missing revision.notes")
    if errors:
        return CheckOutcome(FAIL, _join_errors(errors), {"errors": errors})
    return CheckOutcome(PASS, "all sources declare point-in-time behavior and knowledge time")


def check_source_coverage(ctx: CheckContext, spec: dict[str, Any]) -> CheckOutcome:
    errors: list[str] = []
    for source in as_list(ctx.manifest.get("sources")):
        if not isinstance(source, dict):
            continue
        source_id = source.get("id", "<unknown>")
        coverage = as_dict(source.get("coverage"))
        for key in ("history_start", "cadence", "gap_notes"):
            if not coverage.get(key):
                errors.append(f"{source_id}: missing coverage.{key}")
    if errors:
        return CheckOutcome(FAIL, _join_errors(errors), {"errors": errors})
    return CheckOutcome(PASS, "all sources declare coverage history, cadence, and gap notes")


def check_fundamental_weights(ctx: CheckContext, spec: dict[str, Any]) -> CheckOutcome:
    inputs = [item for item in as_list(ctx.manifest.get("fundamental_inputs")) if isinstance(item, dict)]
    errors: list[str] = []
    if not inputs:
        return CheckOutcome(FAIL, "no fundamental inputs declared")
    total = sum(float(item.get("weight", 0.0)) for item in inputs)
    if abs(total - 1.0) > 1e-9:
        errors.append(f"weights sum to {total:.6f}, expected 1.0")
    for item in inputs:
        weight = float(item.get("weight", 0.0))
        if weight < 0:
            errors.append(f"{item.get('id')}: negative weight")
        if item.get("id") == "central_bank_gold" and abs(weight) > 1e-12:
            errors.append("central_bank_gold weight must be 0.0")
        if item.get("id") == "breakeven_10y" and abs(weight) > 1e-12:
            errors.append("breakeven_10y weight must be 0.0")
    ids = {str(item.get("id")) for item in inputs}
    for required in ("central_bank_gold", "breakeven_10y"):
        if required not in ids:
            errors.append(f"missing diagnostic input: {required}")
    if errors:
        return CheckOutcome(FAIL, _join_errors(errors), {"weights": {item.get("id"): item.get("weight") for item in inputs}})
    return CheckOutcome(PASS, "fundamental weights sum to 1.0 and diagnostics are zero-weight", {"weights": {item.get("id"): item.get("weight") for item in inputs}})


def check_fundamental_point_in_time(ctx: CheckContext, spec: dict[str, Any]) -> CheckOutcome:
    inputs = [item for item in as_list(ctx.manifest.get("fundamental_inputs")) if isinstance(item, dict)]
    errors: list[str] = []
    for item in inputs:
        item_id = str(item.get("id"))
        if item_id not in ALLOWED_FUNDAMENTALS:
            errors.append(f"{item_id}: not a permitted v1 gold fundamental")
        for key in ("field", "transform", "pit_policy", "revision_policy"):
            if not item.get(key):
                errors.append(f"{item_id}: missing {key}")
        if item.get("sign") not in ALLOWED_SIGNS:
            errors.append(f"{item_id}: sign must be one of {sorted(ALLOWED_SIGNS)}")
        if not as_list(item.get("source_ids")):
            errors.append(f"{item_id}: no source_ids")
    if errors:
        return CheckOutcome(FAIL, _join_errors(errors), {"errors": errors})
    return CheckOutcome(PASS, "fundamental inputs declare fields, transforms, signs, and PIT policies")


def check_central_bank_policy(ctx: CheckContext, spec: dict[str, Any]) -> CheckOutcome:
    fundamentals = fundamental_index(ctx.manifest)
    sources = source_index(ctx.manifest)
    item = fundamentals.get("central_bank_gold")
    source = sources.get("wgc_central_bank_manual")
    errors: list[str] = []
    if item is None:
        errors.append("missing fundamental input: central_bank_gold")
    else:
        if abs(float(item.get("weight", 1.0))) > 1e-12:
            errors.append("central_bank_gold weight must be 0.0")
        if abs(float(item.get("entry_score_weight", 1.0))) > 1e-12:
            errors.append("central_bank_gold entry_score_weight must be 0.0")
        if item.get("role") != "diagnostic":
            errors.append("central_bank_gold role must be diagnostic")
        if item.get("validation_class") != "HUMAN_JUDGMENT":
            errors.append("central_bank_gold validation_class must be HUMAN_JUDGMENT")
        if item.get("consumed_by_overseer") is not False:
            errors.append("central_bank_gold consumed_by_overseer must be false")
        if item.get("label") != CENTRAL_BANK_LABEL:
            errors.append("central_bank_gold label is missing or not exact")
    if source is None:
        errors.append("missing source: wgc_central_bank_manual")
    else:
        if source.get("type") != "manual_xlsx":
            errors.append("wgc_central_bank_manual type must be manual_xlsx")
        if not str(source.get("path", "")).startswith("data/manual/wgc/"):
            errors.append("wgc_central_bank_manual path must be under data/manual/wgc/")
        if source.get("required_for_m1") is not False:
            errors.append("wgc_central_bank_manual required_for_m1 must be false")
        if source.get("label") != CENTRAL_BANK_LABEL:
            errors.append("wgc_central_bank_manual label is missing or not exact")
    if errors:
        return CheckOutcome(FAIL, _join_errors(errors), {"label": CENTRAL_BANK_LABEL})
    return CheckOutcome(
        PASS,
        "central-bank gold is diagnostic, weight 0, HUMAN_JUDGMENT, and not consumed by the overseer",
        {"label": CENTRAL_BANK_LABEL},
    )


def check_technical_timeframes(ctx: CheckContext, spec: dict[str, Any]) -> CheckOutcome:
    technical = as_dict(ctx.manifest.get("technical"))
    errors: list[str] = []
    if technical.get("execution_bar") != "M15":
        errors.append("technical.execution_bar must be M15")
    if technical.get("context_bar") != "H4":
        errors.append("technical.context_bar must be H4")
    if errors:
        return CheckOutcome(FAIL, _join_errors(errors), {"technical": technical})
    return CheckOutcome(PASS, "technical execution bar is M15 and context bar is H4")


def check_technical_indicators(ctx: CheckContext, spec: dict[str, Any]) -> CheckOutcome:
    technical = as_dict(ctx.manifest.get("technical"))
    indicators = [item for item in as_list(technical.get("indicators")) if isinstance(item, dict)]
    ids = {str(item.get("id")) for item in indicators}
    errors: list[str] = []
    required_groups = {
        "ema": {"ema_stack", "ema", "ema_cross"},
        "rsi": {"rsi"},
        "atr": {"atr"},
        "structure": {"swing_structure", "structure", "market_structure"},
        "h4_context": {"h4_trend", "h4_context", "h4_regime"},
    }
    for group, options in required_groups.items():
        if not (ids & options):
            errors.append(f"missing {group} indicator")
    for item in indicators:
        if not item.get("timeframe"):
            errors.append(f"{item.get('id')}: missing timeframe")
        if not isinstance(item.get("params"), dict):
            errors.append(f"{item.get('id')}: missing params")
    if errors:
        return CheckOutcome(FAIL, _join_errors(errors), {"indicator_ids": sorted(ids)})
    return CheckOutcome(PASS, "technical indicators include EMA, RSI, ATR, structure, and H4 context", {"indicator_ids": sorted(ids)})


def check_fusion_policy(ctx: CheckContext, spec: dict[str, Any]) -> CheckOutcome:
    fusion = as_dict(ctx.manifest.get("fusion"))
    errors: list[str] = []
    try:
        total = float(fusion.get("fundamental_weight", 0.0)) + float(fusion.get("technical_weight", 0.0))
    except (TypeError, ValueError):
        total = -1.0
    if abs(total - 1.0) > 1e-9:
        errors.append(f"fusion weights sum to {total}, expected 1.0")
    if fusion.get("entry_requires_both") is not True:
        errors.append("entry_requires_both must be true")
    if fusion.get("overseer_can_originate") is not False:
        errors.append("overseer_can_originate must be false")
    if fusion.get("conflict_policy") != "no_trade":
        errors.append("conflict_policy must be no_trade")
    if errors:
        return CheckOutcome(FAIL, _join_errors(errors), {"fusion": fusion})
    return CheckOutcome(PASS, "fusion requires both layers and the overseer cannot originate entries")


def check_overseer_policy(ctx: CheckContext, spec: dict[str, Any]) -> CheckOutcome:
    overseer = as_dict(ctx.manifest.get("overseer"))
    authority = as_dict(overseer.get("authority"))
    errors: list[str] = []
    if overseer.get("type") != "rule_based_referee":
        errors.append("overseer.type must be rule_based_referee")
    if overseer.get("originates_entries") is not False:
        errors.append("overseer.originates_entries must be false")
    if overseer.get("arbitrates") is not True:
        errors.append("overseer.arbitrates must be true")
    if overseer.get("risk_exits_veto_immune") is not True:
        errors.append("overseer.risk_exits_veto_immune must be true")
    if authority.get("may_change_scope") is not False:
        errors.append("overseer.authority.may_change_scope must be false")
    if authority.get("may_override_risk_exit") is not False:
        errors.append("overseer.authority.may_override_risk_exit must be false")
    if authority.get("may_emit_advisory_exit") is not True:
        errors.append("overseer.authority.may_emit_advisory_exit must be true")
    if errors:
        return CheckOutcome(FAIL, _join_errors(errors), {"overseer": overseer})
    return CheckOutcome(PASS, "overseer is a rule-based referee and cannot originate trades or override risk exits")


def check_exit_precedence(ctx: CheckContext, spec: dict[str, Any]) -> CheckOutcome:
    exits = as_dict(ctx.manifest.get("exits"))
    precedence = [item for item in as_list(exits.get("precedence")) if isinstance(item, dict)]
    errors: list[str] = []
    if exits.get("risk_exits_veto_immune") is not True:
        errors.append("exits.risk_exits_veto_immune must be true")
    if exits.get("sell_decisions_are_first_class") is not True:
        errors.append("exits.sell_decisions_are_first_class must be true")
    actual = [(int(item.get("priority", -1)), str(item.get("id"))) for item in precedence]
    if actual != EXPECTED_EXIT_PRECEDENCE:
        errors.append(f"precedence is {actual}, expected {EXPECTED_EXIT_PRECEDENCE}")
    if errors:
        return CheckOutcome(FAIL, _join_errors(errors), {"actual": actual, "expected": EXPECTED_EXIT_PRECEDENCE})
    return CheckOutcome(PASS, "exit precedence is correct and risk exits are veto-immune", {"precedence": actual})


def check_storage_sqlite(ctx: CheckContext, spec: dict[str, Any]) -> CheckOutcome:
    sqlite = as_dict(as_dict(ctx.manifest.get("storage")).get("sqlite"))
    errors: list[str] = []
    if str(sqlite.get("journal_mode", "")).upper() != "WAL":
        errors.append("storage.sqlite.journal_mode must be WAL")
    if sqlite.get("single_writer") is not True:
        errors.append("storage.sqlite.single_writer must be true")
    tables = {str(table) for table in as_list(sqlite.get("tables"))}
    missing = sorted(REQUIRED_TABLES - tables)
    if missing:
        errors.append(f"missing SQLite tables: {missing}")
    if errors:
        return CheckOutcome(FAIL, _join_errors(errors), {"tables": sorted(tables)})
    return CheckOutcome(PASS, "SQLite uses WAL, a single writer, and the required tables")


def check_storage_analytics(ctx: CheckContext, spec: dict[str, Any]) -> CheckOutcome:
    analytical = as_dict(as_dict(ctx.manifest.get("storage")).get("analytical"))
    storage_type = str(analytical.get("type", "")).lower()
    errors: list[str] = []
    if storage_type not in {"parquet_duckdb", "duckdb_parquet", "parquet", "duckdb"}:
        errors.append(f"analytical storage type must be Parquet/DuckDB, got {storage_type!r}")
    if "postgres" in storage_type:
        errors.append("PostgreSQL is not allowed in v1")
    if not analytical.get("path"):
        errors.append("analytical storage path is missing")
    if errors:
        return CheckOutcome(FAIL, _join_errors(errors), {"analytical": analytical})
    return CheckOutcome(PASS, "analytical storage is Parquet/DuckDB")


def check_storage_schema(ctx: CheckContext, spec: dict[str, Any]) -> CheckOutcome:
    storage = as_dict(ctx.manifest.get("storage"))
    schema = as_dict(storage.get("schema"))
    path_value = schema.get("path")
    if not path_value:
        return CheckOutcome(FAIL, "storage.schema.path is missing")
    path = ctx.project_root / str(path_value)
    if not path.exists():
        return CheckOutcome(FAIL, f"schema file not found: {path_value}", {"path": str(path)})
    text = path.read_text(encoding="utf-8")
    found = {
        match.group(1).lower()
        for match in re.finditer(
            r"CREATE\s+TABLE(?:\s+IF\s+NOT\s+EXISTS)?\s+[\"`]?([A-Za-z_][A-Za-z0-9_]*)",
            text,
            flags=re.IGNORECASE,
        )
    }
    required = {str(table).lower() for table in as_list(schema.get("required_tables"))} or REQUIRED_TABLES
    missing = sorted(required - found)
    if missing:
        return CheckOutcome(FAIL, f"schema is missing tables: {missing}", {"found": sorted(found), "missing": missing})
    return CheckOutcome(PASS, "schema file defines all required tables", {"found": sorted(found)})


def check_backtest_assumptions(ctx: CheckContext, spec: dict[str, Any]) -> CheckOutcome:
    backtest = as_dict(ctx.manifest.get("backtest"))
    costs = as_dict(backtest.get("costs"))
    errors: list[str] = []
    for key in ("spread", "commission", "slippage", "swap"):
        if not costs.get(key):
            errors.append(f"backtest.costs.{key} is missing")
    if backtest.get("fill_rule") != "next_bar_open":
        errors.append("backtest.fill_rule must be next_bar_open")
    for key in ("stop_first", "no_lookahead", "point_in_time_macro", "out_of_sample", "walk_forward"):
        if backtest.get(key) is not True:
            errors.append(f"backtest.{key} must be true")
    if int(backtest.get("min_demo_months", 0)) < 3:
        errors.append("backtest.min_demo_months must be >= 3")
    if int(backtest.get("min_trades", 0)) < 100:
        errors.append("backtest.min_trades must be >= 100")
    if errors:
        return CheckOutcome(FAIL, _join_errors(errors), {"backtest": backtest})
    return CheckOutcome(PASS, "backtest assumptions include costs, next-bar fills, stop-first, no lookahead, PIT macro, and OOS")


def check_promotion_gates(ctx: CheckContext, spec: dict[str, Any]) -> CheckOutcome:
    backtest = as_dict(ctx.manifest.get("backtest"))
    gates = {str(gate) for gate in as_list(backtest.get("promotion_gates"))}
    errors: list[str] = []
    missing = sorted(REQUIRED_PROMOTION_GATES - gates)
    if missing:
        errors.append(f"missing promotion gates: {missing}")
    if int(backtest.get("min_demo_months", 0)) < 3:
        errors.append("min_demo_months must be >= 3")
    if int(backtest.get("min_trades", 0)) < 100:
        errors.append("min_trades must be >= 100")
    if errors:
        return CheckOutcome(FAIL, _join_errors(errors), {"gates": sorted(gates)})
    return CheckOutcome(PASS, "all promotion gates are declared", {"gates": sorted(gates)})


def check_dashboard_boundary(ctx: CheckContext, spec: dict[str, Any]) -> CheckOutcome:
    dashboard = as_dict(as_dict(ctx.manifest.get("analytics")).get("dashboard"))
    errors: list[str] = []
    if dashboard.get("mt5_access") != "forbidden":
        errors.append("analytics.dashboard.mt5_access must be forbidden")
    if dashboard.get("scope_change") != "forbidden":
        errors.append("analytics.dashboard.scope_change must be forbidden")
    if dashboard.get("read_only") is not True:
        errors.append("analytics.dashboard.read_only must be true")
    if errors:
        return CheckOutcome(FAIL, _join_errors(errors), {"dashboard": dashboard})
    return CheckOutcome(PASS, "dashboard is read-only and has no MT5 or scope access")


def check_runtime_boundaries(ctx: CheckContext, spec: dict[str, Any]) -> CheckOutcome:
    processes = process_index(ctx.manifest)
    errors: list[str] = []
    supervisor = processes.get("supervisor")
    bot = processes.get("bot")
    dashboard = processes.get("dashboard")
    if supervisor is None:
        errors.append("missing supervisor process")
    else:
        if supervisor.get("only_scope_changer") is not True:
            errors.append("supervisor.only_scope_changer must be true")
        if supervisor.get("can_change_scope") is not True:
            errors.append("supervisor.can_change_scope must be true")
    if bot is None:
        errors.append("missing bot process")
    elif bot.get("can_change_scope") is not False:
        errors.append("bot.can_change_scope must be false")
    if dashboard is None:
        errors.append("missing dashboard process")
    else:
        if dashboard.get("mt5_access") != "forbidden":
            errors.append("dashboard.mt5_access must be forbidden")
        if dashboard.get("can_change_scope") is not False:
            errors.append("dashboard.can_change_scope must be false")
    for process_id, process in processes.items():
        if process_id != "supervisor" and process.get("can_change_scope") is True:
            errors.append(f"{process_id} must not change scope")
    if errors:
        return CheckOutcome(FAIL, _join_errors(errors), {"processes": sorted(processes)})
    return CheckOutcome(PASS, "supervisor is the only scope changer; dashboard and bot are constrained")


def check_yaml_file(ctx: CheckContext, spec: dict[str, Any]) -> CheckOutcome:
    path_value = spec.get("path")
    if not path_value:
        return CheckOutcome(FAIL, "yaml_file check requires path")
    path = ctx.project_root / str(path_value)
    if not path.exists():
        return CheckOutcome(FAIL, f"file not found: {path_value}", {"path": str(path)})
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = yaml.safe_load(handle)
    except yaml.YAMLError as exc:
        return CheckOutcome(FAIL, f"invalid YAML: {exc}", {"path": str(path)})
    if not isinstance(data, dict):
        return CheckOutcome(FAIL, "YAML root must be a mapping", {"path": str(path)})
    missing = [key for key in as_list(spec.get("required_keys")) if key not in data]
    if missing:
        return CheckOutcome(FAIL, f"missing keys: {missing}", {"path": str(path), "keys": sorted(data)})
    return CheckOutcome(PASS, f"YAML file is valid and contains required keys", {"path": str(path), "keys": sorted(data)})


def check_calibration_artifact(ctx: CheckContext, spec: dict[str, Any]) -> CheckOutcome:
    overseer = as_dict(ctx.manifest.get("overseer"))
    calibration = as_dict(overseer.get("calibration"))
    path_value = spec.get("path") or calibration.get("artifact")
    if not path_value:
        return CheckOutcome(FAIL, "calibration artifact path is missing")
    path = ctx.project_root / str(path_value)
    if not path.exists():
        return CheckOutcome(FAIL, f"calibration artifact not found: {path_value}", {"path": str(path)})
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return CheckOutcome(FAIL, f"invalid JSON in calibration artifact: {exc}", {"path": str(path)})
    if not isinstance(data, dict):
        return CheckOutcome(FAIL, "calibration artifact must be a JSON object", {"path": str(path)})
    errors: list[str] = []
    required_keys = as_list(spec.get("required_keys")) or as_list(calibration.get("required_keys"))
    for key in required_keys:
        if key not in data:
            errors.append(f"missing key: {key}")
    if data.get("manifest_hash") != ctx.manifest_hash:
        errors.append(
            f"manifest_hash mismatch: artifact={data.get('manifest_hash')} current={ctx.manifest_hash}"
        )
    if data.get("scope_id") != ctx.manifest.get("scope_id"):
        errors.append(
            f"scope_id mismatch: artifact={data.get('scope_id')} current={ctx.manifest.get('scope_id')}"
        )
    status = data.get("status")
    allow_not_fitted = bool(spec.get("allow_not_fitted", calibration.get("allow_not_fitted", False)))
    if status not in {"not_fitted", "fitted"}:
        errors.append(f"invalid calibration status: {status!r}")
    if status == "not_fitted" and not allow_not_fitted:
        errors.append("calibration is not fitted but allow_not_fitted is false")
    if status == "fitted":
        if not data.get("fit_window"):
            errors.append("fitted calibration is missing fit_window")
        if not data.get("metrics"):
            errors.append("fitted calibration is missing metrics")
    if errors:
        return CheckOutcome(FAIL, _join_errors(errors), {"path": str(path), "status": status})
    return CheckOutcome(
        PASS,
        f"calibration artifact is valid (status={status}, manifest hash matches)",
        {"path": str(path), "status": status, "manifest_hash": ctx.manifest_hash},
    )


def check_http_csv(ctx: CheckContext, spec: dict[str, Any]) -> CheckOutcome:
    if ctx.offline:
        return CheckOutcome(SKIP, "offline mode: HTTP check skipped")
    resolved = _resolve_source_spec(ctx, spec)
    url = resolved.get("url")
    if not url:
        return CheckOutcome(FAIL, "source has no URL")
    try:
        response = ctx.http_get(str(url), timeout=resolved.get("timeout_seconds"))
    except httpx.HTTPError as exc:
        return CheckOutcome(FAIL, f"HTTP request failed: {exc}", {"url": url})
    if response.status_code != 200:
        return CheckOutcome(
            FAIL,
            f"HTTP {response.status_code} from {url}",
            {"url": url, "status_code": response.status_code, "body_preview": response.text[:300]},
        )
    headers, rows = _read_csv(response.text)
    errors: list[str] = []
    missing = _check_columns(headers, as_list(resolved.get("required_columns")), resolved.get("value_column_regex"))
    if missing:
        errors.append(f"missing columns: {missing}")
    min_rows = int(resolved.get("min_rows", 0))
    if len(rows) < min_rows:
        errors.append(f"only {len(rows)} rows, expected at least {min_rows}")
    filtered = _filter_rows(rows, resolved.get("filter_column"), resolved.get("filter_value"))
    min_filtered = int(resolved.get("min_filtered_rows", 0))
    if len(filtered) < min_filtered:
        errors.append(f"only {len(filtered)} filtered rows, expected at least {min_filtered}")
    latest = None
    age = None
    date_column = resolved.get("date_column")
    if date_column:
        latest = _latest_date(filtered or rows, str(date_column), as_list(resolved.get("date_formats")))
        if latest is None:
            errors.append(f"no parseable dates in column {date_column}")
        else:
            age = _age_days(latest, ctx.now)
            max_age = resolved.get("max_age_days")
            if max_age is not None and age is not None and age > int(max_age):
                errors.append(f"latest date {latest} is {age} days old, max {max_age}")
    evidence = {
        "url": url,
        "status_code": response.status_code,
        "content_type": response.headers.get("content-type"),
        "bytes": len(response.content),
        "rows": len(rows),
        "filtered_rows": len(filtered),
        "columns": headers,
        "latest_date": latest.isoformat() if latest else None,
        "age_days": age,
    }
    if errors:
        return CheckOutcome(FAIL, _join_errors(errors), evidence)
    return CheckOutcome(PASS, f"CSV reachable and fresh ({len(rows)} rows, latest {latest})", evidence)


def check_http_xlsx(ctx: CheckContext, spec: dict[str, Any]) -> CheckOutcome:
    if ctx.offline:
        return CheckOutcome(SKIP, "offline mode: HTTP check skipped")
    resolved = _resolve_source_spec(ctx, spec)
    url = resolved.get("url")
    if not url:
        return CheckOutcome(FAIL, "source has no URL")
    try:
        response = ctx.http_get(str(url), timeout=resolved.get("timeout_seconds"))
    except httpx.HTTPError as exc:
        return CheckOutcome(FAIL, f"HTTP request failed: {exc}", {"url": url})
    if response.status_code != 200:
        return CheckOutcome(
            FAIL,
            f"HTTP {response.status_code} from {url}",
            {"url": url, "status_code": response.status_code, "body_preview": response.text[:300]},
        )
    try:
        headers, rows, sheets, sheet = _read_xlsx(
            response.content,
            resolved.get("sheet_name"),
            int(resolved.get("header_row", 1)),
        )
    except Exception as exc:
        return CheckOutcome(FAIL, f"XLSX parse failed: {type(exc).__name__}: {exc}", {"url": url})
    errors: list[str] = []
    missing = _check_columns(headers, as_list(resolved.get("required_columns")), resolved.get("value_column_regex"))
    if missing:
        errors.append(f"missing columns: {missing}")
    min_rows = int(resolved.get("min_rows", 0))
    if len(rows) < min_rows:
        errors.append(f"only {len(rows)} rows, expected at least {min_rows}")
    latest = None
    age = None
    date_column = resolved.get("date_column")
    if date_column:
        latest = _latest_date(rows, str(date_column), as_list(resolved.get("date_formats")))
        if latest is None:
            errors.append(f"no parseable dates in column {date_column}")
        else:
            age = _age_days(latest, ctx.now)
            max_age = resolved.get("max_age_days")
            if max_age is not None and age is not None and age > int(max_age):
                errors.append(f"latest date {latest} is {age} days old, max {max_age}")
    evidence = {
        "url": url,
        "status_code": response.status_code,
        "bytes": len(response.content),
        "sheets": sheets,
        "sheet": sheet,
        "rows": len(rows),
        "columns": headers,
        "latest_date": latest.isoformat() if latest else None,
        "age_days": age,
    }
    if errors:
        return CheckOutcome(FAIL, _join_errors(errors), evidence)
    return CheckOutcome(PASS, f"XLSX reachable and fresh ({len(rows)} rows, latest {latest})", evidence)


def check_http_zip(ctx: CheckContext, spec: dict[str, Any]) -> CheckOutcome:
    if ctx.offline:
        return CheckOutcome(SKIP, "offline mode: HTTP check skipped")
    resolved = _resolve_source_spec(ctx, spec)
    url = resolved.get("url")
    if not url:
        return CheckOutcome(FAIL, "source has no URL")
    try:
        response = ctx.http_get(str(url), timeout=resolved.get("timeout_seconds"))
    except httpx.HTTPError as exc:
        return CheckOutcome(FAIL, f"HTTP request failed: {exc}", {"url": url})
    if response.status_code != 200:
        return CheckOutcome(
            FAIL,
            f"HTTP {response.status_code} from {url}",
            {"url": url, "status_code": response.status_code, "body_preview": response.text[:300]},
        )
    try:
        archive = zipfile.ZipFile(io.BytesIO(response.content))
        names = archive.namelist()
    except zipfile.BadZipFile as exc:
        return CheckOutcome(FAIL, f"not a valid ZIP: {exc}", {"url": url, "bytes": len(response.content)})
    errors: list[str] = []
    required_entries = as_list(resolved.get("required_entries"))
    missing_entries = [entry for entry in required_entries if entry not in names]
    if missing_entries:
        errors.append(f"missing ZIP entries: {missing_entries}")
    headers: list[str] = []
    rows: list[dict[str, Any]] = []
    filtered: list[dict[str, Any]] = []
    latest = None
    age = None
    entry = resolved.get("entry_csv")
    if entry:
        try:
            headers, rows, _ = _read_zip_csv(response.content, str(entry))
        except Exception as exc:
            errors.append(f"entry parse failed: {type(exc).__name__}: {exc}")
        else:
            missing = _check_columns(headers, as_list(resolved.get("required_columns")), resolved.get("value_column_regex"))
            if missing:
                errors.append(f"missing columns: {missing}")
            min_rows = int(resolved.get("min_rows", 0))
            if len(rows) < min_rows:
                errors.append(f"only {len(rows)} rows, expected at least {min_rows}")
            filtered = _filter_rows(rows, resolved.get("filter_column"), resolved.get("filter_value"))
            min_filtered = int(resolved.get("min_filtered_rows", 0))
            if len(filtered) < min_filtered:
                errors.append(f"only {len(filtered)} filtered rows, expected at least {min_filtered}")
            date_column = resolved.get("date_column")
            if date_column:
                latest = _latest_date(filtered or rows, str(date_column), as_list(resolved.get("date_formats")))
                if latest is None:
                    errors.append(f"no parseable dates in column {date_column}")
                else:
                    age = _age_days(latest, ctx.now)
                    max_age = resolved.get("max_age_days")
                    if max_age is not None and age is not None and age > int(max_age):
                        errors.append(f"latest date {latest} is {age} days old, max {max_age}")
    evidence = {
        "url": url,
        "status_code": response.status_code,
        "bytes": len(response.content),
        "entries": names,
        "rows": len(rows),
        "filtered_rows": len(filtered),
        "columns": headers,
        "latest_date": latest.isoformat() if latest else None,
        "age_days": age,
    }
    if errors:
        return CheckOutcome(FAIL, _join_errors(errors), evidence)
    return CheckOutcome(PASS, f"ZIP reachable and expected entries present ({len(rows)} rows)", evidence)


def check_http_html(ctx: CheckContext, spec: dict[str, Any]) -> CheckOutcome:
    if ctx.offline:
        return CheckOutcome(SKIP, "offline mode: HTTP check skipped")
    resolved = _resolve_source_spec(ctx, spec)
    url = resolved.get("url")
    if not url:
        return CheckOutcome(FAIL, "source has no URL")
    try:
        response = ctx.http_get(str(url), timeout=resolved.get("timeout_seconds"))
    except httpx.HTTPError as exc:
        return CheckOutcome(FAIL, f"HTTP request failed: {exc}", {"url": url})
    if response.status_code != 200:
        return CheckOutcome(FAIL, f"HTTP {response.status_code} from {url}", {"url": url})
    text = response.text
    missing = [needle for needle in as_list(resolved.get("required_text")) if str(needle) not in text]
    if missing:
        return CheckOutcome(FAIL, f"required text not found: {missing}", {"url": url})
    return CheckOutcome(PASS, "HTML reachable and required text present", {"url": url, "bytes": len(response.content)})


def check_manual_xlsx(ctx: CheckContext, spec: dict[str, Any]) -> CheckOutcome:
    resolved = _resolve_source_spec(ctx, spec)
    path_value = resolved.get("path")
    if not path_value:
        return CheckOutcome(HUMAN_JUDGMENT, "manual XLSX path is missing; human judgment required", {"label": CENTRAL_BANK_LABEL})
    path = ctx.project_root / str(path_value)
    evidence: dict[str, Any] = {
        "path": str(path),
        "file_present": path.exists(),
        "label": CENTRAL_BANK_LABEL,
    }
    if not path.exists():
        return CheckOutcome(
            HUMAN_JUDGMENT,
            "manual XLSX is absent; mechanical checks cannot run. Human must download and verify it.",
            evidence,
        )
    try:
        content = path.read_bytes()
        headers, rows, sheets, sheet = _read_xlsx(
            content,
            resolved.get("sheet_name"),
            int(resolved.get("header_row", 1)),
        )
        evidence.update(
            {
                "parse_ok": True,
                "bytes": len(content),
                "sheets": sheets,
                "sheet": sheet,
                "headers": headers,
                "rows": len(rows),
                "sha256": hashlib.sha256(content).hexdigest(),
            }
        )
    except Exception as exc:
        evidence["parse_ok"] = False
        evidence["parse_error"] = f"{type(exc).__name__}: {exc}"
        return CheckOutcome(
            HUMAN_JUDGMENT,
            "manual XLSX is present but unparseable; mechanical failure recorded, content remains HUMAN_JUDGMENT.",
            evidence,
        )
    mechanical_failures: list[str] = []
    min_rows = int(resolved.get("min_rows", 0))
    if len(rows) < min_rows:
        mechanical_failures.append(f"only {len(rows)} rows, expected at least {min_rows}")
    missing = _check_columns(headers, as_list(resolved.get("required_columns")), resolved.get("value_column_regex"))
    if missing:
        mechanical_failures.append(f"missing columns: {missing}")
    date_column = resolved.get("date_column")
    latest = _latest_date(rows, str(date_column), as_list(resolved.get("date_formats"))) if date_column else None
    if latest is not None:
        age = _age_days(latest, ctx.now)
        evidence["latest_date"] = latest.isoformat()
        evidence["age_days"] = age
        max_age = resolved.get("max_age_days")
        if max_age is not None and age is not None and age > int(max_age):
            mechanical_failures.append(f"latest date {latest} is {age} days old, max {max_age}")
    else:
        mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        file_age = (ctx.now - mtime).days
        evidence["file_mtime_utc"] = mtime.isoformat()
        evidence["file_age_days"] = file_age
        max_age = resolved.get("max_age_days")
        if max_age is not None and file_age > int(max_age):
            mechanical_failures.append(f"file is {file_age} days old, max {max_age}")
    reminder_path_value = resolved.get("reminder_event")
    reminder_ok = None
    if reminder_path_value:
        reminder_path = ctx.project_root / str(reminder_path_value)
        evidence["reminder_event_present"] = reminder_path.exists()
        if reminder_path.exists():
            try:
                reminder = json.loads(reminder_path.read_text(encoding="utf-8"))
                evidence["reminder_event"] = reminder
                last_refresh = reminder.get("last_refresh_utc")
                if last_refresh:
                    parsed = _parse_date(last_refresh)
                    if parsed:
                        reminder_age = _age_days(parsed, ctx.now)
                        evidence["reminder_age_days"] = reminder_age
                        cadence = int(resolved.get("reminder_cadence_days", 30))
                        reminder_ok = reminder_age <= cadence
                        if not reminder_ok:
                            mechanical_failures.append(
                                f"reminder is {reminder_age} days old, cadence {cadence}"
                            )
            except Exception as exc:
                evidence["reminder_error"] = f"{type(exc).__name__}: {exc}"
                reminder_ok = False
                mechanical_failures.append("reminder event is unparseable")
        else:
            reminder_ok = False
            mechanical_failures.append("reminder event is missing")
    evidence["mechanical_failures"] = mechanical_failures
    if mechanical_failures:
        return CheckOutcome(
            HUMAN_JUDGMENT,
            "manual XLSX mechanical checks found issues; content remains HUMAN_JUDGMENT. "
            + _join_errors(mechanical_failures),
            evidence,
        )
    return CheckOutcome(
        HUMAN_JUDGMENT,
        "manual XLSX passes mechanical checks; content correctness remains HUMAN_JUDGMENT.",
        evidence,
    )


def check_mt5_preflight(ctx: CheckContext, spec: dict[str, Any]) -> CheckOutcome:
    if ctx.offline:
        return CheckOutcome(SKIP, "offline mode: MT5 preflight skipped")
    if ctx.no_mt5:
        return CheckOutcome(SKIP, "no-MT5 mode: MT5 preflight skipped")
    resolved = _resolve_source_spec(ctx, spec)
    symbol = str(resolved.get("symbol", "XAUUSD"))
    try:
        import MetaTrader5 as mt5  # type: ignore
    except Exception as exc:
        return CheckOutcome(BLOCKED, f"MetaTrader5 import failed: {type(exc).__name__}: {exc}")
    initialized = False
    try:
        initialized = bool(mt5.initialize(timeout=5000))
        if not initialized:
            return CheckOutcome(
                FAIL,
                f"MT5 initialize failed: {mt5.last_error()}",
                {"last_error": mt5.last_error()},
            )
        terminal = mt5.terminal_info()
        account = mt5.account_info()
        if terminal is None:
            return CheckOutcome(FAIL, "MT5 terminal_info is None", {"last_error": mt5.last_error()})
        if account is None:
            return CheckOutcome(FAIL, "MT5 account_info is None; terminal is not logged in", {"last_error": mt5.last_error()})
        if resolved.get("require_demo", True):
            demo_mode = getattr(mt5, "ACCOUNT_TRADE_MODE_DEMO", 0)
            if getattr(account, "trade_mode", None) != demo_mode:
                return CheckOutcome(
                    FAIL,
                    f"account is not a demo account (trade_mode={getattr(account, 'trade_mode', None)})",
                    {"account": str(account)},
                )
        symbol_info = mt5.symbol_info(symbol)
        if symbol_info is None:
            return CheckOutcome(FAIL, f"symbol not found: {symbol}", {"last_error": mt5.last_error()})
        if not getattr(symbol_info, "visible", True):
            mt5.symbol_select(symbol, True)
            symbol_info = mt5.symbol_info(symbol)
        min_m15 = int(resolved.get("min_execution_bars", 500))
        min_h4 = int(resolved.get("min_context_bars", 500))
        rates_m15 = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M15, 0, min_m15)
        rates_h4 = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_H4, 0, min_h4)
        if rates_m15 is None or len(rates_m15) < min_m15:
            return CheckOutcome(FAIL, f"insufficient M15 bars for {symbol}", {"last_error": mt5.last_error()})
        if rates_h4 is None or len(rates_h4) < min_h4:
            return CheckOutcome(FAIL, f"insufficient H4 bars for {symbol}", {"last_error": mt5.last_error()})
        spread = int(getattr(symbol_info, "spread", 0))
        max_spread = int(resolved.get("max_spread_points", 100))
        if spread > max_spread:
            return CheckOutcome(FAIL, f"spread {spread} points exceeds max {max_spread}", {"spread": spread})
        latest_m15 = datetime.fromtimestamp(int(rates_m15[-1]["time"]), tz=timezone.utc).isoformat()
        latest_h4 = datetime.fromtimestamp(int(rates_h4[-1]["time"]), tz=timezone.utc).isoformat()
        evidence = {
            "terminal": {
                "company": getattr(terminal, "company", None),
                "name": getattr(terminal, "name", None),
                "build": getattr(terminal, "build", None),
                "path": getattr(terminal, "path", None),
            },
            "account": {
                "login": getattr(account, "login", None),
                "server": getattr(account, "server", None),
                "trade_mode": getattr(account, "trade_mode", None),
                "currency": getattr(account, "currency", None),
                "equity": getattr(account, "equity", None),
            },
            "symbol": {
                "name": getattr(symbol_info, "name", symbol),
                "digits": getattr(symbol_info, "digits", None),
                "point": getattr(symbol_info, "point", None),
                "spread": spread,
                "volume_min": getattr(symbol_info, "volume_min", None),
                "volume_step": getattr(symbol_info, "volume_step", None),
                "trade_stops_level": getattr(symbol_info, "trade_stops_level", None),
            },
            "bars": {"m15": len(rates_m15), "h4": len(rates_h4)},
            "latest": {"m15": latest_m15, "h4": latest_h4},
        }
        return CheckOutcome(PASS, f"MT5 reachable; {symbol} M15/H4 bars available; demo account active", evidence)
    finally:
        if initialized:
            try:
                mt5.shutdown()
            except Exception:
                pass


def check_human_review(ctx: CheckContext, spec: dict[str, Any]) -> CheckOutcome:
    return CheckOutcome(
        HUMAN_JUDGMENT,
        "human sign-off is required; the validator cannot certify judgment",
        {"label": spec.get("human_label") or "HUMAN_JUDGMENT"},
    )


CHECKS: dict[str, Callable[[CheckContext, dict[str, Any]], CheckOutcome]] = {
    "manifest_integrity": check_manifest_integrity,
    "market_scope": check_market_scope,
    "risk_limits": check_risk_limits,
    "source_registry": check_source_registry,
    "source_legal": check_source_legal,
    "source_point_in_time": check_source_point_in_time,
    "source_coverage": check_source_coverage,
    "fundamental_weights": check_fundamental_weights,
    "fundamental_point_in_time": check_fundamental_point_in_time,
    "central_bank_policy": check_central_bank_policy,
    "technical_timeframes": check_technical_timeframes,
    "technical_indicators": check_technical_indicators,
    "fusion_policy": check_fusion_policy,
    "overseer_policy": check_overseer_policy,
    "exit_precedence": check_exit_precedence,
    "storage_sqlite": check_storage_sqlite,
    "storage_analytics": check_storage_analytics,
    "storage_schema": check_storage_schema,
    "backtest_assumptions": check_backtest_assumptions,
    "promotion_gates": check_promotion_gates,
    "dashboard_boundary": check_dashboard_boundary,
    "runtime_boundaries": check_runtime_boundaries,
    "yaml_file": check_yaml_file,
    "calibration_artifact": check_calibration_artifact,
    "http_csv": check_http_csv,
    "http_xlsx": check_http_xlsx,
    "http_zip": check_http_zip,
    "http_html": check_http_html,
    "manual_xlsx": check_manual_xlsx,
    "mt5_preflight": check_mt5_preflight,
    "human_review": check_human_review,
}
