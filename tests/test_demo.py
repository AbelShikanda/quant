from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from goldbot.contracts import (
    FundamentalView,
    FusionView,
    OverseerDecision,
    StrategyDecision,
    TechnicalView,
)
from goldbot.data import BarSeries, make_synthetic_bars
from goldbot.demo import (
    NO_TRADE,
    PASS,
    _choose_filling,
    _latest_closed_bar,
    _order_request,
    run_demo_order,
    run_demo_preflight,
)
from goldbot.ledger import Ledger
from goldbot.profiles import load_manifest_with_profile


class FakeFeed:
    def __init__(self, *, now: datetime, bars: list, h4: list) -> None:
        self.now = now
        self.bars = bars
        self.h4 = h4
        self.order_checks: list[dict] = []
        self.send_requests: list[dict] = []
        self.open_positions: list[dict] = []
        self.closed = False

    def connect(self):
        return self.account_info()

    def account_info(self):
        return {
            "login": 123,
            "server": "FBS-Demo",
            "trade_mode": 0,
            "equity": 1000.0,
            "balance": 1000.0,
        }

    def terminal_info(self):
        return {
            "connected": True,
            "trade_allowed": True,
            "tradeapi_disabled": False,
        }

    def symbol_info(self):
        return {
            "name": "XAUUSD",
            "visible": True,
            "trade_mode": 4,
            "volume_min": 0.01,
            "volume_step": 0.01,
            "point": 0.01,
            "filling_mode": 3,
            "trade_stops_level": 0,
        }

    def symbol_tick(self):
        return {
            "bid": 3899.5,
            "ask": 3900.0,
            "time": int(self.now.timestamp()),
        }

    def positions(self):
        return list(self.open_positions)

    def orders(self):
        return []

    def fetch_bars(self, timeframe: str, count: int):
        return self.bars if timeframe == "M15" else self.h4

    def check_order(self, request: dict):
        self.order_checks.append(request)
        return {"retcode": 0, "comment": "Done", "margin": 100.0}

    def send_order(self, request: dict):
        self.send_requests.append(request)
        self.open_positions.append(
            {
                "ticket": 333,
                "volume": request["volume"],
                "price_open": request["price"],
                "sl": request["sl"],
                "tp": request["tp"],
                "time": int(self.now.timestamp()),
                "type": 0,
            }
        )
        return {
            "retcode": 10009,
            "order": 111,
            "deal": 222,
            "price": request["price"],
            "volume": request["volume"],
            "comment": "Done",
        }

    def close(self):
        self.closed = True


def _fake_decision(as_of: datetime) -> StrategyDecision:
    fundamental = FundamentalView(
        as_of_utc=as_of,
        score=0.5,
        components={},
        diagnostics={},
        missing_inputs=[],
        stale_inputs=[],
        is_valid=True,
        reasons=[],
        source_versions={},
    )
    technical = TechnicalView(
        as_of_utc=as_of,
        score=0.8,
        components={},
        indicators={"atr": 10.0},
        entry_triggers={
            "h4_context": True,
            "m15_trigger": True,
            "atr_filter": True,
            "spread_filter": True,
            "session_filter": True,
            "no_news_blackout": True,
        },
        is_valid=True,
        reasons=[],
        bars_used=600,
        context_bars_used=100,
    )
    fusion = FusionView(
        as_of_utc=as_of,
        combined_score=0.65,
        fundamental_contribution=0.2,
        technical_contribution=0.45,
        entry_candidate=True,
        conflict=False,
        reasons=[],
    )
    overseer = OverseerDecision(
        as_of_utc=as_of,
        decision="ENTER_LONG",
        side="LONG",
        confidence=0.8,
        verdict="approved",
        vetoes=[],
        rationale="test entry",
        source_views={},
    )
    return StrategyDecision(
        decision_id="dec-test",
        as_of_utc=as_of,
        decision_type="ENTER_LONG",
        side="LONG",
        confidence=0.8,
        combined_score=0.65,
        fundamental=fundamental,
        technical=technical,
        fusion=fusion,
        overseer=overseer,
        exit_decision=None,
        position_size=0.01,
        stop_loss=3890.0,
        take_profit=3920.0,
        reasons=["test entry"],
    )


def test_choose_filling_prefers_ioc() -> None:
    assert _choose_filling(3) == (1, "IOC")
    assert _choose_filling(1) == (0, "FOK")
    assert _choose_filling(0) == (2, "RETURN")


def test_order_request_is_a_long_market_request() -> None:
    request = _order_request(
        symbol="XAUUSD",
        volume=0.01,
        price=3900.0,
        stop_loss=3850.0,
        take_profit=3950.0,
        deviation_points=20,
        magic=123,
        comment="test",
        filling_mode=3,
    )
    assert request["action"] == 1
    assert request["type"] == 0
    assert request["type_filling"] == 1
    assert request["volume"] == 0.01
    assert request["sl"] < request["price"] < request["tp"]


def test_latest_closed_bar_drops_a_forming_bar() -> None:
    start = datetime(2026, 11, 13, 23, 0, tzinfo=timezone.utc)
    bars = make_synthetic_bars(start_utc=start, count=4)
    series = BarSeries(bars, "M15")
    tick_time = bars[-1].time_utc + timedelta(minutes=5)
    tick = {"time": int(tick_time.timestamp())}
    latest = _latest_closed_bar(series, tick=tick, broker_utc_offset_minutes=0)
    assert latest is not None
    assert latest.time_utc == bars[-2].time_utc


def _fake_no_trade_decision(as_of: datetime) -> StrategyDecision:
    return replace(
        _fake_decision(as_of),
        decision_type="NO_TRADE",
        side="NONE",
        position_size=None,
        stop_loss=None,
        take_profit=None,
        reasons=["no entry"],
    )


def _fake_preflight_inputs(monkeypatch, decision: StrategyDecision):
    start = datetime(2026, 1, 5, tzinfo=timezone.utc)
    m15 = make_synthetic_bars(start_utc=start, count=600)
    h4 = make_synthetic_bars(start_utc=start, count=500)
    now = datetime.now(timezone.utc)
    feed = FakeFeed(now=now, bars=m15, h4=h4)
    manifest = load_manifest_with_profile(
        "config/scope.gold.v1.yaml",
        "config/profiles/standard_1k.yaml",
    )

    class FakeStrategy:
        def __init__(self, _manifest):
            pass

        def decide(self, **_kwargs):
            return decision

    monkeypatch.setattr("goldbot.demo.StrategyEngine", FakeStrategy)
    monkeypatch.setattr("goldbot.demo.load_macro_directory", lambda _path: object())
    return manifest, feed


def test_demo_order_requires_explicit_guards() -> None:
    base = dict(
        manifest={},
        macro_dir="unused",
        ledger_path="unused",
        expected_login=123,
        broker_utc_offset_minutes=0,
        broker_offset_confirmed=True,
        send=False,
        confirm_phrase="SEND DEMO ORDER",
    )
    with pytest.raises(ValueError, match="--send"):
        run_demo_order(**base)
    base["send"] = True
    base["confirm_phrase"] = None
    with pytest.raises(ValueError, match="confirm-order"):
        run_demo_order(**base)
    base["confirm_phrase"] = "SEND DEMO ORDER"
    base["broker_offset_confirmed"] = False
    with pytest.raises(ValueError, match="confirm-broker-offset"):
        run_demo_order(**base)


def test_demo_order_does_not_send_when_preflight_not_ready(
    monkeypatch, tmp_path
) -> None:
    as_of = datetime(2026, 1, 11, 6, 0, tzinfo=timezone.utc)
    decision = _fake_no_trade_decision(as_of)
    manifest, feed = _fake_preflight_inputs(monkeypatch, decision)
    ledger_path = tmp_path / "demo.sqlite"

    result = run_demo_order(
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
    )

    assert result.sent is False
    assert result.preflight.status == NO_TRADE
    assert result.send_result is None
    assert feed.send_requests == []
    assert not hasattr(feed, "order_send")


def test_demo_order_sends_only_after_a_clean_preflight(monkeypatch, tmp_path) -> None:
    as_of = datetime(2026, 1, 11, 6, 0, tzinfo=timezone.utc)
    decision = _fake_decision(as_of)
    manifest, feed = _fake_preflight_inputs(monkeypatch, decision)
    ledger_path = tmp_path / "demo.sqlite"

    result = run_demo_order(
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
    )

    assert result.sent is True
    assert result.preflight.send_ready is True
    assert len(feed.send_requests) == 1
    assert feed.send_requests[0]["comment"] == "goldbot demo standard_1k"
    assert not hasattr(feed, "order_send")

    with Ledger(ledger_path) as ledger:
        order = ledger.connection.execute(
            "SELECT state, broker_order_ticket FROM orders"
        ).fetchone()
        fill = ledger.connection.execute(
            "SELECT broker_deal_ticket FROM fills"
        ).fetchone()
        position = ledger.connection.execute(
            "SELECT state, broker_position_ticket FROM positions"
        ).fetchone()
    assert order["state"] == "FILLED"
    assert order["broker_order_ticket"] == 111
    assert fill["broker_deal_ticket"] == 222
    assert position["state"] == "DEMO_OPEN"
    assert position["broker_position_ticket"] == 333


def test_preflight_calls_order_check_but_never_sends(monkeypatch) -> None:
    start = datetime(2026, 1, 5, tzinfo=timezone.utc)
    m15 = make_synthetic_bars(start_utc=start, count=600)
    h4 = make_synthetic_bars(start_utc=start, count=500)
    last = m15[-1].time_utc
    now = datetime.now(timezone.utc)
    feed = FakeFeed(now=now, bars=m15, h4=h4)
    manifest = load_manifest_with_profile(
        "config/scope.gold.v1.yaml",
        "config/profiles/standard_1k.yaml",
    )
    as_of = last + timedelta(minutes=15)
    decision = _fake_decision(as_of)

    class FakeStrategy:
        def __init__(self, _manifest):
            pass

        def decide(self, **_kwargs):
            return decision

    monkeypatch.setattr("goldbot.demo.StrategyEngine", FakeStrategy)
    monkeypatch.setattr("goldbot.demo.load_macro_directory", lambda _path: object())

    result = run_demo_preflight(
        manifest=manifest,
        macro_dir="unused",
        bars=600,
        context_bars=100,
        expected_login=123,
        feed=feed,
    )

    assert result.status == PASS, [
        (item.name, item.status, item.detail) for item in result.items
    ]
    assert result.decision is decision
    assert result.order_request is not None
    assert result.order_check is not None
    assert len(feed.order_checks) == 1
    assert not hasattr(feed, "order_send")
    assert feed.closed is False
