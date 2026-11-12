"""Fundamental/macro layer: the "what" for gold.

Every threshold, lookback, transform, weight, and sign comes from the scope
manifest.  Missing or stale weight-bearing inputs are reported explicitly and
make the view invalid; they are never forward-filled.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from .contracts import FundamentalComponent, FundamentalView, MacroObservation, ensure_utc
from .data import PointInTimeStore
from .indicators import clamp, mean, percentile_rank, stdev
from .manifest import as_dict, as_list


class StrategyConfigError(RuntimeError):
    """Raised when the manifest is missing a required strategy parameter."""


def _required(mapping: dict[str, Any], dotted: str) -> Any:
    current: Any = mapping
    for part in dotted.split("."):
        if not isinstance(current, dict) or part not in current:
            raise StrategyConfigError(f"missing manifest parameter: {dotted}")
        current = current[part]
    return current


class FundamentalEngine:
    def __init__(self, manifest: dict[str, Any]) -> None:
        self.manifest = manifest
        self.inputs = [item for item in as_list(manifest.get("fundamental_inputs")) if isinstance(item, dict)]
        self.config = as_dict(_required(manifest, "strategy.fundamental"))
        self.zscore_lookback = int(self.config.get("zscore_lookback_days", 252))
        self.min_observations = int(self.config.get("min_observations", 60))
        self.zscore_clip = float(self.config.get("zscore_clip", 1.0))
        self.stale_after = as_dict(self.config.get("stale_after_days"))
        self.transforms = as_dict(self.config.get("transforms"))
        self._validate_weights()

    def _validate_weights(self) -> None:
        total = sum(float(item.get("weight", 0.0)) for item in self.inputs)
        if abs(total - 1.0) > 1e-9:
            raise StrategyConfigError(f"fundamental weights must sum to 1.0, got {total}")

    def evaluate(self, store: PointInTimeStore, as_of_utc: datetime) -> FundamentalView:
        as_of = ensure_utc(as_of_utc)
        components: dict[str, FundamentalComponent] = {}
        diagnostics: dict[str, dict[str, Any]] = {}
        missing: list[str] = []
        stale: list[str] = []
        reasons: list[str] = []
        source_versions: dict[str, str] = {}
        score = 0.0

        for item in self.inputs:
            input_id = str(item["id"])
            weight = float(item.get("weight", 0.0))
            sign = str(item.get("sign", "contextual"))
            series_id = str(item.get("series_id") or item.get("field") or input_id)
            transform = str(item.get("transform", "level_change_20d"))
            transform_config = as_dict(self.transforms.get(input_id))
            observations = store.series(series_id, as_of)
            threshold = float(self.stale_after.get(input_id, 7))

            if not observations:
                component = FundamentalComponent(
                    input_id=input_id,
                    series_id=series_id,
                    value=None,
                    signal=0.0,
                    weight=weight,
                    contribution=0.0,
                    transform=transform,
                    sign=sign,
                    stale=True,
                    note="no point-in-time observation at or before as-of",
                )
                components[input_id] = component
                if weight > 0:
                    missing.append(input_id)
                    reasons.append(f"{input_id}: missing series {series_id}")
                else:
                    diagnostics[input_id] = {
                        "value": None,
                        "signal": 0.0,
                        "status": "missing",
                        "label": item.get("label"),
                    }
                continue

            latest = observations[-1]
            age_days = (as_of - latest.knowledge_time_utc).total_seconds() / 86400.0
            is_stale = age_days > threshold
            if is_stale and weight > 0:
                stale.append(input_id)
                reasons.append(
                    f"{input_id}: stale by {age_days:.1f} days (limit {threshold:.0f})"
                )
            value, signal, note = self._signal(
                observations=observations,
                transform=transform,
                transform_config=transform_config,
                sign=sign,
                latest=latest,
            )
            if value is None or signal is None:
                component = FundamentalComponent(
                    input_id=input_id,
                    series_id=series_id,
                    value=None,
                    signal=0.0,
                    weight=weight,
                    contribution=0.0,
                    transform=transform,
                    sign=sign,
                    stale=is_stale,
                    source_id=latest.source_id,
                    knowledge_time_utc=latest.knowledge_time_utc,
                    note=note,
                )
                components[input_id] = component
                if weight > 0:
                    missing.append(input_id)
                    reasons.append(f"{input_id}: {note}")
                else:
                    diagnostics[input_id] = {
                        "value": None,
                        "signal": 0.0,
                        "status": note,
                        "label": item.get("label"),
                    }
                continue

            contribution = weight * signal
            score += contribution
            component = FundamentalComponent(
                input_id=input_id,
                series_id=series_id,
                value=value,
                signal=signal,
                weight=weight,
                contribution=contribution,
                transform=transform,
                sign=sign,
                stale=is_stale,
                source_id=latest.source_id,
                knowledge_time_utc=latest.knowledge_time_utc,
                note=note,
            )
            components[input_id] = component
            source_versions[input_id] = f"{latest.source_id}:{latest.vintage or latest.knowledge_time_utc.isoformat()}"
            if weight == 0.0:
                diagnostics[input_id] = {
                    "value": value,
                    "signal": signal,
                    "status": "diagnostic",
                    "label": item.get("label"),
                    "knowledge_time_utc": latest.knowledge_time_utc.isoformat(),
                }
            else:
                reasons.append(
                    f"{input_id}: signal {signal:+.3f} x weight {weight:.2f} = {contribution:+.3f}"
                )

        score = clamp(score, -1.0, 1.0)
        is_valid = not missing and not stale
        if not is_valid:
            reasons.append("fundamental view invalid: missing or stale weight-bearing inputs")
        return FundamentalView(
            as_of_utc=as_of,
            score=score,
            components=components,
            diagnostics=diagnostics,
            missing_inputs=missing,
            stale_inputs=stale,
            is_valid=is_valid,
            reasons=reasons,
            source_versions=source_versions,
        )

    def _signal(
        self,
        *,
        observations: list[MacroObservation],
        transform: str,
        transform_config: dict[str, Any],
        sign: str,
        latest: MacroObservation,
    ) -> tuple[float | None, float | None, str]:
        kind = str(transform_config.get("kind", "")).lower()
        if not kind:
            kind = self._kind_from_name(transform)
        if kind == "percentile":
            return self._percentile_signal(observations, transform_config, sign)
        if kind == "change":
            days = int(transform_config.get("days", self._days_from_name(transform)))
            return self._change_signal(observations, days, sign)
        return None, None, f"unsupported transform kind: {kind or transform}"

    def _percentile_signal(
        self,
        observations: list[MacroObservation],
        config: dict[str, Any],
        sign: str,
    ) -> tuple[float | None, float | None, str]:
        lookback = int(config.get("lookback_observations", 156))
        extreme = float(config.get("extreme_threshold", 0.80))
        values = [item.value for item in observations[-lookback:]]
        if len(values) < self.min_observations:
            return None, None, f"insufficient observations: {len(values)} < {self.min_observations}"
        current = values[-1]
        percentile = percentile_rank(values, current)
        mode = str(config.get("mode", "contrarian_extremes")).lower()
        if mode == "contrarian_extremes":
            if percentile >= extreme:
                signal = -1.0
                note = f"percentile {percentile:.2f} >= {extreme:.2f}: crowded long"
            elif percentile <= 1.0 - extreme:
                signal = 1.0
                note = f"percentile {percentile:.2f} <= {1.0 - extreme:.2f}: crowded short"
            else:
                signal = 0.0
                note = f"percentile {percentile:.2f}: neutral"
        else:
            signal = clamp(2.0 * (percentile - 0.5), -1.0, 1.0)
            note = f"percentile {percentile:.2f}"
        if sign == "negative":
            signal = -signal
        return current, signal, note

    def _change_signal(
        self,
        observations: list[MacroObservation],
        days: int,
        sign: str,
    ) -> tuple[float | None, float | None, str]:
        if days <= 0:
            return None, None, f"invalid change window: {days}"
        changes: list[float] = []
        start = max(0, len(observations) - self.zscore_lookback)
        for index in range(start, len(observations)):
            current = observations[index]
            prior = self._prior_observation(observations, index, days)
            if prior is not None:
                changes.append(current.value - prior.value)
        if len(changes) < self.min_observations:
            return None, None, f"insufficient change observations: {len(changes)} < {self.min_observations}"
        current_change = changes[-1]
        average = mean(changes)
        deviation = stdev(changes)
        if deviation < 1e-12:
            zscore = 0.0
        else:
            zscore = (current_change - average) / deviation
        zscore = clamp(zscore, -self.zscore_clip, self.zscore_clip)
        signal = -zscore if sign == "negative" else zscore
        note = f"{days}d change {current_change:+.4f}, z={zscore:+.3f}"
        return current_change, signal, note

    @staticmethod
    def _prior_observation(
        observations: list[MacroObservation], index: int, days: int
    ) -> MacroObservation | None:
        current = observations[index]
        current_date = current.observation_date or current.knowledge_time_utc.date()
        target = current_date - timedelta(days=days)
        for candidate_index in range(index - 1, -1, -1):
            candidate = observations[candidate_index]
            candidate_date = candidate.observation_date or candidate.knowledge_time_utc.date()
            if candidate_date <= target:
                return candidate
        return None

    @staticmethod
    def _kind_from_name(transform: str) -> str:
        lowered = transform.lower()
        if "percentile" in lowered:
            return "percentile"
        if "change" in lowered:
            return "change"
        return ""

    @staticmethod
    def _days_from_name(transform: str) -> int:
        lowered = transform.lower()
        if "3m" in lowered:
            return 90
        for token in lowered.replace("d", " ").split("_"):
            if token.isdigit():
                return int(token)
        return 20
