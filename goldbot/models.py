"""Report and result models for the headless validator."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


PASS = "PASS"
FAIL = "FAIL"
HUMAN_JUDGMENT = "HUMAN_JUDGMENT"
SKIP = "SKIP"
BLOCKED = "BLOCKED"

MECHANICAL = "MECHANICAL"
NOT_VALIDATABLE = "NOT_VALIDATABLE_AT_DESIGN_TIME"


@dataclass
class CheckOutcome:
    """The raw result returned by a check function."""

    status: str
    detail: str
    evidence: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.status not in {PASS, FAIL, HUMAN_JUDGMENT, SKIP, BLOCKED}:
            raise ValueError(f"invalid check status: {self.status}")


@dataclass
class ValidationResult:
    """A single manifest validation item, with its check outcome."""

    item_id: str
    category: str
    what: str
    where: str
    validation_class: str
    required_for_m1: bool
    runtime_only: bool
    status: str
    detail: str
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.item_id,
            "category": self.category,
            "what": self.what,
            "where": self.where,
            "validation_class": self.validation_class,
            "required_for_m1": self.required_for_m1,
            "runtime_only": self.runtime_only,
            "status": self.status,
            "detail": self.detail,
            "evidence": self.evidence,
        }


@dataclass
class ScopeReport:
    """Complete validation report for one scope manifest."""

    scope_id: str
    manifest_path: str
    manifest_hash: str
    generated_at: str
    results: list[ValidationResult]
    offline: bool = False
    no_mt5: bool = False

    @classmethod
    def build(
        cls,
        *,
        scope_id: str,
        manifest_path: str,
        manifest_hash: str,
        results: list[ValidationResult],
        offline: bool = False,
        no_mt5: bool = False,
    ) -> "ScopeReport":
        return cls(
            scope_id=scope_id,
            manifest_path=manifest_path,
            manifest_hash=manifest_hash,
            generated_at=datetime.now(timezone.utc).isoformat(),
            results=results,
            offline=offline,
            no_mt5=no_mt5,
        )

    @property
    def required_failures(self) -> list[ValidationResult]:
        return [
            r
            for r in self.results
            if r.status == FAIL and r.required_for_m1 and not r.runtime_only
        ]

    @property
    def runtime_blockers(self) -> list[ValidationResult]:
        return [
            r
            for r in self.results
            if r.status in {FAIL, BLOCKED} and r.runtime_only
        ]

    @property
    def human_judgment_items(self) -> list[ValidationResult]:
        return [r for r in self.results if r.status == HUMAN_JUDGMENT]

    @property
    def skipped_checks(self) -> list[ValidationResult]:
        return [r for r in self.results if r.status == SKIP]

    @property
    def m1_status(self) -> str:
        return FAIL if self.required_failures else PASS

    @property
    def runtime_status(self) -> str:
        if self.runtime_blockers:
            return BLOCKED
        return PASS

    @property
    def overall_status(self) -> str:
        if self.required_failures:
            return FAIL
        if self.runtime_blockers:
            return "PARTIAL"
        return PASS

    @property
    def counts(self) -> dict[str, int]:
        counts = {PASS: 0, FAIL: 0, HUMAN_JUDGMENT: 0, SKIP: 0, BLOCKED: 0}
        for result in self.results:
            counts[result.status] = counts.get(result.status, 0) + 1
        return counts

    def to_dict(self) -> dict[str, Any]:
        return {
            "scope_id": self.scope_id,
            "manifest_path": self.manifest_path,
            "manifest_hash": self.manifest_hash,
            "generated_at": self.generated_at,
            "offline": self.offline,
            "no_mt5": self.no_mt5,
            "overall_status": self.overall_status,
            "m1_status": self.m1_status,
            "runtime_status": self.runtime_status,
            "counts": self.counts,
            "required_failures": [r.item_id for r in self.required_failures],
            "runtime_blockers": [r.item_id for r in self.runtime_blockers],
            "human_judgment_items": [r.item_id for r in self.human_judgment_items],
            "skipped_checks": [r.item_id for r in self.skipped_checks],
            "results": [r.to_dict() for r in self.results],
        }
