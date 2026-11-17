"""Technical layer: the "when" for gold.

The engine uses only closed bars at or before the decision time.  M15 bars drive
entry timing; H4 bars provide context.  A forming H4 bar is excluded so its
eventual close cannot leak into the decision.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from .contracts import TechnicalView, ensure_utc
from .data import BarSeries
from .indicators import atr, clamp, ema, rolling_high, rolling_low, rsi
from .manifest import as_dict, as_list


class TechnicalConfigError(RuntimeError):
    """Raised when the manifest is missing a required technical parameter."""


class TechnicalEngine:
    def __init__(self, manifest: dict[str, Any]) -> None:
        self.manifest = manifest
        market = as_dict(manifest.get("market"))
        self.execution_minutes = _timeframe_minutes(str(market.get("execution_bar", "M15")))
        self.context_minutes = _timeframe_minutes(str(market.get("context_bar", "H4")))
        technical = as_dict(manifest.get("technical"))
        self.indicators = {
            str(item.get("id")): as_dict(item.get("params"))
            for item in as_list(technical.get("indicators"))
            if isinstance(item, dict) and item.get("id")
        }
        self.config = as_dict(manifest.get("strategy", {}).get("technical", {}))
        self.weights = as_dict(self.config.get("component_weights"))
        if not self.weights:
            raise TechnicalConfigError("missing strategy.technical.component_weights")
        self.rsi_entry_min = float(self.config.get("rsi_entry_min", 50.0))
        self.rsi_entry_max = float(self.config.get("rsi_entry_max", 70.0))
        self.atr_floor = float(self.config.get("atr_floor", 0.0010))
        self.atr_ceiling = float(self.config.get("atr_ceiling", 0.0200))
        self.max_spread_points = float(self.config.get("max_spread_points", 100.0))
        self.structure_buffer = float(self.config.get("structure_break_buffer", 0.0))
        session = as_dict(self.config.get("session_filter"))
        self.session_enabled = bool(session.get("enabled", False))
        self.session_start_minutes = _hhmm_minutes(str(session.get("start_utc", "00:00")))
        self.session_end_minutes = _hhmm_minutes(str(session.get("end_utc", "23:59")))
        self.session_weekdays = {
            int(day) for day in as_list(session.get("weekdays", [0, 1, 2, 3, 4]))
        }
        self.h4_atr_period = int(self.config.get("h4_atr_period", 14))

    def evaluate(
        self,
        execution_bars: BarSeries,
        context_bars: BarSeries,
        decision_time_utc: datetime,
        *,
        news_blackout: bool = False,
    ) -> TechnicalView:
        decision_time = ensure_utc(decision_time_utc)
        ema_params = self.indicators.get("ema_stack", {})
        rsi_params = self.indicators.get("rsi", {})
        atr_params = self.indicators.get("atr", {})
        structure_params = self.indicators.get("swing_structure", {})
        h4_params = self.indicators.get("h4_trend", {})

        fast = int(ema_params.get("fast", 20))
        slow = int(ema_params.get("slow", 50))
        trend = int(ema_params.get("trend", 200))
        rsi_period = int(rsi_params.get("period", 14))
        atr_period = int(atr_params.get("period", 14))
        structure_lookback = int(structure_params.get("lookback", 20))
        h4_fast = int(h4_params.get("ema_fast", 20))
        h4_slow = int(h4_params.get("ema_slow", 50))

        required_execution = max(trend + 1, slow + 1, fast + 1, rsi_period + 2, atr_period + 1, structure_lookback + 2)
        execution = execution_bars.closed_window(decision_time, self.execution_minutes)
        context = context_bars.closed_window(decision_time, self.context_minutes)
        if len(execution) < required_execution:
            return TechnicalView(
                as_of_utc=decision_time,
                score=0.0,
                components={},
                indicators={},
                entry_triggers={},
                is_valid=False,
                reasons=[f"insufficient M15 bars: {len(execution)} < {required_execution}"],
                bars_used=len(execution),
                context_bars_used=len(context),
            )
        if len(context) < max(h4_fast, h4_slow) + 1:
            return TechnicalView(
                as_of_utc=decision_time,
                score=0.0,
                components={},
                indicators={},
                entry_triggers={},
                is_valid=False,
                reasons=[f"insufficient H4 bars: {len(context)}"],
                bars_used=len(execution),
                context_bars_used=len(context),
            )

        closes = [bar.close for bar in execution]
        highs = [bar.high for bar in execution]
        lows = [bar.low for bar in execution]
        ema_fast = ema(closes, fast)[-1]
        ema_slow = ema(closes, slow)[-1]
        ema_trend = ema(closes, trend)[-1]
        rsi_value = rsi(closes, rsi_period)[-1]
        atr_value = atr(execution, atr_period)[-1]
        if None in {ema_fast, ema_slow, ema_trend, rsi_value, atr_value}:
            return TechnicalView(
                as_of_utc=decision_time,
                score=0.0,
                components={},
                indicators={},
                entry_triggers={},
                is_valid=False,
                reasons=["indicator warm-up incomplete"],
                bars_used=len(execution),
                context_bars_used=len(context),
            )
        assert ema_fast is not None and ema_slow is not None and ema_trend is not None
        assert rsi_value is not None and atr_value is not None

        close = closes[-1]
        atr_pct = atr_value / close if close else 0.0
        prior_high = max(highs[-structure_lookback - 1 : -1])
        prior_low = min(lows[-structure_lookback - 1 : -1])
        buffer = self.structure_buffer * atr_value
        if close > prior_high + buffer:
            structure_signal = 1.0
        elif close < prior_low - buffer:
            structure_signal = -1.0
        else:
            structure_signal = 0.0

        if ema_fast > ema_slow and close > ema_trend:
            trend_signal = 1.0
        elif ema_fast < ema_slow and close < ema_trend:
            trend_signal = -1.0
        else:
            trend_signal = 0.0

        momentum_signal = clamp((rsi_value - 50.0) / 20.0, -1.0, 1.0)
        if self.atr_floor <= atr_pct <= self.atr_ceiling:
            volatility_signal = 1.0
        elif atr_pct < self.atr_floor:
            volatility_signal = 0.0
        else:
            volatility_signal = -1.0

        h4_closes = [bar.close for bar in context]
        h4_fast_value = ema(h4_closes, h4_fast)[-1]
        h4_slow_value = ema(h4_closes, h4_slow)[-1]
        h4_atr_values = atr(list(context), self.h4_atr_period)
        h4_atr_value = h4_atr_values[-1]
        if h4_fast_value is None or h4_slow_value is None:
            return TechnicalView(
                as_of_utc=decision_time,
                score=0.0,
                components={},
                indicators={},
                entry_triggers={},
                is_valid=False,
                reasons=["H4 indicator warm-up incomplete"],
                bars_used=len(execution),
                context_bars_used=len(context),
            )
        h4_atr_pct = 0.0
        if h4_atr_value is not None:
            h4_atr_pct = h4_atr_value / h4_closes[-1] if h4_closes[-1] else 0.0
            h4_trend_gap = (h4_fast_value - h4_slow_value) / h4_slow_value if h4_slow_value else 0.0
            if abs(h4_trend_gap) >= 0.01:
                h4_regime = "TREND_" + ("UP" if h4_trend_gap > 0.0 else "DOWN")
            elif h4_atr_pct >= 0.002:
                h4_regime = "VOLATILE"
            else:
                h4_regime = "RANGE"
        else:
            h4_regime = "UNKNOWN"
        h4_context = h4_fast_value > h4_slow_value
        m15_trigger = (
            trend_signal > 0
            and structure_signal > 0
            and self.rsi_entry_min <= rsi_value <= self.rsi_entry_max
        )
        atr_filter = self.atr_floor <= atr_pct <= self.atr_ceiling
        spread = execution[-1].spread_points
        spread_filter = spread <= self.max_spread_points
        session_filter = self._session_ok(decision_time)
        entry_triggers = {
            "h4_context": h4_context,
            "m15_trigger": m15_trigger,
            "atr_filter": atr_filter,
            "spread_filter": spread_filter,
            "session_filter": session_filter,
            "no_news_blackout": not news_blackout,
        }
        components = {
            "trend": trend_signal,
            "momentum": momentum_signal,
            "structure": structure_signal,
            "volatility": volatility_signal,
        }
        score = 0.0
        for name, signal in components.items():
            score += float(self.weights.get(name, 0.0)) * signal
        score = clamp(score, -1.0, 1.0)
        indicators = {
            "ema_fast": float(ema_fast),
            "ema_slow": float(ema_slow),
            "ema_trend": float(ema_trend),
            "rsi": float(rsi_value),
            "atr": float(atr_value),
            "atr_pct": float(atr_pct),
            "close": float(close),
            "prior_high": float(prior_high),
            "prior_low": float(prior_low),
            "h4_ema_fast": float(h4_fast_value),
            "h4_ema_slow": float(h4_slow_value),
            "h4_atr_pct": float(h4_atr_pct),
            "h4_regime": h4_regime,
            "spread_points": float(spread),
        }
        reasons = [
            f"trend={trend_signal:+.0f} momentum={momentum_signal:+.2f} "
            f"structure={structure_signal:+.0f} volatility={volatility_signal:+.0f}",
            f"h4_context={h4_context} m15_trigger={m15_trigger} "
            f"atr_filter={atr_filter} spread_filter={spread_filter} "
            f"session_filter={session_filter}",
        ]
        return TechnicalView(
            as_of_utc=decision_time,
            score=score,
            components=components,
            indicators=indicators,
            entry_triggers=entry_triggers,
            is_valid=True,
            reasons=reasons,
            bars_used=len(execution),
            context_bars_used=len(context),
        )

    def _session_ok(self, decision_time: datetime) -> bool:
        if not self.session_enabled:
            return True
        if decision_time.weekday() not in self.session_weekdays:
            return False
        minute = decision_time.hour * 60 + decision_time.minute
        if self.session_start_minutes <= self.session_end_minutes:
            return self.session_start_minutes <= minute < self.session_end_minutes
        return minute >= self.session_start_minutes or minute < self.session_end_minutes


def _timeframe_minutes(timeframe: str) -> int:
    mapping = {"M1": 1, "M5": 5, "M15": 15, "M30": 30, "H1": 60, "H4": 240, "D1": 1440}
    return mapping.get(timeframe.upper(), 15)


def _hhmm_minutes(value: str) -> int:
    try:
        hour, minute = value.split(":", 1)
        return int(hour) * 60 + int(minute)
    except (ValueError, AttributeError) as exc:
        raise TechnicalConfigError(f"invalid session time: {value!r}") from exc
