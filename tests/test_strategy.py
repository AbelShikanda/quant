from __future__ import annotations

import copy
import json
from datetime import timedelta
from pathlib import Path

from goldbot.backtest import BacktestRunner
from goldbot.data import (
    BarSeries,
    aggregate_bars,
    make_synthetic_bars,
    make_synthetic_macro_store,
)
from goldbot.manifest import load_manifest
from goldbot.profiles import apply_profile, load_manifest_with_profile, load_profile
from goldbot.strategy import StrategyEngine


ROOT = Path(__file__).resolve().parents[1]
GOLD = ROOT / "config" / "scope.gold.v1.yaml"
MICRO_100 = ROOT / "config" / "profiles" / "micro_100.yaml"


def _inputs():
    bars = make_synthetic_bars(count=1600)
    h4 = aggregate_bars(bars, "H4", 4)
    as_of = bars[-1].time_utc + timedelta(minutes=15)
    macro = make_synthetic_macro_store(as_of_utc=as_of, days=520)
    return BarSeries(bars, "M15"), BarSeries(h4, "H4"), macro


def test_synthetic_backtest_produces_explained_trades() -> None:
    manifest = load_manifest(GOLD)
    execution, context, macro = _inputs()
    result = BacktestRunner(
        manifest=manifest,
        execution_bars=execution,
        context_bars=context,
        macro_store=macro,
        initial_equity=10000.0,
    ).run()
    assert result.metrics["trade_count"] > 0
    assert result.metrics["entry_decisions"] > 0
    assert result.final_equity != result.initial_equity
    assert all(trade.exit_reason for trade in result.trades)
    first_entry = next(d for d in result.decisions if d.decision_type == "ENTER_LONG")
    assert first_entry.fundamental.components
    assert first_entry.technical.indicators
    assert first_entry.fusion.reasons
    assert first_entry.overseer.decision == "ENTER_LONG"
    assert json.dumps(first_entry.to_dict(), default=str)


def test_standard_1k_profile_sizes_minimum_lot_within_cap() -> None:
    manifest = load_manifest(GOLD)
    engine = StrategyEngine(manifest)
    sizing = engine.size_position(equity=1000.0, atr_value=7.1088, entry_price=4114.11)
    assert sizing.volume == 0.01
    assert sizing.risk_pct <= 1.25
    assert sizing.min_lot_risk_pct <= 1.25
    assert sizing.notional <= sizing.max_notional
    assert "minimum lot" in sizing.reason


def test_standard_1k_profile_blocks_100_dollar_account() -> None:
    manifest = load_manifest(GOLD)
    engine = StrategyEngine(manifest)
    sizing = engine.size_position(equity=100.0, atr_value=7.1088, entry_price=4114.11)
    assert sizing.volume == 0.0
    assert sizing.min_lot_risk_pct > 1.25
    assert "min-lot risk cap" in sizing.reason


def test_micro_100_profile_allows_minimum_lot_on_100_dollars() -> None:
    manifest = load_manifest_with_profile(GOLD, MICRO_100)
    engine = StrategyEngine(manifest)
    sizing = engine.size_position(equity=100.0, atr_value=7.1088, entry_price=4114.11)
    assert sizing.volume == 0.01
    assert 10.0 < sizing.risk_pct <= 11.0
    assert sizing.min_lot_risk_pct <= 11.0
    assert sizing.notional <= sizing.max_notional


def test_metrics_include_sharpe_sortino_and_exposure() -> None:
    manifest = load_manifest(GOLD)
    execution, context, macro = _inputs()
    result = BacktestRunner(
        manifest=manifest,
        execution_bars=execution,
        context_bars=context,
        macro_store=macro,
        initial_equity=10000.0,
    ).run()
    assert "sharpe_daily_annualized" in result.metrics
    assert "sortino_daily_annualized" in result.metrics
    assert result.metrics["daily_return_count"] >= 1
    assert result.metrics["max_entry_notional"] > 0
    assert result.metrics["max_entry_exposure_pct"] > 0
    assert result.metrics["average_entry_exposure_pct"] > 0


def test_profile_overlay_does_not_mutate_base_manifest() -> None:
    base = load_manifest(GOLD)
    profile = load_profile(MICRO_100)
    merged = apply_profile(base, profile)
    assert merged["risk"]["per_trade_equity_pct"] == 11.0
    assert merged["risk"]["leverage_cap"] == 50.0
    assert merged["market"]["max_notional_exposure_ratio"] == 50.0
    assert merged["profile_high_risk_override"] is True
    assert merged["profile_promotion_eligible"] is False
    assert base["risk"]["per_trade_equity_pct"] == 0.25
    assert base["risk"]["leverage_cap"] == 5.0


def test_manifest_parameter_change_alters_behavior() -> None:
    manifest = load_manifest(GOLD)
    execution, context, macro = _inputs()
    baseline = BacktestRunner(
        manifest=manifest,
        execution_bars=execution,
        context_bars=context,
        macro_store=macro,
        initial_equity=10000.0,
    ).run()
    changed_manifest = copy.deepcopy(manifest)
    changed_manifest["strategy"]["fusion"]["entry_threshold"] = 0.99
    changed = BacktestRunner(
        manifest=changed_manifest,
        execution_bars=execution,
        context_bars=context,
        macro_store=macro,
        initial_equity=10000.0,
    ).run()
    assert baseline.metrics["entry_decisions"] > changed.metrics["entry_decisions"]
    assert baseline.manifest_hash != changed.manifest_hash
