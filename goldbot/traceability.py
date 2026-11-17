"""Decision reconstruction from the ledger for human accountability.

Answers the audit question: *what did the bot know, when, and why did it
do what it did?* — recovered entirely from persisted inputs. It never
connects to MT5 and never re-runs the strategy; it reads the records the
bot wrote at decision time.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from .manifest import as_dict, as_list


def _parse(dt_value: Any):
    if not dt_value:
        return None
    text = str(dt_value)
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed


def reconstruct(
    *,
    ledger_path: str | Path,
    decision_id: str | None = None,
    latest: bool = False,
) -> dict[str, Any]:
    """Reconstruct a decision (or the latest) into a human-readable report."""
    import sqlite3

    path = Path(ledger_path)
    if not path.exists():
        return {"found": False, "error": f"ledger not found: {path}"}

    connection = sqlite3.connect(
        f"file:{path.resolve().as_posix()}?mode=ro", uri=True, timeout=2.0
    )
    connection.row_factory = sqlite3.Row
    try:
        # Strategy decisions broadcast the whole payload; they carry everything.
        if latest or not decision_id:
            row = connection.execute(
                """
                SELECT payload_json, as_of_utc, decision_type, side, decision_id
                FROM strategy_decisions
                ORDER BY as_of_utc DESC LIMIT 1
                """
            ).fetchone()
        else:
            row = connection.execute(
                """
                SELECT payload_json, as_of_utc, decision_type, side, decision_id
                FROM strategy_decisions
                WHERE decision_id = ? OR decision_id LIKE ?
                LIMIT 1
                """,
                (decision_id, f"{decision_id}%"),
            ).fetchone()

        if not row:
            return {
                "found": False,
                "error": f"no decision found for id {decision_id!r}",
            }

        import json as _json

        payload = _json.loads(row["payload_json"])
        snapshot_id = f"{row['decision_id']}:snapshot"
        snapshot = connection.execute(
            "SELECT * FROM snapshots WHERE snapshot_id = ?",
            (snapshot_id,),
        ).fetchone()
        orders = connection.execute(
            "SELECT * FROM orders WHERE decision_id = ?",
            (row["decision_id"],),
        ).fetchall()
        fills = connection.execute(
            "SELECT fills.* FROM fills JOIN orders ON orders.order_id = fills.order_id "
            "WHERE orders.decision_id = ?",
            (row["decision_id"],),
        ).fetchall()
        positions = connection.execute(
            "SELECT * FROM positions WHERE position_id IN ("
            "  SELECT 'broker-' || broker_position_ticket FROM orders WHERE decision_id = ?"
            ")",
            (row["decision_id"],),
        ).fetchall()
        incidents = connection.execute(
            "SELECT * FROM incidents WHERE opened_at_utc >= ?",
            (row["as_of_utc"],),
        ).fetchall()

        fundamental = payload.get("fundamental", {})
        technical = payload.get("technical", {})
        fusion = payload.get("fusion", {})
        overseer = payload.get("overseer", {})

        components = as_dict(fundamental.get("components"))
        component_rows = []
        for key, component in components.items():
            if not isinstance(component, dict):
                component_rows.append({"input": key, "raw": str(component)[:80]})
                continue
            component_rows.append(
                {
                    "input_id": component.get("input_id") or key,
                    "series_id": component.get("series_id"),
                    "value": component.get("value"),
                    "signal": component.get("signal"),
                    "weight": component.get("weight"),
                    "contribution": component.get("contribution"),
                    "stale": component.get("stale"),
                    "source_id": component.get("source_id"),
                    "note": component.get("note"),
                }
            )

        return {
            "found": True,
            "decision_id": row["decision_id"],
            "as_of_utc": row["as_of_utc"],
            "decision_type": row["decision_type"],
            "side": row["side"],
            "confidence": payload.get("confidence"),
            "combined_score": payload.get("combined_score"),
            "reasons": payload.get("reasons", []),
            "missing_inputs": fundamental.get("missing_inputs", []),
            "stale_inputs": fundamental.get("stale_inputs", []),
            "source_versions": fundamental.get("source_versions", {}),
            "fundamental_components": component_rows,
            "technical": {
                "score": technical.get("score"),
                "indicators": technical.get("indicators"),
                "entry_triggers": technical.get("entry_triggers"),
                "bars_used": technical.get("bars_used"),
                "context_bars_used": technical.get("context_bars_used"),
            },
            "fusion": {
                "combined_score": fusion.get("combined_score"),
                "entry_candidate": fusion.get("entry_candidate"),
                "conflict": fusion.get("conflict"),
                "reasons": fusion.get("reasons", []),
                "fundamental_contribution": fusion.get("fundamental_contribution"),
                "technical_contribution": fusion.get("technical_contribution"),
            },
            "overseer": {
                "decision": overseer.get("decision"),
                "verdict": overseer.get("verdict"),
                "confidence": overseer.get("confidence"),
                "vetoes": overseer.get("vetoes", []),
                "rationale": overseer.get("rationale"),
            },
            "order_count": len(orders),
            "orders": [
                {
                    "order_id": o["order_id"],
                    "state": o["state"],
                    "volume": o["volume"],
                    "price": o["price"],
                    "stop_loss": o["stop_loss"],
                    "take_profit": o["take_profit"],
                    "broker_order_ticket": o["broker_order_ticket"],
                    "idempotency_key": o["idempotency_key"],
                    "submitted_at_utc": o["submitted_at_utc"],
                }
                for o in orders
            ],
            "fills": [
                {
                    "fill_id": f["fill_id"],
                    "volume": f["volume"],
                    "price": f["price"],
                    "slippage_points": f["slippage_points"],
                    "broker_deal_ticket": f["broker_deal_ticket"],
                }
                for f in fills
            ],
            "snapshot_present": snapshot is not None,
            "incidents_since": [
                {
                    "incident_id": i["incident_id"],
                    "category": i["category"],
                    "severity": i["severity"],
                    "summary": i["summary"],
                }
                for i in incidents
            ],
        }
    finally:
        connection.close()


def render_markdown(report: dict[str, Any]) -> str:
    if not report.get("found"):
        return f"# Decision not found\n\n{report.get('error', 'unknown error')}\n"

    technicals = report.get("technical", {})
    fusion = report.get("fusion", {})
    overseer = report.get("overseer", {})

    def _row(label: str, value: Any) -> str:
        return f"| {label} | `{value}` |\n"

    lines = [
        f"# Decision {report['decision_id']}\n",
        "",
        "## Identity\n",
        "| Field | Value |\n|---|---|\n",
        _row("as_of_utc", report.get("as_of_utc")),
        _row("decision_type", report.get("decision_type")),
        _row("side", report.get("side")),
        _row("confidence", report.get("confidence")),
        _row("combined_score", report.get("combined_score")),
        _row("decision reasons", "; ".join(report.get("reasons", []))),
        "",
        "## Fundamentals\n",
        "| Input | Series | Value | Signal | Weight | Contribution | Stale | Source | Note |\n|---|---|---|---|---|---|---|---|---|\n",
    ]
    for comp in report.get("fundamental_components", []):
        lines.append(
            "| {input} | {series} | {value} | {signal} | {weight} | {contribution} | {stale} | {source} | {note} |\n".format(
                input=comp.get("input_id", ""),
                series=comp.get("series_id", "") or "",
                value=comp.get("value", "") or "",
                signal=comp.get("signal", "") or "",
                weight=comp.get("weight", "") or "",
                contribution=comp.get("contribution", "") or "",
                stale=comp.get("stale"),
                source=comp.get("source_id", "") or "",
                note=(comp.get("note") or "").replace("|", "/"),
            )
        )
    lines += [
        "",
        f"- missing_inputs: `{', '.join(report.get('missing_inputs', [])) or 'none'}`",
        f"- stale_inputs: `{', '.join(report.get('stale_inputs', [])) or 'none'}`",
        "",
        "## Technicals\n",
        f"- score: `{technicals.get('score')}`",
        f"- bars_used: `{technicals.get('bars_used')}` · context_bars_used: `{technicals.get('context_bars_used')}`",
        f"- entry_triggers: `{technicals.get('entry_triggers')}`",
        f"- indicators: `{technicals.get('indicators')}`",
        "",
        "## Fusion\n",
        f"- combined_score: `{fusion.get('combined_score')}`",
        f"- entry_candidate: `{fusion.get('entry_candidate')}`",
        f"- conflict: `{fusion.get('conflict')}`",
        f"- fundamental_contribution: `{fusion.get('fundamental_contribution')}`",
        f"- technical_contribution: `{fusion.get('technical_contribution')}`",
        f"- reasons: `{'; '.join(fusion.get('reasons', []))}`",
        "",
        "## Overseer\n",
        f"- decision: `{overseer.get('decision')}`",
        f"- verdict: `{overseer.get('verdict')}`",
        f"- confidence: `{overseer.get('confidence')}`",
        f"- vetoes: `{', '.join(overseer.get('vetoes', [])) or 'none'}`",
        f"- rationale: `{overseer.get('rationale')}`",
        "",
        "## Orders & Fills\n",
        f"- orders: `{report.get('order_count', 0)}`",
    ]
    for o in report.get("orders", []):
        lines.append(
            "  - `{order_id}` state=`{state}` vol=`{volume}` price=`{price}` sl=`{stop}` tp=`{tp}` ticket=`{ticket}` idem=`{idem}`\n".format(
                order_id=o.get("order_id"),
                state=o.get("state"),
                volume=o.get("volume"),
                price=o.get("price"),
                stop=o.get("stop_loss"),
                tp=o.get("take_profit"),
                ticket=o.get("broker_order_ticket"),
                idem=o.get("idempotency_key"),
            )
        )
    for f in report.get("fills", []):
        lines.append(
            "  - fill `{fill_id}` vol=`{volume}` price=`{price}` slippage_points=`{slippage}` deal=`{deal}`\n".format(
                fill_id=f.get("fill_id"),
                volume=f.get("volume"),
                price=f.get("price"),
                slippage=f.get("slippage_points"),
                deal=f.get("broker_deal_ticket"),
            )
        )
    lines += [
        "",
        "## Reconstruction notes\n",
        "- Every number above came from the ledger; the strategy was not re-run.",
        "- The reconstruction is only as honest as the inputs the bot logged at decision time.",
        f"- Snapshot present: `{report.get('snapshot_present')}`",
        f"- Incidents since: `{len(report.get('incidents_since', []))}`",
    ]
    return "".join(lines)
