"""Unit tests for the Tier-1 econ calendar and blackout windows."""

from datetime import datetime, timezone

from goldbot.econ_calendar import all_events, blackout_at, upcoming


def test_event_list_is_sorted():
    events = all_events()
    assert events
    times = [e.starts_at_utc for e in events]
    assert times == sorted(times)


def test_fomc_event_present_with_tight_window():
    events = [e for e in all_events() if e.category == "fomc"]
    assert any(e.event_id == "fomc-20261209-1400" for e in events)
    fomc = next(e for e in events if e.event_id == "fomc-20261209-1400")
    assert fomc.before_minutes == 30
    assert fomc.after_minutes == 30


def test_treasury_auction_window_is_sixty_minutes():
    auctions = [e for e in all_events() if e.category == "treasury_auction"]
    assert auctions
    for a in auctions:
        assert a.before_minutes == 60
        assert a.after_minutes == 60


def test_blackout_hits_during_fomc():
    # FOMC 2026-12-09 19:00 UTC; window is 18:30-19:30 UTC.
    ts = datetime(2026, 12, 9, 19, 0, tzinfo=timezone.utc)
    blocked, event, minutes_left = blackout_at(ts)
    assert blocked is True
    assert event.category == "fomc"
    assert minutes_left is not None and minutes_left > 0


def test_blackout_clear_between_events():
    ts = datetime(2026, 11, 18, 15, 0, tzinfo=timezone.utc)
    blocked, event, minutes_until = blackout_at(ts)
    assert blocked is False
    assert event is not None
    assert minutes_until is not None and minutes_until > 0


def test_blackout_end_time_is_respected():
    ts = datetime(2026, 12, 9, 19, 31, tzinfo=timezone.utc)
    blocked, _, _ = blackout_at(ts)
    assert blocked is False


def test_upcoming_respects_horizon():
    now = datetime(2026, 11, 17, 12, 0, tzinfo=timezone.utc)
    events = upcoming(now, horizon_days=14)
    assert len(events) >= 2
    assert all(e.blackout_end_utc >= now for e in events)
