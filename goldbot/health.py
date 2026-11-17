"""Read-only health check for the bot stack.

Not a daemon, not a supervisor — a probe a human or an external scheduler can
call to know, right now, whether the machine is in a healthy state:

- halt flag armed? (bot must not trade)
- MT5 terminal reachable? (no orders are placed)
- ledger readable? when was the last activity?
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .contracts import utc_now

DEFAULT_LEDGER = Path("reports/demo.execution.ledger.sqlite")


@dataclass
class HealthReport:
    generated_at_utc: datetime
    halt_flag: bool
    halt_flag_path: str
    ledger_exists: bool
    last_activity_utc: datetime | None
    last_activity_age_seconds: float | None
    mt5: dict[str, Any] = field(default_factory=dict)
    account: dict[str, Any] = field(default_factory=dict)
    verdict: str = "HEALTHY"

    @property
    def ok(self) -> bool:
        return self.verdict == "HEALTHY"

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": "health",
            "generated_at_utc": self.generated_at_utc.isoformat(),
            "halt_flag": self.halt_flag,
            "halt_flag_path": self.halt_flag_path,
            "ledger_exists": self.ledger_exists,
            "last_activity_utc": self.last_activity_utc.isoformat() if self.last_activity_utc else None,
            "last_activity_age_seconds": self.last_activity_age_seconds,
            "mt5": self.mt5,
            "account": self.account,
            "verdict": self.verdict,
        }


def _last_activity(ledger_path: Path) -> datetime | None:
    if not ledger_path.is_absolute():
        ledger_path = Path.cwd() / ledger_path
    try:
        connection = sqlite3.connect(
            f"file:{ledger_path.as_posix()}?mode=ro", uri=True, timeout=1.0
        )
    except sqlite3.Error:
        return None
    try:
        connection.row_factory = sqlite3.Row
        candidates: list[datetime] = []
        for table, column in (
            ("snapshots", "captured_at_utc"),
            ("strategy_runs", "started_at_utc"),
            ("logs", "logged_at_utc"),
            ("decisions", "decided_at_utc"),
            ("orders", "submitted_at_utc"),
        ):
            try:
                row = connection.execute(
                    f"SELECT MAX({column}) AS latest FROM {table}"
                ).fetchone()
            except sqlite3.Error:
                continue
            value = row["latest"] if row else None
            if value:
                try:
                    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
                    candidates.append(
                        parsed
                        if parsed.tzinfo
                        else parsed.replace(tzinfo=timezone.utc)
                    )
                except ValueError:
                    continue
        return max(candidates) if candidates else None
    finally:
        connection.close()


def run_health(
    *,
    ledger_path: str | Path = DEFAULT_LEDGER,
    skip_mt5: bool = False,
    socket_timeout_seconds: float = 5.0,
) -> HealthReport:
    generated_at = utc_now()
    halt_path = Path(ledger_path).parent / "kill-switch.halt"
    halt_flag = halt_path.exists()

    ledger_file = Path(ledger_path)
    ledger_exists = ledger_file.exists()
    last_activity = _last_activity(ledger_file) if ledger_exists else None
    last_activity_age = (
        (generated_at - last_activity).total_seconds()
        if last_activity is not None
        else None
    )

    mt5_report: dict[str, Any] = {"status": "skipped" if skip_mt5 else "unknown"}
    account: dict[str, Any] = {}
    if not skip_mt5:
        try:
            from .data import MT5DataFeed

            feed = MT5DataFeed(symbol="XAUUSD", broker_utc_offset_minutes=120)
            feed.connect()
            try:
                account = feed.account_info()
                terminal = feed.terminal_info()
                mt5_report = {
                    "status": "up",
                    "login": account.get("login"),
                    "server": account.get("server"),
                    "trade_mode": account.get("trade_mode"),
                    "trade_allowed": terminal.get("trade_allowed"),
                    "connected": terminal.get("connected"),
                    "tradeapi_disabled": terminal.get("tradeapi_disabled"),
                }
            finally:
                feed.close()
        except Exception as exc:  # noqa: BLE001 - probe must report, not crash
            mt5_report = {"status": "down", "error": f"{type(exc).__name__}: {exc}"}

    mt5_ok = bool(skip_mt5 or mt5_report.get("status") == "up")
    verdict = "HEALTHY" if (mt5_ok and not halt_flag) else ("HALTED" if halt_flag else "DEGRADED")

    return HealthReport(
        generated_at_utc=generated_at,
        halt_flag=halt_flag,
        halt_flag_path=str(halt_path),
        ledger_exists=ledger_exists,
        last_activity_utc=last_activity,
        last_activity_age_seconds=last_activity_age,
        mt5=mt5_report,
        account=account,
        verdict=verdict,
    )
