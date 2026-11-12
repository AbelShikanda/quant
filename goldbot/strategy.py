"""Headless strategy orchestrator.

The strategy reads its behavior from the manifest, produces a fully explained
decision for every bar, and has no dashboard dependency.  It does not place
orders; the execution layer is a later milestone.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .contracts import (
    FundamentalView,
    FusionView,
    OverseerDecision,
    PositionState,
    RiskState,
    StrategyDecision,
    TechnicalView,
    ensure_utc,
)
from .data import BarSeries, PointInTimeStore
from .exits import ExitEngine
from .fundamental import FundamentalEngine
from .fusion import FusionEngine
from .manifest import as_dict
from .overseer import Overseer
from .technical import TechnicalEngine


@dataclass
class SizingResult:
    volume: float
    risk_amount: float
    stop_distance: float
    notional: float
    max_notional: float
    reason: str


class StrategyEngine:
    def __init__(self, manifest: dict[str, Any]) -> None:
        self.manifest = manifest
        self.scope_id = str(manifest.get("scope_id", "unknown"))
        self.fundamental_engine = FundamentalEngine(manifest)
        self.technical_engine = TechnicalEngine(manifest)
        self.fusion_engine = FusionEngine(manifest)
        self.overseer = Overseer(manifest)
        self.exit_engine = ExitEngine(manifest)
        self.sizing_config = as_dict(as_dict(manifest.get("strategy")).get("sizing"))
        self.risk = as_dict(manifest.get("risk"))
        self.market = as_dict(manifest.get("market"))
        self.execution_minutes = _timeframe_minutes(str(self.market.get("execution_bar", "M15")))
        self.contract_size_oz = float(self.sizing_config.get("contract_size_oz", 100.0))
        self.min_lot = float(self.sizing_config.get("min_lot", 0.01))
        self.lot_step = float(self.sizing_config.get("lot_step", 0.01))
        self.max_notional_ratio = float(
            self.sizing_config.get(
                "max_notional_exposure_ratio",
                self.market.get("max_notional_exposure_ratio", 1.0),
            )
        )

    def decide(
        self,
        *,
        execution_bars: BarSeries,
        context_bars: BarSeries,
        macro_store: PointInTimeStore,
        as_of_utc: datetime,
        equity: float,
        position: PositionState | None = None,
        risk_state: RiskState | None = None,
        news_blackout: bool = False,
        advisory_exit: bool = False,
    ) -> StrategyDecision:
        as_of = ensure_utc(as_of_utc)
        risk = risk_state or RiskState(
            equity=equity,
            starting_equity=equity,
            daily_start_equity=equity,
            peak_equity=equity,
        )
        fundamental = self.fundamental_engine.evaluate(macro_store, as_of)
        technical = self.technical_engine.evaluate(
            execution_bars,
            context_bars,
            as_of,
            news_blackout=news_blackout,
        )
        fusion = self.fusion_engine.evaluate(fundamental, technical)
        bar = self._last_closed_bar(execution_bars, as_of)
        if bar is None:
            raise ValueError("no closed execution bar at as_of")

        if position is not None:
            unrealized_r = self._unrealized_r(position, bar.close)
            candidates = self.exit_engine.candidates(
                position=position,
                bar=bar,
                fundamental=fundamental,
                technical=technical,
                risk_state=risk,
                unrealized_r=unrealized_r,
                advisory=advisory_exit,
            )
            exit_decision = self.overseer.arbitrate_exit(candidates, as_of)
            overseer_decision = self.overseer.entry_decision(fusion, risk, position)
            if exit_decision.should_exit:
                return self._decision(
                    as_of=as_of,
                    decision_type="EXIT",
                    side="LONG",
                    confidence=1.0 if exit_decision.risk_veto_immune else 0.8,
                    fundamental=fundamental,
                    technical=technical,
                    fusion=fusion,
                    overseer=overseer_decision,
                    exit_decision=exit_decision,
                    position_size=None,
                    stop_loss=None,
                    take_profit=None,
                    reasons=[exit_decision.reason or "exit"],
                )
            return self._decision(
                as_of=as_of,
                decision_type="HOLD",
                side="LONG",
                confidence=0.0,
                fundamental=fundamental,
                technical=technical,
                fusion=fusion,
                overseer=overseer_decision,
                exit_decision=exit_decision,
                position_size=None,
                stop_loss=position.stop_loss,
                take_profit=position.take_profit,
                reasons=["position held; no exit candidate won"],
            )

        overseer_decision = self.overseer.entry_decision(fusion, risk, None)
        if overseer_decision.decision != "ENTER_LONG":
            return self._decision(
                as_of=as_of,
                decision_type="NO_TRADE",
                side="NONE",
                confidence=0.0,
                fundamental=fundamental,
                technical=technical,
                fusion=fusion,
                overseer=overseer_decision,
                exit_decision=None,
                position_size=None,
                stop_loss=None,
                take_profit=None,
                reasons=[overseer_decision.rationale],
            )

        atr_value = float(technical.indicators.get("atr", 0.0))
        entry_price = bar.close
        sizing = self.size_position(equity=equity, atr_value=atr_value, entry_price=entry_price)
        if sizing.volume <= 0.0:
            return self._decision(
                as_of=as_of,
                decision_type="NO_TRADE",
                side="NONE",
                confidence=0.0,
                fundamental=fundamental,
                technical=technical,
                fusion=fusion,
                overseer=overseer_decision,
                exit_decision=None,
                position_size=None,
                stop_loss=None,
                take_profit=None,
                reasons=[sizing.reason],
            )
        stop_loss = self.exit_engine.initial_stop(entry_price, atr_value)
        take_profit = entry_price + self.exit_engine.profit_take_r * (entry_price - stop_loss)
        return self._decision(
            as_of=as_of,
            decision_type="ENTER_LONG",
            side="LONG",
            confidence=overseer_decision.confidence,
            fundamental=fundamental,
            technical=technical,
            fusion=fusion,
            overseer=overseer_decision,
            exit_decision=None,
            position_size=sizing.volume,
            stop_loss=stop_loss,
            take_profit=take_profit,
            reasons=[
                f"entry approved at {entry_price:.2f}",
                f"volume {sizing.volume:.2f} lots, risk {sizing.risk_amount:.2f}, "
                f"notional {sizing.notional:.2f} <= {sizing.max_notional:.2f}",
            ],
        )

    def size_position(self, *, equity: float, atr_value: float, entry_price: float) -> SizingResult:
        risk_pct = float(self.risk.get("per_trade_equity_pct", 0.25))
        risk_amount = equity * risk_pct / 100.0
        stop_distance = self.exit_engine.stop_atr_multiple * atr_value
        if stop_distance <= 0.0 or entry_price <= 0.0:
            return SizingResult(0.0, risk_amount, stop_distance, 0.0, 0.0, "invalid stop distance")
        raw_volume = risk_amount / (stop_distance * self.contract_size_oz)
        volume = self._floor_lot(raw_volume)
        max_notional = equity * self.max_notional_ratio
        if volume <= 0.0 or volume < self.min_lot:
            return SizingResult(
                0.0,
                risk_amount,
                stop_distance,
                0.0,
                max_notional,
                (
                    "no trade: risk-sized volume is below the broker minimum lot "
                    f"({self.min_lot:.2f}); cash-equivalent/no-leverage account cannot "
                    f"hold the minimum notional"
                ),
            )
        notional = volume * self.contract_size_oz * entry_price
        if notional > max_notional:
            capped = math.floor(
                (max_notional / (self.contract_size_oz * entry_price)) / self.lot_step
            ) * self.lot_step
            capped = round(capped, 8)
            if capped < self.min_lot:
                return SizingResult(
                    0.0,
                    risk_amount,
                    stop_distance,
                    0.0,
                    max_notional,
                    (
                        "no trade: minimum lot notional exceeds the cash-equivalent "
                        f"exposure cap ({max_notional:.2f})"
                    ),
                )
            volume = capped
            notional = volume * self.contract_size_oz * entry_price
        return SizingResult(
            volume=round(volume, 8),
            risk_amount=risk_amount,
            stop_distance=stop_distance,
            notional=notional,
            max_notional=max_notional,
            reason="sized",
        )

    def _floor_lot(self, volume: float) -> float:
        if self.lot_step <= 0:
            return volume
        return math.floor(volume / self.lot_step) * self.lot_step

    def _last_closed_bar(self, execution_bars: BarSeries, as_of: datetime):
        window = execution_bars.closed_window(as_of, self.execution_minutes)
        return window[-1] if window else None

    @staticmethod
    def _unrealized_r(position: PositionState, price: float) -> float:
        if position.stop_loss is None:
            return 0.0
        risk_distance = position.open_price - position.stop_loss
        if risk_distance <= 0:
            return 0.0
        return (price - position.open_price) / risk_distance

    def _decision(
        self,
        *,
        as_of: datetime,
        decision_type: str,
        side: str,
        confidence: float,
        fundamental: FundamentalView,
        technical: TechnicalView,
        fusion: FusionView,
        overseer: OverseerDecision,
        exit_decision,
        position_size: float | None,
        stop_loss: float | None,
        take_profit: float | None,
        reasons: list[str],
    ) -> StrategyDecision:
        return StrategyDecision(
            decision_id=f"{self.scope_id}:{as_of.isoformat()}",
            as_of_utc=as_of,
            decision_type=decision_type,
            side=side,
            confidence=confidence,
            combined_score=fusion.combined_score,
            fundamental=fundamental,
            technical=technical,
            fusion=fusion,
            overseer=overseer,
            exit_decision=exit_decision,
            position_size=position_size,
            stop_loss=stop_loss,
            take_profit=take_profit,
            reasons=reasons,
        )


def _timeframe_minutes(timeframe: str) -> int:
    mapping = {"M1": 1, "M5": 5, "M15": 15, "M30": 30, "H1": 60, "H4": 240, "D1": 1440}
    return mapping.get(timeframe.upper(), 15)
