from __future__ import annotations

from datetime import datetime, timedelta, timezone

from goldbot.contracts import (
    FundamentalView,
    FusionView,
    OverseerDecision,
    StrategyDecision,
    TechnicalView,
)
from goldbot.data import BarSeries, make_synthetic_bars
from goldbot.demo import (
    PASS,
    _choose_filling,
    _latest_closed_bar,
    _order_request,
    run_demo_preflight,
)
from goldbot.profiles import load_manifest_with_profile


class FakeFeed:
    def __init__(self, *, now: datetime, bars: list, h4: list) -> None:
        self.now = now
        self.bars = bars
        self.h4 = h4
        self.order_checks: list[dict] = []
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
        return []

    def orders(self):
        return []

    def fetch_bars(self, timeframe: str, count: int):
        return self.bars if timeframe == "M15" else self.h4

    def check_order(self, request: dict):
        self.order_checks.append(request)
        return {"retcode": 0, "comment": "Done", "margin": 100.0}

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
