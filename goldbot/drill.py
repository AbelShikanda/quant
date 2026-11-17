"""Reconnect drill: simulate a broker/terminal reconnect and prove state recovery.

What this drill proves (and what it cannot):

- It proves the Python -> MT5 session can drop and re-establish, that the
  ledger is unchanged by the drop, and that ledger and broker agree on open
  positions afterward.
- It cannot prove anything about a live strategy bar that was mid-flight
  during a disconnect; that requires tick-level replay (a P0/P1 item).

The drill is read-only on the market: it never sends, modifies, or closes
any order. It DOES write drill evidence into the ledger (an event and an
incident), because a drill that leaves no trace is not a drill.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from .contracts import utc_now
from .ledger import Ledger
from .reconcile import compare_position_sets


@dataclass
class DrillCheck:
    name: str
    status: str  # PASS | FAIL | BLOCKED
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "status": self.status, "detail": self.detail}


@dataclass
class ReconnectDrillResult:
    generated_at_utc: datetime
    ledger_path: str
    checks: list[DrillCheck] = field(default_factory=list)
    positions_before: list[dict[str, Any]] = field(default_factory=list)
    positions_after: list[dict[str, Any]] = field(default_factory=list)
    ledger_open: list[dict[str, Any]] = field(default_factory=list)
    orders_before: int = 0
    orders_after: int = 0
    incident_id: str | None = None

    @property
    def verdict(self) -> str:
        if any(check.status == "BLOCKED" for check in self.checks):
            return "BLOCKED"
        if any(check.status == "FAIL" for check in self.checks):
            return "FAIL"
        return "PASS" if self.checks else "FAIL"

    @property
    def ok(self) -> bool:
        return self.verdict == "PASS"

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": "reconnect_drill",
            "generated_at_utc": self.generated_at_utc.isoformat(),
            "ledger_path": self.ledger_path,
            "verdict": self.verdict,
            "positions_before": self.positions_before,
            "positions_after": self.positions_after,
            "ledger_open": self.ledger_open,
            "orders_before": self.orders_before,
            "orders_after": self.orders_after,
            "checks": [check.to_dict() for check in self.checks],
            "incident_id": self.incident_id,
        }


def evaluate_drill_checks(
    *,
    first_session_ok: bool,
    reconnect_ok: bool,
    positions_before: list[dict[str, Any]],
    positions_after: list[dict[str, Any]],
    ledger_open: list[dict[str, Any]],
    orders_before: int,
    orders_after: int,
) -> list[DrillCheck]:
    checks: list[DrillCheck] = [
        DrillCheck(
            "mt5_first_session",
            "PASS" if first_session_ok else "BLOCKED",
            "MT5 terminal connected in session 1"
            if first_session_ok
            else "MT5 terminal did not connect in session 1",
        ),
        DrillCheck(
            "mt5_reconnect_session",
            "PASS" if reconnect_ok else "BLOCKED",
            "MT5 terminal reconnected in session 2"
            if reconnect_ok
            else "MT5 terminal did not reconnect in session 2",
        ),
    ]

    before_tickets = sorted(
        int(position.get("ticket", 0)) for position in positions_before
    )
    after_tickets = sorted(
        int(position.get("ticket", 0)) for position in positions_after
    )
    positions_stable = before_tickets == after_tickets
    checks.append(
        DrillCheck(
            "positions_stable_across_reconnect",
            "PASS" if positions_stable else "FAIL",
            f"open positions before={before_tickets} after={after_tickets}",
        )
    )

    orders_stable = orders_before == orders_after
    checks.append(
        DrillCheck(
            "order_ledger_stable",
            "PASS" if orders_stable else "FAIL",
            f"ledger orders before={orders_before} after={orders_after}",
        )
    )

    comparison = compare_position_sets(ledger_open, positions_after)
    aligned = not (
        comparison["ledger_only"]
        or comparison["mt5_only"]
        or comparison["mismatches"]
    )
    checks.append(
        DrillCheck(
            "ledger_matches_broker_after_reconnect",
            "PASS" if aligned else "FAIL",
            (
                "ledger and broker agree after reconnect"
                if aligned
                else f"ledger_only={comparison['ledger_only']} "
                f"mt5_only={comparison['mt5_only']} mismatches={comparison['mismatches']}"
            ),
        )
    )
    return checks


def _counts(ledger: Ledger) -> dict[str, int]:
    try:
        return ledger.counts()
    except Exception:  # noqa: BLE001 - best effort for evidence
        return {}


def run_reconnect_drill(
    *,
    ledger_path: str | Path,
    symbol: str = "XAUUSD",
    broker_utc_offset_minutes: int = 120,
    feed_factory: Any | None = None,
) -> ReconnectDrillResult:
    generated_at = utc_now()
    ledger_text = str(ledger_path)
    checks: list[DrillCheck] = []

    def _make_feed() -> Any:
        if feed_factory is not None:
            return feed_factory()
        from .data import MT5DataFeed

        return MT5DataFeed(
            symbol=symbol, broker_utc_offset_minutes=broker_utc_offset_minutes
        )

    # Session 1: initial connect, snapshot broker state.
    feed1 = _make_feed()
    first_ok = False
    positions_before: list[dict[str, Any]] = []
    try:
        feed1.connect()
        first_ok = True
        positions_before = feed1.open_positions(symbol) or []
    except Exception:
        first_ok = False
    finally:
        try:
            feed1.close()
        except Exception:  # noqa: BLE001 - best effort
            pass

    with Ledger(ledger_path) as ledger:
        counts_before = _counts(ledger)
        orders_before = int(counts_before.get("orders", 0))
        ledger_open = ledger.open_positions()

    # Session 2: reconnect and snapshot again (the actual drill).
    feed2 = _make_feed()
    reconnect_ok = False
    positions_after: list[dict[str, Any]] = []
    try:
        feed2.connect()
        reconnect_ok = True
        positions_after = feed2.open_positions(symbol) or []
    except Exception:
        reconnect_ok = False
    finally:
        try:
            feed2.close()
        except Exception:  # noqa: BLE001 - best effort
            pass

    with Ledger(ledger_path) as ledger:
        counts_after = _counts(ledger)
        orders_after = int(counts_after.get("orders", 0))

    checks = evaluate_drill_checks(
        first_session_ok=first_ok,
        reconnect_ok=reconnect_ok,
        positions_before=positions_before,
        positions_after=positions_after,
        ledger_open=ledger_open,
        orders_before=orders_before,
        orders_after=orders_after,
    )

    verdict = "BLOCKED" if any(c.status == "BLOCKED" for c in checks) else (
        "FAIL" if any(c.status == "FAIL" for c in checks) else "PASS"
    )

    incident_id = (
        "reconnect-drill-"
        + hashlib.sha256(generated_at.isoformat().encode("utf-8")).hexdigest()[:16]
    )
    with Ledger(ledger_path) as ledger:
        ledger.log_event(
            level="INFO" if verdict == "PASS" else "CRITICAL",
            component="reconnect_drill",
            event_type="reconnect_drill",
            message=f"reconnect drill verdict={verdict}",
            correlation_id=incident_id,
            payload={
                "checks": [c.to_dict() for c in checks],
                "positions_before": positions_before,
                "positions_after": positions_after,
                "orders_before": orders_before,
                "orders_after": orders_after,
            },
            logged_at_utc=generated_at,
        )
        ledger.record_incident(
            incident_id=incident_id,
            opened_at_utc=generated_at,
            closed_at_utc=generated_at if verdict == "PASS" else None,
            severity="info" if verdict == "PASS" else "critical",
            category="reconnect_drill",
            summary=f"reconnect drill verdict={verdict}",
            details={
                "checks": [c.to_dict() for c in checks],
                "positions_before": positions_before,
                "positions_after": positions_after,
                "orders_before": orders_before,
                "orders_after": orders_after,
            },
            action_taken="none" if verdict == "PASS" else "investigate",
            resolved_by="drill" if verdict == "PASS" else None,
        )

    return ReconnectDrillResult(
        generated_at_utc=generated_at,
        ledger_path=ledger_text,
        checks=checks,
        positions_before=positions_before,
        positions_after=positions_after,
        ledger_open=ledger_open,
        orders_before=orders_before,
        orders_after=orders_after,
        incident_id=incident_id,
    )
