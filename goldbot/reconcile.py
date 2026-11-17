"""Post-trade reconciliation: compare the bot's ledger against the MT5 broker.

Read-only. This module never places, modifies, or closes any order.
It answers one question: *does what the bot believes match what the broker
actually holds?* A mismatch must stop the machine for review, not be
papered over.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from .contracts import utc_now
from .ledger import Ledger


@dataclass
class ReconcileReport:
    generated_at_utc: datetime
    ledger_path: str
    mt5_available: bool
    mt5_error: str | None
    ledger_open_count: int
    mt5_open_count: int
    matched: list[str] = field(default_factory=list)
    ledger_only: list[str] = field(default_factory=list)
    mt5_only: list[str] = field(default_factory=list)
    mismatches: list[dict[str, Any]] = field(default_factory=list)
    verdict: str = "UNKNOWN"

    @property
    def ok(self) -> bool:
        return self.verdict == "MATCH"

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": "reconcile",
            "generated_at_utc": self.generated_at_utc.isoformat(),
            "ledger_path": self.ledger_path,
            "mt5_available": self.mt5_available,
            "mt5_error": self.mt5_error,
            "ledger_open_count": self.ledger_open_count,
            "mt5_open_count": self.mt5_open_count,
            "matched": sorted(self.matched),
            "ledger_only": sorted(self.ledger_only),
            "mt5_only": sorted(self.mt5_only),
            "mismatches": self.mismatches,
            "verdict": self.verdict,
        }


def compare_position_sets(
    ledger_open: list[dict[str, Any]],
    mt5_open: list[dict[str, Any]],
) -> dict[str, Any]:
    """Pure comparison of ledger vs broker open XAUUSD positions.

    Both inputs are lists of dicts. ``ledger_open`` comes from
    ``Ledger.open_positions()``; ``mt5_open`` comes from MT5's position list.
    Matching is by broker ticket (ledger ``broker_position_ticket`` vs MT5 ``ticket``).
    """
    ledger_by_ticket: dict[str, dict[str, Any]] = {}
    for row in ledger_open:
        ticket = row.get("broker_position_ticket")
        if ticket is not None:
            ledger_by_ticket[str(int(ticket))] = row

    mt5_by_ticket: dict[str, dict[str, Any]] = {}
    for position in mt5_open:
        ticket = position.get("ticket")
        if ticket is not None:
            mt5_by_ticket[str(int(ticket))] = position

    matched: list[str] = []
    ledger_only: list[str] = sorted(set(ledger_by_ticket) - set(mt5_by_ticket))
    mt5_only: list[str] = sorted(set(mt5_by_ticket) - set(ledger_by_ticket))
    mismatches: list[dict[str, Any]] = []

    for ticket in sorted(set(ledger_by_ticket) & set(mt5_by_ticket)):
        row = ledger_by_ticket[ticket]
        position = mt5_by_ticket[ticket]
        problems: list[str] = []

        ledger_volume = float(row.get("volume") or 0.0)
        mt5_volume = float(position.get("volume") or 0.0)
        if abs(ledger_volume - mt5_volume) > 1e-9:
            problems.append(f"volume ledger={ledger_volume} mt5={mt5_volume}")

        ledger_sl = float(row.get("stop_loss") or 0.0)
        mt5_sl = float(position.get("sl") or 0.0)
        if abs(ledger_sl - mt5_sl) > 1e-6:
            problems.append(f"stop_loss ledger={ledger_sl} mt5={mt5_sl}")

        ledger_tp = float(row.get("take_profit") or 0.0)
        mt5_tp = float(position.get("tp") or 0.0)
        if abs(ledger_tp - mt5_tp) > 1e-6:
            problems.append(f"take_profit ledger={ledger_tp} mt5={mt5_tp}")

        if problems:
            mismatches.append({"ticket": ticket, "problems": problems})
        else:
            matched.append(ticket)

    return {
        "matched": matched,
        "ledger_only": ledger_only,
        "mt5_only": mt5_only,
        "mismatches": mismatches,
    }


def run_reconcile(
    *,
    ledger_path: str | Path,
    symbol: str = "XAUUSD",
    broker_utc_offset_minutes: int = 120,
    feed: Any | None = None,
) -> ReconcileReport:
    """Compare the ledger against the broker. Read-only; no orders are sent."""
    generated_at = utc_now()
    mt5_error: str | None = None
    mt5_open: list[dict[str, Any]] = []
    mt5_available = False

    owns_feed = feed is None
    if feed is None:
        from .data import MT5DataFeed

        feed = MT5DataFeed(
            symbol=symbol, broker_utc_offset_minutes=broker_utc_offset_minutes
        )

    try:
        try:
            feed.connect()
            mt5_open = feed.open_positions() or []
            mt5_available = True
        except Exception as exc:  # noqa: BLE001 - report, don't crash
            mt5_error = f"{type(exc).__name__}: {exc}"
    finally:
        if owns_feed:
            try:
                feed.close()
            except Exception:  # noqa: BLE001 - best-effort close
                pass

    with Ledger(ledger_path) as ledger:
        ledger_open = ledger.open_positions()

    comparison = compare_position_sets(ledger_open, mt5_open)
    if not mt5_available:
        verdict = "BLOCKED"
    elif comparison["ledger_only"] or comparison["mt5_only"] or comparison["mismatches"]:
        verdict = "MISMATCH"
    else:
        verdict = "MATCH"

    return ReconcileReport(
        generated_at_utc=generated_at,
        ledger_path=str(ledger_path),
        mt5_available=mt5_available,
        mt5_error=mt5_error,
        ledger_open_count=len(ledger_open),
        mt5_open_count=len(mt5_open),
        matched=comparison["matched"],
        ledger_only=comparison["ledger_only"],
        mt5_only=comparison["mt5_only"],
        mismatches=comparison["mismatches"],
        verdict=verdict,
    )
