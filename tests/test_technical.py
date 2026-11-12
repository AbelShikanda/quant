from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
from pathlib import Path

from goldbot.contracts import Bar
from goldbot.data import BarSeries, aggregate_bars, make_synthetic_bars
from goldbot.manifest import load_manifest
from goldbot.technical import TechnicalEngine


ROOT = Path(__file__).resolve().parents[1]
GOLD = ROOT / "config" / "scope.gold.v1.yaml"


def test_technical_view_on_synthetic_bars() -> None:
    manifest = load_manifest(GOLD)
    m15 = make_synthetic_bars(count=1600)
    h4 = aggregate_bars(m15, "H4", 4)
    decision_time = m15[-1].time_utc + timedelta(minutes=15)
    view = TechnicalEngine(manifest).evaluate(BarSeries(m15), BarSeries(h4), decision_time)
    assert view.is_valid
    assert view.bars_used == 1600
    assert view.context_bars_used == 100
    assert -1.0 <= view.score <= 1.0
    assert set(view.entry_triggers) == {
        "h4_context",
        "m15_trigger",
        "atr_filter",
        "spread_filter",
        "session_filter",
        "no_news_blackout",
    }
    assert {"ema_fast", "ema_slow", "ema_trend", "rsi", "atr", "atr_pct"} <= set(view.indicators)


def test_technical_insufficient_bars_is_invalid() -> None:
    manifest = load_manifest(GOLD)
    m15 = make_synthetic_bars(count=10)
    h4 = aggregate_bars(m15, "H4", 4)
    view = TechnicalEngine(manifest).evaluate(
        BarSeries(m15), BarSeries(h4), m15[-1].time_utc + timedelta(minutes=15)
    )
    assert view.is_valid is False
    assert "insufficient" in view.reasons[0]


def test_forming_h4_bar_is_excluded() -> None:
    manifest = load_manifest(GOLD)
    m15 = make_synthetic_bars(count=1600)
    h4 = aggregate_bars(m15, "H4", 4)
    decision_time = m15[-1].time_utc + timedelta(minutes=15)
    forming = Bar(
        time_utc=decision_time,
        open=9999.0,
        high=10000.0,
        low=9998.0,
        close=9999.0,
        timeframe="H4",
    )
    view = TechnicalEngine(manifest).evaluate(
        BarSeries(m15), BarSeries(h4 + [forming]), decision_time
    )
    assert view.context_bars_used == 100
    assert view.indicators["h4_ema_fast"] < 5000.0


def test_spread_filter_reads_manifest_parameter() -> None:
    manifest = load_manifest(GOLD)
    m15 = make_synthetic_bars(count=1600, spread_points=31.0)
    h4 = aggregate_bars(m15, "H4", 4)
    decision_time = m15[-1].time_utc + timedelta(minutes=15)
    changed = copy.deepcopy(manifest)
    changed["strategy"]["technical"]["max_spread_points"] = 1.0
    view = TechnicalEngine(changed).evaluate(BarSeries(m15), BarSeries(h4), decision_time)
    assert view.entry_triggers["spread_filter"] is False


def test_session_filter_blocks_weekend_and_allows_weekday_session() -> None:
    manifest = load_manifest(GOLD)
    m15 = make_synthetic_bars(count=1600)
    h4 = aggregate_bars(m15, "H4", 4)
    changed = copy.deepcopy(manifest)
    changed["strategy"]["technical"]["session_filter"]["enabled"] = True
    engine = TechnicalEngine(changed)
    saturday = datetime(2026, 1, 17, 12, 0, tzinfo=timezone.utc)
    friday = datetime(2026, 1, 16, 12, 0, tzinfo=timezone.utc)
    weekend_view = engine.evaluate(BarSeries(m15), BarSeries(h4), saturday)
    weekday_view = engine.evaluate(BarSeries(m15), BarSeries(h4), friday)
    assert weekend_view.is_valid
    assert weekend_view.entry_triggers["session_filter"] is False
    assert weekday_view.is_valid
    assert weekday_view.entry_triggers["session_filter"] is True
