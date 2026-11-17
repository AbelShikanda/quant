"""Tier-1 US macro calendar with blackout windows for the strategy.

Hard-coded 2026 events verified against the official sources listed per event.
Treasury auction dates cover the November 2026 - February 2027 refunding
cycle; the calendar must be refreshed quarterly by the human owner when the
next refunding statement lands (see ``goldbot calendar`` output).

Blackout semantics (owner-approved 2026-11-17):
    FOMC/CPI/NFP/PCE: +/- 30 minutes around the release time
    Treasury auction: +/- 60 minutes around the auction time

Blackout blocks entries only. Risk exits and protective stops are
veto-immune and are never touched by this module.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")

WINDOW_BEFORE_MINUTES = {
    "fomc": 30,
    "cpi": 30,
    "nfp": 30,
    "pce": 30,
    "treasury_auction": 60,
}
WINDOW_AFTER_MINUTES = WINDOW_BEFORE_MINUTES

SOURCE = {
    "fomc": "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm",
    "cpi": "https://www.bls.gov/schedule/news_release/cpi.htm",
    "nfp": "https://www.bls.gov/schedule/news_release/empsit.htm",
    "pce": "https://www.bea.gov/news/schedule",
    "treasury_auction": "https://home.treasury.gov/news/press-releases/sb0678 + tentative quarterly schedule",
}
WAIT_LABEL = {
    "fomc": "FOMC statement",
    "cpi": "CPI (Consumer Price Index)",
    "nfp": "Employment Situation (NFP)",
    "pce": "Personal Income and Outlays (PCE)",
    "treasury_auction": "Treasury auction",
}


def _et(year: int, month: int, day: int, hour: int, minute: int) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=ET).astimezone(timezone.utc)


def _build_events() -> list["CalendarEvent"]:
    events: list[CalendarEvent] = []

    def add(cat: str, ymd: tuple[int, int, int], hour: int, minute: int, label: str) -> None:
        start = _et(ymd[0], ymd[1], ymd[2], hour, minute)
        before = WINDOW_BEFORE_MINUTES[cat]
        after = WINDOW_AFTER_MINUTES[cat]
        events.append(
            CalendarEvent(
                event_id=f"{cat}-{ymd[0]:04d}{ymd[1]:02d}{ymd[2]:02d}-{hour:02d}{minute:02d}",
                category=cat,
                label=label,
                starts_at_utc=start,
                blackout_start_utc=start - timedelta(minutes=before),
                blackout_end_utc=start + timedelta(minutes=after),
                before_minutes=before,
                after_minutes=after,
                source=SOURCE[cat],
            )
        )

    fomc_statement_dates = [
        (2026, 1, 28), (2026, 3, 18), (2026, 4, 29), (2026, 6, 17),
        (2026, 7, 29), (2026, 9, 16), (2026, 10, 28), (2026, 12, 9),
    ]
    for date in fomc_statement_dates:
        add("fomc", date, 14, 0, "FOMC statement + press conference")

    cpi_dates = [
        (2026, 1, 13), (2026, 2, 13), (2026, 3, 11), (2026, 4, 10),
        (2026, 5, 12), (2026, 6, 10), (2026, 7, 14), (2026, 8, 12),
        (2026, 9, 11), (2026, 10, 14), (2026, 11, 10), (2026, 12, 10),
    ]
    for date in cpi_dates:
        add("cpi", date, 8, 30, "CPI (Consumer Price Index)")

    nfp_dates = [
        (2026, 1, 9), (2026, 2, 11), (2026, 3, 6), (2026, 4, 3),
        (2026, 5, 8), (2026, 6, 5), (2026, 7, 2), (2026, 8, 7),
        (2026, 9, 4), (2026, 10, 2), (2026, 11, 6), (2026, 12, 4),
    ]
    for date in nfp_dates:
        add("nfp", date, 8, 30, "Employment Situation")

    pce_dates = [
        (2026, 1, 22), (2026, 2, 20), (2026, 3, 13), (2026, 4, 9),
        (2026, 4, 30), (2026, 5, 28), (2026, 6, 25), (2026, 7, 30),
        (2026, 8, 26), (2026, 9, 30), (2026, 10, 29), (2026, 11, 25),
        (2026, 12, 23),
    ]
    for date in pce_dates:
        add("pce", date, 8, 30, "Personal Income and Outlays (PCE)")

    auctions = [
        ((2026, 11, 9), "3-Year Note auction"),
        ((2026, 11, 10), "10-Year Note auction"),
        ((2026, 11, 12), "30-Year Bond auction"),
        ((2026, 11, 19), "10-Year TIPS auction (reopening)"),
        ((2026, 12, 7), "3-Year Note auction"),
        ((2026, 12, 8), "10-Year Note auction (reopening)"),
        ((2026, 12, 10), "30-Year Bond auction (reopening)"),
        ((2027, 1, 12), "10-Year Note auction (reopening)"),
        ((2027, 1, 13), "30-Year Bond auction"),
        ((2027, 2, 11), "30-Year Bond auction"),
    ]
    for date, label in auctions:
        add("treasury_auction", date, 13, 0, label)

    return sorted(events, key=lambda event: event.starts_at_utc)


@dataclass(frozen=True)
class CalendarEvent:
    event_id: str
    category: str
    label: str
    starts_at_utc: datetime
    blackout_start_utc: datetime
    blackout_end_utc: datetime
    before_minutes: int
    after_minutes: int
    source: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "category": self.category,
            "label": self.label,
            "starts_at_utc": self.starts_at_utc.isoformat(),
            "blackout_start_utc": self.blackout_start_utc.isoformat(),
            "blackout_end_utc": self.blackout_end_utc.isoformat(),
            "before_minutes": self.before_minutes,
            "after_minutes": self.after_minutes,
            "source": self.source,
        }


_ALL_EVENTS: list[CalendarEvent] | None = None


def all_events() -> list[CalendarEvent]:
    global _ALL_EVENTS
    if _ALL_EVENTS is None:
        _ALL_EVENTS = _build_events()
    return list(_ALL_EVENTS)


def blackout_at(ts_utc: datetime) -> tuple[bool, CalendarEvent | None, float | None]:
    """Return (blocked, event, minutes_until_start) for an instant."""
    from .contracts import ensure_utc

    ts = ensure_utc(ts_utc)
    for event in all_events():
        if event.blackout_start_utc <= ts <= event.blackout_end_utc:
            minutes_left = (event.blackout_end_utc - ts).total_seconds() / 60.0
            return True, event, minutes_left
    future = [
        e for e in all_events() if e.blackout_end_utc >= ts
    ]
    if not future:
        return False, None, None
    next_event = future[0]
    minutes_until = (next_event.blackout_start_utc - ts).total_seconds() / 60.0
    return False, next_event, minutes_until


def upcoming(now_utc: datetime, horizon_days: int = 14) -> list[CalendarEvent]:
    from .contracts import ensure_utc

    ts = ensure_utc(now_utc)
    cutoff = ts + timedelta(days=horizon_days)
    return [
        e for e in all_events()
        if e.blackout_end_utc >= ts and e.blackout_start_utc <= cutoff
    ]
