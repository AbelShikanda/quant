"""Exit engine.

Exits are first-class.  The engine produces candidates with the manifest's
priority order; the overseer selects the winner.  Risk candidates are
veto-immune by construction.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from .contracts import (
    Bar,
    ExitCandidate,
    FundamentalView,
    PositionState,
    RiskState,
    TechnicalView,
    ensure_utc,
)
from .manifest import as_dict, as_list


class ExitEngine:
    def __init__(self, manifest: dict[str, Any]) -> None:
        self.manifest = manifest
        self.config = as_dict(as_dict(manifest.get("strategy")).get("exits"))
        self.risk = as_dict(manifest.get("risk"))
        self.precedence = {
            str(item.get("id")): int(item.get("priority", 0))
            for item in as_list(as_dict(manifest.get("exits")).get("precedence"))
            if isinstance(item, dict) and item.get("id")
        }
        self.thesis_invalidated_score = float(self.config.get("thesis_invalidated_score", -0.35))
        self.thesis_weakened_score = float(self.config.get("thesis_weakened_score", -0.15))
        self.technical_exit_score = float(self.config.get("technical_exit_score", -0.25))
        self.time_stop_bars = int(self.config.get("time_stop_bars", 96))
        self.profit_take_r = float(self.config.get("profit_take_r", 2.0))
        self.stop_atr_multiple = float(self.config.get("stop_atr_multiple", 1.5))
        self.trailing_atr_multiple = float(self.config.get("trailing_atr_multiple", 2.0))
        self.trailing_start_r = float(self.config.get("trailing_start_r", 1.0))

    def candidates(
        self,
        *,
        position: PositionState,
        bar: Bar,
        fundamental: FundamentalView,
        technical: TechnicalView,
        risk_state: RiskState,
        unrealized_r: float,
        advisory: bool = False,
    ) -> list[ExitCandidate]:
        as_of = ensure_utc(bar.time_utc)
        price = bar.close
        result: list[ExitCandidate] = []
        risk_priority = self.precedence.get("emergency_risk", 100)
        stop_priority = self.precedence.get("hard_or_trailing_stop", 90)
        invalidated_priority = self.precedence.get("thesis_invalidated", 80)
        weakened_priority = self.precedence.get("thesis_weakened", 70)
        time_priority = self.precedence.get("time_stop", 60)
        technical_priority = self.precedence.get("technical_or_profit_trail", 50)
        profit_priority = self.precedence.get("profit_take", 40)
        advisory_priority = self.precedence.get("advisory", 30)

        daily_loss_pct = 0.0
        if risk_state.daily_start_equity > 0 and risk_state.daily_pnl < 0:
            daily_loss_pct = -risk_state.daily_pnl / risk_state.daily_start_equity * 100.0
        risk_reasons: list[str] = []
        if risk_state.kill_switch:
            risk_reasons.append("kill_switch")
        if risk_state.daily_halt:
            risk_reasons.append("daily_halt")
        if risk_state.max_drawdown_halt:
            risk_reasons.append("max_drawdown_halt")
        if daily_loss_pct >= float(self.risk.get("daily_loss_halt_pct", 1.5)):
            risk_reasons.append(f"daily_loss={daily_loss_pct:.2f}%")
        if risk_state.current_drawdown_pct >= float(self.risk.get("max_drawdown_flatten_pct", 8.0)):
            risk_reasons.append(f"drawdown={risk_state.current_drawdown_pct:.2f}%")
        if risk_reasons:
            result.append(
                ExitCandidate(
                    rule_id="emergency_risk",
                    priority=risk_priority,
                    action=str(as_dict(self.manifest.get("risk")).get("daily_halt_action", "flatten_and_halt")),
                    reason="risk halt: " + ", ".join(risk_reasons),
                    evidence={"risk_reasons": risk_reasons, "as_of_utc": as_of.isoformat()},
                )
            )

        if position.stop_loss is not None and price <= position.stop_loss:
            result.append(
                ExitCandidate(
                    rule_id="hard_or_trailing_stop",
                    priority=stop_priority,
                    action="close",
                    reason=f"hard stop hit: price {price:.2f} <= stop {position.stop_loss:.2f}",
                    evidence={"price": price, "stop_loss": position.stop_loss},
                )
            )
        trailing_stop = self._trailing_stop(position)
        if trailing_stop is not None and price <= trailing_stop:
            result.append(
                ExitCandidate(
                    rule_id="hard_or_trailing_stop",
                    priority=stop_priority,
                    action="close",
                    reason=f"trailing stop hit: price {price:.2f} <= trail {trailing_stop:.2f}",
                    evidence={"price": price, "trailing_stop": trailing_stop},
                )
            )

        if fundamental.score <= self.thesis_invalidated_score:
            result.append(
                ExitCandidate(
                    rule_id="thesis_invalidated",
                    priority=invalidated_priority,
                    action="close",
                    reason=(
                        f"fundamental thesis invalidated: {fundamental.score:+.3f} <= "
                        f"{self.thesis_invalidated_score:+.3f}"
                    ),
                    evidence={"fundamental_score": fundamental.score},
                )
            )
        elif fundamental.score <= self.thesis_weakened_score:
            result.append(
                ExitCandidate(
                    rule_id="thesis_weakened",
                    priority=weakened_priority,
                    action="reduce_or_close",
                    reason=(
                        f"fundamental thesis weakened: {fundamental.score:+.3f} <= "
                        f"{self.thesis_weakened_score:+.3f}"
                    ),
                    evidence={"fundamental_score": fundamental.score},
                )
            )

        if position.bars_in_trade >= self.time_stop_bars:
            result.append(
                ExitCandidate(
                    rule_id="time_stop",
                    priority=time_priority,
                    action="close",
                    reason=f"time stop: {position.bars_in_trade} bars >= {self.time_stop_bars}",
                    evidence={"bars_in_trade": position.bars_in_trade},
                )
            )

        if technical.score <= self.technical_exit_score:
            result.append(
                ExitCandidate(
                    rule_id="technical_or_profit_trail",
                    priority=technical_priority,
                    action="trail_or_close",
                    reason=(
                        f"technical score deteriorated: {technical.score:+.3f} <= "
                        f"{self.technical_exit_score:+.3f}"
                    ),
                    evidence={"technical_score": technical.score},
                )
            )

        if unrealized_r >= self.profit_take_r:
            result.append(
                ExitCandidate(
                    rule_id="profit_take",
                    priority=profit_priority,
                    action="close",
                    reason=f"profit target: {unrealized_r:.2f}R >= {self.profit_take_r:.2f}R",
                    evidence={"unrealized_r": unrealized_r},
                )
            )

        if advisory:
            result.append(
                ExitCandidate(
                    rule_id="advisory",
                    priority=advisory_priority,
                    action="notify_only",
                    reason="overseer advisory exit",
                    evidence={},
                )
            )
        return result

    def _trailing_stop(self, position: PositionState) -> float | None:
        if position.highest_price is None or position.entry_atr is None:
            return None
        if position.open_price <= 0 or position.stop_loss is None:
            return None
        risk_distance = position.open_price - position.stop_loss
        if risk_distance <= 0:
            return None
        best_r = (position.highest_price - position.open_price) / risk_distance
        if best_r < self.trailing_start_r:
            return None
        return position.highest_price - self.trailing_atr_multiple * position.entry_atr

    def initial_stop(self, entry_price: float, atr_value: float) -> float:
        return entry_price - self.stop_atr_multiple * atr_value
