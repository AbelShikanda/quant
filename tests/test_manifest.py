from __future__ import annotations

import json
from pathlib import Path

from goldbot.manifest import canonical_manifest_json, load_manifest, manifest_hash


ROOT = Path(__file__).resolve().parents[1]
GOLD = ROOT / "config" / "scope.gold.v1.yaml"
BROKEN = ROOT / "config" / "scope.broken.sec_edgar.yaml"
CALIBRATION = ROOT / "config" / "calibration" / "gold.v1.overseer.json"


def test_gold_manifest_loads() -> None:
    manifest = load_manifest(GOLD)
    assert manifest["scope_id"] == "gold.v1"
    assert manifest["market"]["symbol"] == "XAUUSD"
    assert manifest["market"]["execution_bar"] == "M15"
    assert manifest["market"]["context_bar"] == "H4"
    assert len(manifest["sources"]) == 12
    assert len(manifest["fundamental_inputs"]) == 6
    assert len(manifest["validation_items"]) == 37


def test_manifest_hash_is_stable() -> None:
    first = load_manifest(GOLD)
    second = load_manifest(GOLD)
    assert manifest_hash(first) == manifest_hash(second)
    assert canonical_manifest_json(first) == canonical_manifest_json(second)


def test_gold_manifest_hash_matches_calibration_artifact() -> None:
    manifest = load_manifest(GOLD)
    artifact = json.loads(CALIBRATION.read_text(encoding="utf-8"))
    assert artifact["scope_id"] == manifest["scope_id"]
    assert artifact["manifest_hash"] == manifest_hash(manifest)
    assert artifact["status"] == "not_fitted"


def test_broken_manifest_loads() -> None:
    manifest = load_manifest(BROKEN)
    assert manifest["scope_id"] == "broken.sec_edgar_gold.v1"
    assert manifest["sources"][0]["type"] == "sec_edgar"
