"""Single-writer SQLite ledger for M3 decisions, orders, fills, and positions.

The ledger is the source of truth for a running bot.  It uses the existing
``config/storage.schema.sql`` contract and WAL mode.  A lock file prevents two
writer processes from opening the same ledger at the same time.

The dashboard and analytics components are read-only: they open a separate
SQLite connection and never call the write methods here.
"""

from __future__ import annotations

import json
import os
import socket
from contextlib import AbstractContextManager
from datetime import datetime
from pathlib import Path
from typing import Any

from .contracts import StrategyDecision, ensure_utc, utc_now
from .data import SQLiteStore

DEFAULT_SCHEMA_PATH = Path(__file__).resolve().parents[1] / "config" / "storage.schema.sql"


class LedgerError(RuntimeError):
    """Base ledger error."""


class LedgerLockError(LedgerError):
    """Raised when another process already owns the ledger writer lock."""


class Ledger(AbstractContextManager["Ledger"]):
    """The only writer for a bot ledger.

    Usage::

        with Ledger("reports/goldbot.ledger.sqlite") as ledger:
            ledger.start_run(...)
            ledger.record_decision(...)
    """

    def __init__(
        self,
        path: str | Path,
        *,
        schema_path: str | Path | None = None,
    ) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock_path = self.path.with_name(self.path.name + ".writer.lock")
        self._closed = False
        self._acquire_lock()
        try:
            self.store = SQLiteStore(
                self.path,
                schema_path=schema_path or DEFAULT_SCHEMA_PATH,
            )
            self.connection = self.store.connection
        except Exception:
            self._release_lock()
            raise

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def _acquire_lock(self) -> None:
        try:
            descriptor = os.open(
                self.lock_path,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
            )
        except FileExistsError as exc:
            raise LedgerLockError(
                "another writer owns this ledger: "
                f"{self.path}; lock file: {self.lock_path}. "
                "If the previous process crashed, remove the stale lock file."
            ) from exc
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(
                {
                    "pid": os.getpid(),
                    "host": socket.gethostname(),
                    "created_at_utc": utc_now().isoformat(),
                    "ledger": str(self.path),
                },
                handle,
                ensure_ascii=False,
            )

    def _release_lock(self) -> None:
        self.lock_path.unlink(missing_ok=True)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self.store.close()
        finally:
            self._release_lock()

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    # ------------------------------------------------------------------
    # Runs and decisions
    # ------------------------------------------------------------------
    def start_run(
        self,
        *,
        run_id: str,
        scope_id: str,
        manifest_hash: str,
        started_at_utc: datetime,
        summary: dict[str, Any] | None = None,
        allow_multiple_runs: bool = False,
    ) -> None:
        existing = self.connection.execute(
            """
            SELECT manifest_hash FROM strategy_runs
            WHERE scope_id = ? AND manifest_hash <> ?
            LIMIT 1
            """,
            (scope_id, manifest_hash),
        ).fetchone()
        if existing is not None:
            raise LedgerError(
                f"ledger {self.path} already contains scope {scope_id} with manifest hash "
                f"{existing['manifest_hash']}; use a separate ledger for manifest hash "
                f"{manifest_hash}. Mixing manifest versions in one ledger would make "
                "decision IDs ambiguous."
            )
        if not allow_multiple_runs:
            existing_run = self.connection.execute(
                """
                SELECT run_id FROM strategy_runs
                WHERE scope_id = ? AND manifest_hash = ? AND run_id <> ?
                LIMIT 1
                """,
                (scope_id, manifest_hash, run_id),
            ).fetchone()
            if existing_run is not None:
                raise LedgerError(
                    f"ledger {self.path} already contains run {existing_run['run_id']} for "
                    f"manifest hash {manifest_hash}; use a separate ledger for each run. "
                    "Reusing the same run_id is allowed for an idempotent restart."
                )
        self.connection.execute(
            """
            INSERT OR REPLACE INTO strategy_runs
                (run_id, scope_id, manifest_hash, started_at_utc, finished_at_utc, summary_json)
            VALUES (?, ?, ?, ?, NULL, ?)
            """,
            (
                run_id,
                scope_id,
                manifest_hash,
                ensure_utc(started_at_utc).isoformat(),
                json.dumps(summary or {}, ensure_ascii=False, default=str),
            ),
        )
        self.connection.commit()

    def finish_run(
        self,
        *,
        run_id: str,
        finished_at_utc: datetime,
        summary: dict[str, Any],
    ) -> None:
        self.connection.execute(
            """
            UPDATE strategy_runs
            SET finished_at_utc = ?, summary_json = ?
            WHERE run_id = ?
            """,
            (
                ensure_utc(finished_at_utc).isoformat(),
                json.dumps(summary, ensure_ascii=False, default=str),
                run_id,
            ),
        )
        self.connection.commit()

    def record_decision(
        self,
        decision: StrategyDecision,
        *,
        scope_id: str,
        scope_version: int = 1,
        symbol: str = "XAUUSD",
        execution_bar: str = "M15",
        context_bar: str = "H4",
        market: dict[str, Any] | None = None,
    ) -> str:
        payload = decision.to_dict()
        as_of = ensure_utc(decision.as_of_utc)
        snapshot_id = f"{decision.decision_id}:snapshot"
        market_payload = market or {}
        self.connection.execute(
            """
            INSERT OR IGNORE INTO snapshots
                (snapshot_id, scope_id, scope_version, captured_at_utc, symbol,
                 execution_bar, context_bar, market_json, fundamentals_json,
                 technicals_json, fusion_json, overseer_json, source_versions_json,
                 data_quality_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                snapshot_id,
                scope_id,
                int(scope_version),
                as_of.isoformat(),
                symbol,
                execution_bar,
                context_bar,
                json.dumps(market_payload, ensure_ascii=False, default=str),
                json.dumps(payload.get("fundamental", {}), ensure_ascii=False, default=str),
                json.dumps(payload.get("technical", {}), ensure_ascii=False, default=str),
                json.dumps(payload.get("fusion", {}), ensure_ascii=False, default=str),
                json.dumps(payload.get("overseer", {}), ensure_ascii=False, default=str),
                json.dumps(
                    payload.get("fundamental", {}).get("source_versions", {}),
                    ensure_ascii=False,
                    default=str,
                ),
                json.dumps(
                    {
                        "missing_inputs": payload.get("fundamental", {}).get("missing_inputs", []),
                        "stale_inputs": payload.get("fundamental", {}).get("stale_inputs", []),
                        "indicators": payload.get("technical", {}).get("indicators", {}),
                    },
                    ensure_ascii=False,
                    default=str,
                ),
            ),
        )
        overseer = payload.get("overseer", {})
        self.connection.execute(
            """
            INSERT OR IGNORE INTO decisions
                (decision_id, snapshot_id, decided_at_utc, scope_id, scope_version,
                 decision_type, side, confidence, entry_score, fundamental_score,
                 technical_score, overseer_verdict, veto_reasons_json, exit_plan_json,
                 rationale)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                decision.decision_id,
                snapshot_id,
                as_of.isoformat(),
                scope_id,
                int(scope_version),
                decision.decision_type,
                decision.side,
                float(decision.confidence),
                float(decision.combined_score),
                float(decision.fundamental.score),
                float(decision.technical.score),
                str(overseer.get("decision", "")),
                json.dumps(overseer.get("vetoes", []), ensure_ascii=False, default=str),
                json.dumps(payload.get("exit_decision") or {}, ensure_ascii=False, default=str),
                "; ".join(str(item) for item in decision.reasons),
            ),
        )
        self.connection.execute(
            """
            INSERT OR REPLACE INTO strategy_decisions
                (decision_id, as_of_utc, decision_type, side, payload_json)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                decision.decision_id,
                as_of.isoformat(),
                decision.decision_type,
                decision.side,
                json.dumps(payload, ensure_ascii=False, default=str),
            ),
        )
        self.connection.commit()
        return decision.decision_id

    # ------------------------------------------------------------------
    # Orders, fills, positions
    # ------------------------------------------------------------------
    def record_order(
        self,
        *,
        order_id: str,
        decision_id: str,
        submitted_at_utc: datetime,
        symbol: str,
        side: str,
        order_type: str,
        volume: float,
        price: float | None,
        stop_loss: float | None,
        take_profit: float | None,
        state: str,
        idempotency_key: str,
        raw_request: dict[str, Any] | None = None,
        raw_response: dict[str, Any] | None = None,
        deviation_points: int | None = None,
        magic: int | None = None,
        comment: str | None = None,
        broker_order_ticket: int | None = None,
    ) -> None:
        self.connection.execute(
            """
            INSERT OR IGNORE INTO orders
                (order_id, decision_id, submitted_at_utc, symbol, side, order_type,
                 volume, price, stop_loss, take_profit, deviation_points, magic,
                 comment, state, broker_order_ticket, idempotency_key,
                 raw_request_json, raw_response_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                order_id,
                decision_id,
                ensure_utc(submitted_at_utc).isoformat(),
                symbol,
                side,
                order_type,
                float(volume),
                price,
                stop_loss,
                take_profit,
                deviation_points,
                magic,
                comment,
                state,
                broker_order_ticket,
                idempotency_key,
                json.dumps(raw_request or {}, ensure_ascii=False, default=str),
                json.dumps(raw_response or {}, ensure_ascii=False, default=str),
            ),
        )
        self.connection.commit()

    def record_fill(
        self,
        *,
        fill_id: str,
        order_id: str,
        filled_at_utc: datetime,
        symbol: str,
        side: str,
        volume: float,
        price: float,
        commission: float = 0.0,
        swap: float = 0.0,
        slippage_points: float | None = None,
        broker_deal_ticket: int | None = None,
        raw_deal: dict[str, Any] | None = None,
    ) -> None:
        self.connection.execute(
            """
            INSERT OR IGNORE INTO fills
                (fill_id, order_id, filled_at_utc, symbol, side, volume, price,
                 commission, swap, slippage_points, broker_deal_ticket, raw_deal_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                fill_id,
                order_id,
                ensure_utc(filled_at_utc).isoformat(),
                symbol,
                side,
                float(volume),
                float(price),
                float(commission),
                float(swap),
                slippage_points,
                broker_deal_ticket,
                json.dumps(raw_deal or {}, ensure_ascii=False, default=str),
            ),
        )
        self.connection.commit()

    def record_position(
        self,
        *,
        position_id: str,
        symbol: str,
        side: str,
        volume: float,
        open_price: float,
        open_time_utc: datetime,
        stop_loss: float | None = None,
        take_profit: float | None = None,
        close_price: float | None = None,
        close_time_utc: datetime | None = None,
        close_reason: str | None = None,
        realized_pnl: float | None = None,
        mae: float | None = None,
        mfe: float | None = None,
        broker_position_ticket: int | None = None,
        state: str = "PAPER_OPEN",
        raw_position: dict[str, Any] | None = None,
    ) -> None:
        self.connection.execute(
            """
            INSERT OR REPLACE INTO positions
                (position_id, symbol, side, volume, open_price, open_time_utc,
                 stop_loss, take_profit, close_price, close_time_utc, close_reason,
                 realized_pnl, mae, mfe, broker_position_ticket, state, raw_position_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                position_id,
                symbol,
                side,
                float(volume),
                float(open_price),
                ensure_utc(open_time_utc).isoformat(),
                stop_loss,
                take_profit,
                close_price,
                ensure_utc(close_time_utc).isoformat() if close_time_utc else None,
                close_reason,
                realized_pnl,
                mae,
                mfe,
                broker_position_ticket,
                state,
                json.dumps(raw_position or {}, ensure_ascii=False, default=str),
            ),
        )
        self.connection.commit()

    def record_chart_bars(
        self,
        *,
        scope_id: str,
        timeframe: str,
        bars: list[Any],
        recorded_at_utc: datetime | None = None,
    ) -> None:
        """Upsert OHLCV bars so read-only dashboards can render them."""
        if not bars:
            return
        now = ensure_utc(recorded_at_utc or utc_now()).isoformat()
        self.connection.executemany(
            """
            INSERT OR REPLACE INTO chart_bars
                (scope_id, timeframe, bar_time_utc,
                 open_price, high_price, low_price, close_price,
                 volume, recorded_at_utc)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    scope_id,
                    str(timeframe),
                    ensure_utc(bar.time_utc).isoformat(),
                    float(bar.open),
                    float(bar.high),
                    float(bar.low),
                    float(bar.close),
                    float(getattr(bar, "volume", 0.0) or 0.0),
                    now,
                )
                for bar in bars
            ],
        )
        self.connection.commit()

    # ------------------------------------------------------------------
    # Logs and incidents
    # ------------------------------------------------------------------
    def log_event(
        self,
        *,
        level: str,
        component: str,
        event_type: str,
        message: str,
        correlation_id: str | None = None,
        payload: dict[str, Any] | None = None,
        logged_at_utc: datetime | None = None,
    ) -> None:
        self.connection.execute(
            """
            INSERT INTO logs
                (logged_at_utc, level, component, event_type, correlation_id, message, payload_json)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                ensure_utc(logged_at_utc or utc_now()).isoformat(),
                level,
                component,
                event_type,
                correlation_id,
                message,
                json.dumps(payload or {}, ensure_ascii=False, default=str),
            ),
        )
        self.connection.commit()

    def record_incident(
        self,
        *,
        incident_id: str,
        opened_at_utc: datetime,
        severity: str,
        category: str,
        summary: str,
        details: dict[str, Any] | None = None,
        action_taken: str = "none",
        closed_at_utc: datetime | None = None,
        resolved_by: str | None = None,
    ) -> None:
        self.connection.execute(
            """
            INSERT OR REPLACE INTO incidents
                (incident_id, opened_at_utc, closed_at_utc, severity, category,
                 summary, details_json, action_taken, resolved_by)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                incident_id,
                ensure_utc(opened_at_utc).isoformat(),
                ensure_utc(closed_at_utc).isoformat() if closed_at_utc else None,
                severity,
                category,
                summary,
                json.dumps(details or {}, ensure_ascii=False, default=str),
                action_taken,
                resolved_by,
            ),
        )
        self.connection.commit()

    # ------------------------------------------------------------------
    # Read-only queries for reports/dashboard
    # ------------------------------------------------------------------
    def latest_run(self) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT * FROM strategy_runs ORDER BY started_at_utc DESC LIMIT 1"
        ).fetchone()
        return dict(row) if row else None

    def recent_decisions(self, limit: int = 200) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """
            SELECT payload_json FROM strategy_decisions
            ORDER BY as_of_utc DESC LIMIT ?
            """,
            (int(limit),),
        ).fetchall()
        return [json.loads(str(row["payload_json"])) for row in reversed(rows)]

    def recent_orders(self, limit: int = 200) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            "SELECT * FROM orders ORDER BY submitted_at_utc DESC LIMIT ?",
            (int(limit),),
        ).fetchall()
        return [dict(row) for row in reversed(rows)]

    def recent_fills(self, limit: int = 200) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            "SELECT * FROM fills ORDER BY filled_at_utc DESC LIMIT ?",
            (int(limit),),
        ).fetchall()
        return [dict(row) for row in reversed(rows)]

    def recent_positions(self, limit: int = 200) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            "SELECT * FROM positions ORDER BY open_time_utc DESC LIMIT ?",
            (int(limit),),
        ).fetchall()
        return [dict(row) for row in reversed(rows)]

    def open_positions(self) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            "SELECT * FROM positions WHERE close_time_utc IS NULL ORDER BY open_time_utc ASC"
        ).fetchall()
        return [dict(row) for row in rows]

    def closed_trades(self, limit: int = 500) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """
            SELECT * FROM positions
            WHERE close_time_utc IS NOT NULL
            ORDER BY close_time_utc ASC LIMIT ?
            """,
            (int(limit),),
        ).fetchall()
        trades: list[dict[str, Any]] = []
        for row in rows:
            trades.append(
                {
                    "trade_id": row["position_id"],
                    "side": row["side"],
                    "volume": row["volume"],
                    "entry_time_utc": row["open_time_utc"],
                    "entry_price": row["open_price"],
                    "exit_time_utc": row["close_time_utc"],
                    "exit_price": row["close_price"],
                    "exit_reason": row["close_reason"],
                    "pnl": row["realized_pnl"],
                    "mae": row["mae"],
                    "mfe": row["mfe"],
                }
            )
        return trades

    def recent_logs(self, limit: int = 200) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            "SELECT * FROM logs ORDER BY logged_at_utc DESC LIMIT ?",
            (int(limit),),
        ).fetchall()
        return [dict(row) for row in reversed(rows)]

    def counts(self) -> dict[str, int]:
        result: dict[str, int] = {}
        for table in (
            "snapshots",
            "decisions",
            "orders",
            "fills",
            "positions",
            "logs",
            "incidents",
        ):
            row = self.connection.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()
            result[table] = int(row["n"]) if row else 0
        return result
