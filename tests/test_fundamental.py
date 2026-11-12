from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Callable

from goldbot.contracts import MacroObservation
from goldbot.data import PointInTimeStore
from goldbot.fundamental import FundamentalEngine
from goldbot.manifest import load_manifest


ROOT = Path(__file__).resolve().parents[1]
GOLD = ROOT / "config" / "scope.gold.v1.yaml"


def _daily(
    store: PointInTimeStore,
    series_id: str,
    *,
    start: date,
    count: int,
    value: Callable[[int], float],
) -> None:
    for index in range(count):
        observation_date = start + timedelta(days=index)
        store.add(
            MacroObservation(
                series_id=series_id,
                knowledge_time_utc=datetime.combine(
                    observation_date + timedelta(days=1), time(0, 0), tzinfo=timezone.utc
                ),
                observation_date=observation_date,
                value=value(index),
                source_id=f"test.{series_id}",
                vintage="test",
            )
        )


def _weekly(
    store: PointInTimeStore,
    series_id: str,
    *,
    start: date,
    count: int,
    value: Callable[[int], float],
) -> None:
    for index in range(count):
        observation_date = start + timedelta(days=7 * index)
        store.add(
            MacroObservation(
                series_id=series_id,
                knowledge_time_utc=datetime.combine(
                    observation_date + timedelta(days=3), time(20, 30), tzinfo=timezone.utc
                ),
                observation_date=observation_date,
                value=value(index),
                source_id=f"test.{series_id}",
                vintage="test",
            )
        )


def _store_with_core_inputs(*, include_gld: bool = True, include_cot: bool = True) -> tuple[PointInTimeStore, datetime]:
    start = date(2025, 1, 1)
    count = 400
    store = PointInTimeStore()
    _daily(
        store,
        "DFII10",
        start=start,
        count=count,
        value=lambda i: 2.0 + 0.01 * i + (0.50 if i >= count - 40 else 0.0),
    )
    _daily(
        store,
        "DTWEXBGS",
        start=start,
        count=count,
        value=lambda i: 120.0 - 0.02 * i - (0.50 if i >= count - 40 else 0.0),
    )
    if include_gld:
        _daily(
            store,
            "GLD_TONNES",
            start=start,
            count=count,
            value=lambda i: 850.0 + 0.5 * i + (5.0 if i >= count - 30 else 0.0),
        )
    if include_cot:
        _weekly(store, "COT_GOLD_NET", start=date(2024, 6, 1), count=100, value=lambda i: 100000.0 + 1000.0 * i)
    _daily(store, "T10YIE", start=start, count=count, value=lambda i: 2.3 + 0.001 * i)
    as_of = datetime(2026, 1, 10, tzinfo=timezone.utc)
    return store, as_of


def test_sign_conventions_and_diagnostics() -> None:
    manifest = load_manifest(GOLD)
    store, as_of = _store_with_core_inputs()
    view = FundamentalEngine(manifest).evaluate(store, as_of)

    assert view.components["real_yield_10y"].signal < 0.0
    assert view.components["broad_usd"].signal > 0.0
    assert view.components["gold_etf_flows"].signal > 0.0
    assert view.components["cot_managed_money"].signal == -1.0
    assert view.components["central_bank_gold"].weight == 0.0
    assert view.components["central_bank_gold"].contribution == 0.0
    assert "central_bank_gold" in view.diagnostics
    assert view.is_valid


def test_missing_weight_bearing_input_invalidates_without_forward_fill() -> None:
    manifest = load_manifest(GOLD)
    store, as_of = _store_with_core_inputs(include_gld=False)
    view = FundamentalEngine(manifest).evaluate(store, as_of)
    assert "gold_etf_flows" in view.missing_inputs
    assert view.components["gold_etf_flows"].value is None
    assert view.components["gold_etf_flows"].signal == 0.0
    assert view.is_valid is False


def test_stale_weight_bearing_input_is_reported() -> None:
    manifest = load_manifest(GOLD)
    store, _ = _store_with_core_inputs()
    late_as_of = datetime(2026, 2, 20, tzinfo=timezone.utc)
    view = FundamentalEngine(manifest).evaluate(store, late_as_of)
    assert "real_yield_10y" in view.stale_inputs
    assert view.is_valid is False
    assert any("stale" in reason for reason in view.reasons)
