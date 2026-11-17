"""Demo-account preflight.

This module is deliberately a *preflight only*: it never calls ``order_send``.
It checks the MT5 terminal/account/symbol, loads real macro files, asks the
strategy for the latest closed-bar decision, and runs ``order_check`` on the
would-be request.  A later, separately-reviewed command can submit an order
only after this preflight is clean and the human owner explicitly asks for it.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .contracts import RiskState, StrategyDecision, ensure_utc, utc_now
from .data import BarSeries, MT5DataFeed, load_macro_directory
from .ledger import Ledger
from .manifest import as_dict, manifest_hash
from .strategy import StrategyEngine
from .indicators import atr
from .contracts import FundamentalView, TechnicalView, FusionView, OverseerDecision


PASS = "PASS"
FAIL = "FAIL"
BLOCKED = "BLOCKED"
NO_TRADE = "NO_TRADE"
HUMAN_JUDGMENT = "HUMAN_JUDGMENT"


@dataclass
class PreflightItem:
    name: str
    status: str
    detail: str
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "detail": self.detail,
            "evidence": self.evidence,
        }


@dataclass
class DemoPreflightResult:
    generated_at_utc: datetime
    manifest_hash: str
    scope_id: str
    account_profile: str
    high_risk_override: bool
    promotion_eligible: bool
    account: dict[str, Any]
    terminal: dict[str, Any]
    symbol: dict[str, Any]
    tick: dict[str, Any]
    items: list[PreflightItem]
    decision: StrategyDecision | None
    order_request: dict[str, Any] | None
    order_check: dict[str, Any] | None
    ledger_path: str | None

    @property
    def status(self) -> str:
        return _overall_status(self.items, self.decision)

    @property
    def send_ready(self) -> bool:
        """True only when every item is PASS and the decision is ENTER_LONG."""
        return self.status == PASS and all(item.status == PASS for item in self.items)

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": "demo_preflight",
            "generated_at_utc": self.generated_at_utc.isoformat(),
            "manifest_hash": self.manifest_hash,
            "scope_id": self.scope_id,
            "account_profile": self.account_profile,
            "high_risk_override": self.high_risk_override,
            "promotion_eligible": self.promotion_eligible,
            "status": self.status,
            "account": self.account,
            "terminal": self.terminal,
            "symbol": self.symbol,
            "tick": self.tick,
            "items": [item.to_dict() for item in self.items],
            "decision": self.decision.to_dict() if self.decision else None,
            "order_request": self.order_request,
            "order_check": self.order_check,
            "ledger_path": self.ledger_path,
            "broker_orders_sent": 0,
        }


@dataclass
class DemoOrderResult:
    preflight: DemoPreflightResult
    send_requested: bool
    sent: bool
    send_result: dict[str, Any] | None
    ledger_path: str | None

    @property
    def status(self) -> str:
        if not self.send_requested:
            return self.preflight.status
        if not self.sent:
            return self.preflight.status
        return "SENT"

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": "demo_order",
            "status": self.status,
            "send_requested": self.send_requested,
            "sent": self.sent,
            "send_result": self.send_result,
            "preflight": self.preflight.to_dict(),
            "ledger_path": self.ledger_path,
            "broker_orders_sent": 1 if self.sent else 0,
        }


def _overall_status(
    items: list[PreflightItem], decision: StrategyDecision | None
) -> str:
    if any(item.status == FAIL for item in items):
        return FAIL
    if any(item.status == BLOCKED for item in items):
        return BLOCKED
    if decision is None or decision.decision_type != "ENTER_LONG":
        return NO_TRADE
    return PASS


def _add(
    items: list[PreflightItem],
    name: str,
    status: str,
    detail: str,
    **evidence: Any,
) -> None:
    items.append(
        PreflightItem(
            name=name,
            status=status,
            detail=detail,
            evidence={key: value for key, value in evidence.items() if value is not None},
        )
    )


def _mt5_time_to_utc(epoch_seconds: Any, broker_utc_offset_minutes: int) -> datetime | None:
    if epoch_seconds is None:
        return None
    try:
        raw = datetime.fromtimestamp(int(epoch_seconds), tz=timezone.utc)
    except (TypeError, ValueError, OSError):
        return None
    return raw - timedelta(minutes=broker_utc_offset_minutes)


def _latest_closed_bar(
    execution_bars: BarSeries,
    *,
    tick: dict[str, Any],
    broker_utc_offset_minutes: int,
    execution_minutes: int = 15,
    tolerance_seconds: int = 60,
) -> Any | None:
    """Return the last completed M15 bar, dropping the forming bar if present."""
    bars = execution_bars.bars
    if not bars:
        return None
    last = bars[-1]
    tick_time = _mt5_time_to_utc(tick.get("time"), broker_utc_offset_minutes)
    if tick_time is None:
        return last
    if last.time_utc + timedelta(minutes=execution_minutes) > tick_time + timedelta(
        seconds=tolerance_seconds
    ):
        return bars[-2] if len(bars) >= 2 else None
    return last


def _choose_filling(filling_mode: int) -> tuple[int, str]:
    """Map the symbol filling bitmask to an MT5 ORDER_FILLING_* value."""
    if filling_mode & 2:
        return 1, "IOC"
    if filling_mode & 1:
        return 0, "FOK"
    return 2, "RETURN"


def _order_request(
    *,
    symbol: str,
    volume: float,
    price: float,
    stop_loss: float,
    take_profit: float,
    deviation_points: int,
    magic: int,
    comment: str,
    filling_mode: int,
) -> dict[str, Any]:
    filling, _ = _choose_filling(filling_mode)
    return {
        "action": 1,  # TRADE_ACTION_DEAL
        "symbol": symbol,
        "volume": float(volume),
        "type": 0,  # ORDER_TYPE_BUY
        "price": float(price),
        "sl": float(stop_loss),
        "tp": float(take_profit),
        "deviation": int(deviation_points),
        "magic": int(magic),
        "comment": comment,
        "type_time": 0,  # ORDER_TIME_GTC
        "type_filling": filling,
    }


def run_demo_preflight(
    *,
    manifest: dict[str, Any],
    macro_dir: str | Path,
    ledger_path: str | Path | None = None,
    bars: int = 1600,
    context_bars: int = 500,
    broker_utc_offset_minutes: int = 0,
    deviation_points: int = 20,
    magic: int = 20261114,
    expected_login: int | None = None,
    broker_offset_confirmed: bool = False,
    feed: MT5DataFeed | None = None,
) -> DemoPreflightResult:
    """Run a read-only demo preflight and persist the decision when asked.

    ``order_check`` is called only when the strategy returns ``ENTER_LONG``.
    ``order_send`` is never called by this function.
    """
    generated_at = utc_now()
    scope_id = str(manifest.get("scope_id", "unknown"))
    profile = str(manifest.get("active_account_profile", "base"))
    high_risk = bool(manifest.get("profile_high_risk_override", False))
    promotion_eligible = bool(manifest.get("profile_promotion_eligible", True))
    digest = manifest_hash(manifest)
    market = as_dict(manifest.get("market"))
    symbol_name = str(market.get("symbol", "XAUUSD"))
    execution_bar = str(market.get("execution_bar", "M15"))
    context_bar = str(market.get("context_bar", "H4"))
    execution_minutes = 15 if execution_bar.upper() == "M15" else 60
    items: list[PreflightItem] = []
    account: dict[str, Any] = {}
    terminal: dict[str, Any] = {}
    symbol: dict[str, Any] = {}
    tick: dict[str, Any] = {}
    decision: StrategyDecision | None = None
    order_request: dict[str, Any] | None = None
    order_check: dict[str, Any] | None = None

    active_feed = feed or MT5DataFeed(
        symbol=symbol_name,
        broker_utc_offset_minutes=broker_utc_offset_minutes,
    )
    owns_feed = feed is None
    try:
        try:
            active_feed.connect()
        except Exception as exc:  # noqa: BLE001 - preflight must report the blocker
            _add(items, "mt5_initialize", BLOCKED, f"MT5 initialize failed: {exc}")
            return DemoPreflightResult(
                generated_at_utc=generated_at,
                manifest_hash=digest,
                scope_id=scope_id,
                account_profile=profile,
                high_risk_override=high_risk,
                promotion_eligible=promotion_eligible,
                account=account,
                terminal=terminal,
                symbol=symbol,
                tick=tick,
                items=items,
                decision=None,
                order_request=None,
                order_check=None,
                ledger_path=str(ledger_path) if ledger_path else None,
            )

        account = active_feed.account_info()
        terminal = active_feed.terminal_info()
        symbol = active_feed.symbol_info()
        tick = active_feed.symbol_tick()

        connected = bool(terminal.get("connected", False))
        _add(
            items,
            "mt5_connected",
            PASS if connected else FAIL,
            "terminal is connected" if connected else "terminal is not connected",
        )
        trade_allowed = bool(terminal.get("trade_allowed", False))
        _add(
            items,
            "algo_trading",
            PASS if trade_allowed else FAIL,
            (
                "terminal trade_allowed=True"
                if trade_allowed
                else "terminal trade_allowed=False; enable the Algo Trading toolbar button"
            ),
        )
        api_disabled = bool(terminal.get("tradeapi_disabled", True))
        _add(
            items,
            "python_trade_api",
            PASS if not api_disabled else FAIL,
            (
                "Python trade API is enabled"
                if not api_disabled
                else "tradeapi_disabled=True; uncheck Tools > Options > Expert Advisors > "
                "Disable automated trading via external Python API"
            ),
        )

        trade_mode = account.get("trade_mode")
        is_demo = trade_mode == 0
        _add(
            items,
            "demo_account",
            PASS if is_demo else FAIL,
            (
                f"account {account.get('login')} on {account.get('server')} is a demo account"
                if is_demo
                else f"account trade_mode={trade_mode!r}; expected demo mode 0"
            ),
            login=account.get("login"),
            server=account.get("server"),
            trade_mode=trade_mode,
        )
        if expected_login is not None and account.get("login") != expected_login:
            _add(
                items,
                "expected_login",
                FAIL,
                f"connected account {account.get('login')} != expected {expected_login}",
            )
        else:
            _add(
                items,
                "expected_login",
                PASS,
                "connected account matches the requested login"
                if expected_login is not None
                else "no expected login was specified",
            )

        if not symbol:
            _add(items, "symbol", FAIL, f"symbol {symbol_name} is not available")
        else:
            visible = bool(symbol.get("visible", False))
            _add(
                items,
                "symbol_visible",
                PASS if visible else FAIL,
                f"{symbol_name} visible={visible}",
            )
            symbol_trade_mode = symbol.get("trade_mode")
            _add(
                items,
                "symbol_trade_mode",
                PASS if symbol_trade_mode == 4 else FAIL,
                f"{symbol_name} trade_mode={symbol_trade_mode!r}; expected 4 (full)",
            )
            volume_min = float(symbol.get("volume_min") or 0.0)
            volume_step = float(symbol.get("volume_step") or 0.0)
            volume_ok = volume_min > 0.0 and volume_min <= 0.01 + 1e-12 and (
                volume_step <= 0.01 + 1e-12
            )
            _add(
                items,
                "symbol_volume",
                PASS if volume_ok else FAIL,
                f"volume_min={volume_min}, volume_step={volume_step}",
                volume_min=volume_min,
                volume_step=volume_step,
            )
            filling_mode = int(symbol.get("filling_mode") or 0)
            _, filling_name = _choose_filling(filling_mode)
            _add(
                items,
                "symbol_filling",
                PASS,
                f"filling_mode bitmask={filling_mode}; preflight will use {filling_name}",
                filling_mode=filling_mode,
                selected_filling=filling_name,
            )

        point = float(symbol.get("point") or 0.01)
        bid = float(tick.get("bid") or 0.0)
        ask = float(tick.get("ask") or 0.0)
        spread_points: float | None = None
        if point > 0.0 and ask > 0.0 and bid > 0.0:
            spread_points = (ask - bid) / point
        technical = as_dict(as_dict(manifest.get("strategy")).get("technical"))
        max_spread = float(technical.get("max_spread_points", 100.0))
        spread_ok = spread_points is not None and spread_points <= max_spread + 1e-9
        _add(
            items,
            "spread",
            PASS if spread_ok else FAIL,
            (
                f"spread={spread_points:.1f} points <= {max_spread:.1f}"
                if spread_points is not None and spread_ok
                else f"spread={spread_points!r} points; max {max_spread:.1f}"
            ),
            spread_points=spread_points,
            max_spread_points=max_spread,
        )

        tick_time = _mt5_time_to_utc(tick.get("time"), broker_utc_offset_minutes)
        tick_age_seconds: float | None = None
        if tick_time is not None:
            tick_age_seconds = (generated_at - tick_time).total_seconds()
        market_open = tick_age_seconds is not None and tick_age_seconds <= 600.0
        _add(
            items,
            "market_open",
            PASS if market_open else BLOCKED,
            (
                f"last tick age={tick_age_seconds:.1f}s; market appears open"
                if market_open
                else (
                    f"last tick age={tick_age_seconds!r}s; market appears closed "
                    + (
                        "(stale tick, weekend/holiday)"
                        if broker_offset_confirmed
                        else "(stale tick, weekend/holiday, or broker offset not calibrated)"
                    )
                )
            ),
            tick_age_seconds=tick_age_seconds,
            broker_utc_offset_minutes=broker_utc_offset_minutes,
        )
        _add(
            items,
            "broker_time_calibration",
            PASS if broker_offset_confirmed else HUMAN_JUDGMENT,
            (
                f"human confirmed broker UTC offset {broker_utc_offset_minutes} minutes"
                if broker_offset_confirmed
                else "broker UTC offset is a human calibration item; the manifest "
                "requires it before live execution, and no offset is recorded in the "
                "manifest."
            ),
            broker_utc_offset_minutes=broker_utc_offset_minutes,
            human_confirmed=broker_offset_confirmed,
        )

        positions = active_feed.positions()
        pending_orders = active_feed.orders()
        _add(
            items,
            "existing_position",
            PASS if not positions else BLOCKED,
            (
                "no open XAUUSD position"
                if not positions
                else f"{len(positions)} open XAUUSD position(s); reconcile before sending"
            ),
            positions=positions,
        )
        _add(
            items,
            "pending_orders",
            PASS if not pending_orders else BLOCKED,
            (
                "no pending XAUUSD orders"
                if not pending_orders
                else f"{len(pending_orders)} pending XAUUSD order(s); reconcile before sending"
            ),
            orders=pending_orders,
        )

        try:
            macro_store = load_macro_directory(macro_dir)
        except Exception as exc:  # noqa: BLE001 - report the data blocker
            _add(items, "macro_data", FAIL, f"could not load macro directory: {exc}")
            macro_store = None

        execution_bars: BarSeries | None = None
        context_series: BarSeries | None = None
        context_count = context_bars
        try:
            execution_bars = BarSeries(active_feed.fetch_bars("M15", bars), "M15")
            context_series = BarSeries(active_feed.fetch_bars("H4", context_count), "H4")
        except Exception as exc:  # noqa: BLE001 - report the data blocker
            _add(items, "mt5_bars", FAIL, f"could not load MT5 bars: {exc}")
        if execution_bars is not None and context_series is not None:
            min_bars_ok = len(execution_bars) >= 500 and len(context_series) >= 500
            _add(
                items,
                "mt5_bars",
                PASS if min_bars_ok else FAIL,
                f"M15={len(execution_bars)} H4={len(context_series)}; minimum is 500/500",
                m15_bars=len(execution_bars),
                h4_bars=len(context_series),
            )

        latest_bar = None
        if execution_bars is not None:
            latest_bar = _latest_closed_bar(
                execution_bars,
                tick=tick,
                broker_utc_offset_minutes=broker_utc_offset_minutes,
                execution_minutes=execution_minutes,
            )
            _add(
                items,
                "latest_closed_bar",
                PASS if latest_bar is not None else FAIL,
                (
                    f"latest closed {execution_bar} bar={latest_bar.time_utc.isoformat()}"
                    if latest_bar is not None
                    else "no closed execution bar was found"
                ),
            )

        if (
            macro_store is not None
            and execution_bars is not None
            and context_series is not None
            and latest_bar is not None
        ):
            as_of = latest_bar.time_utc + timedelta(minutes=execution_minutes)
            equity = float(account.get("equity") or account.get("balance") or 0.0)
            if equity <= 0.0:
                _add(items, "account_equity", FAIL, f"account equity={equity!r}")
            else:
                risk_state = RiskState(
                    equity=equity,
                    starting_equity=equity,
                    daily_start_equity=equity,
                    peak_equity=equity,
                )
                strategy = StrategyEngine(manifest)
                decision = strategy.decide(
                    execution_bars=execution_bars,
                    context_bars=context_series,
                    macro_store=macro_store,
                    as_of_utc=as_of,
                    equity=equity,
                    position=None,
                    risk_state=risk_state,
                )
                fundamental = decision.fundamental
                missing = list(fundamental.missing_inputs)
                stale = list(fundamental.stale_inputs)
                _add(
                    items,
                    "macro_data",
                    BLOCKED if missing or stale else PASS,
                    (
                        "all weight-bearing macro inputs are present and fresh"
                        if not missing and not stale
                        else f"missing={missing}; stale={stale}"
                    ),
                    missing_inputs=missing,
                    stale_inputs=stale,
                    fundamental_score=fundamental.score,
                    source_versions=fundamental.source_versions,
                )
                _add(
                    items,
                    "strategy_decision",
                    PASS if decision.decision_type == "ENTER_LONG" else NO_TRADE,
                    f"{decision.decision_type} at {as_of.isoformat()}; "
                    f"score={decision.combined_score:+.3f}; "
                    f"reasons={' | '.join(decision.reasons)}",
                    decision_type=decision.decision_type,
                    combined_score=decision.combined_score,
                    fundamental_score=decision.fundamental.score,
                    technical_score=decision.technical.score,
                )
                if decision.decision_type == "ENTER_LONG":
                    volume = float(decision.position_size or 0.0)
                    entry_price = ask if ask > 0.0 else float(latest_bar.close)
                    stop_loss = float(decision.stop_loss or 0.0)
                    take_profit = float(decision.take_profit or 0.0)
                    contract_size = float(
                        as_dict(as_dict(manifest.get("strategy")).get("sizing")).get(
                            "contract_size_oz", 100.0
                        )
                    )
                    max_notional_ratio = float(
                        market.get("max_notional_exposure_ratio", 5.0)
                    )
                    max_notional = equity * max_notional_ratio
                    notional = volume * contract_size * entry_price
                    risk_amount = (entry_price - stop_loss) * volume * contract_size
                    risk_pct = risk_amount / equity * 100.0 if equity > 0 else 0.0
                    sizing_config = as_dict(
                        as_dict(manifest.get("strategy")).get("sizing")
                    )
                    risk_config = as_dict(manifest.get("risk"))
                    max_risk_pct = max(
                        float(risk_config.get("per_trade_equity_pct", 0.25)),
                        float(sizing_config.get("min_lot_risk_cap_pct", 0.0)),
                    )
                    stops_level = int(symbol.get("trade_stops_level") or 0)
                    min_stop_distance = stops_level * point
                    stop_distance = entry_price - stop_loss
                    risk_ok = (
                        volume > 0.0
                        and stop_loss > 0.0
                        and take_profit > 0.0
                        and stop_loss < entry_price < take_profit
                        and stop_distance + 1e-9 >= min_stop_distance
                        and notional <= max_notional + 1e-6
                        and risk_pct <= max_risk_pct + 1e-9
                    )
                    _add(
                        items,
                        "order_risk",
                        PASS if risk_ok else FAIL,
                        (
                            f"volume={volume:.2f}, entry={entry_price:.2f}, "
                            f"stop={stop_loss:.2f}, target={take_profit:.2f}, "
                            f"risk={risk_amount:.2f} ({risk_pct:.4f}%), "
                            f"notional={notional:.2f} <= {max_notional:.2f}, "
                            f"min_stop_distance={min_stop_distance:.2f}"
                        ),
                        volume=volume,
                        entry_price=entry_price,
                        stop_loss=stop_loss,
                        take_profit=take_profit,
                        risk_amount=risk_amount,
                        risk_pct=risk_pct,
                        max_risk_pct=max_risk_pct,
                        notional=notional,
                        max_notional=max_notional,
                        min_stop_distance=min_stop_distance,
                    )
                    order_request = _order_request(
                        symbol=symbol_name,
                        volume=volume,
                        price=entry_price,
                        stop_loss=stop_loss,
                        take_profit=take_profit,
                        deviation_points=deviation_points,
                        magic=magic,
                        comment="goldbot demo preflight; no order sent",
                        filling_mode=int(symbol.get("filling_mode") or 0),
                    )
                    try:
                        order_check = active_feed.check_order(order_request)
                        check_ok = int(order_check.get("retcode", -1)) == 0
                        _add(
                            items,
                            "order_check",
                            PASS if check_ok else FAIL,
                            (
                                "MT5 order_check accepted the would-be request"
                                if check_ok
                                else "MT5 order_check rejected the would-be request: "
                                f"retcode={order_check.get('retcode')!r} "
                                f"comment={order_check.get('comment')!r}"
                            ),
                            order_check=order_check,
                        )
                    except Exception as exc:  # noqa: BLE001 - report the check failure
                        _add(items, "order_check", BLOCKED, f"order_check failed: {exc}")

                if ledger_path:
                    _persist_preflight(
                        ledger_path=Path(ledger_path),
                        manifest=manifest,
                        manifest_digest=digest,
                        scope_id=scope_id,
                        account=account,
                        terminal=terminal,
                        symbol=symbol,
                        tick=tick,
                        decision=decision,
                        items=items,
                        order_request=order_request,
                        order_check=order_check,
                        started_at=as_of,
                    )
    finally:
        if owns_feed:
            active_feed.close()

    return DemoPreflightResult(
        generated_at_utc=generated_at,
        manifest_hash=digest,
        scope_id=scope_id,
        account_profile=profile,
        high_risk_override=high_risk,
        promotion_eligible=promotion_eligible,
        account=account,
        terminal=terminal,
        symbol=symbol,
        tick=tick,
        items=items,
        decision=decision,
        order_request=order_request,
        order_check=order_check,
        ledger_path=str(ledger_path) if ledger_path else None,
    )


def _ensure_decision_persisted(
    ledger: Ledger,
    *,
    preflight: DemoPreflightResult,
    decision: StrategyDecision,
    request: dict[str, Any],
    manifest: dict[str, Any],
    mode: str,
) -> None:
    """Persist snapshot/decision/run if they are not already recorded.

    The normal preflight path persists everything; the override path does not.
    Orders reference decisions by foreign key, so the override path must
    record the decision (or its order would fail the FK).
    """
    existing = ledger.connection.execute(
        "SELECT 1 FROM decisions WHERE decision_id = ? LIMIT 1",
        (decision.decision_id,),
    ).fetchone()
    if existing is not None:
        return

    scope_id = str(manifest.get("scope_id", "gold.v1"))
    execution_bar = str(as_dict(manifest.get("market")).get("execution_bar", "M15"))
    context_bar = str(as_dict(manifest.get("market")).get("context_bar", "H4"))
    started_at = decision.as_of_utc
    run_id = hashlib.sha256(
        f"{scope_id}:{started_at.isoformat()}:demo_order".encode("utf-8")
    ).hexdigest()[:16]
    ledger.start_run(
        run_id=run_id,
        scope_id=scope_id,
        manifest_hash=preflight.manifest_hash,
        started_at_utc=started_at,
        summary={
            "mode": mode,
            "symbol": str(request.get("symbol")),
            "account": preflight.account.get("login"),
            "server": preflight.account.get("server"),
            "equity": preflight.account.get("equity"),
            "account_trade_mode": preflight.account.get("trade_mode"),
            "terminal_connected": bool(preflight.terminal.get("connected", False)),
            "trade_allowed": bool(preflight.terminal.get("trade_allowed", False)),
            "tradeapi_disabled": bool(preflight.terminal.get("tradeapi_disabled", True)),
            "status": "PENDING",
            "broker_orders_sent": 0,
        },
        allow_multiple_runs=True,
    )
    ledger.record_decision(
        decision,
        scope_id=scope_id,
        symbol=str(request.get("symbol")),
        execution_bar=execution_bar,
        context_bar=context_bar,
        market={
            "mode": mode,
            "account": preflight.account,
            "terminal": preflight.terminal,
            "symbol": preflight.symbol,
            "tick": preflight.tick,
            "order_request": request,
            "order_check": preflight.order_check,
        },
    )


def _reconcile_pending_intent(feed: Any, request: dict[str, Any]) -> dict[str, Any]:
    """Ask the broker whether a pending intent was actually received.

    A previous run may have died between the broker send and the ledger
    write. Before any resend, look for a broker position that matches the
    request (symbol, magic, volume, and our bot comment).
    """
    try:
        positions = feed.positions()
    except Exception:  # noqa: BLE001 - broker unreadable; safest is to refuse
        return {
            "status": "AMBIGUOUS",
            "detail": "could not read broker positions; refusing to resend until the broker state is verified",
            "candidates": [],
        }

    def _as_dict(position: Any) -> dict[str, Any]:
        if isinstance(position, dict):
            return position
        try:
            return dict(position._asdict())
        except Exception:  # noqa: BLE001 - best-effort field extraction
            return {
                key: getattr(position, key, None)
                for key in (
                    "ticket", "symbol", "type", "volume", "price_open",
                    "sl", "tp", "magic", "comment", "time",
                )
            }

    symbol = str(request.get("symbol") or "").upper()
    magic = int(request.get("magic") or -1)
    volume = float(request.get("volume") or 0.0)
    candidates: list[dict[str, Any]] = []
    for position in positions:
        pos = _as_dict(position)
        pos_symbol = str(pos.get("symbol") or "XAUUSD").upper()
        pos_magic = pos.get("magic")
        pos_volume = pos.get("volume")
        comment = str(pos.get("comment") or "").lower()
        try:
            magic_match = int(pos_magic or -1) == magic
        except (TypeError, ValueError):
            magic_match = False
        try:
            volume_match = abs(float(pos_volume or 0.0) - volume) < 1e-9
        except (TypeError, ValueError):
            volume_match = False
        if (
            pos_symbol == symbol
            and magic_match
            and volume_match
            and comment.startswith("goldbot")
        ):
            candidates.append(pos)

    if len(candidates) == 1:
        return {
            "status": "FILLED_MATCHED",
            "matched": candidates[0],
            "detail": (
                f"broker position ticket={candidates[0].get('ticket')} matches the pending intent"
            ),
        }
    if len(candidates) > 1:
        return {
            "status": "AMBIGUOUS",
            "detail": (
                f"{len(candidates)} broker positions match the pending intent; "
                "refusing to resend, reconcile manually"
            ),
            "candidates": candidates,
        }
    return {
        "status": "NOTHING_SENT",
        "detail": "pending intent found but no matching broker position; safe to send",
    }


def _build_override_preflight(
    *,
    manifest: dict[str, Any],
    active_feed: MT5DataFeed,
    ledger_text: str | None,
    bars: int,
    context_bars: int,
    broker_utc_offset_minutes: int,
    deviation_points: int,
    magic: int,
    expected_login: int,
    broker_offset_confirmed: bool,
) -> DemoPreflightResult:
    """Synthesize a minimal ENTER_LONG-shaped preflight for an explicit emergency override.

    Used only by the guarded ``demo-send --override-blocks`` path. It still refuses to
    run on a non-demo account, on a mismatched login, or when the symbol is not
    available, but it skips the market-condition gate (macro freshness, fusion) so the
    operator can verify the execution chain.
    """
    items: list[PreflightItem] = []
    market = as_dict(manifest.get("market"))
    symbol_name = str(market.get("symbol", "XAUUSD"))
    execution_minutes = 15 if str(market.get("execution_bar", "M15")).upper() == "M15" else 60

    try:
        active_feed.connect()
    except Exception as exc:  # noqa: BLE001 - report and stop the override path
        raise RuntimeError(f"MT5 connect failed: {type(exc).__name__}: {exc}") from exc

    account = active_feed.account_info()
    terminal = active_feed.terminal_info()
    symbol = active_feed.symbol_info()
    tick = active_feed.symbol_tick()

    connected = bool(terminal.get("connected", False))
    _add(
        items,
        "override_mt5",
        PASS if connected else FAIL,
        "terminal is connected" if connected else "terminal is not connected",
    )
    trade_allowed = bool(terminal.get("trade_allowed", False))
    _add(
        items,
        "override_algo_trading",
        PASS if trade_allowed else FAIL,
        "algo trading is enabled" if trade_allowed else "algo trading is disabled",
    )
    api_disabled = bool(terminal.get("tradeapi_disabled", True))
    _add(
        items,
        "override_python_api",
        PASS if not api_disabled else FAIL,
        "Python trade API is enabled" if not api_disabled else "Python trade API is disabled",
    )
    is_demo = account.get("trade_mode") == 0
    _add(
        items,
        "override_demo_account",
        PASS if is_demo else FAIL,
        (
            f"demo account {account.get('login')} on {account.get('server')}"
            if is_demo
            else f"account trade_mode={account.get('trade_mode')!r}; refusing to override"
        ),
        login=account.get("login"),
        server=account.get("server"),
    )
    login_ok = expected_login is not None and account.get("login") == expected_login
    _add(
        items,
        "override_expected_login",
        PASS if login_ok else FAIL,
        (
            f"connected login {account.get('login')} matches expected login"
            if login_ok
            else f"connected login {account.get('login')} != expected {expected_login}"
        ),
    )
    if not symbol:
        _add(items, "override_symbol", FAIL, f"symbol {symbol_name} is not available")
    else:
        _add(
            items,
            "override_symbol",
            PASS,
            f"{symbol_name} visible={symbol.get('visible')}; symbol is available",
        )

    _add(
        items,
        "emergency_demo_override",
        PASS,
        "DEMO OVERRIDE ACTIVE - macro/fusion gates are bypassed. "
        "An order will be placed using only the broker minimum lot.",
    )
    _add(
        items,
        "broker_time_calibration",
        PASS if broker_offset_confirmed else FAIL,
        (
            f"human-confirmed broker UTC offset {broker_utc_offset_minutes} minutes"
            if broker_offset_confirmed
            else "broker offset was not human-confirmed; refusing to override"
        ),
    )

    # Build enough state for a minimal ENTER_LONG-shaped order.
    execution_bars = BarSeries(active_feed.fetch_bars("M15", bars), "M15")
    context_bars_series = BarSeries(active_feed.fetch_bars("H4", context_bars), "H4")
    latest_bar = _latest_closed_bar(
        execution_bars,
        tick=tick,
        broker_utc_offset_minutes=broker_utc_offset_minutes,
        execution_minutes=execution_minutes,
    )
    if latest_bar is None:
        latest_bar = execution_bars.bars[-1]

    try:
        indicator_atr = atr(execution_bars, 14)
        atr_value = float(indicator_atr[-1]) if indicator_atr else float(execution_bars.bars[-1].close) / 100.0
    except Exception:  # noqa: BLE001 - use a safe fallback
        atr_value = float(execution_bars.bars[-1].close) / 100.0
    entry_price = float(tick.get("ask") or latest_bar.close)
    stop_distance = 1.5 * atr_value
    stop_loss = entry_price - stop_distance
    take_profit = entry_price + 2.0 * stop_distance
    as_of = latest_bar.time_utc + timedelta(minutes=execution_minutes)
    equity = float(account.get("equity") or 0.0)

    sizing_config = as_dict(as_dict(manifest.get("strategy")).get("sizing"))
    min_lot = float(sizing_config.get("min_lot", 0.01))

    fundamental = FundamentalView(
        as_of_utc=as_of,
        score=1.0,
        components={},
        diagnostics={},
        missing_inputs=[],
        stale_inputs=[],
        is_valid=True,
        reasons=["DEMO OVERRIDE - gates bypassed"],
        source_versions={},
    )
    technical = TechnicalView(
        as_of_utc=as_of,
        score=1.0,
        components={},
        indicators={"atr": atr_value, "close": latest_bar.close},
        entry_triggers={"override": True},
        is_valid=True,
        reasons=["DEMO OVERRIDE - gates bypassed"],
        bars_used=len(execution_bars.bars),
        context_bars_used=len(context_bars_series.bars),
    )
    fusion = FusionView(
        as_of_utc=as_of,
        combined_score=1.0,
        fundamental_contribution=0.6,
        technical_contribution=0.4,
        entry_candidate=True,
        conflict=False,
        reasons=["DEMO OVERRIDE - gates bypassed"],
    )
    overseer = OverseerDecision(
        as_of_utc=as_of,
        decision="ENTER_LONG",
        side="LONG",
        confidence=1.0,
        verdict="demo_override",
        vetoes=[],
        rationale="DEMO OVERRIDE - operator explicitly activated the bypass",
        source_views={},
    )
    decision = StrategyDecision(
        decision_id=hashlib.sha256(f"OVERRIDE:{as_of.isoformat()}".encode("utf-8")).hexdigest()[:16],
        as_of_utc=as_of,
        decision_type="ENTER_LONG",
        side="LONG",
        confidence=1.0,
        combined_score=1.0,
        fundamental=fundamental,
        technical=technical,
        fusion=fusion,
        overseer=overseer,
        exit_decision=None,
        position_size=min_lot,
        stop_loss=stop_loss,
        take_profit=take_profit,
        reasons=["DEMO OVERRIDE - operator explicitly activated the bypass"],
    )

    order_request = _order_request(
        symbol=symbol_name,
        volume=min_lot,
        price=entry_price,
        stop_loss=stop_loss,
        take_profit=take_profit,
        deviation_points=deviation_points,
        magic=magic,
        comment="goldbot DEMO OVERRIDE",
        filling_mode=int(symbol.get("filling_mode") or 0) if symbol else 0,
    )
    try:
        order_check = active_feed.check_order(order_request)
        retcode = int(order_check.get("retcode", -1))
        _add(
            items,
            "override_order_check",
            PASS if retcode == 0 else FAIL,
            (
                "MT5 order_check accepted the override request"
                if retcode == 0
                else f"MT5 order_check rejected the override request: retcode={retcode} comment={order_check.get('comment')!r}"
            ),
            order_check=order_check,
        )
    except Exception as exc:  # noqa: BLE001
        order_check = None
        _add(
            items,
            "override_order_check",
            BLOCKED,
            f"order_check failed: {exc}",
        )

    return DemoPreflightResult(
        generated_at_utc=utc_now(),
        manifest_hash=manifest_hash(manifest),
        scope_id=str(manifest.get("scope_id", "gold.v1")),
        account_profile=str(manifest.get("active_account_profile", "base")),
        high_risk_override=bool(manifest.get("profile_high_risk_override", False)),
        promotion_eligible=bool(manifest.get("profile_promotion_eligible", True)),
        account=account,
        terminal=terminal,
        symbol=symbol,
        tick=tick,
        items=items,
        decision=decision,
        order_request=order_request,
        order_check=order_check,
        ledger_path=ledger_text,
    )


def run_demo_order(
    *,
    manifest: dict[str, Any],
    macro_dir: str | Path,
    ledger_path: str | Path,
    expected_login: int,
    broker_utc_offset_minutes: int,
    broker_offset_confirmed: bool,
    send: bool,
    confirm_phrase: str | None,
    bars: int = 1600,
    context_bars: int = 500,
    deviation_points: int = 20,
    magic: int = 20261114,
    feed: MT5DataFeed | None = None,
    override_blocks: bool = False,
) -> DemoOrderResult:
    """Run preflight and, only with explicit guards, submit one demo order.

    The order is never sent unless all of the following are true:

    - ``send`` is true,
    - ``confirm_phrase`` is exactly ``SEND DEMO ORDER``,
    - the broker UTC offset has been human-confirmed,
    - the expected demo login was supplied,
    - every preflight item is ``PASS`` and the decision is ``ENTER_LONG``,
    - no order with the same decision idempotency key is already recorded.
    """
    if not send:
        raise ValueError("refusing to send: explicit send=True (--send) is required")
    if confirm_phrase != "SEND DEMO ORDER":
        raise ValueError(
            "refusing to send: --confirm-order must be exactly 'SEND DEMO ORDER'"
        )
    if expected_login is None:
        raise ValueError("refusing to send: --expected-login is required")
    if not broker_offset_confirmed:
        raise ValueError(
            "refusing to send: --confirm-broker-offset is required after human calibration"
        )

    active_feed = feed or MT5DataFeed(
        symbol=str(as_dict(manifest.get("market")).get("symbol", "XAUUSD")),
        broker_utc_offset_minutes=broker_utc_offset_minutes,
    )
    owns_feed = feed is None
    ledger_text = str(ledger_path)
    try:
        if override_blocks:
            preflight = _build_override_preflight(
                manifest=manifest,
                active_feed=active_feed,
                ledger_text=ledger_text,
                bars=bars,
                context_bars=context_bars,
                broker_utc_offset_minutes=broker_utc_offset_minutes,
                deviation_points=deviation_points,
                magic=magic,
                expected_login=expected_login,
                broker_offset_confirmed=broker_offset_confirmed,
            )
        else:
            preflight = run_demo_preflight(
                manifest=manifest,
                macro_dir=macro_dir,
                ledger_path=ledger_path,
                bars=bars,
                context_bars=context_bars,
                broker_utc_offset_minutes=broker_utc_offset_minutes,
                deviation_points=deviation_points,
                magic=magic,
                expected_login=expected_login,
                broker_offset_confirmed=broker_offset_confirmed,
                feed=active_feed,
            )
        if not preflight.send_ready:
            return DemoOrderResult(
                preflight=preflight,
                send_requested=True,
                sent=False,
                send_result=None,
                ledger_path=ledger_text,
            )
        decision = preflight.decision
        request = preflight.order_request
        if decision is None or request is None:
            raise RuntimeError("preflight is send-ready but has no decision/order request")

        idempotency_key = f"{decision.decision_id}:ENTRY"
        order_id = f"broker-order-{decision.decision_id}"
        request = dict(request)
        request["comment"] = (
            "goldbot DEMO OVERRIDE" if override_blocks else "goldbot demo standard_1k"
        )
        with Ledger(ledger_path) as ledger:
            _ensure_decision_persisted(
                ledger,
                preflight=preflight,
                decision=decision,
                request=request,
                manifest=manifest,
                mode="demo_order_override" if override_blocks else "demo_order",
            )
            existing = ledger.connection.execute(
                """
                SELECT order_id, state FROM orders
                WHERE idempotency_key = ?
                LIMIT 1
                """,
                (idempotency_key,),
            ).fetchone()
            if existing is not None:
                state = existing["state"]
                if state in ("FILLED", "REJECTED", "CANCELLED"):
                    return DemoOrderResult(
                        preflight=preflight,
                        send_requested=True,
                        sent=False,
                        send_result={
                            "status": "ALREADY_RECORDED",
                            "order_id": existing["order_id"],
                            "state": state,
                        },
                        ledger_path=ledger_text,
                    )
                # A pending intent means a previous run died between the
                # broker send and the ledger write. Reconcile with the broker.
                recovery = _reconcile_pending_intent(active_feed, request)
                if recovery["status"] == "AMBIGUOUS":
                    return DemoOrderResult(
                        preflight=preflight,
                        send_requested=True,
                        sent=False,
                        send_result={
                            "status": "RECONCILE_NEEDED",
                            "detail": recovery["detail"],
                            "candidates": recovery.get("candidates", []),
                        },
                        ledger_path=ledger_text,
                    )
                if recovery["status"] == "FILLED_MATCHED":
                    order_id = existing["order_id"]
                    matched = recovery["matched"]
                    ledger.update_order_after_broker(
                        order_id=order_id,
                        state="FILLED",
                        broker_order_ticket=(
                            int(matched.get("ticket")) if matched.get("ticket") else None
                        ),
                        raw_response={
                            "status": "RECOVERED_FILLED",
                            "matched": matched,
                        },
                    )
                    submitted_at = _mt5_time_to_utc(
                        matched.get("time"), broker_utc_offset_minutes
                    ) or utc_now()
                    fill_price = float(
                        matched.get("price_open") or request["price"]
                    )
                    fill_volume = float(
                        matched.get("volume") or request["volume"]
                    )
                    point = float((preflight.symbol or {}).get("point") or 0.01)
                    slippage_points = (
                        (fill_price - float(request["price"])) / point
                        if point > 0.0
                        else None
                    )
                    ledger.record_fill(
                        fill_id=f"broker-fill-recover-{decision.decision_id}",
                        order_id=order_id,
                        filled_at_utc=submitted_at,
                        symbol=str(request["symbol"]),
                        side="LONG",
                        volume=fill_volume,
                        price=fill_price,
                        commission=0.0,
                        swap=0.0,
                        slippage_points=slippage_points,
                        broker_deal_ticket=None,
                        raw_deal={"recovered_from": "positions", "matched": matched},
                    )
                    open_time = _mt5_time_to_utc(
                        matched.get("time"), broker_utc_offset_minutes
                    ) or submitted_at
                    ledger.record_position(
                        position_id=f"broker-position-{matched.get('ticket') or decision.decision_id}",
                        symbol=str(request["symbol"]),
                        side="LONG",
                        volume=float(matched.get("volume") or fill_volume),
                        open_price=float(matched.get("price_open") or fill_price),
                        open_time_utc=open_time,
                        stop_loss=float(matched.get("sl") or request["sl"]),
                        take_profit=float(matched.get("tp") or request["tp"]),
                        broker_position_ticket=(
                            int(matched.get("ticket")) if matched.get("ticket") else None
                        ),
                        state="DEMO_OPEN",
                        raw_position=matched,
                    )
                    ledger.log_event(
                        level="INFO",
                        component="demo_order",
                        event_type="demo_order_recovered",
                        message="pending intent matched a broker position; recorded from MT5 reality",
                        correlation_id=decision.decision_id,
                        payload={"matched": matched},
                        logged_at_utc=submitted_at,
                    )
                    return DemoOrderResult(
                        preflight=preflight,
                        send_requested=True,
                        sent=True,
                        send_result={
                            "status": "RECOVERED_FILLED",
                            "detail": recovery["detail"],
                            "matched": matched,
                        },
                        ledger_path=ledger_text,
                    )
            submitted_at = utc_now()
            if existing is None:
                ledger.insert_order_intent(
                    order_id=order_id,
                    decision_id=decision.decision_id,
                    submitted_at_utc=submitted_at,
                    symbol=str(request["symbol"]),
                    side="LONG",
                    order_type="MARKET",
                    volume=float(request["volume"]),
                    price=float(request["price"]),
                    stop_loss=float(request["sl"]),
                    take_profit=float(request["tp"]),
                    idempotency_key=idempotency_key,
                    raw_request=request,
                    deviation_points=int(request["deviation"]),
                    magic=int(request["magic"]),
                    comment=str(request["comment"]),
                )
            send_result = active_feed.send_order(request)
            retcode = int(send_result.get("retcode", -1))
            success = retcode in {10008, 10009}
            broker_order_ticket = send_result.get("order")
            ledger.update_order_after_broker(
                order_id=order_id,
                state="FILLED" if success else "REJECTED",
                broker_order_ticket=(
                    int(broker_order_ticket) if broker_order_ticket else None
                ),
                raw_response=send_result,
            )
            if success:
                deal_ticket = send_result.get("deal")
                fill_price = float(
                    send_result.get("price") or request["price"]
                )
                fill_volume = float(
                    send_result.get("volume") or request["volume"]
                )
                point = float((preflight.symbol or {}).get("point") or 0.01)
                slippage_points = (
                    (fill_price - float(request["price"])) / point
                    if point > 0.0
                    else None
                )
                ledger.record_fill(
                    fill_id=f"broker-fill-{deal_ticket or decision.decision_id}",
                    order_id=order_id,
                    filled_at_utc=submitted_at,
                    symbol=str(request["symbol"]),
                    side="LONG",
                    volume=fill_volume,
                    price=fill_price,
                    commission=0.0,
                    swap=0.0,
                    slippage_points=slippage_points,
                    broker_deal_ticket=int(deal_ticket) if deal_ticket else None,
                    raw_deal=send_result,
                )
                positions = active_feed.positions()
                if positions:
                    position = positions[-1]
                    ticket = position.get("ticket")
                    open_time = (
                        _mt5_time_to_utc(
                            position.get("time"), broker_utc_offset_minutes
                        )
                        or submitted_at
                    )
                    ledger.record_position(
                        position_id=(
                            f"broker-position-{ticket or decision.decision_id}"
                        ),
                        symbol=str(request["symbol"]),
                        side="LONG",
                        volume=float(position.get("volume") or fill_volume),
                        open_price=float(
                            position.get("price_open")
                            or fill_price
                            or request["price"]
                        ),
                        open_time_utc=open_time,
                        stop_loss=(
                            float(position["sl"])
                            if position.get("sl")
                            else float(request["sl"])
                        ),
                        take_profit=(
                            float(position["tp"])
                            if position.get("tp")
                            else float(request["tp"])
                        ),
                        broker_position_ticket=(
                            int(ticket) if ticket else None
                        ),
                        state="DEMO_OPEN",
                        raw_position=position,
                    )
            ledger.log_event(
                level="INFO" if success else "ERROR",
                component="demo_order",
                event_type="demo_order_sent" if success else "demo_order_rejected",
                message=(
                    "demo order accepted by MT5"
                    if success
                    else f"demo order rejected: retcode={retcode}"
                ),
                correlation_id=decision.decision_id,
                payload={
                    "request": request,
                    "response": send_result,
                    "success": success,
                    "broker_orders_sent": 1,
                },
                logged_at_utc=submitted_at,
            )
            return DemoOrderResult(
                preflight=preflight,
                send_requested=True,
                sent=success,
                send_result=send_result,
                ledger_path=ledger_text,
            )
    finally:
        if owns_feed:
            active_feed.close()


def _persist_preflight(
    *,
    ledger_path: Path,
    manifest: dict[str, Any],
    manifest_digest: str,
    scope_id: str,
    account: dict[str, Any],
    terminal: dict[str, Any],
    symbol: dict[str, Any],
    tick: dict[str, Any],
    decision: StrategyDecision,
    items: list[PreflightItem],
    order_request: dict[str, Any] | None,
    order_check: dict[str, Any] | None,
    started_at: datetime,
) -> None:
    run_id = hashlib.sha256(
        f"{scope_id}:{manifest_digest}:{started_at.isoformat()}:demo_preflight".encode(
            "utf-8"
        )
    ).hexdigest()[:16]
    status = _overall_status(items, decision)
    with Ledger(ledger_path) as ledger:
        ledger.start_run(
            run_id=run_id,
            scope_id=scope_id,
            manifest_hash=manifest_digest,
            started_at_utc=started_at,
            summary={
                "mode": "demo_preflight",
                "symbol": str(as_dict(manifest.get("market")).get("symbol", "XAUUSD")),
                "account": account.get("login"),
                "server": account.get("server"),
                "equity": account.get("equity"),
                "status": status,
                "broker_orders_sent": 0,
            },
            allow_multiple_runs=True,
        )
        ledger.record_decision(
            decision,
            scope_id=scope_id,
            symbol=str(as_dict(manifest.get("market")).get("symbol", "XAUUSD")),
            execution_bar=str(as_dict(manifest.get("market")).get("execution_bar", "M15")),
            context_bar=str(as_dict(manifest.get("market")).get("context_bar", "H4")),
            market={
                "mode": "demo_preflight",
                "account": account,
                "terminal": terminal,
                "symbol": symbol,
                "tick": tick,
                "order_request": order_request,
                "order_check": order_check,
            },
        )
        ledger.log_event(
            level="INFO",
            component="demo_preflight",
            event_type="demo_preflight",
            message="demo preflight completed; no broker order was sent",
            correlation_id=run_id,
            payload={
                "status": status,
                "items": [item.to_dict() for item in items],
                "order_request": order_request,
                "order_check": order_check,
                "broker_orders_sent": 0,
            },
            logged_at_utc=ensure_utc(started_at),
        )
