from __future__ import annotations

import json
import socket
from datetime import datetime, timezone
from pathlib import Path

import pytest

from goldbot.contracts import (
    FundamentalView,
    FusionView,
    OverseerDecision,
    StrategyDecision,
    TechnicalView,
)
from goldbot.ledger import Ledger, LedgerError, LedgerLockError

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "config" / "storage.schema.sql"


def _decision() -> StrategyDecision:
    as_of = datetime(2026, 1, 19, 15, 15, tzinfo=timezone.utc)
    return StrategyDecision(
        decision_id="gold.v1:2026-01-19T15:15:00+00:00",
        as_of_utc=as_of,
        decision_type="ENTER_LONG",
        side="LONG",
        confidence=0.8,
        combined_score=0.45,
        fundamental=FundamentalView(
            as_of_utc=as_of,
            score=0.5,
            components={},
            diagnostics={},
            source_versions={"DFII10": "vintage-1"},
        ),
        technical=TechnicalView(
            as_of_utc=as_of,
            score=0.4,
            components={},
            indicators={"atr": 1.25},
            entry_triggers={},
            is_valid=True,
        ),
        fusion=FusionView(
            as_of_utc=as_of,
            combined_score=0.45,
            fundamental_contribution=0.3,
            technical_contribution=0.15,
            entry_candidate=True,
            conflict=False,
        ),
        overseer=OverseerDecision(
            as_of_utc=as_of,
            decision="ENTER_LONG",
            side="LONG",
            confidence=0.8,
            verdict="approved",
        ),
        exit_decision=None,
        position_size=0.01,
        stop_loss=4100.0,
        take_profit=4130.0,
        reasons=["entry approved", "sizing: minimum lot"],
    )


def test_ledger_is_wal_single_writer_and_round_trips_records(tmp_path: Path) -> None:
    path = tmp_path / "ledger.sqlite"
    with Ledger(path, schema_path=SCHEMA) as ledger:
        mode = ledger.connection.execute("PRAGMA journal_mode").fetchone()[0]
        assert str(mode).lower() == "wal"
        assert ledger.counts()["decisions"] == 0

        started = datetime(2026, 1, 19, 15, 15, tzinfo=timezone.utc)
        ledger.start_run(
            run_id="run-1",
            scope_id="gold.v1",
            manifest_hash="abc",
            started_at_utc=started,
        )
        decision = _decision()
        ledger.record_decision(decision, scope_id="gold.v1")
        # Re-recording the same decision must be idempotent.
        ledger.record_decision(decision, scope_id="gold.v1")
        ledger.record_order(
            order_id="ord-1",
            decision_id=decision.decision_id,
            submitted_at_utc=started,
            symbol="XAUUSD",
            side="LONG",
            order_type="MARKET",
            volume=0.01,
            price=4114.0,
            stop_loss=4100.0,
            take_profit=4130.0,
            state="PAPER_FILLED",
            idempotency_key="ord-1",
            raw_request={"mode": "paper_replay"},
            raw_response={"state": "PAPER_FILLED"},
        )
        ledger.record_fill(
            fill_id="fill-1",
            order_id="ord-1",
            filled_at_utc=started,
            symbol="XAUUSD",
            side="LONG",
            volume=0.01,
            price=4114.2,
            commission=0.0,
            raw_deal={"mode": "paper_replay"},
        )
        ledger.record_position(
            position_id="pos-1",
            symbol="XAUUSD",
            side="LONG",
            volume=0.01,
            open_price=4114.2,
            open_time_utc=started,
            stop_loss=4100.0,
            take_profit=4130.0,
            close_price=4116.9,
            close_time_utc=started,
            close_reason="profit_take",
            realized_pnl=2.7,
            mae=-0.5,
            mfe=3.0,
            state="PAPER_CLOSED",
        )
        ledger.log_event(
            level="INFO",
            component="test",
            event_type="smoke",
            message="hello",
            correlation_id="run-1",
        )
        ledger.finish_run(
            run_id="run-1",
            finished_at_utc=started,
            summary={"final_equity": 10002.7},
        )

        counts = ledger.counts()
        assert counts["decisions"] == 1
        assert counts["orders"] == 1
        assert counts["fills"] == 1
        assert counts["positions"] == 1
        assert counts["logs"] == 1

        decisions = ledger.recent_decisions()
        assert len(decisions) == 1
        assert decisions[0]["decision_type"] == "ENTER_LONG"
        assert decisions[0]["fundamental"]["source_versions"]["DFII10"] == "vintage-1"

        orders = ledger.recent_orders()
        assert orders[0]["order_id"] == "ord-1"
        assert orders[0]["broker_order_ticket"] is None
        assert orders[0]["state"] == "PAPER_FILLED"

        fills = ledger.recent_fills()
        assert fills[0]["fill_id"] == "fill-1"
        assert fills[0]["price"] == 4114.2

        trades = ledger.closed_trades()
        assert len(trades) == 1
        assert trades[0]["trade_id"] == "pos-1"
        assert trades[0]["pnl"] == 2.7

        run = ledger.latest_run()
        assert run is not None
        assert run["run_id"] == "run-1"
        assert "10002.7" in run["summary_json"]

        with pytest.raises(LedgerError, match="separate ledger for each run"):
            ledger.start_run(
                run_id="run-2",
                scope_id="gold.v1",
                manifest_hash="abc",
                started_at_utc=started,
            )

        with pytest.raises(LedgerError, match="separate ledger"):
            ledger.start_run(
                run_id="run-2",
                scope_id="gold.v1",
                manifest_hash="different-manifest",
                started_at_utc=started,
            )

        ledger.start_run(
            run_id="run-2",
            scope_id="gold.v1",
            manifest_hash="abc",
            started_at_utc=started,
            allow_multiple_runs=True,
        )

        with pytest.raises(LedgerLockError):
            Ledger(path, schema_path=SCHEMA)

    # Closing the first ledger releases the writer lock.
    with Ledger(path, schema_path=SCHEMA) as reopened:
        assert reopened.counts()["decisions"] == 1


def test_stale_lock_file_owned_by_dead_pid_is_reclaimed(tmp_path: Path) -> None:
    path = tmp_path / "stale.sqlite"
    lock_path = Path(str(path) + ".writer.lock")
    lock_path.write_text(
        json.dumps(
            {
                "pid": 999999,
                "host": socket.gethostname(),
                "created_at_utc": "2000-01-01T00:00:00Z",
                "ledger": str(path),
            }
        ),
        encoding="utf-8",
    )

    with Ledger(path, schema_path=SCHEMA) as ledger:
        assert ledger is not None

    assert not lock_path.exists()
