"""Manifest loading and deterministic hashing."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import yaml


class ManifestError(RuntimeError):
    """Raised when a manifest cannot be loaded."""


def load_manifest(path: str | Path) -> dict[str, Any]:
    """Load a YAML manifest and require a mapping at the top level."""
    manifest_path = Path(path)
    if not manifest_path.exists():
        raise ManifestError(f"manifest not found: {manifest_path}")
    try:
        with manifest_path.open("r", encoding="utf-8") as handle:
            data = yaml.safe_load(handle)
    except yaml.YAMLError as exc:
        raise ManifestError(f"invalid YAML in {manifest_path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ManifestError(f"manifest must be a mapping: {manifest_path}")
    return data


def canonical_manifest_json(manifest: dict[str, Any]) -> str:
    """Return a stable JSON representation for hashing."""
    return json.dumps(
        manifest,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    )


def manifest_hash(manifest: dict[str, Any]) -> str:
    """Return the deterministic SHA-256 of a manifest."""
    return hashlib.sha256(canonical_manifest_json(manifest).encode("utf-8")).hexdigest()


def as_dict(value: Any) -> dict[str, Any]:
    """Return value if it is a dict, otherwise an empty dict."""
    return value if isinstance(value, dict) else {}


def as_list(value: Any) -> list[Any]:
    """Return value if it is a list, otherwise an empty list."""
    return value if isinstance(value, list) else []


def deep_get(data: dict[str, Any], dotted: str, default: Any = None) -> Any:
    """Read a dotted path from nested dicts."""
    current: Any = data
    for part in dotted.split("."):
        if not isinstance(current, dict) or part not in current:
            return default
        current = current[part]
    return current


def source_index(manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Index sources by ID. Later duplicates overwrite earlier ones."""
    index: dict[str, dict[str, Any]] = {}
    for source in as_list(manifest.get("sources")):
        if isinstance(source, dict) and source.get("id"):
            index[str(source["id"])] = source
    return index


def fundamental_index(manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Index fundamental inputs by ID."""
    index: dict[str, dict[str, Any]] = {}
    for item in as_list(manifest.get("fundamental_inputs")):
        if isinstance(item, dict) and item.get("id"):
            index[str(item["id"])] = item
    return index


def process_index(manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Index runtime processes by ID."""
    index: dict[str, dict[str, Any]] = {}
    for item in as_list(manifest.get("runtime_processes")):
        if isinstance(item, dict) and item.get("id"):
            index[str(item["id"])] = item
    return index
