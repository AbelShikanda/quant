"""SELL review: how well did the bot's exits work?

Read-only. Scans the ledger for EXIT decisions and their fills/positions,
then reports entry/exit quality. Exits are first-class; this is the
feedback dossier for the SELL side of the book.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any


def run_sell_review(*, ledger_path: str | Path) -> dict[str, Any]:
    path = Path(ledger_path)
    if not path.exists():
        return {
            "mode": "sell_review",
            "ok": False,
            "error": f"ledger not found: {path}",
            "exits": [],
            "summary": {"exits": 0},
        }

    connection = sqlite3.connect(
        f"file:{path.resolve().as_posix()}?mode=ro", uri=True, timeout=2.0
    )
    connection.row_factory = sqlite3.Row
    try:
        exits = connection.execute(
            """
            SELECT decision_id, as_of_utc, decision_type, side, payload_json
            FROM strategy_decisions
            WHERE decision_type = 'EXIT'
            ORDER BY as_of_utc ASC
            """
        ).fetchall()
        rows: list[dict[str, Any]] = []
        for row in exits:
            payload = json.loads(row["payload_json"] or "{}")
            exit_decision = payload.get("exit_decision") or {}
            rows.append(
                {
                    "decision_id": row["decision_id"],
                    "as_of_utc": row["as_of_utc"],
                    "exit_reason": exit_decision.get("reason"),
                    "exit_priority": exit_decision.get("priority"),
                    "position_side": exit_decision.get("side", "LONG"),
                    "verdict": exit_decision.get("verdict"),
                    "vetoes": exit_decision.get("vetoes", []),
                    "unrealized_r_at_exit": exit_decision.get("unrealized_r_at_exit"),
                }
            )
        exit_fills = connection.execute(
            """
            SELECT order_id, fill_id, filled_at_utc, price, volume, slippage_points
            FROM fills ORDER BY filled_at_utc ASC LIMIT 500
            """
        ).fetchall()
        return {
            "mode": "sell_review",
            "ok": True,
            "ledger": str(path),
            "exits": rows,
            "exit_fill_count": len(exit_fills),
            "summary": {
                "exits": len(rows),
                "risk_veto_immune_exits": sum(1 for r in rows if "kill_switch" in (r.get("vetoes") or []) or r.get("exit_priority") == 100),
                "distinct_reasons": sorted({str(r.get("exit_reason")) for r in rows}),
            },
        }
    finally:
        connection.close()
