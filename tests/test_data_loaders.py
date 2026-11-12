from __future__ import annotations

from datetime import date, datetime, timezone

from goldbot.data import (
    aggregate_bars,
    load_cot_csv,
    load_fred_csv,
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


def test_aggregate_bars_into_h4() -> None:
    m15 = make_synthetic_bars(count=16, start_utc=datetime(2026, 1, 5, tzinfo=timezone.utc))
    h4 = aggregate_bars(m15, "H4", 4)
    assert len(h4) == 1
    assert h4[0].open == m15[0].open
    assert h4[0].close == m15[-1].close
    assert h4[0].high == max(bar.high for bar in m15)
    assert h4[0].low == min(bar.low for bar in m15)
    assert h4[0].tick_volume == sum(bar.tick_volume for bar in m15)
