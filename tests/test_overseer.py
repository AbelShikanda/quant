from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from goldbot.contracts import ExitCandidate, FusionView, PositionState, RiskState
from goldbot.manifest import load_manifest
from goldbot.overseer import Overseer


ROOT = Path(__file__).resolve().parents[1]
GOLD = ROOT / "config" / "scope.gold.v1.yaml"
AS_OF = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _risk(**overrides) -> RiskState:
    state = RiskState(
        equity=10000.0,
        starting_equity=10000.0,
        daily_start_equity=10000.0,
        peak_equity=10000.0,
    )
    for key, value in overrides.items():
        setattr(state, key, value)
    return state


def _fusion(candidate: bool = True) -> FusionView:
    return FusionView(
        as_of_utc=AS_OF,
        combined_score=0.5,
        fundamental_contribution=0.3,
        technical_contribution=0.2,
        entry_candidate=candidate,
        conflict=False,
    )


def test_overseer_approves_only_fusion_candidate() -> None:
    overseer = Overseer(load_manifest(GOLD))
    decision = overseer.entry_decision(_fusion(True), _risk(), None)
    assert decision.decision == "ENTER_LONG"
    assert decision.verdict == "approved"
    assert decision.vetoes == []


def test_overseer_cannot_originate_entry() -> None:
    overseer = Overseer(load_manifest(GOLD))
    decision = overseer.entry_decision(_fusion(False), _risk(), None)
    assert decision.decision == "NO_TRADE"
    assert decision.verdict == "no_trade"


def test_risk_veto_blocks_entry() -> None:
    overseer = Overseer(load_manifest(GOLD))
    decision = overseer.entry_decision(_fusion(True), _risk(kill_switch=True), None)
    assert decision.decision == "NO_TRADE"
    assert decision.verdict == "vetoed_risk"
    assert "kill_switch" in decision.vetoes


def test_position_is_held_not_reentered() -> None:
    overseer = Overseer(load_manifest(GOLD))
    decision = overseer.entry_decision(
        _fusion(True), _risk(), PositionState(position_id="p1", volume=0.1, open_price=2000.0)
    )
    assert decision.decision == "HOLD"


def test_exit_precedence_risk_exit_wins() -> None:
    overseer = Overseer(load_manifest(GOLD))
    decision = overseer.arbitrate_exit(
        [
            ExitCandidate("technical_or_profit_trail", 50, "trail_or_close", "technical"),
            ExitCandidate("thesis_invalidated", 80, "close", "thesis"),
            ExitCandidate("emergency_risk", 100, "flatten_and_halt", "risk"),
        ],
        AS_OF,
    )
    assert decision.should_exit is True
    assert decision.rule_id == "emergency_risk"
    assert decision.priority == 100
    assert decision.risk_veto_immune is True


def test_exit_precedence_thesis_beats_technical() -> None:
    overseer = Overseer(load_manifest(GOLD))
    decision = overseer.arbitrate_exit(
        [
            ExitCandidate("technical_or_profit_trail", 50, "trail_or_close", "technical"),
            ExitCandidate("thesis_invalidated", 80, "close", "thesis"),
        ],
        AS_OF,
    )
    assert decision.rule_id == "thesis_invalidated"
    assert decision.risk_veto_immune is False


def test_no_exit_candidates_means_hold() -> None:
    overseer = Overseer(load_manifest(GOLD))
    decision = overseer.arbitrate_exit([], AS_OF)
    assert decision.should_exit is False
    assert decision.rule_id is None
