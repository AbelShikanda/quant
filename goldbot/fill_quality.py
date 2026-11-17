"""Fill quality summary: slippage, RTT, and MT5-checked fills vs ledger fills.

Read-only reconciliation of what the broker did (MT5 deals) with what the
ledger recorded. Strong evidence for fill quality; never places orders.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def run_fill_quality(
    *,
    ledger_path: str | Path,
    feed: Any | None = None,
) -> dict[str, Any]:
    path = Path(ledger_path)
    if not path.exists():
        return {
            "mode": "fill_quality",
            "ok": False,
            "error": f"ledger not found: {path}",
            "fills": [],
        }

    connection = sqlite3.connect(
        f"file:{path.resolve().as_posix()}?mode=ro", uri=True, timeout=2.0
    )
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            """
            SELECT fill_id, order_id, filled_at_utc, volume, price,
                   slippage_points, broker_deal_ticket, raw_deal_json
            FROM fills ORDER BY filled_at_utc ASC LIMIT 500
            """
        ).fetchall()
    finally:
        connection.close()

    fills = [
        {
            "fill_id": row["fill_id"],
            "order_id": row["order_id"],
            "filled_at_utc": row["filled_at_utc"],
            "volume": row["volume"],
            "price": row["price"],
            "slippage_points": row["slippage_points"],
            "deal_ticket": row["broker_deal_ticket"],
        }
        for row in rows
    ]

    slippages = [float(f["slippage_points"]) for f in fills if f["slippage_points"] is not None]
    abs_slippages = [abs(s) for s in slippages]
    fills_with_deal = [f for f in fills if f.get("deal_ticket")]

    summary: dict[str, Any] = {
        "fills": len(fills),
        "fills_with_deal_ticket": len(fills_with_deal),
        "slippage_points": {
            "n": len(abs_slippages),
            "min": min(abs_slippages) if abs_slippages else None,
            "max": max(abs_slippages) if abs_slippages else None,
            "mean": round(sum(abs_slippages) / len(abs_slippages), 4) if abs_slippages else None,
        },
    }

    broker_deals: list[dict[str, Any]] = []
    owns_feed = feed is None
    if feed is None:
        try:
            from .data import MT5DataFeed

            feed = MT5DataFeed(symbol="XAUUSD", broker_utc_offset_minutes=120)
            feed.connect()
        except Exception:  # noqa: BLE001 - report, don't crash
            feed = None
    if feed is not None:
        try:
            raw_deals = feed.history_deals_get(0, datetime.now(timezone.utc).timestamp())
            broker_deals = [
                d
                for d in raw_deals
                if str(d.get("comment") or "").lower().startswith("goldbot")
                and str(d.get("symbol") or "").upper() == "XAUUSD"
            ]
        except Exception:  # noqa: BLE001 - best effort
            broker_deals = []
    summary["broker_deals_matched_prefix"] = len(broker_deals)

    if owns_feed and feed is not None:
        try:
            feed.close()
        except Exception:  # noqa: BLE001 - best effort
            pass

    return {
        "mode": "fill_quality",
        "ok": True,
        "ledger": str(path),
        "fills": fills,
        "broker_deals": broker_deals,
        "summary": summary,
        "note": "slippage = fill price - quoted price at signal time (points). Latency: send_rtt_ms recorded on demo sends.",
    }
