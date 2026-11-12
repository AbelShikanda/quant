from __future__ import annotations

import copy
from datetime import datetime, timezone
from pathlib import Path

from goldbot.contracts import FundamentalView, TechnicalView
from goldbot.fusion import FusionEngine
from goldbot.manifest import load_manifest


ROOT = Path(__file__).resolve().parents[1]
GOLD = ROOT / "config" / "scope.gold.v1.yaml"
AS_OF = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _fundamental(score: float, valid: bool = True) -> FundamentalView:
    return FundamentalView(
        as_of_utc=AS_OF,
        score=score,
        components={},
        diagnostics={},
        is_valid=valid,
    )


def _technical(
    score: float,
    *,
    valid: bool = True,
    triggers: dict[str, bool] | None = None,
) -> TechnicalView:
    return TechnicalView(
        as_of_utc=AS_OF,
        score=score,
        components={},
        indicators={},
        entry_triggers=triggers
        or {
            "h4_context": True,
            "m15_trigger": True,
            "atr_filter": True,
            "spread_filter": True,
            "no_news_blackout": True,
        },
        is_valid=valid,
    )


def test_both_layers_agree_and_threshold_passes() -> None:
    engine = FusionEngine(load_manifest(GOLD))
    view = engine.evaluate(_fundamental(0.5), _technical(0.5))
    assert view.conflict is False
    assert view.combined_score == 0.5
    assert view.entry_candidate is True


def test_layers_disagree_produces_no_entry() -> None:
    engine = FusionEngine(load_manifest(GOLD))
    view = engine.evaluate(_fundamental(0.5), _technical(-0.5))
    assert view.conflict is True
    assert view.entry_candidate is False


def test_scores_below_minimum_produce_no_entry() -> None:
    engine = FusionEngine(load_manifest(GOLD))
    view = engine.evaluate(_fundamental(0.01), _technical(0.01))
    assert view.entry_candidate is False


def test_missing_technical_trigger_produces_no_entry() -> None:
    engine = FusionEngine(load_manifest(GOLD))
    triggers = {
        "h4_context": True,
        "m15_trigger": False,
        "atr_filter": True,
        "spread_filter": True,
        "no_news_blackout": True,
    }
    view = engine.evaluate(_fundamental(0.5), _technical(0.5, triggers=triggers))
    assert view.entry_candidate is False


def test_invalid_layer_produces_no_entry() -> None:
    engine = FusionEngine(load_manifest(GOLD))
    view = engine.evaluate(_fundamental(0.5, valid=False), _technical(0.5))
    assert view.entry_candidate is False


def test_required_triggers_are_read_from_manifest() -> None:
    manifest = copy.deepcopy(load_manifest(GOLD))
    manifest["technical"]["entry_requires"] = ["spread_filter"]
    engine = FusionEngine(manifest)
    triggers = {
        "h4_context": True,
        "m15_trigger": False,
        "atr_filter": True,
        "spread_filter": True,
        "no_news_blackout": True,
    }
    view = engine.evaluate(_fundamental(0.5), _technical(0.5, triggers=triggers))
    assert view.entry_candidate is True
