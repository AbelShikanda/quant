from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from goldbot.checks import CheckContext, check_http_csv, check_manual_xlsx
from goldbot.cli import main
from goldbot.manifest import load_manifest, manifest_hash
from goldbot.models import FAIL, HUMAN_JUDGMENT, PASS
from goldbot.validator import validate_manifest, write_markdown_report


ROOT = Path(__file__).resolve().parents[1]
GOLD = ROOT / "config" / "scope.gold.v1.yaml"
BROKEN = ROOT / "config" / "scope.broken.sec_edgar.yaml"


def test_gold_scope_offline_passes_design_checks() -> None:
    report = validate_manifest(GOLD, offline=True, no_mt5=True)
    assert report.overall_status == PASS
    assert report.m1_status == PASS
    assert report.required_failures == []
    assert report.counts[FAIL] == 0
    assert report.counts[HUMAN_JUDGMENT] >= 2


def test_broken_scope_fails_for_specific_reasons() -> None:
    report = validate_manifest(BROKEN, offline=True, no_mt5=True)
    assert report.overall_status == FAIL
    failure_ids = {result.item_id for result in report.required_failures}
    expected = {
        "manifest.integrity",
        "market.scope",
        "risk.limits",
        "source.registry",
        "fundamental.central_bank_policy",
        "technical.timeframes",
        "technical.indicators",
        "fusion.policy",
        "overseer.policy",
        "overseer.calibration",
        "exits.precedence",
        "storage.sqlite",
        "storage.analytics",
        "storage.schema",
        "backtest.assumptions",
        "analytics.baselines",
        "analytics.dashboard_boundary",
        "runtime.boundaries",
        "promotion.gates",
    }
    assert expected <= failure_ids
    # The validator must report, not crash, on unsupported source types.
    source_registry = next(r for r in report.results if r.item_id == "source.registry")
    assert "sec_edgar" in source_registry.detail
    storage_schema = next(r for r in report.results if r.item_id == "storage.schema")
    assert "not found" in storage_schema.detail


def test_central_bank_missing_file_is_human_judgment() -> None:
    manifest = load_manifest(GOLD)
    ctx = CheckContext(
        manifest=manifest,
        manifest_path=GOLD,
        project_root=ROOT,
        manifest_hash=manifest_hash(manifest),
        offline=True,
        no_mt5=True,
    )
    outcome = check_manual_xlsx(ctx, {"source_id": "wgc_central_bank_manual"})
    assert outcome.status == HUMAN_JUDGMENT
    assert "absent" in outcome.detail
    assert outcome.evidence["label"] == (
        "derived from human-downloaded file; weight in entry score = 0; "
        "NOT consumed by the overseer (§9) in v1."
    )


def test_runtime_blocker_yields_partial_and_exit_code(tmp_path: Path) -> None:
    manifest = {
        "manifest_version": 1,
        "scope_id": "test.partial",
        "validation_items": [
            {
                "id": "runtime.missing",
                "category": "runtime",
                "what": "deliberate runtime blocker",
                "where": "test",
                "validation_class": "MECHANICAL",
                "required_for_m1": False,
                "runtime_only": True,
                "check": {"kind": "yaml_file", "path": "missing.yaml"},
            }
        ],
    }
    path = tmp_path / "scope.yaml"
    path.write_text(yaml.safe_dump(manifest), encoding="utf-8")
    report = validate_manifest(path, offline=True, no_mt5=True, project_root=tmp_path)
    assert report.overall_status == "PARTIAL"
    assert report.m1_status == PASS
    assert report.runtime_status == "BLOCKED"
    assert report.runtime_blockers[0].item_id == "runtime.missing"
    assert main(["validate", "--manifest", str(path), "--offline", "--no-mt5"]) == 2
    assert main(["validate", "--manifest", str(path), "--offline", "--no-mt5", "--strict"]) == 1


def test_unknown_check_kind_fails_without_crash(tmp_path: Path) -> None:
    manifest = {
        "manifest_version": 1,
        "scope_id": "test.unknown",
        "validation_items": [
            {
                "id": "unknown.check",
                "category": "test",
                "what": "unknown check",
                "where": "test",
                "validation_class": "MECHANICAL",
                "required_for_m1": True,
                "check": {"kind": "does_not_exist"},
            }
        ],
    }
    path = tmp_path / "scope.yaml"
    path.write_text(yaml.safe_dump(manifest), encoding="utf-8")
    report = validate_manifest(path, offline=True, no_mt5=True, project_root=tmp_path)
    assert report.overall_status == FAIL
    assert report.required_failures[0].item_id == "unknown.check"
    assert "unknown check kind" in report.required_failures[0].detail


def test_markdown_report_written(tmp_path: Path) -> None:
    report = validate_manifest(GOLD, offline=True, no_mt5=True)
    output = tmp_path / "report.md"
    write_markdown_report(report, output)
    text = output.read_text(encoding="utf-8")
    assert "Overall: PASS" in text
    assert "Human judgment required" in text
    assert "source.wgc_central_bank_manual" in text


def test_http_csv_check_with_fake_response(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    manifest = {"manifest_version": 1, "scope_id": "test.http"}
    ctx = CheckContext(
        manifest=manifest,
        manifest_path=tmp_path / "scope.yaml",
        project_root=tmp_path,
        manifest_hash="test",
    )
    fake = SimpleNamespace(
        status_code=200,
        text="observation_date,DFII10\n2026-11-10,2.10\n2026-11-09,2.05\n",
        content=b"observation_date,DFII10\n2026-11-10,2.10\n2026-11-09,2.05\n",
        headers={"content-type": "text/csv"},
    )
    monkeypatch.setattr(ctx, "http_get", lambda url, timeout=None: fake)
    outcome = check_http_csv(
        ctx,
        {
            "url": "https://example.invalid/fred.csv",
            "required_columns": ["observation_date", "DFII10"],
            "min_rows": 2,
            "max_age_days": 10,
            "date_column": "observation_date",
            "date_formats": ["%Y-%m-%d"],
        },
    )
    assert outcome.status == PASS
    assert outcome.evidence["latest_date"] == "2026-11-10"


def test_http_csv_stale_data_fails(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    manifest = {"manifest_version": 1, "scope_id": "test.http"}
    ctx = CheckContext(
        manifest=manifest,
        manifest_path=tmp_path / "scope.yaml",
        project_root=tmp_path,
        manifest_hash="test",
    )
    fake = SimpleNamespace(
        status_code=200,
        text="observation_date,DFII10\n2020-01-02,2.10\n",
        content=b"observation_date,DFII10\n2020-01-02,2.10\n",
        headers={"content-type": "text/csv"},
    )
    monkeypatch.setattr(ctx, "http_get", lambda url, timeout=None: fake)
    outcome = check_http_csv(
        ctx,
        {
            "url": "https://example.invalid/fred.csv",
            "required_columns": ["observation_date", "DFII10"],
            "min_rows": 1,
            "max_age_days": 10,
            "date_column": "observation_date",
            "date_formats": ["%Y-%m-%d"],
        },
    )
    assert outcome.status == FAIL
    assert "days old" in outcome.detail


def test_report_json_is_serializable(tmp_path: Path) -> None:
    report = validate_manifest(GOLD, offline=True, no_mt5=True)
    payload = json.dumps(report.to_dict(), default=str)
    assert '"overall_status": "PASS"' in payload
    assert '"manifest_hash"' in payload
