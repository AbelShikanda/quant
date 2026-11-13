"""Fixed data contracts between the M2 strategy modules.

The field names and meanings here are the interface.  A module may add
diagnostic fields, but it must not silently change the meaning of these
fields.  All timestamps are timezone-aware UTC.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields, is_dataclass
from datetime import date, datetime, timezone
from typing import Any


def ensure_utc(value: datetime) -> datetime:
    """Return a timezone-aware UTC datetime."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def to_jsonable(value: Any) -> Any:
    """Recursively convert dataclasses and timestamps to JSON-safe values."""
    if is_dataclass(value) and not isinstance(value, type):
        return {item.name: to_jsonable(getattr(value, item.name)) for item in fields(value)}
    if isinstance(value, datetime):
        return ensure_utc(value).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): to_jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_jsonable(item) for item in value]
    return value


@dataclass(frozen=True)
class Bar:
    """One OHLCV bar.  ``time_utc`` is the bar-open time."""

    time_utc: datetime
    open: float
    high: float
    low: float
    close: float
    tick_volume: int = 0
    spread_points: float = 0.0
    timeframe: str = "M15"

    def __post_init__(self) -> None:
        object.__setattr__(self, "time_utc", ensure_utc(self.time_utc))

    def to_dict(self) -> dict[str, Any]:
        return to_jsonable(self)


@dataclass(frozen=True)
class MacroObservation:
    """A point-in-time macro value.

    ``knowledge_time_utc`` is the first time the value was knowable.  For a
    vintage series this is the vintage/release time, not the observation date.
    """

    series_id: str
    knowledge_time_utc: datetime
    value: float
    source_id: str
    vintage: str | None = None
    observation_date: date | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "knowledge_time_utc", ensure_utc(self.knowledge_time_utc))

    def to_dict(self) -> dict[str, Any]:
        return to_jsonable(self)


@dataclass
class FundamentalComponent:
    input_id: str
    series_id: str
    value: float | None
    signal: float
    weight: float
    contribution: float
    transform: str
    sign: str
    stale: bool
    source_id: str | None = None
    knowledge_time_utc: datetime | None = None
    note: str = ""


@dataclass
class FundamentalView:
    as_of_utc: datetime
    score: float
    components: dict[str, FundamentalComponent]
    diagnostics: dict[str, dict[str, Any]]
    missing_inputs: list[str] = field(default_factory=list)
    stale_inputs: list[str] = field(default_factory=list)
    is_valid: bool = True
    reasons: list[str] = field(default_factory=list)
    source_versions: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return to_jsonable(self)


@dataclass
class TechnicalView:
    as_of_utc: datetime
    score: float
    components: dict[str, float]
    indicators: dict[str, float]
    entry_triggers: dict[str, bool]
    is_valid: bool
    reasons: list[str] = field(default_factory=list)
    bars_used: int = 0
    context_bars_used: int = 0

    def to_dict(self) -> dict[str, Any]:
        return to_jsonable(self)


@dataclass
class FusionView:
    as_of_utc: datetime
    combined_score: float
    fundamental_contribution: float
    technical_contribution: float
    entry_candidate: bool
    conflict: bool
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return to_jsonable(self)


@dataclass
class RiskState:
    equity: float
    starting_equity: float
    daily_start_equity: float
    peak_equity: float
    daily_pnl: float = 0.0
    current_drawdown_pct: float = 0.0
    daily_halt: bool = False
    max_drawdown_halt: bool = False
    kill_switch: bool = False

    def update(self, equity: float) -> None:
        self.equity = equity
        self.daily_pnl = equity - self.daily_start_equity
        self.peak_equity = max(self.peak_equity, equity)
        if self.peak_equity > 0:
            self.current_drawdown_pct = (self.peak_equity - equity) / self.peak_equity * 100.0
        else:
            self.current_drawdown_pct = 0.0

    def start_new_day(self) -> None:
        """Reset the daily loss budget from the last marked equity, including open PnL."""
        self.daily_start_equity = self.equity
        self.daily_pnl = 0.0
        self.daily_halt = False

    def to_dict(self) -> dict[str, Any]:
        return to_jsonable(self)


@dataclass
class PositionState:
    position_id: str
    side: str = "LONG"
    volume: float = 0.0
    open_price: float = 0.0
    open_time_utc: datetime | None = None
    stop_loss: float | None = None
    take_profit: float | None = None
    highest_price: float | None = None
    bars_in_trade: int = 0
    entry_atr: float | None = None
    entry_score: float = 0.0
    thesis_valid: bool = True

    def to_dict(self) -> dict[str, Any]:
        return to_jsonable(self)


@dataclass(frozen=True)
class ExitCandidate:
    rule_id: str
    priority: int
    action: str
    reason: str
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return to_jsonable(self)


@dataclass
class ExitDecision:
    as_of_utc: datetime
    should_exit: bool
    rule_id: str | None
    priority: int | None
    action: str | None
    reason: str | None
    candidates: list[ExitCandidate] = field(default_factory=list)
    risk_veto_immune: bool = False

    def to_dict(self) -> dict[str, Any]:
        return to_jsonable(self)


@dataclass
class OverseerDecision:
    as_of_utc: datetime
    decision: str
    side: str
    confidence: float
    verdict: str
    vetoes: list[str] = field(default_factory=list)
    rationale: str = ""
    source_views: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return to_jsonable(self)


@dataclass
class StrategyDecision:
    decision_id: str
    as_of_utc: datetime
    decision_type: str
    side: str
    confidence: float
    combined_score: float
    fundamental: FundamentalView
    technical: TechnicalView
    fusion: FusionView
    overseer: OverseerDecision
    exit_decision: ExitDecision | None
    position_size: float | None
    stop_loss: float | None
    take_profit: float | None
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return to_jsonable(self)


@dataclass
class Trade:
    trade_id: str
    side: str
    volume: float
    entry_time_utc: datetime
    entry_price: float
    exit_time_utc: datetime | None = None
    exit_price: float | None = None
    exit_reason: str | None = None
    pnl: float = 0.0
    mae: float = 0.0
    mfe: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return to_jsonable(self)
