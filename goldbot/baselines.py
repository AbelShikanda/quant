"""Cost-free, unlevered baseline controls for walk-forward comparison.

These controls are deliberately simple and are not strategy candidates:

* buy-and-hold: long the first open to the last close in each test window,
* simple trend: long when EMA(fast) > EMA(slow), flat otherwise,
* random entry: long with 50% probability on each bar, repeated over seeds.

They use 1x notional, no spread, no commission, and no slippage.  They are a
sanity check for the strategy, not a profitability claim.
"""

from __future__ import annotations

import bisect
import random
from datetime import datetime
from typing import Any, Sequence

from .contracts import ensure_utc
from .data import BarSeries
from .indicators import ema


def _percentile(sorted_values: Sequence[float], percentile: float) -> float:
    if not sorted_values:
        return 0.0
    index = int(round((len(sorted_values) - 1) * percentile))
    index = min(len(sorted_values) - 1, max(0, index))
    return float(sorted_values[index])


def _empty_result(*, random_runs: int, window_count: int, fast: int, slow: int) -> dict[str, Any]:
    return {
        "buy_and_hold_return_pct": 0.0,
        "simple_trend_return_pct": 0.0,
        "random_entry_mean_return_pct": 0.0,
        "random_entry_median_return_pct": 0.0,
        "random_entry_p05_return_pct": 0.0,
        "random_entry_p95_return_pct": 0.0,
        "random_runs": random_runs,
        "window_count": window_count,
        "fast_period": fast,
        "slow_period": slow,
        "note": "unlevered, cost-free, 1x-notional controls; not a strategy result",
    }


def compute_baselines(
    execution_bars: BarSeries,
    windows: Sequence[tuple[datetime, datetime]],
    *,
    fast_period: int = 50,
    slow_period: int = 200,
    seed: int = 42,
    random_runs: int = 200,
) -> dict[str, Any]:
    """Compute baseline controls over one or more non-overlapping windows.

    ``windows`` are half-open ``[start, end)`` intervals.  Returns are chained
    across windows, so a walk-forward comparison is flat between test windows.
    """
    bars = execution_bars.bars
    random_runs = max(1, int(random_runs))
    normalized: list[tuple[datetime, datetime]] = []
    for start, end in windows:
        start_utc = ensure_utc(start)
        end_utc = ensure_utc(end)
        if start_utc < end_utc:
            normalized.append((start_utc, end_utc))
    if not bars or not normalized:
        return _empty_result(
            random_runs=random_runs,
            window_count=len(normalized),
            fast=fast_period,
            slow=slow_period,
        )

    times = [bar.time_utc for bar in bars]
    closes = [bar.close for bar in bars]
    fast = ema(closes, fast_period)
    slow = ema(closes, slow_period)

    buy_hold_equity = 1.0
    trend_equity = 1.0
    for start, end in normalized:
        start_index = bisect.bisect_left(times, start)
        end_index = bisect.bisect_left(times, end)
        if end_index <= start_index:
            continue
        first = bars[start_index]
        last = bars[end_index - 1]
        if first.open > 0.0:
            buy_hold_equity *= last.close / first.open
        for index in range(start_index, end_index - 1):
            if index + 1 >= len(bars):
                break
            if fast[index] is None or slow[index] is None:
                continue
            if fast[index] > slow[index] and bars[index].close > 0.0:
                trend_equity *= bars[index + 1].close / bars[index].close

    random_returns: list[float] = []
    for run in range(random_runs):
        rng = random.Random(seed + run)
        equity = 1.0
        for start, end in normalized:
            start_index = bisect.bisect_left(times, start)
            end_index = bisect.bisect_left(times, end)
            for index in range(start_index, end_index - 1):
                if index + 1 >= len(bars):
                    break
                if bars[index].close <= 0.0:
                    continue
                if rng.random() < 0.5:
                    equity *= bars[index + 1].close / bars[index].close
        random_returns.append((equity - 1.0) * 100.0)
    random_returns.sort()

    return {
        "buy_and_hold_return_pct": (buy_hold_equity - 1.0) * 100.0,
        "simple_trend_return_pct": (trend_equity - 1.0) * 100.0,
        "random_entry_mean_return_pct": (
            sum(random_returns) / len(random_returns) if random_returns else 0.0
        ),
        "random_entry_median_return_pct": _percentile(random_returns, 0.50),
        "random_entry_p05_return_pct": _percentile(random_returns, 0.05),
        "random_entry_p95_return_pct": _percentile(random_returns, 0.95),
        "random_runs": random_runs,
        "window_count": len(normalized),
        "fast_period": fast_period,
        "slow_period": slow_period,
        "note": "unlevered, cost-free, 1x-notional controls; not a strategy result",
    }
