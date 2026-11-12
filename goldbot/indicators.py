"""Point-in-time technical indicators used by the M2 technical layer.

The functions are deliberately small and dependency-light.  They only use bars
at or before the caller's as-of bar, so the caller cannot accidentally read the
future through an indicator helper.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

from .contracts import Bar


def clamp(value: float, low: float = -1.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def sma(values: Sequence[float], period: int) -> list[float | None]:
    result: list[float | None] = [None] * len(values)
    if period <= 0:
        return result
    running = 0.0
    for index, value in enumerate(values):
        running += value
        if index >= period:
            running -= values[index - period]
        if index >= period - 1:
            result[index] = running / period
    return result


def ema(values: Sequence[float], period: int) -> list[float | None]:
    result: list[float | None] = [None] * len(values)
    if period <= 0 or len(values) < period:
        return result
    seed = sum(values[:period]) / period
    result[period - 1] = seed
    multiplier = 2.0 / (period + 1.0)
    previous = seed
    for index in range(period, len(values)):
        previous = (values[index] - previous) * multiplier + previous
        result[index] = previous
    return result


def rsi(values: Sequence[float], period: int = 14) -> list[float | None]:
    result: list[float | None] = [None] * len(values)
    if period <= 0 or len(values) <= period:
        return result
    gains = 0.0
    losses = 0.0
    for index in range(1, period + 1):
        change = values[index] - values[index - 1]
        if change >= 0:
            gains += change
        else:
            losses -= change
    average_gain = gains / period
    average_loss = losses / period
    result[period] = _rsi_value(average_gain, average_loss)
    for index in range(period + 1, len(values)):
        change = values[index] - values[index - 1]
        gain = max(change, 0.0)
        loss = max(-change, 0.0)
        average_gain = (average_gain * (period - 1) + gain) / period
        average_loss = (average_loss * (period - 1) + loss) / period
        result[index] = _rsi_value(average_gain, average_loss)
    return result


def _rsi_value(average_gain: float, average_loss: float) -> float:
    if average_loss == 0.0:
        return 100.0 if average_gain > 0.0 else 50.0
    relative_strength = average_gain / average_loss
    return 100.0 - (100.0 / (1.0 + relative_strength))


def true_ranges(bars: Sequence[Bar]) -> list[float]:
    result: list[float] = []
    previous_close: float | None = None
    for bar in bars:
        if previous_close is None:
            result.append(bar.high - bar.low)
        else:
            result.append(
                max(
                    bar.high - bar.low,
                    abs(bar.high - previous_close),
                    abs(bar.low - previous_close),
                )
            )
        previous_close = bar.close
    return result


def atr(bars: Sequence[Bar], period: int = 14) -> list[float | None]:
    result: list[float | None] = [None] * len(bars)
    ranges = true_ranges(bars)
    if period <= 0 or len(ranges) < period:
        return result
    seed = sum(ranges[:period]) / period
    result[period - 1] = seed
    previous = seed
    for index in range(period, len(ranges)):
        previous = (previous * (period - 1) + ranges[index]) / period
        result[index] = previous
    return result


def rolling_high(values: Sequence[float], lookback: int) -> float | None:
    if lookback <= 0 or len(values) < lookback:
        return None
    return max(values[-lookback:])


def rolling_low(values: Sequence[float], lookback: int) -> float | None:
    if lookback <= 0 or len(values) < lookback:
        return None
    return min(values[-lookback:])


def mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def stdev(values: Sequence[float]) -> float:
    if len(values) < 2:
        return 0.0
    average = mean(values)
    variance = sum((value - average) ** 2 for value in values) / (len(values) - 1)
    return math.sqrt(max(variance, 0.0))


def percentile_rank(values: Sequence[float], value: float) -> float:
    """Fraction of historical values less than or equal to ``value``."""
    if not values:
        return 0.0
    count = sum(1 for item in values if item <= value)
    return count / len(values)
