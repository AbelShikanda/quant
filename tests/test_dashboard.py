from __future__ import annotations

import json
from pathlib import Path

from goldbot.cli import main
from goldbot.dashboard import build_dashboard, build_dashboard_from_file


def _report() -> dict:
    return {
        "run_id": "run-test-0001",
        "mode": "paper_replay",
        "scope_id": "gold.v1",
        "manifest_hash": "abc123",
        "initial_equity": 100.0,
        "final_equity": 108.5,
        "metrics": {
            "total_return_pct": 8.5,
            "max_drawdown_pct": 10.66,
            "trade_count": 1,
            "win_count": 1,
            "loss_count": 0,
            "win_rate_pct": 100.0,
            "profit_factor": None,
            "expectancy": 8.5,
            "decision_count": 2,
            "entry_decisions": 1,
            "exit_decisions": 1,
        },
        "account_profile": "micro_100",
        "account_profile_description": "Experimental $100 profile.",
        "high_risk_override": True,
        "promotion_eligible": False,
        "risk_summary": {
            "account_model": "leveraged",
            "leverage_cap": 50.0,
            "max_notional_exposure_ratio": 50.0,
            "per_trade_equity_pct": 11.0,
            "min_lot_risk_cap_pct": 11.0,
            "daily_loss_halt_pct": 12.0,
            "max_drawdown_flatten_pct": 25.0,
            "contract_size_oz": 100.0,
            "min_lot": 0.01,
        },
        "data_quality": {"execution_bars": 500, "context_bars": 500, "macro_series": 6},
        "orders": [
            {
                "order_id": "ord-0001",
                "submitted_at_utc": "2026-11-11T21:15:00+00:00",
                "side": "LONG",
                "order_type": "MARKET",
                "volume": 0.01,
                "price": 4114.11,
                "stop_loss": 4100.0,
                "take_profit": 4130.0,
                "state": "PAPER_FILLED",
            }
        ],
        "trades": [
            {
                "trade_id": "pos-0001",
                "side": "LONG",
                "volume": 0.01,
                "entry_price": 4114.11,
                "exit_price": 4122.61,
                "pnl": 8.5,
                "exit_reason": "profit_take",
            }
        ],
        "decision_count": 2,
        "decisions": [
            {
                "as_of_utc": "2026-11-11T21:15:00+00:00",
                "decision_type": "ENTER_LONG",
                "side": "LONG",
                "combined_score": 0.42,
                "fundamental": {"score": 0.5},
                "technical": {"score": 0.34},
                "overseer": {"decision": "ENTER_LONG"},
                "reasons": ["entry approved", "sizing: minimum lot"],
            },
            {
                "as_of_utc": "2026-11-11T22:15:00+00:00",
                "decision_type": "EXIT",
                "side": "LONG",
                "combined_score": 0.42,
                "fundamental": {"score": 0.5},
                "technical": {"score": 0.34},
                "overseer": {"decision": "HOLD"},
                "reasons": ["profit take"],
            },
        ],
    }


def test_dashboard_renders_high_risk_banner_and_read_only_notice(tmp_path: Path) -> None:
    output = build_dashboard(_report(), tmp_path / "dashboard.html")
    text = output.read_text(encoding="utf-8")
    assert "Gold Quantamental Dashboard" in text
    assert "HIGH RISK OVERRIDE" in text
    assert "not promotion-eligible" in text
    assert "micro_100" in text
    assert "Read-only dashboard" in text
    assert "does not connect to MT5" in text
    assert "pos-0001" in text
    assert "Orders / paper executions" in text
    assert "ord-0001" in text
    assert "paper_replay" in text
    assert "no broker orders were sent" in text
    assert "<svg" in text
    assert "import MetaTrader5" not in text
    assert "<script" not in text


def test_dashboard_from_saved_json_and_cli(tmp_path: Path) -> None:
    report_path = tmp_path / "report.json"
    report_path.write_text(json.dumps(_report()), encoding="utf-8")
    output = build_dashboard_from_file(report_path, tmp_path / "from-file.html")
    assert output.exists()
    cli_output = tmp_path / "from-cli.html"
    assert (
        main(
            [
                "dashboard",
                "--report",
                str(report_path),
                "--output",
                str(cli_output),
                "--max-decisions",
                "10",
            ]
        )
        == 0
    )
    assert cli_output.exists()
    assert "Gold Quantamental Dashboard" in cli_output.read_text(encoding="utf-8")
