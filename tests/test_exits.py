from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from goldbot.contracts import (
    Bar,
    FundamentalView,
    PositionState,
    RiskState,
    TechnicalView,
)
from goldbot.exits import ExitEngine
from goldbot.manifest import load_manifest
from goldbot.overseer import Overseer


ROOT = Path(__file__).resolve().parents[1]
GOLD = ROOT / "config" / "scope.gold.v1.yaml"
AS_OF = datetime(2026, 1, 1, 0, 15, tzinfo=timezone.utc)


def _fundamental(score: float) -> FundamentalView:
    return FundamentalView(
        as_of_utc=AS_OF,
        score=score,
        components={},
        diagnostics={},
        is_valid=True,
    )


def _technical(score: float) -> TechnicalView:
    return TechnicalView(
        as_of_utc=AS_OF,
        score=score,
        components={},
        indicators={},
        entry_triggers={},
        is_valid=True,
    )


def _risk() -> RiskState:
    return RiskState(
        equity=10000.0,
        starting_equity=10000.0,
        daily_start_equity=10000.0,
        peak_equity=10000.0,
    )


def _position(**overrides) -> PositionState:
    position = PositionState(
        position_id="p1",
        side="LONG",
        volume=0.1,
        open_price=2000.0,
        open_time_utc=datetime(2026, 1, 1, tzinfo=timezone.utc),
        stop_loss=1990.0,
        take_profit=2020.0,
        highest_price=2005.0,
        bars_in_trade=10,
        entry_atr=5.0,
    )
    for key, value in overrides.items():
        setattr(position, key, value)
    return position


def _bar(close: float) -> Bar:
    return Bar(
        time_utc=AS_OF,
        open=close,
        high=close + 1.0,
        low=close - 1.0,
        close=close,
        spread_points=31,
    )


def _winner(manifest, candidates) -> str | None:
    return Overseer(manifest).arbitrate_exit(candidates, AS_OF).rule_id


def test_thesis_invalidated_beats_technical_trail() -> None:
    manifest = load_manifest(GOLD)
    engine = ExitEngine(manifest)
    candidates = engine.candidates(
        position=_position(),
        bar=_bar(2005.0),
        fundamental=_fundamental(-0.50),
        technical=_technical(-0.50),
        risk_state=_risk(),
        unrealized_r=0.5,
    )
    assert _winner(manifest, candidates) == "thesis_invalidated"


def test_technical_deterioration_exits_when_fundamental_is_fine() -> None:
    manifest = load_manifest(GOLD)
    engine = ExitEngine(manifest)
    candidates = engine.candidates(
        position=_position(),
        bar=_bar(2005.0),
        fundamental=_fundamental(0.50),
        technical=_technical(-0.50),
        risk_state=_risk(),
        unrealized_r=0.5,
    )
    assert _winner(manifest, candidates) == "technical_or_profit_trail"


def test_risk_exit_is_veto_immune() -> None:
    manifest = load_manifest(GOLD)
    engine = ExitEngine(manifest)
    risk = _risk()
    risk.daily_halt = True
    candidates = engine.candidates(
        position=_position(),
        bar=_bar(2005.0),
        fundamental=_fundamental(-0.50),
        technical=_technical(-0.50),
        risk_state=risk,
        unrealized_r=0.5,
    )
    decision = Overseer(manifest).arbitrate_exit(candidates, AS_OF)
    assert decision.rule_id == "emergency_risk"
    assert decision.risk_veto_immune is True


def test_hard_stop_precedes_thesis_invalidated() -> None:
    manifest = load_manifest(GOLD)
    engine = ExitEngine(manifest)
    candidates = engine.candidates(
        position=_position(stop_loss=2010.0),
        bar=_bar(2005.0),
        fundamental=_fundamental(-0.50),
        technical=_technical(0.50),
        risk_state=_risk(),
        unrealized_r=0.5,
    )
    assert _winner(manifest, candidates) == "hard_or_trailing_stop"


def test_profit_take_candidate() -> None:
    manifest = load_manifest(GOLD)
    engine = ExitEngine(manifest)
    candidates = engine.candidates(
        position=_position(),
        bar=_bar(2005.0),
        fundamental=_fundamental(0.50),
        technical=_technical(0.50),
        risk_state=_risk(),
        unrealized_r=2.5,
    )
    assert _winner(manifest, candidates) == "profit_take"


def test_time_stop_candidate() -> None:
    manifest = load_manifest(GOLD)
    engine = ExitEngine(manifest)
    candidates = engine.candidates(
        position=_position(bars_in_trade=100),
        bar=_bar(2005.0),
        fundamental=_fundamental(0.50),
        technical=_technical(0.50),
        risk_state=_risk(),
        unrealized_r=0.5,
    )
    assert _winner(manifest, candidates) == "time_stop"


def test_trailing_stop_candidate() -> None:
    manifest = load_manifest(GOLD)
    engine = ExitEngine(manifest)
    candidates = engine.candidates(
        position=_position(highest_price=2020.0, entry_atr=5.0),
        bar=_bar(2005.0),
        fundamental=_fundamental(0.50),
        technical=_technical(0.50),
        risk_state=_risk(),
        unrealized_r=0.5,
    )
    assert _winner(manifest, candidates) == "hard_or_trailing_stop"


def test_initial_stop_uses_manifest_multiple() -> None:
    manifest = load_manifest(GOLD)
    engine = ExitEngine(manifest)
    assert engine.initial_stop(2000.0, 10.0) == 1985.0
