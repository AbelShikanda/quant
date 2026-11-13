from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from goldbot.baselines import compute_baselines
from goldbot.backtest import BacktestRunner
from goldbot.cli import main
from goldbot.contracts import Bar
from goldbot.data import (
    BarSeries,
    aggregate_bars,
    make_synthetic_bars,
    make_synthetic_macro_store,
)
from goldbot.manifest import load_manifest
from goldbot.walkforward import (
    WalkForwardConfig,
    WalkForwardError,
    WalkForwardRunner,
    generate_folds,
)


ROOT = Path(__file__).resolve().parents[1]
GOLD = ROOT / "config" / "scope.gold.v1.yaml"


def _inputs(count: int = 1600):
    bars = make_synthetic_bars(count=count)
    h4 = aggregate_bars(bars, "H4", 4)
    as_of = bars[-1].time_utc + timedelta(minutes=15)
    macro = make_synthetic_macro_store(as_of_utc=as_of, days=520)
    return BarSeries(bars, "M15"), BarSeries(h4, "H4"), macro


def _manual_series(prices: list[float], *, rising: bool = True) -> BarSeries:
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    bars = []
    for index, price in enumerate(prices):
        offset = index * 0.10 if rising else -index * 0.10
        open_price = price + offset
        close_price = open_price + (0.05 if rising else -0.05)
        bars.append(
            Bar(
                time_utc=start + timedelta(minutes=15 * index),
                open=open_price,
                high=max(open_price, close_price) + 0.2,
                low=min(open_price, close_price) - 0.2,
                close=close_price,
                tick_volume=100,
                spread_points=10.0,
            )
        )
    return BarSeries(bars, "M15")


def test_generate_folds_rolling_and_expanding_are_non_overlapping() -> None:
    execution, _, _ = _inputs(1600)
    rolling = generate_folds(
        execution,
        WalkForwardConfig(train_bars=400, test_bars=200, mode="rolling"),
    )
    assert len(rolling) == 6
    assert rolling[0].train_start_index == 0
    assert rolling[0].test_start_index == 400
    assert all(fold.test_bars == 200 for fold in rolling)
    assert all(
        later.test_start_utc >= earlier.test_end_utc
        for earlier, later in zip(rolling, rolling[1:])
    )

    expanding = generate_folds(
        execution,
        WalkForwardConfig(train_bars=400, test_bars=200, mode="expanding"),
    )
    assert len(expanding) == 6
    assert all(fold.train_start_index == 0 for fold in expanding)
    assert expanding[0].train_bars == 400
    assert expanding[-1].train_bars > expanding[0].train_bars
    assert all(
        later.test_start_utc >= earlier.test_end_utc
        for earlier, later in zip(expanding, expanding[1:])
    )


def test_walkforward_config_rejects_overlapping_test_windows() -> None:
    with pytest.raises(WalkForwardError):
        WalkForwardConfig(train_bars=400, test_bars=200, step_bars=100)


def test_walkforward_run_records_only_test_window_trades() -> None:
    manifest = load_manifest(GOLD)
    execution, context, macro = _inputs(1600)
    result = WalkForwardRunner(
        manifest=manifest,
        execution_bars=execution,
        context_bars=context,
        macro_store=macro,
        initial_equity=10000.0,
        train_bars=600,
        test_bars=200,
        mode="rolling",
        random_runs=20,
    ).run()
    assert len(result.folds) == 5
    assert result.metrics["trade_count"] == len(result.trades)
    assert result.final_equity == result.folds[-1].final_equity
    assert result.baselines["window_count"] == len(result.folds)
    assert all(
        later.fold.test_start_utc >= earlier.fold.test_end_utc
        for earlier, later in zip(result.folds, result.folds[1:])
    )
    for trade in result.trades:
        assert any(
            fold.fold.test_start_utc <= trade.entry_time_utc <= fold.fold.test_end_utc
            for fold in result.folds
        )
    payload = result.to_dict()
    assert payload["walk_forward"]["mode"] == "rolling"
    assert len(payload["folds"]) == 5
    assert "buy_and_hold_return_pct" in payload["baselines"]


def test_backtest_entry_window_suppresses_out_of_window_entries() -> None:
    manifest = load_manifest(GOLD)
    execution, context, macro = _inputs(1600)
    start = execution.bars[800].time_utc
    end = execution.bars[1000].time_utc
    result = BacktestRunner(
        manifest=manifest,
        execution_bars=execution,
        context_bars=context,
        macro_store=macro,
        initial_equity=10000.0,
        entry_start_utc=start,
        entry_end_utc=end,
    ).run()
    assert result.trades
    assert all(start <= trade.entry_time_utc <= end for trade in result.trades)
    suppressed = [
        decision
        for decision in result.decisions
        if "entry suppressed" in " ".join(decision.reasons)
    ]
    assert suppressed
    assert all(decision.decision_type == "NO_TRADE" for decision in suppressed)
    assert not any(
        decision.decision_type == "ENTER_LONG" and decision.as_of_utc < start
        for decision in result.decisions
    )


def test_baselines_on_rising_and_falling_series() -> None:
    rising = _manual_series([100.0] * 300, rising=True)
    falling = _manual_series([100.0] * 300, rising=False)
    window = [
        (
            rising.bars[0].time_utc,
            rising.bars[-1].time_utc + timedelta(minutes=15),
        )
    ]
    up = compute_baselines(rising, window, fast_period=5, slow_period=10, random_runs=20)
    down = compute_baselines(falling, window, fast_period=5, slow_period=10, random_runs=20)
    assert up["buy_and_hold_return_pct"] > 0
    assert up["simple_trend_return_pct"] > 0
    assert down["buy_and_hold_return_pct"] < 0
    assert down["simple_trend_return_pct"] <= 0
    assert up["random_entry_p05_return_pct"] <= up["random_entry_p95_return_pct"]


def test_baselines_are_deterministic_for_a_seed() -> None:
    execution, _, _ = _inputs(400)
    window = [
        (
            execution.bars[0].time_utc,
            execution.bars[-1].time_utc + timedelta(minutes=15),
        )
    ]
    first = compute_baselines(execution, window, random_runs=25, seed=7)
    second = compute_baselines(execution, window, random_runs=25, seed=7)
    assert first == second


def test_walkforward_cli_writes_report_and_dashboard(tmp_path: Path) -> None:
    report = tmp_path / "walkforward.json"
    dashboard = tmp_path / "walkforward.html"
    code = main(
        [
            "walkforward",
            "--manifest",
            str(GOLD),
            "--bars",
            "1000",
            "--train-bars",
            "400",
            "--test-bars",
            "200",
            "--random-runs",
            "10",
            "--report",
            str(report),
            "--dashboard",
            str(dashboard),
            "--json",
        ]
    )
    assert code == 0
    assert report.exists()
    assert dashboard.exists()
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["walk_forward"]["train_bars"] == 400
    assert len(payload["folds"]) == 3
    html = dashboard.read_text(encoding="utf-8")
    assert "Walk-forward / out-of-sample" in html
    assert "Baseline controls" in html
    assert "Buy and hold" in html
