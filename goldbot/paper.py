"""Deterministic paper/dry-run execution for M3.

This module is deliberately not a broker router.  It replays closed M15 bars
through the real strategy and records every decision, paper order, paper fill,
and paper position in the SQLite ledger.  No ``order_send`` call exists here.

The paper fill model is a market order at the close of the bar that produced the
decision, plus half-spread and configured slippage.  Protective stops and
take-profits are checked against the next bar's high/low.  This is an execution
and logging exercise, not profitability evidence.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Iterable

from .backtest import compute_metrics, risk_summary_from_manifest
from .contracts import (
    Bar,
    ExitDecision,
    OverseerDecision,
    PositionState,
    RiskState,
    StrategyDecision,
    Trade,
    ensure_utc,
    utc_now,
)
from .data import BarSeries, PointInTimeStore
from .ledger import Ledger
from .manifest import as_dict, manifest_hash
from .strategy import StrategyEngine


@dataclass
class PaperRunResult:
    run_id: str
    scope_id: str
    manifest_hash: str
    started_at_utc: datetime
    finished_at_utc: datetime
    initial_equity: float
    final_equity: float
    trades: list[Trade]
    decisions: list[StrategyDecision]
    metrics: dict[str, float | int | None]
    orders: list[dict[str, Any]] = field(default_factory=list)
    fills: list[dict[str, Any]] = field(default_factory=list)
    positions: list[dict[str, Any]] = field(default_factory=list)
    data_quality: dict[str, Any] = field(default_factory=dict)
    account_profile: str = "base"
    account_profile_description: str = ""
    high_risk_override: bool = False
    promotion_eligible: bool = True
    risk_summary: dict[str, Any] = field(default_factory=dict)
    mode: str = "paper_replay"
    ledger_path: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "mode": self.mode,
            "scope_id": self.scope_id,
            "manifest_hash": self.manifest_hash,
            "started_at_utc": ensure_utc(self.started_at_utc).isoformat(),
            "finished_at_utc": ensure_utc(self.finished_at_utc).isoformat(),
            "initial_equity": self.initial_equity,
            "final_equity": self.final_equity,
            "metrics": self.metrics,
            "trades": [trade.to_dict() for trade in self.trades],
            "decision_count": len(self.decisions),
            "decisions": [decision.to_dict() for decision in self.decisions],
            "orders": self.orders,
            "fills": self.fills,
            "positions": self.positions,
            "data_quality": self.data_quality,
            "account_profile": self.account_profile,
            "account_profile_description": self.account_profile_description,
            "high_risk_override": self.high_risk_override,
            "promotion_eligible": self.promotion_eligible,
            "risk_summary": self.risk_summary,
            "ledger_path": self.ledger_path,
        }


class PaperRuntime:
    """Replay bars through the strategy and record paper executions."""

    def __init__(
        self,
        *,
        manifest: dict[str, Any],
        ledger: Ledger,
        execution_bars: BarSeries,
        context_bars: BarSeries,
        macro_store: PointInTimeStore,
        initial_equity: float | None = None,
        run_id: str | None = None,
    ) -> None:
        self.manifest = manifest
        self.ledger = ledger
        self.execution_bars = execution_bars
        self.context_bars = context_bars
        self.macro_store = macro_store
        self.strategy = StrategyEngine(manifest)
        self.symbol = str(as_dict(manifest.get("market")).get("symbol", "XAUUSD"))
        self.execution_bar = str(as_dict(manifest.get("market")).get("execution_bar", "M15"))
        self.context_bar = str(as_dict(manifest.get("market")).get("context_bar", "H4"))
        self.execution_minutes = _timeframe_minutes(self.execution_bar)
        self.scope_id = str(manifest.get("scope_id", "unknown"))
        self.scope_version = int(manifest.get("scope_version", 1))
        self.manifest_hash = manifest_hash(manifest)
        self.initial_equity = float(
            initial_equity
            if initial_equity is not None
            else as_dict(as_dict(manifest.get("strategy")).get("sizing")).get(
                "initial_equity", 10000.0
            )
        )
        self.run_id = run_id or hashlib.sha256(
            f"{self.scope_id}:{self.manifest_hash}:{utc_now().isoformat()}:paper".encode("utf-8")
        ).hexdigest()[:16]
        backtest_config = as_dict(as_dict(manifest.get("strategy")).get("backtest"))
        self.point_value = float(backtest_config.get("point_value", 0.01))
        self.commission_per_lot = float(backtest_config.get("commission_per_lot", 0.0))
        self.slippage_points = float(backtest_config.get("slippage_points", 0.0))
        self.contract_size_oz = float(
            as_dict(as_dict(manifest.get("strategy")).get("sizing")).get(
                "contract_size_oz", 100.0
            )
        )

        self.equity = self.initial_equity
        self.risk_state = RiskState(
            equity=self.equity,
            starting_equity=self.initial_equity,
            daily_start_equity=self.equity,
            peak_equity=self.equity,
        )
        self.position: PositionState | None = None
        self.positions_by_id: dict[str, dict[str, Any]] = {}
        self.trades: list[Trade] = []
        self.decisions: list[StrategyDecision] = []
        self.orders: list[dict[str, Any]] = []
        self.fills: list[dict[str, Any]] = []
        self.last_as_of: datetime | None = None
        self.last_bar: Bar | None = None
        self.current_day = None
        self.trade_mae = 0.0
        self.trade_mfe = 0.0
        self.started_at_utc: datetime | None = None
        self.finished_at_utc: datetime | None = None
        self._finished = False

    # ------------------------------------------------------------------
    # Public run API
    # ------------------------------------------------------------------
    def run_replay(self, as_of_times: Iterable[datetime]) -> PaperRunResult:
        """Process each supplied decision time once, in ascending order."""
        if self._finished:
            raise RuntimeError("this PaperRuntime has already run")
        times = sorted({ensure_utc(value) for value in as_of_times})
        if not times:
            raise ValueError("paper replay needs at least one as-of time")
        self.started_at_utc = times[0]
        self.ledger.start_run(
            run_id=self.run_id,
            scope_id=self.scope_id,
            manifest_hash=self.manifest_hash,
            started_at_utc=self.started_at_utc,
            summary={
                "mode": "paper_replay",
                "symbol": self.symbol,
                "initial_equity": self.initial_equity,
                "account_profile": str(self.manifest.get("active_account_profile", "base")),
                "high_risk_override": bool(
                    self.manifest.get("profile_high_risk_override", False)
                ),
                "promotion_eligible": bool(
                    self.manifest.get("profile_promotion_eligible", True)
                ),
                "ledger_path": str(self.ledger.path),
            },
        )
        self.ledger.log_event(
            level="INFO",
            component="paper_runtime",
            event_type="run_started",
            message=f"paper replay started with {len(times)} decision times",
            correlation_id=self.run_id,
            payload={
                "mode": "paper_replay",
                "initial_equity": self.initial_equity,
                "symbol": self.symbol,
                "ledger_path": str(self.ledger.path),
            },
            logged_at_utc=self.started_at_utc,
        )
        for as_of in times:
            self.process_bar(as_of)
        self._close_open_position_at_end()
        self.finished_at_utc = self.last_as_of or self.started_at_utc
        metrics = compute_metrics(
            self.initial_equity,
            self.equity,
            self.trades,
            self.decisions,
            contract_size_oz=self.contract_size_oz,
        )
        result = PaperRunResult(
            run_id=self.run_id,
            scope_id=self.scope_id,
            manifest_hash=self.manifest_hash,
            started_at_utc=self.started_at_utc,
            finished_at_utc=self.finished_at_utc,
            initial_equity=self.initial_equity,
            final_equity=self.equity,
            trades=self.trades,
            decisions=self.decisions,
            metrics=metrics,
            orders=self.orders,
            fills=self.fills,
            positions=list(self.positions_by_id.values()),
            data_quality={
                "mode": "paper_replay",
                "execution_bars": len(self.execution_bars),
                "context_bars": len(self.context_bars),
                "macro_series": len(self.macro_store.series_ids()),
                "replay_decisions": len(times),
                "fill_model": "market at decision-bar close plus half-spread and slippage",
                "protective_exit_model": "next-bar high/low, stop first",
                "ledger_path": str(self.ledger.path),
            },
            account_profile=str(self.manifest.get("active_account_profile", "base")),
            account_profile_description=str(
                self.manifest.get("account_profile_description", "")
            ),
            high_risk_override=bool(
                self.manifest.get("profile_high_risk_override", False)
            ),
            promotion_eligible=bool(
                self.manifest.get("profile_promotion_eligible", True)
            ),
            risk_summary=risk_summary_from_manifest(self.manifest),
            mode="paper_replay",
            ledger_path=str(self.ledger.path),
        )
        self.ledger.finish_run(
            run_id=self.run_id,
            finished_at_utc=self.finished_at_utc,
            summary={
                "mode": "paper_replay",
                "initial_equity": self.initial_equity,
                "final_equity": self.equity,
                "trade_count": len(self.trades),
                "decision_count": len(self.decisions),
                "order_count": len(self.orders),
                "fill_count": len(self.fills),
                "metrics": metrics,
                "ledger_path": str(self.ledger.path),
            },
        )
        self.ledger.log_event(
            level="INFO",
            component="paper_runtime",
            event_type="run_finished",
            message=(
                f"paper replay finished: {len(self.decisions)} decisions, "
                f"{len(self.trades)} closed paper trades, final equity {self.equity:.2f}"
            ),
            correlation_id=self.run_id,
            payload={
                "final_equity": self.equity,
                "trade_count": len(self.trades),
                "decision_count": len(self.decisions),
                "order_count": len(self.orders),
            },
            logged_at_utc=self.finished_at_utc,
        )
        self._finished = True
        return result

    # ------------------------------------------------------------------
    # Per-bar processing
    # ------------------------------------------------------------------
    def process_bar(self, as_of_utc: datetime) -> StrategyDecision | None:
        as_of = ensure_utc(as_of_utc)
        if self.last_as_of is not None and as_of <= self.last_as_of:
            return None
        window = self.execution_bars.closed_window(as_of, self.execution_minutes)
        if not window:
            raise ValueError(f"no closed execution bar at {as_of.isoformat()}")
        bar = window[-1]
        self.last_bar = bar
        day = bar.time_utc.date()
        if self.current_day is None:
            self.current_day = day
        elif day != self.current_day:
            self.current_day = day
            self.risk_state.start_new_day()

        # Protective orders are checked before discretionary decisions.
        if self.position is not None:
            exit_price, exit_reason = self._protective_exit(self.position, bar)
            if exit_price is not None:
                exit_time = bar.time_utc + timedelta(minutes=self.execution_minutes)
                stop_hit = exit_reason == "hard_or_trailing_stop"
                protective_decision = self._record_synthetic_exit_decision(
                    bar=bar,
                    exit_time=exit_time,
                    exit_price=exit_price,
                    exit_reason=exit_reason,
                    rule_id=exit_reason,
                    priority=90 if stop_hit else 40,
                    risk_veto_immune=stop_hit,
                    verdict="protective_exit",
                )
                self._close_position(
                    exit_time=exit_time,
                    exit_price=exit_price,
                    exit_reason=exit_reason,
                    decision_id=protective_decision.decision_id,
                )

        if self.position is not None:
            self.position.bars_in_trade += 1
            if self.position.highest_price is None or bar.high > self.position.highest_price:
                self.position.highest_price = bar.high
            self.trade_mae = min(self.trade_mae, bar.low - self.position.open_price)
            self.trade_mfe = max(self.trade_mfe, bar.high - self.position.open_price)

        if self.position is None:
            mark_equity = self.equity
        else:
            mark_equity = self.equity + (
                bar.close - self.position.open_price
            ) * self.position.volume * self.contract_size_oz
        self.risk_state.update(mark_equity)
        self.risk_state.peak_equity = max(self.risk_state.peak_equity, mark_equity)
        risk_config = as_dict(self.manifest.get("risk"))
        if self.risk_state.daily_pnl <= -self.risk_state.daily_start_equity * float(
            risk_config.get("daily_loss_halt_pct", 1.5)
        ) / 100.0:
            self.risk_state.daily_halt = True
        if self.risk_state.current_drawdown_pct >= float(
            risk_config.get("max_drawdown_flatten_pct", 8.0)
        ):
            self.risk_state.max_drawdown_halt = True

        decision = self.strategy.decide(
            execution_bars=self.execution_bars,
            context_bars=self.context_bars,
            macro_store=self.macro_store,
            as_of_utc=as_of,
            equity=mark_equity,
            position=self.position,
            risk_state=self.risk_state,
        )
        self.decisions.append(decision)
        self.ledger.record_decision(
            decision,
            scope_id=self.scope_id,
            scope_version=self.scope_version,
            symbol=self.symbol,
            execution_bar=self.execution_bar,
            context_bar=self.context_bar,
            market={
                "symbol": self.symbol,
                "as_of_utc": as_of.isoformat(),
                "bar_open_utc": bar.time_utc.isoformat(),
                "open": bar.open,
                "high": bar.high,
                "low": bar.low,
                "close": bar.close,
                "spread_points": bar.spread_points,
                "equity": mark_equity,
                "position": self.position.to_dict() if self.position else None,
                "risk_state": self.risk_state.to_dict(),
            },
        )

        if decision.decision_type == "ENTER_LONG" and self.position is None:
            self._open_paper_position(decision=decision, bar=bar, as_of=as_of)
        elif decision.decision_type == "EXIT" and self.position is not None:
            exit_price = self._exit_fill(bar.close, bar.spread_points)
            self._record_exit_order_and_fill(
                decision=decision,
                price=bar.close,
                fill_price=exit_price,
                as_of=as_of,
            )
            self._close_position(
                exit_time=as_of,
                exit_price=exit_price,
                exit_reason=(
                    decision.exit_decision.reason
                    if decision.exit_decision and decision.exit_decision.reason
                    else "strategy_exit"
                ),
                decision_id=decision.decision_id,
            )
        self.last_as_of = as_of
        return decision

    # ------------------------------------------------------------------
    # Paper execution
    # ------------------------------------------------------------------
    def _open_paper_position(
        self,
        *,
        decision: StrategyDecision,
        bar: Bar,
        as_of: datetime,
    ) -> None:
        volume = float(decision.position_size or 0.0)
        if volume <= 0.0:
            self.ledger.log_event(
                level="WARNING",
                component="paper_runtime",
                event_type="entry_rejected",
                message="strategy returned ENTER_LONG with non-positive volume",
                correlation_id=self.run_id,
                payload={"decision_id": decision.decision_id, "volume": volume},
                logged_at_utc=as_of,
            )
            return
        fill_price = self._entry_fill(bar.close, bar.spread_points)
        order_id = f"ord-{decision.decision_id}"
        idempotency_key = f"{decision.decision_id}:ENTRY"
        order = {
            "order_id": order_id,
            "decision_id": decision.decision_id,
            "submitted_at_utc": as_of.isoformat(),
            "symbol": self.symbol,
            "side": "LONG",
            "order_type": "MARKET",
            "volume": volume,
            "price": bar.close,
            "stop_loss": decision.stop_loss,
            "take_profit": decision.take_profit,
            "state": "PAPER_FILLED",
            "idempotency_key": idempotency_key,
        }
        commission = volume * self.commission_per_lot
        fill_id = f"fill-{order_id}"
        fill = {
            "fill_id": fill_id,
            "order_id": order_id,
            "filled_at_utc": as_of.isoformat(),
            "symbol": self.symbol,
            "side": "LONG",
            "volume": volume,
            "price": fill_price,
            "commission": commission,
            "swap": 0.0,
            "slippage_points": self.slippage_points,
            "broker_deal_ticket": None,
        }
        self.ledger.record_order(
            order_id=order_id,
            decision_id=decision.decision_id,
            submitted_at_utc=as_of,
            symbol=self.symbol,
            side="LONG",
            order_type="MARKET",
            volume=volume,
            price=bar.close,
            stop_loss=decision.stop_loss,
            take_profit=decision.take_profit,
            state="PAPER_FILLED",
            idempotency_key=idempotency_key,
            raw_request={
                "mode": "paper_replay",
                "decision_id": decision.decision_id,
                "price": bar.close,
                "spread_points": bar.spread_points,
            },
            raw_response={"state": "PAPER_FILLED", "fill_price": fill_price},
            comment="paper replay; no broker order was sent",
        )
        self.ledger.record_fill(
            fill_id=fill_id,
            order_id=order_id,
            filled_at_utc=as_of,
            symbol=self.symbol,
            side="LONG",
            volume=volume,
            price=fill_price,
            commission=commission,
            slippage_points=self.slippage_points,
            raw_deal={"mode": "paper_replay"},
        )
        position = PositionState(
            position_id=f"pos-{decision.decision_id}",
            side="LONG",
            volume=volume,
            open_price=fill_price,
            open_time_utc=as_of,
            stop_loss=decision.stop_loss,
            take_profit=decision.take_profit,
            highest_price=fill_price,
            entry_atr=float(decision.technical.indicators.get("atr", 0.0)),
            entry_score=decision.combined_score,
        )
        position_payload = {
            "position_id": position.position_id,
            "symbol": self.symbol,
            "side": position.side,
            "volume": position.volume,
            "open_price": position.open_price,
            "open_time_utc": as_of.isoformat(),
            "stop_loss": position.stop_loss,
            "take_profit": position.take_profit,
            "close_price": None,
            "close_time_utc": None,
            "close_reason": None,
            "realized_pnl": None,
            "mae": 0.0,
            "mfe": 0.0,
            "state": "PAPER_OPEN",
        }
        self.ledger.record_position(
            position_id=position.position_id,
            symbol=self.symbol,
            side=position.side,
            volume=position.volume,
            open_price=position.open_price,
            open_time_utc=as_of,
            stop_loss=position.stop_loss,
            take_profit=position.take_profit,
            state="PAPER_OPEN",
            raw_position=position.to_dict(),
        )
        self.orders.append(order)
        self.fills.append(fill)
        self.positions_by_id[position.position_id] = position_payload
        self.position = position
        self.trade_mae = 0.0
        self.trade_mfe = 0.0
        self.ledger.log_event(
            level="INFO",
            component="paper_runtime",
            event_type="paper_entry",
            message=(
                f"paper LONG {volume:.2f} lots at {fill_price:.2f}; "
                f"stop {decision.stop_loss}, take profit {decision.take_profit}"
            ),
            correlation_id=self.run_id,
            payload={
                "decision_id": decision.decision_id,
                "order_id": order_id,
                "fill_id": fill_id,
                "volume": volume,
                "fill_price": fill_price,
                "stop_loss": decision.stop_loss,
                "take_profit": decision.take_profit,
            },
            logged_at_utc=as_of,
        )

    def _record_synthetic_exit_decision(
        self,
        *,
        bar: Bar,
        exit_time: datetime,
        exit_price: float,
        exit_reason: str,
        rule_id: str,
        priority: int,
        risk_veto_immune: bool,
        verdict: str,
    ) -> StrategyDecision:
        """Record a synthetic EXIT decision for a non-discretionary order event.

        A protective stop/take-profit or an end-of-replay close is an order
        event, not a fresh strategy evaluation.  We still persist an explicit
        EXIT decision so the order, fill, and position close have a complete
        audit chain.
        """
        last = self.decisions[-1] if self.decisions else None
        if last is None:
            raise RuntimeError(f"{verdict} without a prior strategy decision")
        exit_decision = ExitDecision(
            as_of_utc=exit_time,
            should_exit=True,
            rule_id=rule_id,
            priority=priority,
            action="close",
            reason=exit_reason,
            candidates=[],
            risk_veto_immune=risk_veto_immune,
        )
        overseer = OverseerDecision(
            as_of_utc=exit_time,
            decision="EXIT",
            side="LONG",
            confidence=1.0 if risk_veto_immune else 0.8,
            verdict=verdict,
            vetoes=[],
            rationale=f"{verdict}: {exit_reason}",
            source_views=last.overseer.source_views,
        )
        decision = StrategyDecision(
            decision_id=f"{self.scope_id}:{exit_time.isoformat()}:{verdict}",
            as_of_utc=exit_time,
            decision_type="EXIT",
            side="LONG",
            confidence=1.0 if risk_veto_immune else 0.8,
            combined_score=last.combined_score,
            fundamental=last.fundamental,
            technical=last.technical,
            fusion=last.fusion,
            overseer=overseer,
            exit_decision=exit_decision,
            position_size=None,
            stop_loss=None,
            take_profit=None,
            reasons=[f"{verdict}: {exit_reason}"],
        )
        self.decisions.append(decision)
        self.ledger.record_decision(
            decision,
            scope_id=self.scope_id,
            scope_version=self.scope_version,
            symbol=self.symbol,
            execution_bar=self.execution_bar,
            context_bar=self.context_bar,
            market={
                "symbol": self.symbol,
                "as_of_utc": exit_time.isoformat(),
                "bar_open_utc": bar.time_utc.isoformat(),
                "open": bar.open,
                "high": bar.high,
                "low": bar.low,
                "close": bar.close,
                "spread_points": bar.spread_points,
                "synthetic_exit_reason": exit_reason,
                "synthetic_exit_price": exit_price,
                "position": self.position.to_dict() if self.position else None,
            },
        )
        self._record_exit_order_and_fill(
            decision=decision,
            price=exit_price,
            fill_price=exit_price,
            as_of=exit_time,
        )
        return decision

    def _record_exit_order_and_fill(
        self,
        *,
        decision: StrategyDecision,
        price: float,
        fill_price: float,
        as_of: datetime,
    ) -> None:
        if self.position is None:
            return
        order_id = f"ord-{decision.decision_id}-exit"
        idempotency_key = f"{decision.decision_id}:EXIT"
        volume = self.position.volume
        commission = volume * self.commission_per_lot
        order = {
            "order_id": order_id,
            "decision_id": decision.decision_id,
            "submitted_at_utc": as_of.isoformat(),
            "symbol": self.symbol,
            "side": "LONG",
            "order_type": "MARKET",
            "volume": volume,
            "price": price,
            "stop_loss": None,
            "take_profit": None,
            "state": "PAPER_FILLED",
            "idempotency_key": idempotency_key,
        }
        fill_id = f"fill-{order_id}"
        fill = {
            "fill_id": fill_id,
            "order_id": order_id,
            "filled_at_utc": as_of.isoformat(),
            "symbol": self.symbol,
            "side": "LONG",
            "volume": volume,
            "price": fill_price,
            "commission": commission,
            "swap": 0.0,
            "slippage_points": self.slippage_points,
            "broker_deal_ticket": None,
        }
        self.ledger.record_order(
            order_id=order_id,
            decision_id=decision.decision_id,
            submitted_at_utc=as_of,
            symbol=self.symbol,
            side="LONG",
            order_type="MARKET",
            volume=volume,
            price=price,
            stop_loss=None,
            take_profit=None,
            state="PAPER_FILLED",
            idempotency_key=idempotency_key,
            raw_request={
                "mode": "paper_replay",
                "decision_id": decision.decision_id,
                "price": price,
            },
            raw_response={"state": "PAPER_FILLED", "fill_price": fill_price},
            comment="paper replay exit; no broker order was sent",
        )
        self.ledger.record_fill(
            fill_id=fill_id,
            order_id=order_id,
            filled_at_utc=as_of,
            symbol=self.symbol,
            side="LONG",
            volume=volume,
            price=fill_price,
            commission=commission,
            slippage_points=self.slippage_points,
            raw_deal={"mode": "paper_replay"},
        )
        self.orders.append(order)
        self.fills.append(fill)

    def _close_position(
        self,
        *,
        exit_time: datetime,
        exit_price: float,
        exit_reason: str,
        decision_id: str | None,
    ) -> Trade | None:
        if self.position is None:
            return None
        position = self.position
        gross = (
            exit_price - position.open_price
        ) * position.volume * self.contract_size_oz
        commission = position.volume * self.commission_per_lot * 2.0
        pnl = gross - commission
        trade = Trade(
            trade_id=position.position_id,
            side=position.side,
            volume=position.volume,
            entry_time_utc=position.open_time_utc or exit_time,
            entry_price=position.open_price,
            exit_time_utc=exit_time,
            exit_price=exit_price,
            exit_reason=exit_reason,
            pnl=pnl,
            mae=self.trade_mae,
            mfe=self.trade_mfe,
        )
        self.trades.append(trade)
        self.equity += pnl
        self.risk_state.update(self.equity)
        self.risk_state.peak_equity = max(self.risk_state.peak_equity, self.equity)
        position_payload = self.positions_by_id.get(position.position_id, {})
        position_payload.update(
            {
                "close_price": exit_price,
                "close_time_utc": ensure_utc(exit_time).isoformat(),
                "close_reason": exit_reason,
                "realized_pnl": pnl,
                "mae": self.trade_mae,
                "mfe": self.trade_mfe,
                "state": "PAPER_CLOSED",
            }
        )
        self.positions_by_id[position.position_id] = position_payload
        self.ledger.record_position(
            position_id=position.position_id,
            symbol=self.symbol,
            side=position.side,
            volume=position.volume,
            open_price=position.open_price,
            open_time_utc=position.open_time_utc or exit_time,
            stop_loss=position.stop_loss,
            take_profit=position.take_profit,
            close_price=exit_price,
            close_time_utc=exit_time,
            close_reason=exit_reason,
            realized_pnl=pnl,
            mae=self.trade_mae,
            mfe=self.trade_mfe,
            state="PAPER_CLOSED",
            raw_position=position.to_dict(),
        )
        self.ledger.log_event(
            level="INFO",
            component="paper_runtime",
            event_type="paper_exit",
            message=(
                f"paper closed {position.position_id} at {exit_price:.2f}; "
                f"pnl {pnl:.2f}; reason {exit_reason}"
            ),
            correlation_id=self.run_id,
            payload={
                "position_id": position.position_id,
                "decision_id": decision_id,
                "exit_price": exit_price,
                "exit_reason": exit_reason,
                "pnl": pnl,
                "equity": self.equity,
            },
            logged_at_utc=exit_time,
        )
        self.position = None
        self.trade_mae = 0.0
        self.trade_mfe = 0.0
        return trade

    def _close_open_position_at_end(self) -> None:
        if self.position is None or self.last_bar is None:
            return
        bar = self.last_bar
        exit_price = self._exit_fill(bar.close, bar.spread_points)
        exit_time = bar.time_utc + timedelta(minutes=self.execution_minutes)
        decision = self._record_synthetic_exit_decision(
            bar=bar,
            exit_time=exit_time,
            exit_price=exit_price,
            exit_reason="end_of_replay",
            rule_id="end_of_replay",
            priority=0,
            risk_veto_immune=False,
            verdict="end_of_replay",
        )
        self._close_position(
            exit_time=exit_time,
            exit_price=exit_price,
            exit_reason="end_of_replay",
            decision_id=decision.decision_id,
        )

    # ------------------------------------------------------------------
    # Fill/exit helpers (mirror the M2 backtest conventions)
    # ------------------------------------------------------------------
    def _protective_exit(
        self, position: PositionState, bar: Bar
    ) -> tuple[float | None, str | None]:
        if position.stop_loss is not None and bar.low <= position.stop_loss:
            return min(bar.open, position.stop_loss), "hard_or_trailing_stop"
        if position.take_profit is not None and bar.high >= position.take_profit:
            return max(bar.open, position.take_profit), "profit_take"
        return None, None

    def _entry_fill(self, price: float, spread_points: float) -> float:
        half_spread = spread_points * self.point_value / 2.0
        slippage = self.slippage_points * self.point_value
        return price + half_spread + slippage

    def _exit_fill(self, price: float, spread_points: float) -> float:
        half_spread = spread_points * self.point_value / 2.0
        slippage = self.slippage_points * self.point_value
        return price - half_spread - slippage


def _timeframe_minutes(timeframe: str) -> int:
    mapping = {"M1": 1, "M5": 5, "M15": 15, "M30": 30, "H1": 60, "H4": 240, "D1": 1440}
    return mapping.get(timeframe.upper(), 15)


def replay_times_from_bars(
    execution_bars: BarSeries,
    *,
    replay_bars: int,
    execution_minutes: int = 15,
) -> list[datetime]:
    """Return decision times for the last ``replay_bars`` closed bars."""
    if replay_bars <= 0:
        if not execution_bars.bars:
            return []
        last = execution_bars.bars[-1]
        return [last.time_utc + timedelta(minutes=execution_minutes)]
    bars = execution_bars.bars[-replay_bars:]
    return [
        bar.time_utc + timedelta(minutes=execution_minutes)
        for bar in bars
    ]
