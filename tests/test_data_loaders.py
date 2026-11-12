from __future__ import annotations

from datetime import date, datetime, timezone

from goldbot.data import (
    aggregate_bars,
    load_cot_csv,
    load_fred_csv,
    load_gvz_csv,
    load_macro_directory,
    load_treasury_real_yield_csv,
    make_synthetic_bars,
)


def test_fred_csv_uses_conservative_knowledge_lag() -> None:
    observations = load_fred_csv(
        "observation_date,DFII10\n2026-01-02,2.10\n2026-01-05,2.15\n",
        series_id="DFII10",
        source_id="fred_dfii10",
    )
    assert len(observations) == 2
    assert observations[0].observation_date == date(2026, 1, 2)
    assert observations[0].knowledge_time_utc == datetime(2026, 1, 3, tzinfo=timezone.utc)
    assert observations[1].value == 2.15


def test_alfred_csv_uses_realtime_start_as_knowledge_time() -> None:
    observations = load_fred_csv(
        "observation_date,DFII10,realtime_start,realtime_end\n"
        "2026-01-02,2.10,2026-01-03,2026-01-31\n"
        "2026-01-02,2.20,2026-02-01,9999-12-31\n",
        series_id="DFII10",
        source_id="alfred_dfii10",
    )
    assert len(observations) == 2
    assert observations[0].knowledge_time_utc == datetime(2026, 1, 3, tzinfo=timezone.utc)
    assert observations[1].knowledge_time_utc == datetime(2026, 2, 1, tzinfo=timezone.utc)
    assert observations[1].vintage == "2026-02-01"


def test_macro_directory_prefers_alfred_vintage(tmp_path) -> None:
    (tmp_path / "DFII10.csv").write_text(
        "observation_date,DFII10\n2026-01-02,9.99\n",
        encoding="utf-8",
    )
    (tmp_path / "ALFRED_DFII10.csv").write_text(
        "observation_date,DFII10,realtime_start,realtime_end\n"
        "2026-01-02,2.10,2026-01-03,9999-12-31\n",
        encoding="utf-8",
    )
    store = load_macro_directory(tmp_path)
    series = store.series("DFII10")
    assert len(series) == 1
    assert series[0].source_id == "alfred_dfii10"
    assert series[0].value == 2.10


def test_treasury_real_yield_csv() -> None:
    observations = load_treasury_real_yield_csv(
        "Date,10 YR\n01/02/2026,2.10\n01/05/2026,2.15\n"
    )
    assert len(observations) == 2
    assert observations[0].observation_date == date(2026, 1, 2)
    assert observations[1].knowledge_time_utc == datetime(2026, 1, 6, tzinfo=timezone.utc)


def test_cot_csv_filters_gold_contract_and_computes_net() -> None:
    text = (
        "Market and Exchange Names,As of Date in Form YYYY-MM-DD,CFTC Contract Market Code,"
        "Noncommercial Positions-Long (All),Noncommercial Positions-Short (All)\n"
        "GOLD - COMMODITY EXCHANGE INC.,2026-01-06,088691,\"200,000\",\"150,000\"\n"
        "SILVER - COMMODITY EXCHANGE INC.,2026-01-06,084691,\"50,000\",\"40,000\"\n"
    )
    observations = load_cot_csv(text)
    assert len(observations) == 1
    assert observations[0].value == 50000.0
    assert observations[0].knowledge_time_utc == datetime(2026, 1, 9, 20, 30, tzinfo=timezone.utc)


def test_gvz_csv_loads_diagnostic_series() -> None:
    observations = load_gvz_csv(
        "DATE,OPEN,HIGH,LOW,CLOSE,GVZ\n"
        "01/02/2026,20.0,21.0,19.0,20.5,20.5\n"
        "01/05/2026,20.5,22.0,20.0,21.5,21.5\n"
    )
    assert len(observations) == 2
    assert observations[0].series_id == "GVZ_CLOSE"
    assert observations[0].value == 20.5
    assert observations[0].knowledge_time_utc == datetime(2026, 1, 3, tzinfo=timezone.utc)
    assert observations[1].observation_date == date(2026, 1, 5)


def test_aggregate_bars_into_h4() -> None:
    m15 = make_synthetic_bars(count=16, start_utc=datetime(2026, 1, 5, tzinfo=timezone.utc))
    h4 = aggregate_bars(m15, "H4", 4)
    assert len(h4) == 1
    assert h4[0].open == m15[0].open
    assert h4[0].close == m15[-1].close
    assert h4[0].high == max(bar.high for bar in m15)
    assert h4[0].low == min(bar.low for bar in m15)
    assert h4[0].tick_volume == sum(bar.tick_volume for bar in m15)
