"""Fusion: combine the fundamental "what" with the technical "when"."""

from __future__ import annotations

from typing import Any

from .contracts import FundamentalView, FusionView, TechnicalView
from .indicators import clamp
from .manifest import as_dict, as_list


class FusionConfigError(RuntimeError):
    """Raised when fusion parameters are missing from the manifest."""


class FusionEngine:
    def __init__(self, manifest: dict[str, Any]) -> None:
        fusion = as_dict(manifest.get("fusion"))
        self.fundamental_weight = float(fusion.get("fundamental_weight", 0.0))
        self.technical_weight = float(fusion.get("technical_weight", 0.0))
        self.entry_requires_both = bool(fusion.get("entry_requires_both", True))
        self.conflict_policy = str(fusion.get("conflict_policy", "no_trade"))
        if self.conflict_policy not in {"no_trade", "prefer_fundamental", "prefer_technical"}:
            raise FusionConfigError(f"unsupported conflict_policy: {self.conflict_policy}")
        technical_config = as_dict(manifest.get("technical"))
        self.required_triggers = [
            str(item) for item in as_list(technical_config.get("entry_requires"))
        ]
        strategy_fusion = as_dict(as_dict(manifest.get("strategy")).get("fusion"))
        if "entry_threshold" not in strategy_fusion:
            raise FusionConfigError("missing strategy.fusion.entry_threshold")
        self.entry_threshold = float(strategy_fusion["entry_threshold"])
        self.fundamental_min = float(strategy_fusion.get("fundamental_min", 0.0))
        self.technical_min = float(strategy_fusion.get("technical_min", 0.0))

    def evaluate(self, fundamental: FundamentalView, technical: TechnicalView) -> FusionView:
        fundamental_contribution = self.fundamental_weight * fundamental.score
        technical_contribution = self.technical_weight * technical.score
        combined = clamp(fundamental_contribution + technical_contribution, -1.0, 1.0)
        conflict = (
            (fundamental.score > 0.0 and technical.score < 0.0)
            or (fundamental.score < 0.0 and technical.score > 0.0)
        )
        reasons: list[str] = [
            f"fundamental={fundamental.score:+.3f} x {self.fundamental_weight:.2f} "
            f"= {fundamental_contribution:+.3f}",
            f"technical={technical.score:+.3f} x {self.technical_weight:.2f} "
            f"= {technical_contribution:+.3f}",
            f"combined={combined:+.3f}, entry_threshold={self.entry_threshold:.3f}",
        ]
        if conflict:
            reasons.append(f"layers disagree; conflict policy is {self.conflict_policy}")
        if not fundamental.is_valid:
            reasons.append("fundamental view is invalid")
        if not technical.is_valid:
            reasons.append("technical view is invalid")
        fundamental_ok = fundamental.score >= self.fundamental_min
        technical_ok = technical.score >= self.technical_min
        if not fundamental_ok:
            reasons.append(
                f"fundamental score below minimum ({fundamental.score:+.3f} < {self.fundamental_min:+.3f})"
            )
        if not technical_ok:
            reasons.append(
                f"technical score below minimum ({technical.score:+.3f} < {self.technical_min:+.3f})"
            )
        layers_ok = (
            (fundamental_ok and technical_ok)
            if self.entry_requires_both
            else (fundamental_ok or technical_ok)
        )
        if combined < self.entry_threshold:
            reasons.append(
                f"combined score below entry threshold ({combined:+.3f} < {self.entry_threshold:+.3f})"
            )
        required_triggers = self.required_triggers or sorted(technical.entry_triggers)
        triggers_ok = bool(required_triggers) and all(
            technical.entry_triggers.get(key, False) for key in required_triggers
        )
        if not triggers_ok:
            failed = [key for key in required_triggers if not technical.entry_triggers.get(key, False)]
            reasons.append("technical entry triggers not satisfied: " + ", ".join(failed))
        conflict_blocks = conflict and self.conflict_policy == "no_trade"
        entry_candidate = (
            fundamental.is_valid
            and technical.is_valid
            and not conflict_blocks
            and layers_ok
            and combined >= self.entry_threshold
            and triggers_ok
        )
        return FusionView(
            as_of_utc=fundamental.as_of_utc,
            combined_score=combined,
            fundamental_contribution=fundamental_contribution,
            technical_contribution=technical_contribution,
            entry_candidate=entry_candidate,
            conflict=conflict,
            reasons=reasons,
        )
