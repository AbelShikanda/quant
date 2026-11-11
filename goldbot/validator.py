"""Headless validator orchestration and report writers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .checks import CHECKS, CheckContext
from .manifest import as_dict, as_list, load_manifest, manifest_hash
from .models import (
    FAIL,
    HUMAN_JUDGMENT,
    MECHANICAL,
    NOT_VALIDATABLE,
    PASS,
    CheckOutcome,
    ScopeReport,
    ValidationResult,
)


def _project_root_for(manifest_path: Path) -> Path:
    resolved = manifest_path.resolve()
    if resolved.parent.name == "config":
        return resolved.parent.parent
    return resolved.parent


def _result_for_item(
    item: dict[str, Any],
    outcome: CheckOutcome,
) -> ValidationResult:
    validation_class = str(item.get("validation_class", MECHANICAL))
    if validation_class in {HUMAN_JUDGMENT, NOT_VALIDATABLE} and outcome.status == PASS:
        outcome = CheckOutcome(
            HUMAN_JUDGMENT,
            "mechanical proxy passed; content remains human judgment",
            outcome.evidence,
        )
    return ValidationResult(
        item_id=str(item.get("id", "<unknown>")),
        category=str(item.get("category", "uncategorized")),
        what=str(item.get("what", "")),
        where=str(item.get("where", "")),
        validation_class=validation_class,
        required_for_m1=bool(item.get("required_for_m1", True)),
        runtime_only=bool(item.get("runtime_only", False)),
        status=outcome.status,
        detail=outcome.detail,
        evidence=outcome.evidence,
    )


def validate_manifest(
    manifest_path: str | Path,
    *,
    offline: bool = False,
    no_mt5: bool = False,
    timeout: float = 30.0,
    project_root: str | Path | None = None,
) -> ScopeReport:
    """Run every validation item in a manifest and return a report.

    The function must never raise because of a single failed check. Each item is
    isolated; an unexpected exception becomes a FAIL with the exception type and
    message in the evidence.
    """
    path = Path(manifest_path)
    manifest = load_manifest(path)
    root = Path(project_root) if project_root is not None else _project_root_for(path)
    digest = manifest_hash(manifest)
    ctx = CheckContext(
        manifest=manifest,
        manifest_path=path,
        project_root=root,
        manifest_hash=digest,
        offline=offline,
        no_mt5=no_mt5,
        http_timeout=timeout,
    )

    results: list[ValidationResult] = []
    for raw_item in as_list(manifest.get("validation_items")):
        if not isinstance(raw_item, dict):
            results.append(
                ValidationResult(
                    item_id="<non-mapping>",
                    category="manifest",
                    what="validation_items entry",
                    where=str(path),
                    validation_class=MECHANICAL,
                    required_for_m1=True,
                    runtime_only=False,
                    status=FAIL,
                    detail="validation_items contains a non-mapping entry",
                    evidence={},
                )
            )
            continue
        item = raw_item
        validation_class = str(item.get("validation_class", MECHANICAL))
        check = as_dict(item.get("check"))
        if not check:
            if validation_class in {HUMAN_JUDGMENT, NOT_VALIDATABLE}:
                outcome = CheckOutcome(
                    HUMAN_JUDGMENT,
                    str(item.get("human_label") or "human judgment required"),
                    {"label": item.get("human_label")},
                )
            else:
                outcome = CheckOutcome(FAIL, "mechanical item has no check block")
        else:
            kind = str(check.get("kind", ""))
            check_fn = CHECKS.get(kind)
            if check_fn is None:
                outcome = CheckOutcome(FAIL, f"unknown check kind: {kind!r}", {"kind": kind})
            else:
                try:
                    outcome = check_fn(ctx, check)
                except Exception as exc:  # noqa: BLE001 - isolation is intentional
                    outcome = CheckOutcome(
                        FAIL,
                        f"check crashed: {type(exc).__name__}: {exc}",
                        {"kind": kind, "exception": type(exc).__name__},
                    )
        results.append(_result_for_item(item, outcome))

    return ScopeReport.build(
        scope_id=str(manifest.get("scope_id", "<unknown>")),
        manifest_path=str(path),
        manifest_hash=digest,
        results=results,
        offline=offline,
        no_mt5=no_mt5,
    )


def write_json_report(report: ScopeReport, path: str | Path) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report.to_dict(), indent=2, ensure_ascii=False, default=str) + "\n",
        encoding="utf-8",
    )


def _status_badge(status: str) -> str:
    return {
        PASS: "PASS",
        FAIL: "FAIL",
        HUMAN_JUDGMENT: "HUMAN_JUDGMENT",
        "SKIP": "SKIP",
        "BLOCKED": "BLOCKED",
        "PARTIAL": "PARTIAL",
    }.get(status, status)


def write_markdown_report(report: ScopeReport, path: str | Path) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    lines.append(f"# Validation report: `{report.scope_id}`")
    lines.append("")
    lines.append(f"- Manifest: `{report.manifest_path}`")
    lines.append(f"- Manifest SHA-256: `{report.manifest_hash}`")
    lines.append(f"- Generated (UTC): `{report.generated_at}`")
    lines.append(f"- Offline mode: `{report.offline}`")
    lines.append(f"- No-MT5 mode: `{report.no_mt5}`")
    lines.append(f"- **Overall: {_status_badge(report.overall_status)}**")
    lines.append(f"- M1 design status: {_status_badge(report.m1_status)}")
    lines.append(f"- Runtime status: {_status_badge(report.runtime_status)}")
    lines.append("")
    lines.append("## Counts")
    lines.append("")
    lines.append("| Status | Count |")
    lines.append("|---|---:|")
    for status in (PASS, FAIL, HUMAN_JUDGMENT, "SKIP", "BLOCKED"):
        lines.append(f"| {status} | {report.counts.get(status, 0)} |")
    lines.append("")
    if report.required_failures:
        lines.append("## Required M1 failures")
        lines.append("")
        for result in report.required_failures:
            lines.append(f"- **{result.item_id}** — {result.detail}")
        lines.append("")
    if report.runtime_blockers:
        lines.append("## Runtime blockers")
        lines.append("")
        for result in report.runtime_blockers:
            lines.append(f"- **{result.item_id}** — {result.detail}")
        lines.append("")
    if report.human_judgment_items:
        lines.append("## Human judgment required")
        lines.append("")
        for result in report.human_judgment_items:
            lines.append(f"- **{result.item_id}** — {result.detail}")
            label = result.evidence.get("label")
            if label:
                lines.append(f"  - Label: {label}")
        lines.append("")
    if report.skipped_checks:
        lines.append("## Skipped checks")
        lines.append("")
        for result in report.skipped_checks:
            lines.append(f"- **{result.item_id}** — {result.detail}")
        lines.append("")
    lines.append("## All items")
    lines.append("")
    lines.append("| Item | Class | Required M1 | Runtime only | Status | Detail |")
    lines.append("|---|---|---|---|---|---|")
    for result in report.results:
        detail = result.detail.replace("|", "\\|")
        lines.append(
            f"| `{result.item_id}` | {result.validation_class} | "
            f"{result.required_for_m1} | {result.runtime_only} | "
            f"{_status_badge(result.status)} | {detail} |"
        )
    lines.append("")
    lines.append("## Evidence")
    lines.append("")
    for result in report.results:
        if not result.evidence:
            continue
        lines.append(f"### `{result.item_id}`")
        lines.append("")
        lines.append("```json")
        lines.append(json.dumps(result.evidence, indent=2, ensure_ascii=False, default=str))
        lines.append("```")
        lines.append("")
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")
