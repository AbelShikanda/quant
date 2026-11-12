from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from goldbot.backtest import BacktestRunner
from goldbot.contracts import Bar, MacroObservation, PositionState
from goldbot.data import BarSeries, PointInTimeStore
from goldbot.fundamental import FundamentalEngine
from goldbot.manifest import load_manifest


ROOT = Path(__file__).resolve().parents[1]
GOLD = ROOT / "config" / "scope.gold.v1.yaml"


def _runner() -> BacktestRunner:
    manifest = load_manifest(GOLD)
    return BacktestRunner(
        manifest=manifest,
        execution_bars=BarSeries([], "M15"),
        context_bars=BarSeries([], "H4"),
        macro_store=PointInTimeStore(),
    )


def test_gap_through_stop_fills_at_gap_open_not_stop() -> None:
    position = PositionState(
        position_id="gap-stop",
        side="LONG",
        volume=0.01,
        open_price=100.0,
        stop_loss=95.0,
    )
    bar = Bar(
        time_utc=datetime(2026, 1, 2, tzinfo=timezone.utc),
        open=90.0,
        high=92.0,
        low=89.0,
        close=91.0,
    )
    price, reason = _runner()._protective_exit(position, bar)
    assert price == 90.0
    assert reason == "hard_or_trailing_stop"


def test_gap_through_take_profit_fills_at_gap_open_not_target() -> None:
    position = PositionState(
        position_id="gap-target",
        side="LONG",
        volume=0.01,
        open_price=100.0,
        take_profit=105.0,
    )
    bar = Bar(
        time_utc=datetime(2026, 1, 2, tzinfo=timezone.utc),
        open=110.0,
        high=112.0,
        low=108.0,
        close=111.0,
    )
    price, reason = _runner()._protective_exit(position, bar)
    assert price == 110.0
    assert reason == "profit_take"


def test_missing_macro_data_is_reported_not_forward_filled() -> None:
    manifest = load_manifest(GOLD)
    view = FundamentalEngine(manifest).evaluate(
        PointInTimeStore(),
        datetime(2026, 1, 2, tzinfo=timezone.utc),
    )
    assert view.is_valid is False
    assert view.missing_inputs
    assert view.score == 0.0
    assert all(component.value is None for component in view.components.values())


def test_stale_macro_data_is_reported() -> None:
    manifest = load_manifest(GOLD)
    store = PointInTimeStore(
        [
            MacroObservation(
                series_id="DFII10",
                knowledge_time_utc=datetime(2020, 1, 2, tzinfo=timezone.utc),
                observation_date=datetime(2020, 1, 1, tzinfo=timezone.utc).date(),
                value=2.0,
                source_id="test",
            )
        ]
    )
    view = FundamentalEngine(manifest).evaluate(
        store,
        datetime(2026, 1, 2, tzinfo=timezone.utc),
    )
    assert "real_yield_10y" in view.stale_inputs
    assert view.is_valid is False
