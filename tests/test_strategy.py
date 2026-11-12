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
from goldbot.strategy import StrategyEngine


ROOT = Path(__file__).resolve().parents[1]
GOLD = ROOT / "config" / "scope.gold.v1.yaml"


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


def test_cash_equivalent_account_blocks_minimum_lot() -> None:
    manifest = load_manifest(GOLD)
    engine = StrategyEngine(manifest)
    sizing = engine.size_position(equity=1000.0, atr_value=2.0, entry_price=2000.0)
    assert sizing.volume == 0.0
    assert "minimum lot" in sizing.reason or "exposure cap" in sizing.reason


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
