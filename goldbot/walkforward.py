"""Walk-forward and out-of-sample splitting for the M2/M3 strategy.

The harness is deliberately strict:

* each fold has a train window used only as warm-up/context,
* entries are allowed only inside the fold's test window,
* test windows do not overlap,
* equity is chained from one test window to the next,
* every fold carries its own metrics and baseline controls.

The train window is not used to fit anything yet.  It exists so the strategy
has the same lookback it would have in live trading, and so a future parameter
fit can be added without changing the split contract.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from .backtest import (
    BacktestRunner,
    compute_metrics,
    risk_summary_from_manifest,
    _timeframe_minutes,
)
from .baselines import compute_baselines
from .contracts import StrategyDecision, Trade, ensure_utc
from .data import BarSeries, PointInTimeStore
from .manifest import as_dict, manifest_hash


class WalkForwardError(ValueError):
    """Raised when a walk-forward split is invalid."""


@dataclass(frozen=True)
class WalkForwardConfig:
    """Train/test split contract for a walk-forward run."""

    train_bars: int = 600
    test_bars: int = 200
    step_bars: int | None = None
    mode: str = "rolling"

    def __post_init__(self) -> None:
        if self.train_bars < 1:
            raise WalkForwardError("train_bars must be >= 1")
        if self.test_bars < 1:
            raise WalkForwardError("test_bars must be >= 1")
        if self.step_bars is not None and self.step_bars < 1:
            raise WalkForwardError("step_bars must be >= 1")
        if self.step_bars is not None and self.step_bars < self.test_bars:
            raise WalkForwardError("step_bars must be >= test_bars to avoid overlapping test windows")
        if self.mode not in {"rolling", "expanding"}:
            raise WalkForwardError("mode must be 'rolling' or 'expanding'")

    @property
    def effective_step(self) -> int:
        return int(self.step_bars or self.test_bars)

    def to_dict(self) -> dict[str, Any]:
        return {
            "train_bars": self.train_bars,
            "test_bars": self.test_bars,
            "step_bars": self.effective_step,
            "mode": self.mode,
        }


@dataclass(frozen=True)
class Fold:
    """One train/test split expressed as bar indices and UTC boundaries."""

    fold_id: int
    train_start_index: int
    test_start_index: int
    test_end_index: int
    train_start_utc: datetime
    train_end_utc: datetime
    test_start_utc: datetime
    test_end_utc: datetime

    @property
    def train_bars(self) -> int:
        return self.test_start_index - self.train_start_index

    @property
    def test_bars(self) -> int:
        return self.test_end_index - self.test_start_index


@dataclass
class WalkForwardFoldResult:
    fold: Fold
    initial_equity: float
    final_equity: float
    trades: list[Trade]
    decisions: list[StrategyDecision]
    metrics: dict[str, float | int | None]
    baselines: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "fold_id": self.fold.fold_id,
            "train_start_utc": ensure_utc(self.fold.train_start_utc).isoformat(),
            "train_end_utc": ensure_utc(self.fold.train_end_utc).isoformat(),
            "test_start_utc": ensure_utc(self.fold.test_start_utc).isoformat(),
            "test_end_utc": ensure_utc(self.fold.test_end_utc).isoformat(),
            "train_bars": self.fold.train_bars,
            "test_bars": self.fold.test_bars,
            "initial_equity": self.initial_equity,
            "final_equity": self.final_equity,
            "metrics": self.metrics,
            "baselines": self.baselines,
            "decision_count": len(self.decisions),
            "trades": [trade.to_dict() for trade in self.trades],
        }


@dataclass
class WalkForwardResult:
    run_id: str
    scope_id: str
    manifest_hash: str
    started_at_utc: datetime
    finished_at_utc: datetime
    initial_equity: float
    final_equity: float
    folds: list[WalkForwardFoldResult]
    trades: list[Trade]
    decisions: list[StrategyDecision]
    metrics: dict[str, float | int | None]
    baselines: dict[str, Any]
    config: WalkForwardConfig
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
            "walk_forward": self.config.to_dict(),
            "folds": [fold.to_dict() for fold in self.folds],
            "baselines": self.baselines,
        }


def generate_folds(
    execution_bars: BarSeries,
    config: WalkForwardConfig,
    *,
    execution_minutes: int = 15,
) -> list[Fold]:
    """Split an execution series into non-overlapping train/test folds.

    Test windows are half-open ``[test_start, test_end)``.  The train window is
    the bars immediately before the test window (rolling) or all bars before it
    (expanding).  No entry is allowed outside the test window.
    """
    bars = execution_bars.bars
    total = len(bars)
    if total < config.train_bars + config.test_bars:
        raise WalkForwardError(
            f"not enough bars for one fold: need at least "
            f"{config.train_bars + config.test_bars}, got {total}"
        )
    folds: list[Fold] = []
    test_start = config.train_bars
    step = config.effective_step
    while test_start + config.test_bars <= total:
        test_end = test_start + config.test_bars
        train_start = 0 if config.mode == "expanding" else test_start - config.train_bars
        if train_start < 0:
            break
        test_start_utc = ensure_utc(bars[test_start].time_utc)
        if test_end < total:
            test_end_utc = ensure_utc(bars[test_end].time_utc)
        else:
            test_end_utc = ensure_utc(bars[test_end - 1].time_utc) + timedelta(
                minutes=execution_minutes
            )
        folds.append(
            Fold(
                fold_id=len(folds),
                train_start_index=train_start,
                test_start_index=test_start,
                test_end_index=test_end,
                train_start_utc=ensure_utc(bars[train_start].time_utc),
                train_end_utc=test_start_utc,
                test_start_utc=test_start_utc,
                test_end_utc=test_end_utc,
            )
        )
        test_start += step
    if not folds:
        raise WalkForwardError("no folds could be generated")
    return folds


class WalkForwardRunner:
    """Run a strategy over chained, non-overlapping out-of-sample windows."""

    def __init__(
        self,
        *,
        manifest: dict[str, Any],
        execution_bars: BarSeries,
        context_bars: BarSeries,
        macro_store: PointInTimeStore,
        initial_equity: float | None = None,
        train_bars: int = 600,
        test_bars: int = 200,
        step_bars: int | None = None,
        mode: str = "rolling",
        random_runs: int = 200,
        baseline_fast: int = 50,
        baseline_slow: int = 200,
    ) -> None:
        self.manifest = manifest
        self.execution_bars = execution_bars
        self.context_bars = context_bars
        self.macro_store = macro_store
        self.initial_equity = float(
            initial_equity
            if initial_equity is not None
            else as_dict(as_dict(manifest.get("strategy")).get("sizing")).get(
                "initial_equity", 10000.0
            )
        )
        self.config = WalkForwardConfig(
            train_bars=train_bars,
            test_bars=test_bars,
            step_bars=step_bars,
            mode=mode,
        )
        self.random_runs = max(1, int(random_runs))
        self.baseline_fast = int(baseline_fast)
        self.baseline_slow = int(baseline_slow)
        self.execution_minutes = _timeframe_minutes(
            str(as_dict(manifest.get("market")).get("execution_bar", "M15"))
        )
        self.contract_size_oz = float(
            as_dict(as_dict(manifest.get("strategy")).get("sizing")).get(
                "contract_size_oz", 100.0
            )
        )

    def run(self) -> WalkForwardResult:
        bars = self.execution_bars.bars
        folds = generate_folds(
            self.execution_bars,
            self.config,
            execution_minutes=self.execution_minutes,
        )
        current_equity = self.initial_equity
        all_trades: list[Trade] = []
        all_decisions: list[StrategyDecision] = []
        fold_results: list[WalkForwardFoldResult] = []

        for fold in folds:
            backtest_end = min(fold.test_end_index + 1, len(bars))
            fold_bars = BarSeries(
                bars[fold.train_start_index : backtest_end],
                self.execution_bars.timeframe,
            )
            runner = BacktestRunner(
                manifest=self.manifest,
                execution_bars=fold_bars,
                context_bars=self.context_bars,
                macro_store=self.macro_store,
                initial_equity=current_equity,
                entry_start_utc=fold.test_start_utc,
                entry_end_utc=fold.test_end_utc,
            )
            result = runner.run()
            fold_decisions = [
                decision
                for decision in result.decisions
                if fold.test_start_utc <= ensure_utc(decision.as_of_utc) < fold.test_end_utc
            ]
            # A decision on the second-to-last test bar can fill on the first
            # bar after the window.  Include that boundary fill so the fold's
            # trade list reconciles with its final equity.
            fold_trades = [
                trade
                for trade in result.trades
                if fold.test_start_utc <= ensure_utc(trade.entry_time_utc) <= fold.test_end_utc
            ]
            fold_metrics = compute_metrics(
                current_equity,
                result.final_equity,
                fold_trades,
                fold_decisions,
                contract_size_oz=self.contract_size_oz,
            )
            fold_baselines = compute_baselines(
                self.execution_bars,
                [(fold.test_start_utc, fold.test_end_utc)],
                fast_period=self.baseline_fast,
                slow_period=self.baseline_slow,
                random_runs=self.random_runs,
            )
            fold_results.append(
                WalkForwardFoldResult(
                    fold=fold,
                    initial_equity=current_equity,
                    final_equity=result.final_equity,
                    trades=fold_trades,
                    decisions=fold_decisions,
                    metrics=fold_metrics,
                    baselines=fold_baselines,
                )
            )
            all_trades.extend(fold_trades)
            all_decisions.extend(fold_decisions)
            current_equity = result.final_equity

        aggregate_metrics = compute_metrics(
            self.initial_equity,
            current_equity,
            all_trades,
            all_decisions,
            contract_size_oz=self.contract_size_oz,
        )
        aggregate_baselines = compute_baselines(
            self.execution_bars,
            [(fold.fold.test_start_utc, fold.fold.test_end_utc) for fold in fold_results],
            fast_period=self.baseline_fast,
            slow_period=self.baseline_slow,
            random_runs=self.random_runs,
        )
        started = fold_results[0].fold.test_start_utc
        finished = fold_results[-1].fold.test_end_utc
        digest = hashlib.sha256(
            (
                f"{self.manifest.get('scope_id')}:{manifest_hash(self.manifest)}:"
                f"{started.isoformat()}:{self.config.mode}:"
                f"{self.config.train_bars}:{self.config.test_bars}:{self.config.effective_step}"
            ).encode("utf-8")
        ).hexdigest()[:16]
        return WalkForwardResult(
            run_id=digest,
            scope_id=str(self.manifest.get("scope_id", "unknown")),
            manifest_hash=manifest_hash(self.manifest),
            started_at_utc=started,
            finished_at_utc=finished,
            initial_equity=self.initial_equity,
            final_equity=current_equity,
            folds=fold_results,
            trades=all_trades,
            decisions=all_decisions,
            metrics=aggregate_metrics,
            baselines=aggregate_baselines,
            config=self.config,
            data_quality={
                "execution_bars": len(self.execution_bars),
                "context_bars": len(self.context_bars),
                "macro_series": len(self.macro_store.series_ids()),
                "fold_count": len(fold_results),
                "train_bars": self.config.train_bars,
                "test_bars": self.config.test_bars,
                "step_bars": self.config.effective_step,
                "mode": self.config.mode,
                "test_bars_total": sum(
                    fold.fold.test_bars for fold in fold_results
                ),
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
        )
