"""Event-driven M15/H4 backtest harness.

This is the M2 harness: next-bar-open discretionary fills, intrabar stop-first
handling for protective orders, point-in-time macro snapshots, and explicit
costs.  It is intentionally small; walk-forward and OOS splitting are M3 work.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from .contracts import (
    Bar,
    PositionState,
    RiskState,
    StrategyDecision,
    Trade,
    ensure_utc,
)
from .data import BarSeries, PointInTimeStore
from .manifest import as_dict, manifest_hash
from .strategy import StrategyEngine


@dataclass
class BacktestResult:
    run_id: str
    scope_id: str
    manifest_hash: str
    started_at_utc: datetime
    finished_at_utc: datetime
    initial_equity: float
    final_equity: float
    trades: list[Trade]
    decisions: list[StrategyDecision]
    metrics: dict[str, float | int]
    data_quality: dict[str, Any] = field(default_factory=dict)
    account_profile: str = "base"
    account_profile_description: str = ""
    high_risk_override: bool = False
    promotion_eligible: bool = True
    risk_summary: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
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
            "data_quality": self.data_quality,
            "account_profile": self.account_profile,
            "account_profile_description": self.account_profile_description,
            "high_risk_override": self.high_risk_override,
            "promotion_eligible": self.promotion_eligible,
            "risk_summary": self.risk_summary,
        }


class BacktestRunner:
    def __init__(
        self,
        *,
        manifest: dict[str, Any],
        execution_bars: BarSeries,
        context_bars: BarSeries,
        macro_store: PointInTimeStore,
        initial_equity: float | None = None,
        record_decisions: bool = True,
    ) -> None:
        self.manifest = manifest
        self.strategy = StrategyEngine(manifest)
        self.execution_bars = execution_bars
        self.context_bars = context_bars
        self.macro_store = macro_store
        self.initial_equity = float(
            initial_equity
            if initial_equity is not None
            else as_dict(as_dict(manifest.get("strategy")).get("sizing")).get("initial_equity", 10000.0)
        )
        self.record_decisions = record_decisions
        self.execution_minutes = _timeframe_minutes(
            str(as_dict(manifest.get("market")).get("execution_bar", "M15"))
        )
        self.context_minutes = _timeframe_minutes(
            str(as_dict(manifest.get("market")).get("context_bar", "H4"))
        )
        backtest_config = as_dict(as_dict(manifest.get("strategy")).get("backtest"))
        self.point_value = float(backtest_config.get("point_value", 0.01))
        self.commission_per_lot = float(backtest_config.get("commission_per_lot", 0.0))
        self.slippage_points = float(backtest_config.get("slippage_points", 0.0))
        self.contract_size_oz = float(
            as_dict(as_dict(manifest.get("strategy")).get("sizing")).get("contract_size_oz", 100.0)
        )

    def run(self) -> BacktestResult:
        started = ensure_utc(self.execution_bars.bars[0].time_utc) if self.execution_bars.bars else datetime.now(timezone.utc)
        finished = ensure_utc(self.execution_bars.bars[-1].time_utc) if self.execution_bars.bars else started
        run_id = hashlib.sha256(
            f"{self.manifest.get('scope_id')}:{manifest_hash(self.manifest)}:{started.isoformat()}".encode("utf-8")
        ).hexdigest()[:16]
        equity = self.initial_equity
        peak_equity = equity
        daily_start_equity = equity
        current_day = started.date()
        risk_state = RiskState(
            equity=equity,
            starting_equity=self.initial_equity,
            daily_start_equity=daily_start_equity,
            peak_equity=peak_equity,
        )
        position: PositionState | None = None
        entry_atr = 0.0
        trade_mae = 0.0
        trade_mfe = 0.0
        trades: list[Trade] = []
        decisions: list[StrategyDecision] = []

        for index, bar in enumerate(self.execution_bars.bars):
            day = bar.time_utc.date()
            if day != current_day:
                current_day = day
                daily_start_equity = equity
                risk_state.daily_start_equity = daily_start_equity
                risk_state.daily_halt = False
            decision_time = bar.time_utc + timedelta(minutes=self.execution_minutes)

            # Protective orders are checked before discretionary decisions.
            if position is not None:
                exit_price, exit_reason = self._protective_exit(position, bar)
                if exit_price is not None:
                    trade = self._close_trade(
                        position=position,
                        exit_time=bar.time_utc + timedelta(minutes=self.execution_minutes),
                        exit_price=exit_price,
                        exit_reason=exit_reason,
                        trade_mae=trade_mae,
                        trade_mfe=trade_mfe,
                    )
                    trades.append(trade)
                    equity += trade.pnl
                    position = None
                    risk_state.update(equity)
                    peak_equity = max(peak_equity, equity)

            if position is not None:
                position.bars_in_trade += 1
                if position.highest_price is None or bar.high > position.highest_price:
                    position.highest_price = bar.high
                trade_mae = min(trade_mae, bar.low - position.open_price)
                trade_mfe = max(trade_mfe, bar.high - position.open_price)

            if position is None:
                mark_equity = equity
            else:
                mark_equity = equity + (bar.close - position.open_price) * position.volume * self.contract_size_oz
            risk_state.update(mark_equity)
            risk_state.peak_equity = max(risk_state.peak_equity, mark_equity)
            if risk_state.daily_pnl <= -risk_state.daily_start_equity * float(
                as_dict(self.manifest.get("risk")).get("daily_loss_halt_pct", 1.5)
            ) / 100.0:
                risk_state.daily_halt = True
            if risk_state.current_drawdown_pct >= float(
                as_dict(self.manifest.get("risk")).get("max_drawdown_flatten_pct", 8.0)
            ):
                risk_state.max_drawdown_halt = True

            decision = self.strategy.decide(
                execution_bars=self.execution_bars,
                context_bars=self.context_bars,
                macro_store=self.macro_store,
                as_of_utc=decision_time,
                equity=equity,
                position=position,
                risk_state=risk_state,
            )
            if self.record_decisions:
                decisions.append(decision)

            if decision.decision_type == "ENTER_LONG" and position is None and index + 1 < len(self.execution_bars.bars):
                next_bar = self.execution_bars.bars[index + 1]
                fill_price = self._entry_fill(next_bar.open, next_bar.spread_points)
                atr_value = float(decision.technical.indicators.get("atr", 0.0))
                position = PositionState(
                    position_id=f"pos-{len(trades) + 1:04d}",
                    side="LONG",
                    volume=float(decision.position_size or 0.0),
                    open_price=fill_price,
                    open_time_utc=next_bar.time_utc,
                    stop_loss=decision.stop_loss,
                    take_profit=decision.take_profit,
                    highest_price=fill_price,
                    entry_atr=atr_value,
                    entry_score=decision.combined_score,
                )
                entry_atr = atr_value
                trade_mae = 0.0
                trade_mfe = 0.0
            elif decision.decision_type == "EXIT" and position is not None and index + 1 < len(self.execution_bars.bars):
                next_bar = self.execution_bars.bars[index + 1]
                fill_price = self._exit_fill(next_bar.open, next_bar.spread_points)
                trade = self._close_trade(
                    position=position,
                    exit_time=next_bar.time_utc,
                    exit_price=fill_price,
                    exit_reason=decision.exit_decision.reason if decision.exit_decision else "strategy_exit",
                    trade_mae=trade_mae,
                    trade_mfe=trade_mfe,
                )
                trades.append(trade)
                equity += trade.pnl
                position = None
                risk_state.update(equity)
                peak_equity = max(peak_equity, equity)

        if position is not None and self.execution_bars.bars:
            last = self.execution_bars.bars[-1]
            trade = self._close_trade(
                position=position,
                exit_time=last.time_utc + timedelta(minutes=self.execution_minutes),
                exit_price=last.close,
                exit_reason="end_of_data",
                trade_mae=trade_mae,
                trade_mfe=trade_mfe,
            )
            trades.append(trade)
            equity += trade.pnl
            position = None

        metrics = _metrics(
            self.initial_equity,
            equity,
            trades,
            decisions,
            contract_size_oz=self.contract_size_oz,
        )
        market = as_dict(self.manifest.get("market"))
        risk_config = as_dict(self.manifest.get("risk"))
        sizing_config = as_dict(as_dict(self.manifest.get("strategy")).get("sizing"))
        risk_summary = {
            "account_model": market.get("account_model"),
            "leverage_cap": float(risk_config.get("leverage_cap", 1.0)),
            "max_notional_exposure_ratio": float(
                market.get("max_notional_exposure_ratio", 1.0)
            ),
            "per_trade_equity_pct": float(risk_config.get("per_trade_equity_pct", 0.25)),
            "min_lot_risk_cap_pct": float(
                sizing_config.get("min_lot_risk_cap_pct", 0.0)
            ),
            "daily_loss_halt_pct": float(risk_config.get("daily_loss_halt_pct", 1.5)),
            "max_drawdown_flatten_pct": float(
                risk_config.get("max_drawdown_flatten_pct", 8.0)
            ),
            "contract_size_oz": float(sizing_config.get("contract_size_oz", 100.0)),
            "min_lot": float(sizing_config.get("min_lot", 0.01)),
        }
        return BacktestResult(
            run_id=run_id,
            scope_id=str(self.manifest.get("scope_id", "unknown")),
            manifest_hash=manifest_hash(self.manifest),
            started_at_utc=started,
            finished_at_utc=finished,
            initial_equity=self.initial_equity,
            final_equity=equity,
            trades=trades,
            decisions=decisions,
            metrics=metrics,
            data_quality={
                "execution_bars": len(self.execution_bars),
                "context_bars": len(self.context_bars),
                "macro_series": len(self.macro_store.series_ids()),
                "entry_atr_last": entry_atr,
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
            risk_summary=risk_summary,
        )

    def _protective_exit(self, position: PositionState, bar: Bar) -> tuple[float | None, str | None]:
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

    def _close_trade(
        self,
        *,
        position: PositionState,
        exit_time: datetime,
        exit_price: float,
        exit_reason: str,
        trade_mae: float,
        trade_mfe: float,
    ) -> Trade:
        gross = (exit_price - position.open_price) * position.volume * self.contract_size_oz
        commission = position.volume * self.commission_per_lot * 2.0
        pnl = gross - commission
        return Trade(
            trade_id=position.position_id,
            side=position.side,
            volume=position.volume,
            entry_time_utc=position.open_time_utc or exit_time,
            entry_price=position.open_price,
            exit_time_utc=exit_time,
            exit_price=exit_price,
            exit_reason=exit_reason,
            pnl=pnl,
            mae=trade_mae,
            mfe=trade_mfe,
        )


def _timeframe_minutes(timeframe: str) -> int:
    mapping = {"M1": 1, "M5": 5, "M15": 15, "M30": 30, "H1": 60, "H4": 240, "D1": 1440}
    return mapping.get(timeframe.upper(), 15)


def _metrics(
    initial_equity: float,
    final_equity: float,
    trades: list[Trade],
    decisions: list[StrategyDecision],
    *,
    contract_size_oz: float = 100.0,
) -> dict[str, float | int | None]:
    wins = [trade.pnl for trade in trades if trade.pnl > 0]
    losses = [trade.pnl for trade in trades if trade.pnl < 0]
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    peak = initial_equity
    max_drawdown = 0.0
    equity = initial_equity
    entry_notionals: list[float] = []
    daily_pnl: dict[Any, float] = {}
    for trade in trades:
        entry_notionals.append(trade.entry_price * trade.volume * contract_size_oz)
        exit_time = trade.exit_time_utc or trade.entry_time_utc
        day = exit_time.date()
        daily_pnl[day] = daily_pnl.get(day, 0.0) + trade.pnl
        equity += trade.pnl
        peak = max(peak, equity)
        if peak > 0:
            max_drawdown = max(max_drawdown, (peak - equity) / peak * 100.0)

    daily_returns: list[float] = []
    equity_for_returns = initial_equity
    for day in sorted(daily_pnl):
        pnl = daily_pnl[day]
        if equity_for_returns > 0:
            daily_returns.append(pnl / equity_for_returns)
        equity_for_returns += pnl

    sharpe_daily = None
    sortino_daily = None
    # Do not annualize a handful of trades into a misleading Sharpe/Sortino.
    if len(daily_returns) >= 20:
        mean_return = sum(daily_returns) / len(daily_returns)
        variance = sum((value - mean_return) ** 2 for value in daily_returns) / (
            len(daily_returns) - 1
        )
        std_dev = math.sqrt(variance)
        if std_dev > 0:
            sharpe_daily = mean_return / std_dev * math.sqrt(252.0)
        downside = [value for value in daily_returns if value < 0]
        if downside:
            downside_dev = math.sqrt(sum(value * value for value in downside) / len(downside))
            if downside_dev > 0:
                sortino_daily = mean_return / downside_dev * math.sqrt(252.0)

    average_entry_notional = (
        sum(entry_notionals) / len(entry_notionals) if entry_notionals else 0.0
    )
    max_entry_notional = max(entry_notionals) if entry_notionals else 0.0
    return {
        "total_return_pct": (final_equity / initial_equity - 1.0) * 100.0 if initial_equity else 0.0,
        "max_drawdown_pct": max_drawdown,
        "trade_count": len(trades),
        "win_count": len(wins),
        "loss_count": len(losses),
        "win_rate_pct": (len(wins) / len(trades) * 100.0) if trades else 0.0,
        "gross_profit": gross_profit,
        "gross_loss": gross_loss,
        "profit_factor": (gross_profit / gross_loss) if gross_loss > 0 else None,
        "expectancy": (sum(trade.pnl for trade in trades) / len(trades)) if trades else 0.0,
        "sharpe_daily_annualized": sharpe_daily,
        "sortino_daily_annualized": sortino_daily,
        "daily_return_count": len(daily_returns),
        "average_entry_notional": average_entry_notional,
        "max_entry_notional": max_entry_notional,
        "average_entry_exposure_pct": (
            average_entry_notional / initial_equity * 100.0 if initial_equity else 0.0
        ),
        "max_entry_exposure_pct": (
            max_entry_notional / initial_equity * 100.0 if initial_equity else 0.0
        ),
        "decision_count": len(decisions),
        "entry_decisions": sum(1 for decision in decisions if decision.decision_type == "ENTER_LONG"),
        "exit_decisions": sum(1 for decision in decisions if decision.decision_type == "EXIT"),
    }
