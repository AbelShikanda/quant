"""Trade review: entry/exit quality, MAE/MFE, and account-level summaries.

Read-only analysis of closed positions in the ledger. For each completed
trade it pulls M15 bars from MT5 across the trade window to compute:

- MAE (max adverse excursion) in points and USD
- MFE (max favorable excursion) in points and USD

and reports per-trade results plus an aggregate summary (win rate, totals,
avg win/loss, expectancy). Trades it cannot measure it labels SKIPPED —
it never fabricates excursions.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .contracts import utc_now


@dataclass
class TradeReview:
    position_id: str
    open_time_utc: str
    close_time_utc: str
    side: str
    volume: float
    open_price: float
    close_price: float
    realized_pnl: float | None
    mae_points: float | None
    mfe_points: float | None
    mae_usd: float | None
    mfe_usd: float | None
    duration_minutes: float | None
    close_reason: str
    bars_used: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "position_id": self.position_id,
            "open_time_utc": self.open_time_utc,
            "close_time_utc": self.close_time_utc,
            "side": self.side,
            "volume": self.volume,
            "open_price": self.open_price,
            "close_price": self.close_price,
            "realized_pnl": self.realized_pnl,
            "mae_points": self.mae_points,
            "mfe_points": self.mfe_points,
            "mae_usd": self.mae_usd,
            "mfe_usd": self.mfe_usd,
            "duration_minutes": self.duration_minutes,
            "close_reason": self.close_reason,
            "bars_used": self.bars_used,
        }


def _ensure_utc(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        if isinstance(value, datetime):
            parsed = value
        else:
            text = str(value).strip()
            if text.endswith("Z"):
                text = text[:-1] + "+00:00"
            parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _rows(path: Path) -> list[dict[str, Any]]:
    connection = sqlite3.connect(
        f"file:{path.resolve().as_posix()}?mode=ro", uri=True, timeout=2.0
    )
    connection.row_factory = sqlite3.Row
    try:
        return [dict(row) for row in connection.execute("""
            SELECT position_id, side, volume, open_price, open_time_utc,
                   close_price, close_time_utc, close_reason, realized_pnl, state
            FROM positions
            WHERE close_time_utc IS NOT NULL
            ORDER BY open_time_utc ASC
        """).fetchall()]
    finally:
        connection.close()


def run_trade_review(
    *,
    ledger_path: str | Path,
    symbol: str = "XAUUSD",
    broker_utc_offset_minutes: int = 120,
    since: datetime | None = None,
    until: datetime | None = None,
    feed: Any | None = None,
) -> dict[str, Any]:
    path = Path(ledger_path)
    if not path.exists():
        return {
            "mode": "trade_review",
            "ok": False,
            "error": f"ledger not found: {path}",
            "trades": [],
            "summary": {},
        }

    rows = _rows(path)
    if since:
        since_ts = since.timestamp()
        rows = [
            row for row in rows
            if (_ensure_utc(row["open_time_utc"]) or datetime.min.replace(tzinfo=timezone.utc)).timestamp() >= since_ts
        ]
    if until:
        until_ts = until.timestamp()
        rows = [
            row for row in rows
            if (_ensure_utc(row["open_time_utc"]) or datetime.min.replace(tzinfo=timezone.utc)).timestamp() <= until_ts
        ]

    owns_feed = feed is None
    if feed is None:
        try:
            from .data import MT5DataFeed

            feed = MT5DataFeed(
                symbol=symbol, broker_utc_offset_minutes=broker_utc_offset_minutes
            )
        except Exception:  # noqa: BLE001 - report, don't crash
            feed = None

    trades: list[TradeReview] = []
    skipped: list[dict[str, Any]] = []
    for row in rows:
        open_time = _ensure_utc(row["open_time_utc"])
        close_time = _ensure_utc(row["close_time_utc"])
        if open_time is None or close_time is None:
            skipped.append({"position_id": row["position_id"], "reason": "unparseable timestamps"})
            continue
        side = str(row["side"] or "").upper()
        entry_price = float(row["open_price"] or 0.0)
        exit_price = float(row["close_price"] or 0.0)
        volume = float(row["volume"] or 0.0)
        realized = row.get("realized_pnl")
        mae_points: float | None = None
        mfe_points: float | None = None
        bars_used = 0
        close_reason = str(row["close_reason"] or "unknown")

        if feed is not None:
            try:
                if owns_feed and not getattr(feed, "_mt5", None):
                    feed.connect()
                # Fetch M15 bars across the trade window. Bars covering
                # [open, close] are benign for MAE/MFE since the close is known.
                duration_minutes = (close_time - open_time).total_seconds() / 60.0
                # Over-fetch so the window [open,close] is covered; cap at 1600.
                counts = min(max(1600, int(duration_minutes / 15.0) + 8), 1600)
                bars = feed.fetch_bars("M15", counts)
                bars = [
                    b for b in bars
                    if b.time_utc >= open_time - timedelta(minutes=15)
                    and b.time_utc <= close_time + timedelta(minutes=15)
                ]
                if bars:
                    bars_used = len(bars)
                    lows = [float(b.low) for b in bars]
                    highs = [float(b.high) for b in bars]
                    if side == "LONG":
                        min_low = min(lows)
                        max_high = max(highs)
                        mae_points = max(0.0, entry_price - min_low)
                        mfe_points = max(0.0, max_high - entry_price)
                    elif side in {"SELL", "SELLING"} or row.get("side") in ("SHORT",):
                        min_low = min(lows)
                        max_high = max(highs)
                        mae_points = max(0.0, max_high - entry_price)
                        mfe_points = max(0.0, entry_price - min_low)
            except Exception as exc:  # noqa: BLE001 - keep review alive
                skipped.append(
                    {
                        "position_id": row["position_id"],
                        "reason": f"bar fetch failed: {type(exc).__name__}: {exc}",
                    }
                )
                continue

        mae_usd = mae_points * volume * 100.0 if mae_points is not None else None
        mfe_usd = mfe_points * volume * 100.0 if mfe_points is not None else None
        duration_minutes = (close_time - open_time).total_seconds() / 60.0
        trades.append(
            TradeReview(
                position_id=str(row["position_id"]),
                open_time_utc=open_time.isoformat(),
                close_time_utc=close_time.isoformat(),
                side=side,
                volume=volume,
                open_price=entry_price,
                close_price=exit_price,
                realized_pnl=float(realized) if realized is not None else None,
                mae_points=round(mae_points, 2) if mae_points is not None else None,
                mfe_points=round(mfe_points, 2) if mfe_points is not None else None,
                mae_usd=round(mae_usd, 2) if mae_usd is not None else None,
                mfe_usd=round(mfe_usd, 2) if mfe_usd is not None else None,
                duration_minutes=round(duration_minutes, 1),
                close_reason=close_reason,
                bars_used=bars_used,
            )
        )

    wins = [t for t in trades if (t.realized_pnl or 0.0) > 0]
    losses = [t for t in trades if (t.realized_pnl or 0.0) < 0]
    total_pnl = sum(t.realized_pnl or 0.0 for t in trades)
    avg_win = (sum(t.realized_pnl or 0.0 for t in wins) / len(wins)) if wins else None
    avg_loss = (sum(t.realized_pnl or 0.0 for t in losses) / len(losses)) if losses else None
    expectancy = (total_pnl / len(trades)) if trades else None

    return {
        "mode": "trade_review",
        "ok": True,
        "ledger": str(path),
        "generated_at_utc": utc_now().isoformat(),
        "trades": [t.to_dict() for t in trades],
        "skipped": skipped,
        "summary": {
            "trades": len(trades),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate_pct": round(len(wins) / len(trades) * 100, 2) if trades else 0.0,
            "total_pnl": round(total_pnl, 2),
            "avg_win": round(avg_win, 2) if avg_win is not None else None,
            "avg_loss": round(avg_loss, 2) if avg_loss is not None else None,
            "expectancy_per_trade": round(expectancy, 2) if expectancy is not None else None,
            "avg_mae_points": round(sum(t.mae_points or 0.0 for t in trades) / len(trades), 2) if trades else None,
            "avg_mfe_points": round(sum(t.mfe_points or 0.0 for t in trades) / len(trades), 2) if trades else None,
        },
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = ["# Trade review\n"]
    if not report.get("ok"):
        return lines[0] + f"\nledger error: {report.get('error')}\n"
    s = report["summary"]
    lines.append("## Summary\n")
    lines.append(f"- trades: **{s['trades']}** (wins {s['wins']} / losses {s['losses']})")
    lines.append(f"- win rate: **{s['win_rate_pct']}%**")
    lines.append(f"- total P&L: **{s['total_pnl']:+.2f}**")
    lines.append(f"- expectancy: {s['expectancy_per_trade']}")
    lines.append(f"- avg win: {s['avg_win']} / avg loss: {s['avg_loss']}")
    lines.append(f"- avg MAE: {s['avg_mae_points']} pts / avg MFE: {s['avg_mfe_points']} pts\n")
    lines.append("## Per-trade\n")
    lines.append("| id | side | entry | exit | P&L | MAE pts | MFE pts | reason |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for t in report["trades"]:
        pnl = t["realized_pnl"] if t["realized_pnl"] is not None else 0.0
        lines.append(
            f"| {t['position_id']} | {t['side']} | {t['open_price']} | {t['close_price']} "
            f"| {pnl:+.2f} | {t['mae_points']} | {t['mfe_points']} | {t['close_reason']} |"
        )
    if report.get("skipped"):
        lines.append("\n## Skipped\n")
        for item in report["skipped"]:
            lines.append(f"- `{item['position_id']}`: {item['reason']}")
    return "\n".join(lines) + "\n"
