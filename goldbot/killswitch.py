"""Emergency kill switch: flatten all XAUUSD positions and halt the bot.

This is the human/owner's emergency brake. It is deliberately harder to
trigger than to observe, and it is *never* automatic. The guards are:

- ``--expected-login`` is mandatory and must match the connected account;
- ``--confirm-flatten`` must be exactly ``FLATTEN AND HALT``;
- the connected account must be a **demo** account (trade_mode == 0).

On success it:

1. writes a halt flag file next to the ledger (``kill-switch.halt``) which the
   agent loop checks and exits on;
2. closes every open XAUUSD position at market;
3. records an incident in the ledger so the event is evidence, not anecdote.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from .contracts import utc_now
from .ledger import Ledger

CONFIRMATION_PHRASE = "FLATTEN AND HALT"


@dataclass
class KillSwitchResult:
    generated_at_utc: datetime
    ledger_path: str
    halt_file: str
    account_login: int | None
    server: str | None
    positions_before: int
    closed: list[dict[str, Any]] = field(default_factory=list)
    failed: list[dict[str, Any]] = field(default_factory=list)
    incident_id: str | None = None
    halted: bool = False
    drill: bool = False

    @property
    def ok(self) -> bool:
        if self.drill:
            # A drill passes when the guard chain runs and nothing fails;
            # it never arms the halt flag, so `halted` is not part of this.
            return not self.failed
        return self.halted and not self.failed

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": "kill_switch",
            "generated_at_utc": self.generated_at_utc.isoformat(),
            "ledger_path": self.ledger_path,
            "halt_file": self.halt_file,
            "account_login": self.account_login,
            "server": self.server,
            "positions_before": self.positions_before,
            "closed": self.closed,
            "failed": self.failed,
            "incident_id": self.incident_id,
            "halted": self.halted,
            "drill": self.drill,
            "ok": self.ok,
        }


def run_kill_switch_drill(
    *,
    ledger_path: str | Path,
    expected_login: int,
    confirm_flatten: str,
    symbol: str = "XAUUSD",
    broker_utc_offset_minutes: int = 120,
    feed: Any | None = None,
) -> KillSwitchResult:
    """Prove the kill-switch chain without placing orders or arming the halt flag.

    Runs the same guards as the real path and enumerates what it would do,
    then records drill evidence in the ledger. It never sends a close order and
    never writes the halt flag. If positions are open it refuses to flatten
    them: a drill is not an authorization to trade.
    """
    if confirm_flatten != CONFIRMATION_PHRASE:
        raise ValueError(
            f"refusing to drill: --confirm-flatten must be exactly {CONFIRMATION_PHRASE!r}"
        )
    if expected_login is None:
        raise ValueError("refusing to drill: --expected-login is required")

    generated_at = utc_now()
    ledger_text = str(ledger_path)

    owns_feed = feed is None
    if feed is None:
        from .data import MT5DataFeed

        feed = MT5DataFeed(
            symbol=symbol, broker_utc_offset_minutes=broker_utc_offset_minutes
        )

    closed: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    account_login: int | None = None
    server: str | None = None
    positions: list[dict[str, Any]] = []
    checks: list[dict[str, str]] = []

    try:
        feed.connect()
        account = feed.account_info()
        terminal = feed.terminal_info()
        symbol_info = feed.symbol_info()
        account_login = account.get("login")
        server = account.get("server")

        is_demo = account.get("trade_mode") == 0
        checks.append(
            {
                "name": "demo_account",
                "status": "PASS" if is_demo else "FAIL",
                "detail": (
                    f"demo account {account_login} on {server}"
                    if is_demo
                    else f"account trade_mode={account.get('trade_mode')!r}; refusing"
                ),
            }
        )
        login_ok = account_login == int(expected_login)
        checks.append(
            {
                "name": "expected_login",
                "status": "PASS" if login_ok else "FAIL",
                "detail": (
                    f"connected login {account_login} matches expected login"
                    if login_ok
                    else f"connected login {account_login} != expected {expected_login}"
                ),
            }
        )
        checks.append(
            {
                "name": "mt5_connected",
                "status": "PASS" if bool(terminal.get("connected", False)) else "FAIL",
                "detail": f"terminal connected={terminal.get('connected')}",
            }
        )
        checks.append(
            {
                "name": "algo_trading",
                "status": "PASS" if bool(terminal.get("trade_allowed", False)) else "FAIL",
                "detail": f"trade_allowed={terminal.get('trade_allowed')}",
            }
        )
        checks.append(
            {
                "name": "symbol",
                "status": "PASS" if symbol_info else "FAIL",
                "detail": (
                    f"{symbol} available; filling_mode={symbol_info.get('filling_mode') if symbol_info else 'n/a'}"
                    if symbol_info
                    else f"{symbol} not available"
                ),
            }
        )

        if not all(check["status"] == "PASS" for check in checks):
            raise RuntimeError(
                "drill guards failed: "
                + "; ".join(f"{c['name']}={c['status']}" for c in checks if c["status"] != "PASS")
            )

        positions = feed.open_positions(symbol) or []
        checks.append(
            {
                "name": "position_inventory",
                "status": "PASS",
                "detail": (
                    f"{len(positions)} open position(s); a real flatten would close "
                    f"{[p.get('ticket') for p in positions]}"
                ),
            }
        )
    finally:
        if owns_feed:
            try:
                feed.close()
            except Exception:  # noqa: BLE001 - best-effort close
                pass

    incident_id = (
        "killswitch-drill-"
        + hashlib.sha256(generated_at.isoformat().encode("utf-8")).hexdigest()[:16]
    )
    with Ledger(ledger_path) as ledger:
        ledger.log_event(
            level="INFO",
            component="kill_switch_drill",
            event_type="kill_switch_drill",
            message=(
                f"kill-switch drill passed all guards; positions_seen={len(positions)}; "
                "no orders sent; halt flag NOT armed"
            ),
            correlation_id=incident_id,
            payload={
                "account_login": account_login,
                "server": server,
                "checks": checks,
                "positions_seen": positions,
                "orders_sent": 0,
                "halt_armed": False,
            },
            logged_at_utc=generated_at,
        )
        ledger.record_incident(
            incident_id=incident_id,
            opened_at_utc=generated_at,
            closed_at_utc=generated_at,
            severity="info",
            category="kill_switch_drill",
            summary="kill-switch drill passed (guards only; no orders, no halt flag)",
            details={
                "account_login": account_login,
                "server": server,
                "checks": checks,
                "positions_seen": positions,
            },
            action_taken="drill_no_orders_sent",
            resolved_by="drill",
        )

    return KillSwitchResult(
        generated_at_utc=generated_at,
        ledger_path=ledger_text,
        halt_file=str(Path(ledger_path).parent / "kill-switch.halt"),
        account_login=account_login,
        server=server,
        positions_before=len(positions),
        closed=closed,
        failed=failed,
        incident_id=incident_id,
        halted=False,
        drill=True,
    )


def halt_file_for(ledger_path: str | Path) -> Path:
    return Path(ledger_path).parent / "kill-switch.halt"


def run_kill_switch(
    *,
    ledger_path: str | Path,
    expected_login: int,
    confirm_flatten: str,
    symbol: str = "XAUUSD",
    broker_utc_offset_minutes: int = 120,
    close_comment: str = "goldbot kill switch",
    feed: Any | None = None,
) -> KillSwitchResult:
    if confirm_flatten != CONFIRMATION_PHRASE:
        raise ValueError(
            f"refusing to flatten: --confirm-flatten must be exactly {CONFIRMATION_PHRASE!r}"
        )
    if expected_login is None:
        raise ValueError("refusing to flatten: --expected-login is required")

    generated_at = utc_now()
    ledger_text = str(ledger_path)
    halt_file = halt_file_for(ledger_path)

    owns_feed = feed is None
    if feed is None:
        from .data import MT5DataFeed

        feed = MT5DataFeed(
            symbol=symbol, broker_utc_offset_minutes=broker_utc_offset_minutes
        )

    closed: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    account_login: int | None = None
    server: str | None = None
    positions: list[dict[str, Any]] = []

    try:
        feed.connect()
        account = feed.account_info()
        account_login = account.get("login")
        server = account.get("server")
        if account.get("trade_mode") != 0:
            raise RuntimeError(
                f"refusing to flatten: account trade_mode={account.get('trade_mode')!r}; "
                "kill switch is demo-only"
            )
        if account_login != int(expected_login):
            raise RuntimeError(
                f"refusing to flatten: connected login {account_login} != expected {expected_login}"
            )

        positions = feed.open_positions(symbol) or []
        for position in positions:
            ticket = position.get("ticket")
            try:
                result = feed.close_position(
                    ticket=int(ticket),
                    symbol=symbol,
                    comment=close_comment,
                )
                retcode = int((result or {}).get("retcode", -1))
                if retcode in {10008, 10009}:
                    closed.append({"ticket": int(ticket), "retcode": retcode})
                else:
                    failed.append(
                        {
                            "ticket": int(ticket),
                            "retcode": retcode,
                            "comment": (result or {}).get("comment"),
                        }
                    )
            except Exception as exc:  # noqa: BLE001 - collect and report every failure
                failed.append(
                    {"ticket": ticket, "error": f"{type(exc).__name__}: {exc}"}
                )

        halt_file.write_text(
            f"kill switch armed at {generated_at.isoformat()} by owner action\n",
            encoding="utf-8",
        )

        incident_id = (
            "killswitch-"
            + hashlib.sha256(generated_at.isoformat().encode("utf-8")).hexdigest()[:16]
        )
        with Ledger(ledger_path) as ledger:
            ledger.log_event(
                level="CRITICAL",
                component="kill_switch",
                event_type="kill_switch",
                message=(
                    f"kill switch executed: closed={len(closed)} failed={len(failed)}; "
                    "halt flag written; agent must not continue"
                ),
                correlation_id=incident_id,
                payload={
                    "account_login": account_login,
                    "server": server,
                    "positions_before": len(positions),
                    "closed": closed,
                    "failed": failed,
                    "halt_file": str(halt_file),
                },
                logged_at_utc=generated_at,
            )
            ledger.record_incident(
                incident_id=incident_id,
                opened_at_utc=generated_at,
                severity="critical",
                category="kill_switch",
                summary="human-triggered kill switch: flatten and halt",
                details={
                    "account_login": account_login,
                    "server": server,
                    "positions_before": len(positions),
                    "closed": closed,
                    "failed": failed,
                },
                action_taken="flatten_all_and_halt",
                resolved_by="owner",
            )
    finally:
        if owns_feed:
            try:
                feed.close()
            except Exception:  # noqa: BLE001 - best-effort close
                pass

    return KillSwitchResult(
        generated_at_utc=generated_at,
        ledger_path=ledger_text,
        halt_file=str(halt_file),
        account_login=account_login,
        server=server,
        positions_before=len(positions),
        closed=closed,
        failed=failed,
        incident_id=incident_id,
        halted=halt_file.exists(),
    )
