"""Intent-first order flow: crash between broker send and ledger write must
be recoverable without ever sending twice.

Sequence under test:
  1. run_demo_order with a feed whose send_order raises (crash after intent
     insert, before the broker result is recorded);
  2. re-run with a fixed feed and assert the outcome reconciles from the
     broker's actual state.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

_TESTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_TESTS_DIR))

import test_demo as td  # noqa: E402
from goldbot.contracts import StrategyDecision  # noqa: E402
from goldbot.data import make_synthetic_bars  # noqa: E402
from goldbot.demo import run_demo_order  # noqa: E402
from goldbot.ledger import Ledger  # noqa: E402
from goldbot.profiles import load_manifest_with_profile  # noqa: E402


def _fake_decision(as_of: datetime) -> StrategyDecision:
    return td._fake_decision(as_of)


def _inputs(monkeypatch, decision):
    start = datetime(2026, 1, 5, tzinfo=timezone.utc)
    m15 = make_synthetic_bars(start_utc=start, count=600)
    h4 = make_synthetic_bars(start_utc=start, count=500)
    now = datetime.now(timezone.utc)
    feed = td.FakeFeed(now=now, bars=m15, h4=h4)
    manifest = load_manifest_with_profile(
        "config/scope.gold.v1.yaml",
        "config/profiles/micro_50.yaml",
    )

    class FakeStrategy:
        def __init__(self, _manifest):
            pass

        def decide(self, **_kwargs):
            return decision

    monkeypatch.setattr("goldbot.demo.StrategyEngine", FakeStrategy)
    monkeypatch.setattr("goldbot.demo.load_macro_directory", lambda _path: object())
    return manifest, feed


def _order_args(manifest, feed, ledger_path):
    return dict(
        manifest=manifest,
        macro_dir="unused",
        ledger_path=ledger_path,
        expected_login=123,
        broker_utc_offset_minutes=0,
        broker_offset_confirmed=True,
        send=True,
        confirm_phrase="SEND DEMO ORDER",
        bars=600,
        context_bars=500,
        feed=feed,
        override_blocks=True,
    )


def test_intent_is_recorded_before_broker_send(monkeypatch, tmp_path) -> None:
    as_of = datetime(2026, 1, 11, 6, 0, tzinfo=timezone.utc)
    decision = _fake_decision(as_of)
    manifest, feed = _inputs(monkeypatch, decision)
    ledger_path = tmp_path / "demo.sqlite"

    result = run_demo_order(**_order_args(manifest, feed, ledger_path))

    assert result.sent is True
    with Ledger(ledger_path) as ledger:
        row = ledger.connection.execute(
            "SELECT state, broker_order_ticket FROM orders"
        ).fetchone()
    assert row["state"] == "FILLED"
    assert row["broker_order_ticket"] == 111


def test_crash_after_intent_insert_recovers_from_broker_state(
    monkeypatch, tmp_path
) -> None:
    as_of = datetime(2026, 1, 11, 6, 0, tzinfo=timezone.utc)
    decision = _fake_decision(as_of)
    manifest, feed = _inputs(monkeypatch, decision)
    ledger_path = tmp_path / "demo.sqlite"

    def boom(_request):
        raise RuntimeError("simulated crash after broker send")

    feed.send_order = boom
    with pytest.raises(RuntimeError):
        run_demo_order(**_order_args(manifest, feed, ledger_path))

    # The broker now holds the position (as in the real fault).
    feed.open_positions = [
        {
            "ticket": 444,
            "symbol": "XAUUSD",
            "type": 0,
            "volume": 0.01,
            "price_open": 3900.0,
            "sl": 3801.0,
            "tp": 3974.0,
            "magic": 20261114,
            "comment": "goldbot DEMO OVERRIDE",
            "time": int(datetime.now(timezone.utc).timestamp()),
        }
    ]

    result = run_demo_order(**_order_args(manifest, feed, ledger_path))

    assert result.sent is True
    assert result.send_result["status"] == "RECOVERED_FILLED"
    # The broker was NOT called a second time.
    assert feed.send_requests == []

    with Ledger(ledger_path) as ledger:
        order = ledger.connection.execute(
            "SELECT state, broker_order_ticket FROM orders"
        ).fetchone()
        fill = ledger.connection.execute(
            "SELECT volume, price FROM fills"
        ).fetchone()
        position = ledger.connection.execute(
            "SELECT state, broker_position_ticket FROM positions"
        ).fetchone()
    assert order["state"] == "FILLED"
    assert order["broker_order_ticket"] == 444
    assert fill["volume"] == 0.01
    assert position["state"] == "DEMO_OPEN"
    assert position["broker_position_ticket"] == 444


def test_crash_with_no_broker_position_allows_one_resend(
    monkeypatch, tmp_path
) -> None:
    as_of = datetime(2026, 1, 11, 6, 0, tzinfo=timezone.utc)
    decision = _fake_decision(as_of)
    manifest, feed = _inputs(monkeypatch, decision)
    ledger_path = tmp_path / "demo.sqlite"

    def boom(_request):
        raise RuntimeError("simulated crash before broker send")

    feed.send_order = boom
    with pytest.raises(RuntimeError):
        run_demo_order(**_order_args(manifest, feed, ledger_path))

    # Broker holds nothing: resend is safe and must happen exactly once.
    feed.send_order = td.FakeFeed.send_order.__get__(feed, td.FakeFeed)
    result = run_demo_order(**_order_args(manifest, feed, ledger_path))

    assert result.sent is True
    assert len(feed.send_requests) == 1


def test_crash_with_ambiguous_broker_state_refuses_to_resend(
    monkeypatch, tmp_path
) -> None:
    as_of = datetime(2026, 1, 11, 6, 0, tzinfo=timezone.utc)
    decision = _fake_decision(as_of)
    manifest, feed = _inputs(monkeypatch, decision)
    ledger_path = tmp_path / "demo.sqlite"

    def boom(_request):
        raise RuntimeError("simulated crash after broker send")

    feed.send_order = boom
    with pytest.raises(RuntimeError):
        run_demo_order(**_order_args(manifest, feed, ledger_path))

    feed.open_positions = [
        {
            "ticket": ticket,
            "symbol": "XAUUSD",
            "type": 0,
            "volume": 0.01,
            "price_open": 3900.0,
            "sl": 3801.0,
            "tp": 3974.0,
            "magic": 20261114,
            "comment": "goldbot DEMO OVERRIDE",
            "time": int(datetime.now(timezone.utc).timestamp()),
        }
        for ticket in (444, 445)
    ]

    result = run_demo_order(**_order_args(manifest, feed, ledger_path))

    assert result.sent is False
    assert result.send_result["status"] == "RECONCILE_NEEDED"
    assert feed.send_requests == []
