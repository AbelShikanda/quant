"""Explicit account-profile overlays for the scope manifest.

The base manifest is the safe scope.  An account profile is a versioned,
reviewable overlay that changes the account model, leverage cap, sizing guard,
and risk limits for a specific account size.  Profiles are never inferred from
equity at runtime: the caller must select one explicitly.

A profile may declare ``high_risk_override: true``.  Such a profile is allowed
to run only with a visible warning and is not promotion-eligible.  The base
manifest and the validator remain the authority for the safe scope.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import yaml

from .manifest import ManifestError, as_dict, load_manifest


class ProfileError(RuntimeError):
    """Raised when an account profile cannot be loaded or applied."""


def load_profile(path: str | Path) -> dict[str, Any]:
    """Load a YAML account profile and require the expected structure."""
    profile_path = Path(path)
    if not profile_path.exists():
        raise ProfileError(f"account profile not found: {profile_path}")
    try:
        with profile_path.open("r", encoding="utf-8") as handle:
            data = yaml.safe_load(handle)
    except yaml.YAMLError as exc:
        raise ProfileError(f"invalid YAML in {profile_path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ProfileError(f"account profile must be a mapping: {profile_path}")
    if not data.get("profile_id"):
        raise ProfileError(f"account profile missing profile_id: {profile_path}")
    if not isinstance(data.get("overrides"), dict):
        raise ProfileError(f"account profile missing overrides mapping: {profile_path}")
    return data


def deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    """Return a deep-merged copy of ``base`` with ``overlay`` applied."""
    result = copy.deepcopy(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def apply_profile(manifest: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any]:
    """Apply an account profile to a manifest and annotate the result."""
    profile_id = str(profile.get("profile_id", "unknown"))
    merged = deep_merge(manifest, as_dict(profile.get("overrides")))
    merged["active_account_profile"] = profile_id
    merged["account_profile_description"] = str(profile.get("description", ""))
    merged["profile_high_risk_override"] = bool(profile.get("high_risk_override", False))
    merged["profile_promotion_eligible"] = bool(profile.get("promotion_eligible", True))
    return merged


def load_manifest_with_profile(
    manifest_path: str | Path,
    profile_path: str | Path | None = None,
) -> dict[str, Any]:
    """Load a manifest and optionally apply an explicit account profile."""
    manifest = load_manifest(manifest_path)
    if profile_path is None:
        return manifest
    try:
        profile = load_profile(profile_path)
    except ProfileError:
        raise
    except ManifestError as exc:  # pragma: no cover - defensive
        raise ProfileError(str(exc)) from exc
    return apply_profile(manifest, profile)
