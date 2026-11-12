from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path

from goldbot.contracts import Bar, MacroObservation
from goldbot.data import BarSeries, DuckDBBarStore, PointInTimeStore, SQLiteStore


def _obs(value: float, knowledge: datetime, observation_date: date) -> MacroObservation:
    return MacroObservation(
        series_id="DFII10",
        knowledge_time_utc=knowledge,
        observation_date=observation_date,
        value=value,
        source_id="test",
        vintage="v1",
    )


def test_revised_macro_series_uses_only_known_vintage() -> None:
    store = PointInTimeStore()
    store.add(_obs(1.0, datetime(2026, 1, 2, tzinfo=timezone.utc), date(2026, 1, 1)))
    store.add(_obs(2.0, datetime(2026, 2, 1, tzinfo=timezone.utc), date(2026, 1, 1)))

    early = store.latest("DFII10", datetime(2026, 1, 15, tzinfo=timezone.utc))
    late = store.latest("DFII10", datetime(2026, 2, 2, tzinfo=timezone.utc))
    assert early is not None and early.value == 1.0
    assert late is not None and late.value == 2.0
    assert [item.value for item in store.series("DFII10", datetime(2026, 1, 15, tzinfo=timezone.utc))] == [1.0]
    assert [item.value for item in store.series("DFII10", datetime(2026, 2, 2, tzinfo=timezone.utc))] == [1.0, 2.0]


def test_sqlite_store_round_trip_and_as_of(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "goldbot.sqlite")
    try:
        store.write_macro_many(
            [
                _obs(1.0, datetime(2026, 1, 2, tzinfo=timezone.utc), date(2026, 1, 1)),
                _obs(2.0, datetime(2026, 2, 1, tzinfo=timezone.utc), date(2026, 1, 1)),
            ]
        )
        early = store.latest_macro("DFII10", datetime(2026, 1, 15, tzinfo=timezone.utc))
        late = store.latest_macro("DFII10", datetime(2026, 2, 2, tzinfo=timezone.utc))
        assert early is not None and early.value == 1.0
        assert late is not None and late.value == 2.0
        assert [item.value for item in store.read_macro_series("DFII10")] == [1.0, 2.0]
        journal_mode = store.connection.execute("PRAGMA journal_mode").fetchone()[0]
        assert str(journal_mode).lower() == "wal"
    finally:
        store.close()


def test_duckdb_bar_store_round_trip(tmp_path: Path) -> None:
    store = DuckDBBarStore(tmp_path / "bars.duckdb")
    try:
        bars = [
            Bar(
                time_utc=datetime(2026, 1, 1, tzinfo=timezone.utc),
                open=2000.0,
                high=2002.0,
                low=1999.0,
                close=2001.0,
                tick_volume=10,
                spread_points=31,
            ),
            Bar(
                time_utc=datetime(2026, 1, 1, 0, 15, tzinfo=timezone.utc),
                open=2001.0,
                high=2003.0,
                low=2000.0,
                close=2002.0,
                tick_volume=11,
                spread_points=30,
            ),
        ]
        store.write_bars("XAUUSD", "M15", bars)
        loaded = store.read_bars("XAUUSD", "M15")
        assert len(loaded) == 2
        assert loaded[0].close == 2001.0
        assert loaded[1].spread_points == 30.0
        store.export_parquet(tmp_path / "bars.parquet")
        assert (tmp_path / "bars.parquet").exists()
    finally:
        store.close()


def test_closed_window_excludes_forming_bar() -> None:
    bars = [
        Bar(
            time_utc=datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc),
            open=1.0,
            high=2.0,
            low=0.5,
            close=1.5,
        ),
        Bar(
            time_utc=datetime(2026, 1, 1, 0, 15, tzinfo=timezone.utc),
            open=1.5,
            high=3.0,
            low=1.0,
            close=2.5,
        ),
    ]
    series = BarSeries(bars)
    # At 00:15 the first M15 bar is closed, the second is still forming.
    closed = series.closed_window(datetime(2026, 1, 1, 0, 15, tzinfo=timezone.utc), 15)
    assert [bar.close for bar in closed] == [1.5]
