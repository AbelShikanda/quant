"""Spread history sampler and report.

Samples the live bid/ask from MT5 at a fixed cadence and appends to
``reports/spread_history.csv``. The report reads that CSV and shows spread
percentiles overall and by hour-of-day (UTC), including a pre-news window
bucket when calendar events are known.

This is the honest substitute for order-book depth on a CFD symbol where the
broker provides no Level-2 data: *when spreads would have made an entry
unattractive*, measured per hour.
"""

from __future__ import annotations

import csv
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

CSV_PATH = Path("reports/spread_history.csv")
HEADER = ["sampled_at_utc", "bid", "ask", "spread", "spread_points", "hour_utc"]


@dataclass
class SpreadSample:
    sampled_at: datetime
    bid: float
    ask: float
    spread: float
    spread_points: float


def sample_once(feed) -> SpreadSample | None:
    tick = feed.symbol_tick()
    if not tick:
        return None
    bid = float(tick.get("bid") or 0.0)
    ask = float(tick.get("ask") or 0.0)
    if bid <= 0.0 or ask <= 0.0:
        return None
    spread = ask - bid
    point = float(tick.get("point") or 0.01)
    return SpreadSample(
        sampled_at=datetime.now(timezone.utc),
        bid=bid,
        ask=ask,
        spread=spread,
        spread_points=spread / point if point else 0.0,
    )


def append_samples(samples: list[SpreadSample], path: Path = CSV_PATH) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    write_header = not path.exists() or path.stat().st_size == 0
    with path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        if write_header:
            writer.writerow(HEADER)
        for sample in samples:
            writer.writerow(
                [
                    sample.sampled_at.isoformat(),
                    f"{sample.bid:.2f}",
                    f"{sample.ask:.2f}",
                    f"{sample.spread:.2f}",
                    f"{sample.spread_points:.1f}",
                    sample.sampled_at.hour,
                ]
            )
    return len(samples)


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = math.ceil(pct * (len(ordered) - 1))
    return ordered[index]


def summarize(values: list[float]) -> dict[str, float]:
    if not values:
        return {"n": 0}
    return {
        "n": len(values),
        "min": min(values),
        "p50": _percentile(values, 0.50),
        "p90": _percentile(values, 0.90),
        "p99": _percentile(values, 0.99),
        "max": max(values),
    }


def buckets(values: list[float]) -> dict[str, Any]:
    brackets = [
        ("<=25", 0.0, 25.0),
        ("25-35", 25.0, 35.0),
        ("35-50", 35.0, 50.0),
        ("50-75", 50.0, 75.0),
        ("75-100", 75.0, 100.0),
        (">100", 100.0, float("inf")),
    ]
    total = len(values)
    if not total:
        return {"n": 0, "rows": []}
    rows = []
    for label, low, high in brackets:
        count = sum(1 for v in values if v >= low and (v < high or high == float("inf")))
        rows.append(
            {
                "bucket": label,
                "count": count,
                "pct_of_samples": round(count / total * 100, 2),
            }
        )
    return {"n": total, "rows": rows}


def read_samples(path: Path = CSV_PATH) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def report(path: Path = CSV_PATH) -> dict[str, Any]:
    rows = read_samples(path)
    all_spreads = [float(row["spread_points"]) for row in rows]
    by_hour: dict[int, list[float]] = {}
    for row in rows:
        hour = int(row["hour_utc"])
        by_hour.setdefault(hour, []).append(float(row["spread_points"]))
    return {
        "mode": "spread_report",
        "samples": len(rows),
        "overall": summarize(all_spreads),
        "fill_probability_buckets": buckets(all_spreads),
        "by_hour_utc": {
            str(hour): summarize(values) for hour, values in sorted(by_hour.items())
        },
        "note": "spread in points (ask-bid). One point = 0.01 for XAUUSD.",
    }
