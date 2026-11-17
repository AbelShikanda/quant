"""Stop quality under news stress: would a 1.5x-ATR stop survive major releases?

Read-only. Fetches M15 bars (MT5 if available, synthetic otherwise — clearly
labelled), locates each major event in the calendar, and replays a long
entry one closed bar AFTER the event with a 1.5x ATR stop and 2.0R target,
measuring whether the stop would have been hit.

This answers the question the backtester cannot: did news gaps blow through
the stop where a plain stop-first assumption would protect us?
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Sequence

from .data import BarSeries, MT5DataFeed, load_macro_directory, make_synthetic_bars
from .econ_calendar import all_events
from .indicators import atr
from .contracts import utc_now


@dataclass
class CaseResult:
    event_id: str
    label: str
    entry: float
    stop: float
    target: float
    stop_hit: bool
    gap_pct: float
    atr: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "label": self.label,
            "entry": self.entry,
            "stop": self.stop,
            "target": self.target,
            "stop_hit": self.stop_hit,
            "gap_pct": self.gap_pct,
            "atr": self.atr,
        }


def _bar_slices_for_events(
    bars: Sequence,
    events: list,
    *,
    atr_period: int = 14,
    stop_atr_mult: float = 1.5,
    target_atr_mult: float = 2.0,
) -> list[CaseResult]:
    """Simulate an event-time long with a 1.5x-ATR stop on closed M15 bars."""
    results: list[CaseResult] = []
    sorted_events = sorted(events, key=lambda event: event.starts_at_utc)
    for event in sorted_events:
        index = next(
            (
                i
                for i, bar in enumerate(bars)
                if bar.time_utc <= event.starts_at_utc < bar.time_utc + timedelta(minutes=15)
            ),
            None,
        )
        if index is None or index < 1 or index + 5 >= len(bars):
            continue
        window = list(bars[:index])
        atr_values = atr(window, atr_period)
        atr_value = atr_values[-1]
        if not atr_value:
            continue
        entry = float(bars[index].open)
        prev_close = float(bars[index - 1].close)
        gap_pct = (entry - prev_close) / prev_close if prev_close else 0.0
        stop = entry - stop_atr_mult * atr_value
        target = entry + stop_atr_mult * target_atr_mult * atr_value
        future = list(bars[index + 1 : index + 6])
        stop_hit = any(float(b.low) <= stop for b in future)
        results.append(
            CaseResult(
                event_id=event.event_id,
                label=event.label,
                entry=round(entry, 2),
                stop=round(stop, 2),
                target=round(target, 2),
                stop_hit=stop_hit,
                gap_pct=round(gap_pct * 100, 3),
                atr=round(float(atr_value), 3),
            )
        )
    return results


def run_news_stress(
    *,
    source: str = "mt5",
    start: datetime | None = None,
    end: datetime | None = None,
    bars: int = 1600,
    broker_utc_offset_minutes: int = 120,
    feed: MT5DataFeed | None = None,
) -> dict[str, Any]:
    generated_at = utc_now()
    if source == "synthetic":
        bars_list = make_synthetic_bars(
            start_utc=datetime(2025, 7, 1, tzinfo=timezone.utc),
            count=max(bars, 800),
        )
        data_source = "synthetic"
    else:
        owns_feed = feed is None
        feed = feed or MT5DataFeed(
            symbol="XAUUSD", broker_utc_offset_minutes=broker_utc_offset_minutes
        )
        try:
            if owns_feed:
                try:
                    feed.connect()
                    fetched = feed.fetch_bars("M15", bars)
                    bars_list = fetched if isinstance(fetched, list) else list(fetched)
                    data_source = "mt5"
                except Exception as exc:  # noqa: BLE001 - report, don't crash
                    return {
                        "mode": "news_stress",
                        "generated_at_utc": generated_at.isoformat(),
                        "source": "mt5",
                        "ok": False,
                        "error": f"{type(exc).__name__}: {exc}",
                        "cases": [],
                        "summary": {"events": 0, "stop_hits": 0, "stop_hit_rate_pct": 0.0},
                    }
            else:
                fetched = feed.fetch_bars("M15", bars)
                bars_list = fetched if isinstance(fetched, list) else list(fetched)
                data_source = "mt5"
        finally:
            if owns_feed:
                try:
                    feed.close()
                except Exception:
                    pass

    window_from = start or datetime(2026, 1, 1, tzinfo=timezone.utc)
    window_to = end or generated_at
    events = [
        e for e in all_events()
        if window_from <= e.starts_at_utc <= window_to
    ]
    cases = _bar_slices_for_events(bars_list, events)
    stop_hits = sum(1 for c in cases if c.stop_hit)

    return {
        "mode": "news_stress",
        "generated_at_utc": generated_at.isoformat(),
        "source": data_source,
        "window": {"from": window_from.isoformat(), "to": window_to.isoformat()},
        "events_in_window": len(events),
        "cases": [c.to_dict() for c in cases],
        "summary": {
            "events": len(cases),
            "stop_hits": stop_hits,
            "stop_hit_rate_pct": round(stop_hits / len(cases) * 100, 2) if cases else 0.0,
        },
        "note": (
            "MT5 data; outcome is honest to the bars the broker returned. "
            "Synthetic data must be treated as a smoke test only."
            if data_source == "mt5"
            else "SYNTHETIC DATA — smoke test only; never used for promotion decisions."
        ),
    }
