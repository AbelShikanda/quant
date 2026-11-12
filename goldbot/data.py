"""Minimal end-to-end data pipeline for the M2 strategy core.

This module provides:

* an immutable-ish OHLCV series with an as-of window,
* a point-in-time macro store (in-memory and SQLite),
* a DuckDB OHLCV store,
* an MT5 puller that does not depend on any chart being open,
* deterministic synthetic feeds for tests and demos,
* small CSV/XLSX loaders for the free-source files used in v1.

The M2 goal is a correct interface and a runnable path, not a full production
ingester.  Every loader records a knowledge time; no loader silently fills a
missing macro print.
"""

from __future__ import annotations

import csv
import io
import json
import math
import sqlite3
import zipfile
from collections import defaultdict
from collections.abc import Iterable, Sequence
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any

from .contracts import Bar, MacroObservation, ensure_utc


class DataError(RuntimeError):
    """Raised when a data source cannot be read."""


class BarSeries:
    """A sorted OHLCV series with point-in-time windows."""

    def __init__(self, bars: Iterable[Bar], timeframe: str = "M15") -> None:
        self.timeframe = timeframe
        self.bars = sorted(bars, key=lambda item: item.time_utc)

    def __len__(self) -> int:
        return len(self.bars)

    def window(self, end_time_utc: datetime, count: int | None = None) -> list[Bar]:
        end = ensure_utc(end_time_utc)
        eligible = [bar for bar in self.bars if bar.time_utc <= end]
        if count is not None:
            eligible = eligible[-count:]
        return eligible

    def latest(self, end_time_utc: datetime) -> Bar | None:
        window = self.window(end_time_utc, 1)
        return window[-1] if window else None

    def closed_window(
        self,
        decision_time_utc: datetime,
        bar_minutes: int,
        count: int | None = None,
    ) -> list[Bar]:
        """Bars fully closed at or before ``decision_time_utc``.

        A bar's ``time_utc`` is its open time.  Using only bars whose close is
        at or before the decision time prevents a forming higher-timeframe bar
        from leaking its eventual close into the decision.
        """
        decision = ensure_utc(decision_time_utc)
        duration = timedelta(minutes=bar_minutes)
        eligible = [bar for bar in self.bars if bar.time_utc + duration <= decision]
        if count is not None:
            eligible = eligible[-count:]
        return eligible

    def closes(self, end_time_utc: datetime, count: int | None = None) -> list[float]:
        return [bar.close for bar in self.window(end_time_utc, count)]


class PointInTimeStore:
    """In-memory macro observations keyed by knowledge time.

    ``series`` and ``latest`` only expose observations whose knowledge time is
    at or before the requested as-of timestamp.  This is the guard against
    using a future revision in a backtest.
    """

    def __init__(self, observations: Iterable[MacroObservation] | None = None) -> None:
        self._observations: dict[str, list[MacroObservation]] = defaultdict(list)
        if observations:
            self.add_many(observations)

    def add(self, observation: MacroObservation) -> None:
        self._observations[observation.series_id].append(observation)

    def add_many(self, observations: Iterable[MacroObservation]) -> None:
        for observation in observations:
            self.add(observation)

    def series(self, series_id: str, as_of_utc: datetime | None = None) -> list[MacroObservation]:
        items = list(self._observations.get(series_id, []))
        if as_of_utc is not None:
            cutoff = ensure_utc(as_of_utc)
            items = [item for item in items if item.knowledge_time_utc <= cutoff]
        # Stable sort preserves insertion order for duplicate knowledge times.
        return sorted(items, key=lambda item: item.knowledge_time_utc)

    def latest(self, series_id: str, as_of_utc: datetime) -> MacroObservation | None:
        items = self.series(series_id, as_of_utc)
        return items[-1] if items else None

    def snapshot(self, as_of_utc: datetime) -> dict[str, MacroObservation]:
        result: dict[str, MacroObservation] = {}
        for series_id in self._observations:
            latest = self.latest(series_id, as_of_utc)
            if latest is not None:
                result[series_id] = latest
        return result

    def series_ids(self) -> list[str]:
        return sorted(self._observations)


class SQLiteStore:
    """Single-writer SQLite store for macro observations and decisions.

    The connection is opened in WAL mode.  The process that constructs this
    object owns the write connection; no other component should write to the
    same database file.
    """

    def __init__(self, path: str | Path, schema_path: str | Path | None = None) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA journal_mode = WAL")
        self.connection.execute("PRAGMA synchronous = NORMAL")
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.execute("PRAGMA busy_timeout = 5000")
        if schema_path is not None and Path(schema_path).exists():
            self.connection.executescript(Path(schema_path).read_text(encoding="utf-8"))
        self._create_strategy_tables()

    def _create_strategy_tables(self) -> None:
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS macro_observations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                series_id TEXT NOT NULL,
                knowledge_time_utc TEXT NOT NULL,
                observation_date TEXT,
                value REAL NOT NULL,
                source_id TEXT NOT NULL,
                vintage TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_macro_series_time
                ON macro_observations(series_id, knowledge_time_utc);
            CREATE TABLE IF NOT EXISTS strategy_runs (
                run_id TEXT PRIMARY KEY,
                scope_id TEXT NOT NULL,
                manifest_hash TEXT NOT NULL,
                started_at_utc TEXT NOT NULL,
                finished_at_utc TEXT,
                summary_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS strategy_decisions (
                decision_id TEXT PRIMARY KEY,
                as_of_utc TEXT NOT NULL,
                decision_type TEXT NOT NULL,
                side TEXT NOT NULL,
                payload_json TEXT NOT NULL
            );
            """
        )
        self.connection.commit()

    def write_macro(self, observation: MacroObservation) -> None:
        self.connection.execute(
            """
            INSERT INTO macro_observations
                (series_id, knowledge_time_utc, observation_date, value, source_id, vintage)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                observation.series_id,
                observation.knowledge_time_utc.isoformat(),
                observation.observation_date.isoformat() if observation.observation_date else None,
                observation.value,
                observation.source_id,
                observation.vintage,
            ),
        )

    def write_macro_many(self, observations: Iterable[MacroObservation]) -> None:
        for observation in observations:
            self.write_macro(observation)
        self.connection.commit()

    def read_macro_series(
        self, series_id: str, as_of_utc: datetime | None = None
    ) -> list[MacroObservation]:
        if as_of_utc is None:
            rows = self.connection.execute(
                """
                SELECT series_id, knowledge_time_utc, observation_date, value, source_id, vintage
                FROM macro_observations
                WHERE series_id = ?
                ORDER BY knowledge_time_utc ASC, id ASC
                """,
                (series_id,),
            ).fetchall()
        else:
            rows = self.connection.execute(
                """
                SELECT series_id, knowledge_time_utc, observation_date, value, source_id, vintage
                FROM macro_observations
                WHERE series_id = ? AND knowledge_time_utc <= ?
                ORDER BY knowledge_time_utc ASC, id ASC
                """,
                (series_id, ensure_utc(as_of_utc).isoformat()),
            ).fetchall()
        return [self._row_to_observation(row) for row in rows]

    def latest_macro(self, series_id: str, as_of_utc: datetime) -> MacroObservation | None:
        row = self.connection.execute(
            """
            SELECT series_id, knowledge_time_utc, observation_date, value, source_id, vintage
            FROM macro_observations
            WHERE series_id = ? AND knowledge_time_utc <= ?
            ORDER BY knowledge_time_utc DESC, id DESC
            LIMIT 1
            """,
            (series_id, ensure_utc(as_of_utc).isoformat()),
        ).fetchone()
        return self._row_to_observation(row) if row else None

    def as_point_in_time_store(self, as_of_utc: datetime | None = None) -> PointInTimeStore:
        store = PointInTimeStore()
        for series_id in self.series_ids():
            store.add_many(self.read_macro_series(series_id, as_of_utc))
        return store

    def series_ids(self) -> list[str]:
        rows = self.connection.execute(
            "SELECT DISTINCT series_id FROM macro_observations ORDER BY series_id"
        ).fetchall()
        return [str(row["series_id"]) for row in rows]

    def write_run(
        self,
        *,
        run_id: str,
        scope_id: str,
        manifest_hash: str,
        started_at_utc: datetime,
        finished_at_utc: datetime | None,
        summary: dict[str, Any],
    ) -> None:
        self.connection.execute(
            """
            INSERT OR REPLACE INTO strategy_runs
                (run_id, scope_id, manifest_hash, started_at_utc, finished_at_utc, summary_json)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                scope_id,
                manifest_hash,
                ensure_utc(started_at_utc).isoformat(),
                ensure_utc(finished_at_utc).isoformat() if finished_at_utc else None,
                json.dumps(summary, ensure_ascii=False, default=str),
            ),
        )
        self.connection.commit()

    def write_decision(self, decision: Any) -> None:
        payload = decision.to_dict() if hasattr(decision, "to_dict") else decision
        self.connection.execute(
            """
            INSERT OR REPLACE INTO strategy_decisions
                (decision_id, as_of_utc, decision_type, side, payload_json)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                payload["decision_id"],
                payload["as_of_utc"],
                payload["decision_type"],
                payload["side"],
                json.dumps(payload, ensure_ascii=False, default=str),
            ),
        )
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()

    @staticmethod
    def _row_to_observation(row: sqlite3.Row) -> MacroObservation:
        observation_date = row["observation_date"]
        return MacroObservation(
            series_id=str(row["series_id"]),
            knowledge_time_utc=datetime.fromisoformat(str(row["knowledge_time_utc"])),
            observation_date=date.fromisoformat(observation_date) if observation_date else None,
            value=float(row["value"]),
            source_id=str(row["source_id"]),
            vintage=row["vintage"],
        )


class DuckDBBarStore:
    """DuckDB analytical store for OHLCV bars.  Single writer."""

    def __init__(self, path: str | Path) -> None:
        import duckdb

        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = duckdb.connect(str(self.path))
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS bars (
                symbol VARCHAR NOT NULL,
                timeframe VARCHAR NOT NULL,
                time_utc TIMESTAMP NOT NULL,
                open DOUBLE NOT NULL,
                high DOUBLE NOT NULL,
                low DOUBLE NOT NULL,
                close DOUBLE NOT NULL,
                tick_volume BIGINT,
                spread_points DOUBLE
            )
            """
        )
        self.connection.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_bars_unique ON bars(symbol, timeframe, time_utc)"
        )

    def write_bars(self, symbol: str, timeframe: str, bars: Sequence[Bar]) -> None:
        if not bars:
            return
        rows = [
            (
                symbol,
                timeframe,
                ensure_utc(bar.time_utc).replace(tzinfo=None),
                bar.open,
                bar.high,
                bar.low,
                bar.close,
                int(bar.tick_volume),
                float(bar.spread_points),
            )
            for bar in bars
        ]
        self.connection.executemany(
            """
            INSERT OR REPLACE INTO bars
                (symbol, timeframe, time_utc, open, high, low, close, tick_volume, spread_points)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )

    def read_bars(
        self,
        symbol: str,
        timeframe: str,
        start_utc: datetime | None = None,
        end_utc: datetime | None = None,
    ) -> list[Bar]:
        clauses = ["symbol = ?", "timeframe = ?"]
        params: list[Any] = [symbol, timeframe]
        if start_utc is not None:
            clauses.append("time_utc >= ?")
            params.append(ensure_utc(start_utc).replace(tzinfo=None))
        if end_utc is not None:
            clauses.append("time_utc <= ?")
            params.append(ensure_utc(end_utc).replace(tzinfo=None))
        query = (
            "SELECT time_utc, open, high, low, close, tick_volume, spread_points "
            "FROM bars WHERE " + " AND ".join(clauses) + " ORDER BY time_utc"
        )
        rows = self.connection.execute(query, params).fetchall()
        return [
            Bar(
                time_utc=row[0].replace(tzinfo=timezone.utc),
                open=float(row[1]),
                high=float(row[2]),
                low=float(row[3]),
                close=float(row[4]),
                tick_volume=int(row[5] or 0),
                spread_points=float(row[6] or 0.0),
                timeframe=timeframe,
            )
            for row in rows
        ]

    def export_parquet(self, path: str | Path) -> None:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        escaped = str(target).replace("'", "''")
        self.connection.execute(f"COPY bars TO '{escaped}' (FORMAT PARQUET)")

    def close(self) -> None:
        self.connection.close()


class MT5DataFeed:
    """Headless MT5 OHLCV puller.

    ``broker_utc_offset_minutes`` is the broker's offset from UTC.  For example,
    a server on UTC+2 uses ``120``; the raw MT5 timestamp is treated as server
    wall time and the offset is subtracted to get UTC.  The default is 0 and the
    caller must calibrate it for live use.
    """

    _TIMEFRAMES = {"M15": "TIMEFRAME_M15", "H4": "TIMEFRAME_H4"}

    def __init__(
        self,
        symbol: str = "XAUUSD",
        broker_utc_offset_minutes: int = 0,
    ) -> None:
        self.symbol = symbol
        self.broker_utc_offset_minutes = broker_utc_offset_minutes
        self._mt5: Any = None

    def connect(self) -> dict[str, Any]:
        import MetaTrader5 as mt5

        self._mt5 = mt5
        if not mt5.initialize():
            code, message = mt5.last_error()
            raise DataError(f"MT5 initialize failed: ({code}, {message!r})")
        if not mt5.symbol_select(self.symbol, True):
            raise DataError(f"MT5 symbol_select failed for {self.symbol}")
        account = mt5.account_info()
        return account._asdict() if account else {}

    def fetch_bars(self, timeframe: str, count: int = 500) -> list[Bar]:
        if self._mt5 is None:
            raise DataError("MT5DataFeed.connect() must be called first")
        key = self._TIMEFRAMES.get(timeframe.upper())
        if key is None:
            raise DataError(f"unsupported MT5 timeframe: {timeframe}")
        raw = self._mt5.copy_rates_from_pos(self.symbol, getattr(self._mt5, key), 0, count)
        if raw is None or len(raw) == 0:
            code, message = self._mt5.last_error()
            raise DataError(f"MT5 copy_rates_from_pos failed: ({code}, {message!r})")
        bars: list[Bar] = []
        offset = timedelta(minutes=self.broker_utc_offset_minutes)
        for item in raw:
            raw_time = datetime.fromtimestamp(int(item["time"]), tz=timezone.utc)
            bars.append(
                Bar(
                    time_utc=raw_time - offset,
                    open=float(item["open"]),
                    high=float(item["high"]),
                    low=float(item["low"]),
                    close=float(item["close"]),
                    tick_volume=int(item["tick_volume"]),
                    spread_points=float(item["spread"]),
                    timeframe=timeframe.upper(),
                )
            )
        return bars

    def fetch_both(self, execution_count: int = 500, context_count: int = 500) -> tuple[list[Bar], list[Bar]]:
        return (
            self.fetch_bars("M15", execution_count),
            self.fetch_bars("H4", context_count),
        )

    def account_info(self) -> dict[str, Any]:
        if self._mt5 is None:
            raise DataError("MT5DataFeed.connect() must be called first")
        account = self._mt5.account_info()
        return account._asdict() if account else {}

    def symbol_info(self) -> dict[str, Any]:
        if self._mt5 is None:
            raise DataError("MT5DataFeed.connect() must be called first")
        symbol = self._mt5.symbol_info(self.symbol)
        return symbol._asdict() if symbol else {}

    def close(self) -> None:
        if self._mt5 is not None:
            self._mt5.shutdown()
            self._mt5 = None


def aggregate_bars(bars: Sequence[Bar], timeframe: str, hours: int) -> list[Bar]:
    """Aggregate lower-timeframe bars into fixed UTC clock buckets."""
    if hours <= 0:
        raise ValueError("hours must be positive")
    buckets: dict[datetime, list[Bar]] = defaultdict(list)
    for bar in bars:
        bucket = bar.time_utc.replace(
            hour=(bar.time_utc.hour // hours) * hours,
            minute=0,
            second=0,
            microsecond=0,
        )
        buckets[bucket].append(bar)
    result: list[Bar] = []
    for bucket in sorted(buckets):
        group = sorted(buckets[bucket], key=lambda item: item.time_utc)
        result.append(
            Bar(
                time_utc=bucket,
                open=group[0].open,
                high=max(item.high for item in group),
                low=min(item.low for item in group),
                close=group[-1].close,
                tick_volume=sum(item.tick_volume for item in group),
                spread_points=group[-1].spread_points,
                timeframe=timeframe,
            )
        )
    return result


def make_synthetic_bars(
    *,
    start_utc: datetime | None = None,
    count: int = 600,
    minutes: int = 15,
    base_price: float = 2000.0,
    drift_per_bar: float = 0.12,
    amplitude: float = 6.0,
    wave_period: int = 8,
    spread_points: float = 31.0,
    timeframe: str = "M15",
) -> list[Bar]:
    """Deterministic upward-drifting bars for tests and demos."""
    start = ensure_utc(start_utc or datetime(2026, 1, 5, tzinfo=timezone.utc))
    bars: list[Bar] = []
    previous_close = base_price
    for index in range(count):
        wave = amplitude * math.sin(index / wave_period)
        close = base_price + drift_per_bar * index + wave
        open_price = previous_close
        high = max(open_price, close) + 0.5
        low = min(open_price, close) - 0.5
        bars.append(
            Bar(
                time_utc=start + timedelta(minutes=minutes * index),
                open=open_price,
                high=high,
                low=low,
                close=close,
                tick_volume=100 + index,
                spread_points=spread_points,
                timeframe=timeframe,
            )
        )
        previous_close = close
    return bars


def make_synthetic_macro_store(
    *,
    as_of_utc: datetime | None = None,
    days: int = 520,
) -> PointInTimeStore:
    """Generate deterministic daily macro observations.

    The generated path has falling real yields and a falling broad USD, rising
    ETF holdings, and a neutral-ish COT percentile.  It is a demo fixture, not
    a claim about the real world.
    """
    as_of = ensure_utc(as_of_utc or datetime(2026, 11, 12, tzinfo=timezone.utc))
    end_date = as_of.date()
    start_date = end_date - timedelta(days=days - 1)
    store = PointInTimeStore()
    for index in range(days):
        current_date = start_date + timedelta(days=index)
        progress = index / max(days - 1, 1)
        knowledge = datetime.combine(
            current_date + timedelta(days=1), time(0, 0), tzinfo=timezone.utc
        )
        real_yield = 2.25 - 0.55 * progress + 0.06 * math.sin(index / 21.0)
        # Make the final 20-day change negative so the demo fixture has a
        # clearly gold-positive real-yield impulse at the decision date.
        if index > days - 30:
            real_yield -= 0.02 * (index - (days - 30))
        daily = {
            "DFII10": real_yield,
            "DTWEXBGS": 122.0 - 6.0 * progress + 0.8 * math.sin(index / 31.0),
            "GLD_TONNES": 850.0 + 90.0 * progress + 3.0 * math.sin(index / 17.0),
            "T10YIE": 2.30 + 0.05 * math.sin(index / 26.0),
        }
        for series_id, value in daily.items():
            store.add(
                MacroObservation(
                    series_id=series_id,
                    knowledge_time_utc=knowledge,
                    observation_date=current_date,
                    value=value,
                    source_id=f"synthetic.{series_id}",
                    vintage="synthetic",
                )
            )
        if current_date.weekday() == 4:  # Friday COT release
            store.add(
                MacroObservation(
                    series_id="COT_GOLD_NET",
                    knowledge_time_utc=datetime.combine(
                        current_date, time(20, 30), tzinfo=timezone.utc
                    ),
                    observation_date=current_date,
                    value=120000.0 + 45000.0 * math.sin(index / 45.0),
                    source_id="synthetic.COT_GOLD_NET",
                    vintage="synthetic",
                )
            )
        if current_date.day == 1:
            store.add(
                MacroObservation(
                    series_id="WGC_CB_TONNES",
                    knowledge_time_utc=datetime.combine(
                        current_date + timedelta(days=1), time(0, 0), tzinfo=timezone.utc
                    ),
                    observation_date=current_date,
                    value=35000.0 + 20.0 * progress,
                    source_id="synthetic.WGC_CB_TONNES",
                    vintage="synthetic",
                )
            )
    return store


def load_fred_csv(
    text: str,
    *,
    series_id: str,
    source_id: str,
    value_column: str | None = None,
    knowledge_lag_days: int = 1,
) -> list[MacroObservation]:
    """Load a FRED/ALFRED-style CSV.

    If a ``vintage_date`` or ``knowledge_time`` column exists, it is used as the
    knowledge time.  Otherwise the observation date plus a conservative release
    lag is used.  This is a latest-revision live loader, not a vintage replay.
    """
    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames is None:
        raise DataError("CSV has no header")
    fields = list(reader.fieldnames)
    date_column = "observation_date" if "observation_date" in fields else fields[0]
    if value_column is None:
        candidates = [name for name in fields if name != date_column and not name.startswith("vintage")]
        if not candidates:
            raise DataError("CSV has no value column")
        value_column = candidates[0]
    observations: list[MacroObservation] = []
    for row in reader:
        raw_date = (row.get(date_column) or "").strip()
        raw_value = (row.get(value_column) or "").strip()
        if not raw_date or raw_value in {"", ".", "NA"}:
            continue
        observation_date = date.fromisoformat(raw_date[:10])
        knowledge_raw = (row.get("vintage_date") or row.get("knowledge_time") or "").strip()
        if knowledge_raw:
            knowledge_time = datetime.fromisoformat(knowledge_raw.replace("Z", "+00:00"))
        else:
            knowledge_time = datetime.combine(
                observation_date + timedelta(days=knowledge_lag_days),
                time(0, 0),
                tzinfo=timezone.utc,
            )
        observations.append(
            MacroObservation(
                series_id=series_id,
                knowledge_time_utc=knowledge_time,
                observation_date=observation_date,
                value=float(raw_value),
                source_id=source_id,
                vintage=row.get("vintage_date") or None,
            )
        )
    return observations


def load_treasury_real_yield_csv(
    text: str,
    *,
    series_id: str = "DFII10",
    source_id: str = "treasury_real_yield",
) -> list[MacroObservation]:
    reader = csv.DictReader(io.StringIO(text))
    observations: list[MacroObservation] = []
    for row in reader:
        raw_date = (row.get("Date") or "").strip()
        raw_value = (row.get("10 YR") or "").strip()
        if not raw_date or not raw_value:
            continue
        observation_date = datetime.strptime(raw_date, "%m/%d/%Y").date()
        observations.append(
            MacroObservation(
                series_id=series_id,
                knowledge_time_utc=datetime.combine(
                    observation_date + timedelta(days=1), time(0, 0), tzinfo=timezone.utc
                ),
                observation_date=observation_date,
                value=float(raw_value),
                source_id=source_id,
            )
        )
    return observations


def load_gld_xlsx(
    path: str | Path,
    *,
    series_id: str = "GLD_TONNES",
    source_id: str = "spdr_gld_archive",
) -> list[MacroObservation]:
    from openpyxl import load_workbook

    workbook = load_workbook(Path(path), read_only=True, data_only=True)
    try:
        sheet = workbook["US GLD Historical Archive"]
        rows = sheet.iter_rows(values_only=True)
        header = [str(value) if value is not None else "" for value in next(rows)]
        date_index = header.index("Date")
        value_index = header.index("Tonnes of Gold")
        observations: list[MacroObservation] = []
        for row in rows:
            raw_date, raw_value = row[date_index], row[value_index]
            if raw_date is None or raw_value is None:
                continue
            if isinstance(raw_date, datetime):
                observation_date = raw_date.date()
            else:
                observation_date = datetime.strptime(str(raw_date), "%d-%b-%Y").date()
            observations.append(
                MacroObservation(
                    series_id=series_id,
                    knowledge_time_utc=datetime.combine(
                        observation_date + timedelta(days=1), time(0, 0), tzinfo=timezone.utc
                    ),
                    observation_date=observation_date,
                    value=float(raw_value),
                    source_id=source_id,
                )
            )
        return observations
    finally:
        workbook.close()


def load_cot_csv(
    text: str,
    *,
    series_id: str = "COT_GOLD_NET",
    source_id: str = "cftc_cot",
    contract_code: str = "088691",
) -> list[MacroObservation]:
    reader = csv.DictReader(io.StringIO(text))
    observations: list[MacroObservation] = []
    for row in reader:
        if (row.get("CFTC Contract Market Code") or "").strip() != contract_code:
            continue
        raw_date = (row.get("As of Date in Form YYYY-MM-DD") or "").strip()
        long_raw = (row.get("Noncommercial Positions-Long (All)") or "").strip()
        short_raw = (row.get("Noncommercial Positions-Short (All)") or "").strip()
        if not raw_date or not long_raw or not short_raw:
            continue
        observation_date = date.fromisoformat(raw_date)
        net = float(long_raw.replace(",", "")) - float(short_raw.replace(",", ""))
        observations.append(
            MacroObservation(
                series_id=series_id,
                knowledge_time_utc=datetime.combine(
                    observation_date + timedelta(days=3), time(20, 30), tzinfo=timezone.utc
                ),
                observation_date=observation_date,
                value=net,
                source_id=source_id,
            )
        )
    return observations


def load_cot_zip(
    path: str | Path,
    *,
    series_id: str = "COT_GOLD_NET",
    source_id: str = "cftc_cot",
) -> list[MacroObservation]:
    with zipfile.ZipFile(Path(path)) as archive:
        with archive.open("annual.txt") as handle:
            text = io.TextIOWrapper(handle, encoding="utf-8", errors="replace").read()
    return load_cot_csv(text, series_id=series_id, source_id=source_id)


def load_macro_directory(path: str | Path) -> PointInTimeStore:
    """Load a small local macro directory.

    Recognised files: ``DFII10.csv``, ``DTWEXBGS.csv``, ``T10YIE.csv``,
    ``GLD.xlsx``, ``COT.csv``, ``COT.zip``.  Missing files are simply absent;
    the fundamental engine will report them as missing/stale rather than fill
    them silently.
    """
    root = Path(path)
    store = PointInTimeStore()
    fred_files = {
        "DFII10.csv": ("DFII10", "fred_dfii10"),
        "DTWEXBGS.csv": ("DTWEXBGS", "fred_dtwexbgs"),
        "T10YIE.csv": ("T10YIE", "fred_t10yie"),
    }
    for filename, (series_id, source_id) in fred_files.items():
        file_path = root / filename
        if file_path.exists():
            store.add_many(
                load_fred_csv(
                    file_path.read_text(encoding="utf-8"),
                    series_id=series_id,
                    source_id=source_id,
                )
            )
    gld_path = root / "GLD.xlsx"
    if gld_path.exists():
        store.add_many(load_gld_xlsx(gld_path))
    cot_csv = root / "COT.csv"
    cot_zip = root / "COT.zip"
    if cot_csv.exists():
        store.add_many(
            load_cot_csv(cot_csv.read_text(encoding="utf-8"))
        )
    elif cot_zip.exists():
        store.add_many(load_cot_zip(cot_zip))
    return store
