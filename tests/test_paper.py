from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from goldbot.cli import main
from goldbot.contracts import Bar, PositionState
from goldbot.data import (
    BarSeries,
    aggregate_bars,
    make_synthetic_bars,
    make_synthetic_macro_store,
)
from goldbot.ledger import Ledger
from goldbot.manifest import load_manifest
from goldbot.paper import PaperRuntime, replay_times_from_bars

ROOT = Path(__file__).resolve().parents[1]
GOLD = ROOT / "config" / "scope.gold.v1.yaml"


def _inputs(count: int = 1600):
    bars = make_synthetic_bars(count=count)
    h4 = aggregate_bars(bars, "H4", 4)
    as_of = bars[-1].time_utc + timedelta(minutes=15)
    macro = make_synthetic_macro_store(as_of_utc=as_of, days=520)
    return BarSeries(bars, "M15"), BarSeries(h4, "H4"), macro


def test_replay_times_from_bars_uses_last_closed_bars() -> None:
    execution, _, _ = _inputs(100)
    last_only = replay_times_from_bars(execution, replay_bars=0)
    assert last_only == [execution.bars[-1].time_utc + timedelta(minutes=15)]

    last_five = replay_times_from_bars(execution, replay_bars=5)
    assert len(last_five) == 5
    assert last_five == sorted(last_five)
    assert last_five[-1] == last_only[0]


def test_paper_replay_persists_orders_fills_and_positions(tmp_path: Path) -> None:
    manifest = load_manifest(GOLD)
    execution, context, macro = _inputs(1600)
    ledger_path = tmp_path / "paper.ledger.sqlite"
    with Ledger(ledger_path) as ledger:
        runtime = PaperRuntime(
            manifest=manifest,
            ledger=ledger,
            execution_bars=execution,
            context_bars=context,
            macro_store=macro,
            initial_equity=10000.0,
        )
        times = replay_times_from_bars(execution, replay_bars=200)
        result = runtime.run_replay(times)

        assert result.mode == "paper_replay"
        assert len(result.decisions) >= 200
        assert result.orders
        assert len(result.orders) == len(result.fills)
        assert result.trades
        assert result.metrics["trade_count"] == len(result.trades)
        assert result.metrics["entry_decisions"] > 0
        assert result.metrics["exit_decisions"] > 0
        assert result.final_equity != result.initial_equity
        assert result.ledger_path == str(ledger_path)

        payload = result.to_dict()
        assert payload["mode"] == "paper_replay"
        assert payload["orders"]
        assert payload["fills"]
        assert payload["positions"]
        assert "fill_model" in payload["data_quality"]

        orders = ledger.recent_orders()
        assert len(orders) == len(result.orders)
        assert all(order["state"] == "PAPER_FILLED" for order in orders)
        assert all(order["broker_order_ticket"] is None for order in orders)

        counts = ledger.counts()
        assert counts["decisions"] == len(result.decisions)
        assert counts["orders"] == len(result.orders)
        assert counts["fills"] == len(result.fills)
        assert counts["positions"] == len(result.trades)
        assert len(ledger.closed_trades()) == len(result.trades)

    # The writer lock must be released when the ledger context exits.
    with Ledger(ledger_path) as reopened:
        assert reopened.counts()["orders"] == len(result.orders)


def test_paper_replay_force_closes_an_open_position_at_the_end(tmp_path: Path) -> None:
    manifest = load_manifest(GOLD)
    execution, context, macro = _inputs(1600)
    all_times = replay_times_from_bars(execution, replay_bars=200)

    with Ledger(tmp_path / "discover.ledger.sqlite") as ledger:
        discovered = PaperRuntime(
            manifest=manifest,
            ledger=ledger,
            execution_bars=execution,
            context_bars=context,
            macro_store=macro,
            initial_equity=10000.0,
        ).run_replay(all_times)
    entry = next(
        decision for decision in discovered.decisions if decision.decision_type == "ENTER_LONG"
    )
    open_times = [value for value in all_times if value <= entry.as_of_utc]

    with Ledger(tmp_path / "open.ledger.sqlite") as ledger:
        result = PaperRuntime(
            manifest=manifest,
            ledger=ledger,
            execution_bars=execution,
            context_bars=context,
            macro_store=macro,
            initial_equity=10000.0,
        ).run_replay(open_times)
        assert result.trades
        assert result.trades[-1].exit_reason == "end_of_replay"
        assert len(result.orders) == len(result.fills) == 2
        assert any(
            decision.decision_type == "EXIT"
            and decision.overseer.verdict == "end_of_replay"
            for decision in result.decisions
        )
        positions = ledger.recent_positions()
        assert positions[-1]["state"] == "PAPER_CLOSED"
        assert positions[-1]["close_reason"] == "end_of_replay"


def test_protective_exit_prefers_stop_when_both_are_touched() -> None:
    position = PositionState(
        position_id="pos-test",
        side="LONG",
        volume=0.01,
        open_price=100.0,
        open_time_utc=datetime(2026, 1, 1, tzinfo=timezone.utc),
        stop_loss=95.0,
        take_profit=110.0,
    )
    bar = Bar(
        time_utc=datetime(2026, 1, 1, 0, 15, tzinfo=timezone.utc),
        open=99.0,
        high=111.0,
        low=94.0,
        close=100.0,
        spread_points=10.0,
    )
    price, reason = PaperRuntime._protective_exit(None, position, bar)  # type: ignore[arg-type]
    assert price == 95.0
    assert reason == "hard_or_trailing_stop"


def test_paper_cli_writes_report_and_dashboard(tmp_path: Path) -> None:
    report = tmp_path / "paper.json"
    dashboard = tmp_path / "paper.html"
    ledger_path = tmp_path / "paper.ledger.sqlite"
    code = main(
        [
            "paper",
            "--manifest",
            str(GOLD),
            "--bars",
            "1600",
            "--replay-bars",
            "200",
            "--equity",
            "10000",
            "--ledger",
            str(ledger_path),
            "--report",
            str(report),
            "--dashboard",
            str(dashboard),
            "--json",
        ]
    )
    assert code == 0
    assert report.exists()
    assert dashboard.exists()
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["mode"] == "paper_replay"
    assert payload["orders"]
    assert Path(payload["ledger_path"]).resolve() == ledger_path.resolve()
    html = dashboard.read_text(encoding="utf-8")
    assert "Orders / paper executions" in html
    assert "paper_replay" in html
    assert "no broker orders were sent" in html
    with Ledger(ledger_path) as ledger:
        assert ledger.counts()["orders"] == len(payload["orders"])
