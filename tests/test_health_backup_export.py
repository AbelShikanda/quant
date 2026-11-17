"""Unit tests for the health probe, ledger backup, and parquet export."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from goldbot.backup import run_backup
from goldbot.contracts import FundamentalView, FusionView, OverseerDecision, StrategyDecision, TechnicalView
from goldbot.health import run_health
from goldbot.ledger import Ledger
from goldbot.parquet_export import run_export


def _ledger_with_decision(path: Path) -> None:
    as_of = datetime.now(timezone.utc) - timedelta(minutes=10)
    decision = StrategyDecision(
        decision_id="d-1",
        as_of_utc=as_of,
        decision_type="ENTER_LONG",
        side="LONG",
        confidence=0.8,
        combined_score=0.7,
        fundamental=FundamentalView(
            as_of_utc=as_of, score=0.6, components={}, diagnostics={},
            missing_inputs=[], stale_inputs=[], is_valid=True, reasons=[], source_versions={},
        ),
        technical=TechnicalView(
            as_of_utc=as_of, score=0.8, components={}, indicators={"atr": 10.0},
            entry_triggers={"hammer": True}, is_valid=True, reasons=[],
            bars_used=600, context_bars_used=100,
        ),
        fusion=FusionView(
            as_of_utc=as_of, combined_score=0.7,
            fundamental_contribution=0.3, technical_contribution=0.4,
            entry_candidate=True, conflict=False, reasons=[],
        ),
        overseer=OverseerDecision(
            as_of_utc=as_of, decision="ENTER_LONG", side="LONG", confidence=0.8,
            verdict="approved", vetoes=[], rationale="test", source_views={},
        ),
        exit_decision=None,
        position_size=0.01,
        stop_loss=100.0,
        take_profit=200.0,
        reasons=["test"],
    )
    with Ledger(path) as ledger:
        ledger.start_run(
            run_id="run-1",
            scope_id="gold.v1",
            manifest_hash="m" * 64,
            started_at_utc=as_of,
            summary={"mode": "unit_test"},
            allow_multiple_runs=True,
        )
        ledger.record_decision(
            decision,
            scope_id="gold.v1",
            symbol="XAUUSD",
            execution_bar="M15",
            context_bar="H4",
            market={"mode": "unit_test"},
        )


def test_health_healthy_when_no_halt_and_ledger_readable(tmp_path):
    ledger = tmp_path / "exec.sqlite"
    _ledger_with_decision(ledger)
    report = run_health(ledger_path=ledger, skip_mt5=True)
    assert report.verdict == "HEALTHY"
    assert report.ok
    assert report.halt_flag is False
    assert report.last_activity_utc is not None


def test_health_halted_when_halt_flag_present(tmp_path):
    ledger = tmp_path / "exec.sqlite"
    _ledger_with_decision(ledger)
    (tmp_path / "kill-switch.halt").write_text("armed", encoding="utf-8")
    report = run_health(ledger_path=ledger, skip_mt5=True)
    assert report.verdict == "HALTED"
    assert not report.ok


def test_health_reports_missing_ledger(tmp_path):
    report = run_health(ledger_path=tmp_path / "nope.sqlite", skip_mt5=True)
    assert report.ledger_exists is False
    assert report.verdict == "HEALTHY"  # nothing to halt; ledger simply absent


def test_backup_integrity_and_roundtrip(tmp_path):
    ledger = tmp_path / "exec.sqlite"
    _ledger_with_decision(ledger)
    result = run_backup(ledger_path=ledger, out_dir=tmp_path / "backups", keep=5)
    assert result.ok
    assert result.integrity == "ok"
    copy = Path(result.destination)
    assert copy.exists()
    connection = sqlite3.connect(copy)
    try:
        rows = connection.execute("SELECT COUNT(*) FROM decisions").fetchone()[0]
    finally:
        connection.close()
    assert rows == 1


def test_export_parquet_roundtrip(tmp_path):
    pytest.importorskip("duckdb")
    import duckdb

    ledger = tmp_path / "exec.sqlite"
    _ledger_with_decision(ledger)
    result = run_export(ledger_path=ledger, out_dir=tmp_path / "parquet")
    assert result.ok
    assert result.written.get("decisions") == 1
    rows = duckdb.execute(
        f"SELECT COUNT(*) FROM read_parquet('{result.out_dir.replace(chr(92), '/')}/decisions.parquet')"
    ).fetchone()[0]
    assert rows == 1


def test_export_parquet_fails_cleanly_on_missing_ledger(tmp_path):
    result = run_export(ledger_path=tmp_path / "nope.sqlite", out_dir=tmp_path / "parquet")
    assert not result.ok
