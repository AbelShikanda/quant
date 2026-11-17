"""Tick-level fill verification for the strategy bars.

Answers the honest question: *would this window of M15 entries have traded
where we thought they would?* Read-only against MT5 — no orders are placed.

Honest scope notes:
- FBS XAUUSD provides no market depth; this measures quote (bid/ask), not liquidity.
- One entry bar snapshot per bar: the spread at the first tick at/after the bar
  open. Not a fill simulation — a record of what the quote was when the bot
  would have been committing.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .contracts import utc_now
from .indicators import atr as atr_fn  # noqa: F401 - reserved for future trough model


def _percentiles(values: list[float]) -> dict[str, float]:
    if not values:
        return {"n": 0}
    ordered = sorted(values)
    import math

    def pick(pct: float) -> float:
        return ordered[math.ceil(pct * (len(ordered) - 1))]

    return {
        "n": len(ordered),
        "min": ordered[0],
        "p50": pick(0.50),
        "p90": pick(0.90),
        "p99": pick(0.99),
        "max": ordered[-1],
    }


def run_tick_backtest(
    *,
    broker_utc_offset_minutes: int = 120,
    bars: int = 800,
    feed: Any | None = None,
    progress: bool = False,
) -> dict[str, Any]:
    generated_at = utc_now()
    owns_feed = feed is None
    if feed is None:
        from .data import MT5DataFeed

        feed = MT5DataFeed(symbol="XAUUSD", broker_utc_offset_minutes=broker_utc_offset_minutes)
    try:
        feed.connect()
        bars = min(max(1, int(bars)), 1600)
        rates = feed.fetch_bars("M15", bars)
        window: list[Any] = list(rates)
    except Exception as exc:  # noqa: BLE001 - report, don't crash
        return {
            "mode": "tick_backtest",
            "ok": False,
            "generated_at_utc": generated_at.isoformat(),
            "error": f"{type(exc).__name__}: {exc}",
            "bars_measured": 0,
        }
    finally:
        if owns_feed:
            try:
                feed.close()
            except Exception:  # noqa: BLE001 - best effort
                pass

    point = 0.01
    limit_points: float | None = None
    try:
        import yaml

        scope = yaml.safe_load(Path("config/scope.gold.v1.yaml").read_text(encoding="utf-8"))
        technical = (scope or {}).get("strategy", {}).get("technical", {})
        limit_points = float(technical.get("max_spread_points", 100.0))
    except Exception:  # noqa: BLE001 - fall back to a conservative default
        limit_points = 100.0

    per_bar: list[dict[str, Any]] = []
    spread_at_open: list[float] = []
    spread_in_bar: list[float] = []
    trough_points: list[float] = []

    # Reconnect once for the tick loop; keeps MT5 handle short-lived.
    if owns_feed:
        try:
            feed.connect()
        except Exception as exc:  # noqa: BLE001
            return {
                "mode": "tick_backtest",
                "ok": False,
                "generated_at_utc": generated_at.isoformat(),
                "error": f"{type(exc).__name__}: {exc}",
                "bars_measured": 0,
            }
    try:
        for index, bar in enumerate(window):
            start_ts = int(bar.time_utc.timestamp())
            end_ts = start_ts + 15 * 60
            try:
                ticks = feed._mt5.copy_ticks_range(
                    "XAUUSD", start_ts, end_ts, feed._mt5.COPY_TICKS_ALL
                )
            except Exception:  # noqa: BLE001 - skip this bar, don't fail the whole run
                continue
            if ticks is None or len(ticks) == 0:
                continue
            bar_spreads = []
            bar_lows = []
            for t in ticks:
                try:
                    bid = float(t["bid"])
                    ask = float(t["ask"])
                except Exception:  # noqa: BLE001 - tolerate odd tick shapes
                    continue
                if bid > 0 and ask > 0:
                    bar_spreads.append((ask - bid) / point)
                    bar_lows.append((bid + ask) / 2.0)
            if not bar_spreads:
                continue
            entry_spread = bar_spreads[0]
            spread_at_open.append(entry_spread)
            spread_in_bar.extend(bar_spreads)
            open_mid = bar_lows[0]
            window_low = min(bar_lows)
            trough_points.append((open_mid - window_low) / point)
            per_bar.append(
                {
                    "time_utc": bar.time_utc.isoformat(),
                    "open": float(bar.open),
                    "spread_points": round(entry_spread, 1),
                    "max_spread_points": round(max(bar_spreads), 1),
                    "trough_points": round((open_mid - min(bar_lows)) / point, 1),
                }
            )
    finally:
        if owns_feed:
            try:
                feed.close()
            except Exception:  # noqa: BLE001 - best effort
                pass

    over_limit = sum(1 for s in spread_at_open if limit_points is not None and s > limit_points)
    summary = {
        "bars_measured": len(per_bar),
        "bars_available": len(window),
        "spread_at_open": _percentiles(spread_at_open),
        "spread_in_bar_all_ticks": _percentiles(spread_in_bar),
        "trough_points": _percentiles(trough_points),
        "spread_limit_points": limit_points,
        "bars_over_limit": over_limit,
        "bars_over_limit_pct": round(over_limit / len(per_bar) * 100, 2) if per_bar else 0.0,
        "max_trough_points": max(trough_points) if trough_points else 0.0,
        "mean_trough_points": round(sum(trough_points) / len(trough_points), 2) if trough_points else 0.0,
    }
    return {
        "mode": "tick_backtest",
        "ok": True,
        "generated_at_utc": generated_at.isoformat(),
        "symbol": "XAUUSD",
        "window": {
            "from": window[0].time_utc.isoformat() if window else None,
            "to": window[-1].time_utc.isoformat() if window else None,
        },
        "summary": summary,
        "per_bar_sample": per_bar[:200],
        "note": (
            "One snapshot per bar at the first tick at/after open; not a fill simulation. "
            "No market depth available on FBS XAUUSD — quote spread only."
        ),
    }
