"""Rule-based overseer.

The overseer is a referee.  It can approve or veto an entry that the fusion
layer already produced; it cannot originate an entry.  It applies exit
precedence and never lets a non-risk rule override a risk exit.
"""

from __future__ import annotations

from typing import Any

from .contracts import (
    ExitCandidate,
    ExitDecision,
    FusionView,
    OverseerDecision,
    PositionState,
    RiskState,
    ensure_utc,
)
from .manifest import as_dict, as_list


class Overseer:
    def __init__(self, manifest: dict[str, Any]) -> None:
        self.manifest = manifest
        self.config = as_dict(manifest.get("overseer"))
        self.risk = as_dict(manifest.get("risk"))
        self.precedence = {
            str(item.get("id")): int(item.get("priority", 0))
            for item in as_list(as_dict(manifest.get("exits")).get("precedence"))
            if isinstance(item, dict) and item.get("id")
        }

    def entry_decision(
        self,
        fusion: FusionView,
        risk_state: RiskState,
        position: PositionState | None,
    ) -> OverseerDecision:
        as_of = ensure_utc(fusion.as_of_utc)
        vetoes: list[str] = []
        if risk_state.kill_switch:
            vetoes.append("kill_switch")
        if risk_state.daily_halt:
            vetoes.append("daily_loss_halt")
        if risk_state.max_drawdown_halt:
            vetoes.append("max_drawdown_halt")
        if position is not None:
            return OverseerDecision(
                as_of_utc=as_of,
                decision="HOLD",
                side="LONG",
                confidence=0.0,
                verdict="hold",
                vetoes=vetoes,
                rationale="position already open; exit engine owns the next action",
                source_views={
                    "fundamental_score": fusion.fundamental_contribution,
                    "technical_score": fusion.technical_contribution,
                    "combined_score": fusion.combined_score,
                },
            )
        if vetoes:
            return OverseerDecision(
                as_of_utc=as_of,
                decision="NO_TRADE",
                side="NONE",
                confidence=0.0,
                verdict="vetoed_risk",
                vetoes=vetoes,
                rationale="risk veto: " + ", ".join(vetoes),
                source_views={
                    "fundamental_score": fusion.fundamental_contribution,
                    "technical_score": fusion.technical_contribution,
                    "combined_score": fusion.combined_score,
                },
            )
        if not fusion.entry_candidate:
            return OverseerDecision(
                as_of_utc=as_of,
                decision="NO_TRADE",
                side="NONE",
                confidence=0.0,
                verdict="no_trade",
                vetoes=[],
                rationale="fusion did not produce an entry candidate",
                source_views={
                    "fundamental_score": fusion.fundamental_contribution,
                    "technical_score": fusion.technical_contribution,
                    "combined_score": fusion.combined_score,
                },
            )
        confidence = max(0.0, min(1.0, fusion.combined_score))
        return OverseerDecision(
            as_of_utc=as_of,
            decision="ENTER_LONG",
            side="LONG",
            confidence=confidence,
            verdict="approved",
            vetoes=[],
            rationale="both layers agree and all entry triggers passed",
            source_views={
                "fundamental_score": fusion.fundamental_contribution,
                "technical_score": fusion.technical_contribution,
                "combined_score": fusion.combined_score,
            },
        )

    def arbitrate_exit(self, candidates: list[ExitCandidate], as_of_utc: Any) -> ExitDecision:
        as_of = ensure_utc(as_of_utc)
        if not candidates:
            return ExitDecision(
                as_of_utc=as_of,
                should_exit=False,
                rule_id=None,
                priority=None,
                action=None,
                reason=None,
                candidates=[],
                risk_veto_immune=False,
            )
        ordered = sorted(candidates, key=lambda item: (-item.priority, item.rule_id))
        winner = ordered[0]
        risk_veto_immune = any(
            item.rule_id == "emergency_risk" or item.priority >= self.precedence.get("emergency_risk", 100)
            for item in ordered
        )
        return ExitDecision(
            as_of_utc=as_of,
            should_exit=winner.action != "notify_only",
            rule_id=winner.rule_id,
            priority=winner.priority,
            action=winner.action,
            reason=winner.reason,
            candidates=ordered,
            risk_veto_immune=risk_veto_immune,
        )
