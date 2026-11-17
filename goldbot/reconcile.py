"""Post-trade reconciliation: compare the bot's ledger against the MT5 broker.

Read-only. This module never places, modifies, or closes any order.
It answers two questions:

1. Does what the bot believes match what the broker holds (positions)?
2. Do the bot's recorded fills match the broker's deal history?

Mismatches must stop the machine for review, not be papered over.
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
    # deals-level reconciliation (order/fill equality with the broker)
    ledger_fill_count: int = 0
    mt5_deal_count: int = 0
    deals_matched: list[int] = field(default_factory=list)
    deals_ledger_only: list[int] = field(default_factory=list)
    deals_broker_only: list[int] = field(default_factory=list)
    deal_mismatches: list[dict[str, Any]] = field(default_factory=list)
    deals_error: str | None = None
    deals_verdict: str = "UNKNOWN"

    @property
    def ok(self) -> bool:
        # A deals-level ERROR or MISMATCH means "we do not know" or "this is
        # wrong" — neither is acceptable for a promotion gate.
        return self.verdict == "MATCH" and self.deals_verdict == "MATCH"

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
            "ledger_fill_count": self.ledger_fill_count,
            "mt5_deal_count": self.mt5_deal_count,
            "deals_matched": sorted(int(t) for t in self.deals_matched),
            "deals_ledger_only": sorted(int(t) for t in self.deals_ledger_only),
            "deals_broker_only": sorted(int(t) for t in self.deals_broker_only),
            "deal_mismatches": self.deal_mismatches,
            "deals_error": self.deals_error,
            "deals_verdict": self.deals_verdict,
        }


def compare_fill_sets(
    ledger_fills: list[dict[str, Any]],
    mt5_deals: list[dict[str, Any]],
) -> dict[str, Any]:
    """Pure comparison of ledger fills vs MT5 deal history (by broker deal ticket)."""
    ledger_by_ticket: dict[int, dict[str, Any]] = {}
    for fill in ledger_fills:
        ticket = fill.get("broker_deal_ticket")
        if ticket is not None:
            ledger_by_ticket[int(ticket)] = fill

    mt5_by_ticket: dict[int, dict[str, Any]] = {}
    for deal in mt5_deals:
        ticket = deal.get("ticket")
        if ticket is not None:
            mt5_by_ticket[int(ticket)] = deal

    matched: list[int] = sorted(set(ledger_by_ticket) & set(mt5_by_ticket))
    ledger_only: list[int] = sorted(set(ledger_by_ticket) - set(mt5_by_ticket))
    mt5_only: list[int] = sorted(set(mt5_by_ticket) - set(ledger_by_ticket))

    mismatches: list[dict[str, Any]] = []
    for ticket in matched:
        fill = ledger_by_ticket[ticket]
        deal = mt5_by_ticket[ticket]
        problems: list[str] = []
        fill_volume = float(fill.get("volume") or 0.0)
        deal_volume = float(deal.get("volume") or 0.0)
        if abs(fill_volume - deal_volume) > 1e-9:
            problems.append(f"volume ledger={fill_volume} mt5={deal_volume}")
        fill_price = float(fill.get("price") or 0.0)
        deal_price = float(deal.get("price") or 0.0)
        if fill_price and deal_price and abs(fill_price - deal_price) > 1e-6:
            problems.append(f"price ledger={fill_price} mt5={deal_price}")
        if problems:
            mismatches.append({"ticket": ticket, "problems": problems})

    return {
        "matched": matched,
        "ledger_only": ledger_only,
        "mt5_only": mt5_only,
        "mismatches": mismatches,
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

    mt5_deals: list[dict[str, Any]] = []
    deals_error: str | None = None
    try:
        try:
            feed.connect()
            mt5_open = feed.open_positions() or []
            mt5_available = True
            try:
                mt5_deals = list(feed.history_deals_get(0, utc_now().timestamp()) or [])
                mt5_deals = [
                    deal
                    for deal in mt5_deals
                    if str(deal.get("symbol") or "XAUUSD").upper() == symbol.upper()
                    and str(deal.get("comment") or "").lower().startswith("goldbot")
                ]
            except Exception as exc:  # noqa: BLE001 - surface, don't crash
                deals_error = f"{type(exc).__name__}: {exc}"
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
        ledger_fills = ledger.recent_fills(limit=500)

    deals_comparison = compare_fill_sets(ledger_fills, mt5_deals)
    if deals_error is not None:
        deals_verdict = "ERROR"
    elif deals_comparison["ledger_only"] or deals_comparison["mt5_only"] or deals_comparison["mismatches"]:
        deals_verdict = "MISMATCH"
    else:
        deals_verdict = "MATCH"

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
        ledger_fill_count=len(ledger_fills),
        mt5_deal_count=len(mt5_deals),
        deals_matched=deals_comparison["matched"],
        deals_ledger_only=deals_comparison["ledger_only"],
        deals_broker_only=deals_comparison["mt5_only"],
        deal_mismatches=deals_comparison["mismatches"],
        deals_error=deals_error,
        deals_verdict=deals_verdict,
    )
