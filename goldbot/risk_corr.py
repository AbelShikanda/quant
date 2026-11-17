"""Gold/USD correlation check — read-only analytic against the gold-long thesis.

Computes the trailing Pearson correlation (bar-to-bar returns) between
XAUUSD and a USD index symbol available on the broker feed (DXY/USDX/…).
The bot is XAUUSD long-only; a strongly positive ρ would mean gold and
the dollar moving together — the opposite of the classic inverse link our
fundamental engine relies on. Read-only: never places orders.
"""

from __future__ import annotations

import math
from typing import Any

from .data import MT5DataFeed

USD_CANDIDATES = ("DXY", "USDX", "USDIDX", "US dollar index", "DOLLAR_INDEX")


def _candidate_symbol(feed) -> str | None:
    for symbol in USD_CANDIDATES:
        try:
            info = feed._mt5.symbol_info(symbol) if feed._mt5 else None
        except Exception:  # noqa: BLE001 - best effort detection
            info = None
        if info is not None:
            return symbol
    return None


def _returns(closes: list[float]) -> list[float]:
    return [
        (closes[i] - closes[i - 1]) / closes[i - 1]
        for i in range(1, len(closes))
        if closes[i - 1]
    ]


def _pearson(x: list[float], y: list[float]) -> float | None:
    n = min(len(x), len(y))
    if n < 20:
        return None
    x, y = x[-n:], y[-n:]
    mx = sum(x) / n
    my = sum(y) / n
    cov = sum((a - mx) * (b - my) for a, b in zip(x, y))
    var_x = sum((a - mx) ** 2 for a in x)
    var_y = sum((b - my) ** 2 for b in y)
    if var_x == 0 or var_y == 0:
        return None
    return cov / math.sqrt(var_x * var_y)


def run_risk_corr(
    *,
    bars: int = 400,
    broker_utc_offset_minutes: int = 120,
    feed: Any | None = None,
    macro_dir: str = "data/raw/macro",
) -> dict[str, Any]:
    owns_feed = feed is None
    active = feed or MT5DataFeed(
        symbol="XAUUSD", broker_utc_offset_minutes=broker_utc_offset_minutes
    )
    try:
        active.connect()
        symbol = _candidate_symbol(active)
        if symbol is not None:
            usd_feed = MT5DataFeed(
                symbol=symbol, broker_utc_offset_minutes=broker_utc_offset_minutes
            )
            try:
                usd_feed.connect()
                gold_bars = active.fetch_bars("H4", bars)
                usd_bars = usd_feed.fetch_bars("H4", bars)
            finally:
                try:
                    usd_feed.close()
                except Exception:  # noqa: BLE001 - best effort
                    pass
            gold_closes = [b.close for b in gold_bars]
            usd_closes = [b.close for b in usd_bars]
            rho = _pearson(_returns(gold_closes), _returns(usd_closes))
            return _verdict("mt5", symbol, len(gold_closes), rho)

        # MT5 has no USD index symbol on this broker build and we do not carry
        # a weekly gold series locally (GLD.xlsx is daily). Say so honestly
        # rather than fabricate a weaker proxy correlation.
        return {
            "mode": "risk_corr",
            "ok": False,
            "status": "unavailable",
            "symbol": None,
            "bars": 0,
            "rho": None,
            "warning": None,
            "note": (
                "no USD index symbol on this broker build; FRED broad-USD is weekly "
                "and there is no weekly gold series in the local macro store — "
                "cannot compute gold/USD rho honestly"
            ),
        }
    except Exception as exc:  # noqa: BLE001 - surface, don't crash
        return {
            "mode": "risk_corr",
            "ok": False,
            "status": "error",
            "error": f"{type(exc).__name__}: {exc}",
        }
    finally:
        if owns_feed:
            try:
                active.close()
            except Exception:  # noqa: BLE001 - best effort
                pass


def _verdict(series: str, symbol: str, bars: int, rho: float | None) -> dict[str, Any]:
    warning = None
    if rho is not None:
        if rho >= 0.3:
            warning = (
                "gold and USD are moving together at this rho; "
                "the fundamental gold-long thesis is being violated"
            )
        elif rho <= -0.3:
            warning = f"classic inverse link holds at rho={rho:+.3f}"
    return {
        "mode": "risk_corr",
        "ok": rho is not None,
        "status": "ok" if rho is not None else "insufficient_data",
        "symbol": symbol,
        "bars": bars,
        "rho": rho,
        "warning": warning,
        "series": series,
    }
