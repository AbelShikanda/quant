from __future__ import annotations

import hashlib
import json
import threading
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from goldbot.contracts import (
    FundamentalView,
    FusionView,
    OverseerDecision,
    StrategyDecision,
    TechnicalView,
)
from goldbot.cli import build_parser
from goldbot.ledger import Ledger
from goldbot.live_dashboard import load_state, make_server, render_page

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "config" / "storage.schema.sql"


def _decision(as_of: datetime) -> StrategyDecision:
    return StrategyDecision(
        decision_id=f"gold.v1:{as_of.isoformat()}",
        as_of_utc=as_of,
        decision_type="ENTER_LONG",
        side="LONG",
        confidence=0.8,
        combined_score=0.45,
        fundamental=FundamentalView(
            as_of_utc=as_of,
            score=0.5,
            components={},
            diagnostics={},
            source_versions={"DFII10": "vintage-1"},
        ),
        technical=TechnicalView(
            as_of_utc=as_of,
            score=0.4,
            components={},
            indicators={"atr": 1.25},
            entry_triggers={},
            is_valid=True,
        ),
        fusion=FusionView(
            as_of_utc=as_of,
            combined_score=0.45,
            fundamental_contribution=0.3,
            technical_contribution=0.15,
            entry_candidate=True,
            conflict=False,
        ),
        overseer=OverseerDecision(
            as_of_utc=as_of,
            decision="ENTER_LONG",
            side="LONG",
            confidence=0.8,
            verdict="approved",
        ),
        exit_decision=None,
        position_size=0.01,
        stop_loss=4100.0,
        take_profit=4130.0,
        reasons=["entry approved", "sizing: minimum lot"],
    )


def _market() -> dict:
    return {
        "risk_state": {
            "equity": 1002.55,
            "starting_equity": 1000.0,
            "daily_start_equity": 1000.0,
            "peak_equity": 1002.55,
            "daily_pnl": 2.55,
            "current_drawdown_pct": 0.0,
            "daily_halt": False,
            "max_drawdown_halt": False,
            "kill_switch": False,
        },
        "spread": 0.2,
    }


def _populate_ledger(path: Path, *, finished: bool, mode: str) -> datetime:
    started = datetime(2026, 1, 19, 15, 15, tzinfo=timezone.utc)
    with Ledger(path, schema_path=SCHEMA) as ledger:
        ledger.start_run(
            run_id="run-live-dashboard",
            scope_id="gold.v1",
            manifest_hash="manifest-abc",
            started_at_utc=started,
            summary={
                "mode": mode,
                "account_profile": "standard_1k",
                "account_profile_description": "standard profile",
                "high_risk_override": False,
                "promotion_eligible": True,
                "initial_equity": 1000.0,
            },
        )
        decision = _decision(started)
        ledger.record_decision(
            decision,
            scope_id="gold.v1",
            market=_market(),
        )
        ledger.record_order(
            order_id="ord-1",
            decision_id=decision.decision_id,
            submitted_at_utc=started,
            symbol="XAUUSD",
            side="LONG",
            order_type="MARKET",
            volume=0.01,
            price=4114.0,
            stop_loss=4100.0,
            take_profit=4130.0,
            state="FILLED" if mode == "demo_live" else "PAPER_FILLED",
            broker_order_ticket=123 if mode == "demo_live" else None,
            idempotency_key="ord-1",
            raw_request={"mode": mode},
            raw_response={"state": "ok"},
        )
        ledger.record_fill(
            fill_id="fill-1",
            order_id="ord-1",
            filled_at_utc=started,
            symbol="XAUUSD",
            side="LONG",
            volume=0.01,
            price=4114.2,
            commission=0.0,
            broker_deal_ticket=456 if mode == "demo_live" else None,
            raw_deal={"mode": mode},
        )
        ledger.record_position(
            position_id="pos-1",
            symbol="XAUUSD",
            side="LONG",
            volume=0.01,
            open_price=4114.2,
            open_time_utc=started,
            stop_loss=4100.0,
            take_profit=4130.0,
            broker_position_ticket=789 if mode == "demo_live" else None,
            state="OPEN" if mode == "demo_live" else "PAPER_OPEN",
            raw_position={"mode": mode},
        )
        ledger.log_event(
            level="INFO",
            component="test_runtime",
            event_type="smoke",
            message="hello from the test ledger",
            correlation_id="run-live-dashboard",
            logged_at_utc=started,
        )
        if finished:
            ledger.finish_run(
                run_id="run-live-dashboard",
                finished_at_utc=started,
                summary={
                    "mode": mode,
                    "account_profile": "standard_1k",
                    "account_profile_description": "standard profile",
                    "high_risk_override": False,
                    "promotion_eligible": True,
                    "initial_equity": 1000.0,
                    "final_equity": 1002.55,
                    "trade_count": 1,
                    "decision_count": 1,
                    "order_count": 1,
                    "fill_count": 1,
                },
            )
    return started


def test_missing_ledger_is_reported_without_creating_it(tmp_path: Path) -> None:
    path = tmp_path / "missing.sqlite"
    state = load_state(path)
    assert state["ok"] is False
    assert state["ledger_exists"] is False
    assert state["read_only"] is True
    assert state["no_mt5_connection"] is True
    assert "not found" in state["message"].lower()
    assert not path.exists()


def test_load_state_reads_live_demo_run(tmp_path: Path) -> None:
    path = tmp_path / "live.sqlite"
    started = _populate_ledger(path, finished=False, mode="demo_live")
    state = load_state(
        path,
        max_rows=5,
        now=started + timedelta(seconds=5),
        stale_after_seconds=60,
    )
    assert state["ok"] is True
    assert state["mode"] == "demo_live"
    assert state["account_profile"] == "standard_1k"
    assert state["promotion_eligible"] is True
    assert state["high_risk_override"] is False
    assert state["equity"] == 1002.55
    assert state["daily_pnl"] == 2.55
    assert state["open_position"]["position_id"] == "pos-1"
    assert state["open_position"]["broker_position_ticket"] == 789
    assert state["counts"]["orders"] == 1
    assert state["counts"]["fills"] == 1
    assert state["counts"]["strategy_decisions"] == 1
    assert state["latest_decision"]["overseer_verdict"] == "approved"
    assert state["stale"] is False
    assert state["last_activity_age_seconds"] == 5
    assert state["errors"] == []


def test_load_state_marks_completed_paper_run_not_stale(tmp_path: Path) -> None:
    path = tmp_path / "paper.sqlite"
    started = _populate_ledger(path, finished=True, mode="paper_replay")
    state = load_state(
        path,
        now=started + timedelta(days=1),
        stale_after_seconds=60,
    )
    assert state["ok"] is True
    assert state["mode"] == "paper_replay"
    assert state["is_finished"] is True
    assert state["stale"] is False
    assert state["final_equity"] == 1002.55
    page = render_page(state, refresh_seconds=2)
    assert "Paper Replay" in page
    assert "GOLD BOT" in page


def test_read_only_state_does_not_change_ledger_bytes(tmp_path: Path) -> None:
    path = tmp_path / "readonly.sqlite"
    _populate_ledger(path, finished=True, mode="paper_replay")
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    state = load_state(path)
    after = hashlib.sha256(path.read_bytes()).hexdigest()
    assert state["ok"] is True
    assert before == after


def test_read_only_connection_works_while_writer_is_open(tmp_path: Path) -> None:
    path = tmp_path / "writer-open.sqlite"
    started = datetime(2026, 1, 19, 15, 15, tzinfo=timezone.utc)
    with Ledger(path, schema_path=SCHEMA) as ledger:
        ledger.start_run(
            run_id="run-open",
            scope_id="gold.v1",
            manifest_hash="manifest-abc",
            started_at_utc=started,
            summary={"mode": "demo_live", "initial_equity": 1000.0},
        )
        ledger.log_event(
            level="INFO",
            component="test",
            event_type="open-writer",
            message="writer still open",
            logged_at_utc=started,
        )
        state = load_state(path, now=started + timedelta(seconds=1))
        assert state["ok"] is True
        assert state["mode"] == "demo_live"
        assert state["counts"]["logs"] == 1


def test_render_page_is_agentic_and_has_agent_markup(tmp_path: Path) -> None:
    path = tmp_path / "render.sqlite"
    started = _populate_ledger(path, finished=False, mode="demo_live")
    state = load_state(path, now=started + timedelta(seconds=1))
    page = render_page(state, refresh_seconds=2)
    assert "GOLD BOT" in page
    assert "Agent Terminal" in page
    assert "XAUUSD" in page
    assert 'id="startAgentBtn"' in page
    assert 'id="stopAgentBtn"' in page
    assert 'id="priceChart"' in page
    assert 'id="terminal"' in page
    assert 'id="acceptBtn"' in page
    assert "<script" in page.lower()
    assert 'Accept &amp; Execute' in page


def test_http_server_serves_page_and_state(tmp_path: Path) -> None:
    path = tmp_path / "server.sqlite"
    started = _populate_ledger(path, finished=False, mode="demo_live")
    server = make_server(ledger_path=path, host="127.0.0.1", port=0, max_rows=5)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        with urllib.request.urlopen(base + "/", timeout=5) as response:
            page = response.read().decode("utf-8")
            assert response.status == 200
            assert "text/html" in response.headers["Content-Type"]
            assert "GOLD BOT" in page
        with urllib.request.urlopen(base + "/api/state", timeout=5) as response:
            payload = json.loads(response.read().decode("utf-8"))
            assert response.status == 200
            assert payload["v"] == 1
            assert payload["state"]["mode"] == "demo_live"
            assert payload["state"]["open_position"]["position_id"] == "pos-1"
        with urllib.request.urlopen(base + "/health", timeout=5) as response:
            health = json.loads(response.read().decode("utf-8"))
            assert health == {
                "v": 1,
                "ok": True,
                "read_only": True,
                "no_mt5_connection": True,
            }
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_dashboard_server_rejects_non_loopback_host(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="loopback only"):
        make_server(ledger_path=tmp_path / "ledger.sqlite", host="0.0.0.0")


def test_cli_parser_wires_dashboard_live() -> None:
    args = build_parser().parse_args(
        [
            "dashboard-live",
            "--ledger",
            "reports/example.sqlite",
            "--host",
            "127.0.0.1",
            "--port",
            "0",
            "--refresh-seconds",
            "1.5",
            "--max-rows",
            "10",
        ]
    )
    assert args.func.__name__ == "_cmd_dashboard_live"
    assert args.ledger == "reports/example.sqlite"
    assert args.port == 0
    assert args.refresh_seconds == 1.5
    assert args.max_rows == 10


def _post(url: str, payload, *, content_type: str = "application/json"):
    data = json.dumps(payload).encode("utf-8") if not isinstance(payload, (bytes, str)) else payload
    if isinstance(data, str):
        data = data.encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": content_type}, method="POST")
    return urllib.request.urlopen(req, timeout=5)


def test_no_mt5_import_in_live_dashboard() -> None:
    source = Path("goldbot/live_dashboard.py").read_text(encoding="utf-8")
    assert "MetaTrader5" not in source


def test_execute_requires_send_demo_order_phrase(tmp_path: Path) -> None:
    path = tmp_path / "exec.sqlite"
    _populate_ledger(path, finished=False, mode="demo_live")
    server = make_server(ledger_path=path, host="127.0.0.1", port=0, max_rows=5)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            _post(base + "/api/agent/execute", {"confirm": "send demo order"})
        assert exc_info.value.code == 400
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_decline_logs_reason(tmp_path: Path) -> None:
    path = tmp_path / "decline.sqlite"
    _populate_ledger(path, finished=False, mode="demo_live")
    server = make_server(ledger_path=path, host="127.0.0.1", port=0, max_rows=5)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        response = _post(base + "/api/agent/decline", {"reason": "fundamental gate still failing"})
        payload = json.loads(response.read().decode("utf-8"))
        assert payload["ok"] is True
        state = load_state(path)
        assert any(log["message"] == "fundamental gate still failing" for log in state["logs"])
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_post_requires_json_content_type(tmp_path: Path) -> None:
    path = tmp_path / "decline2.sqlite"
    _populate_ledger(path, finished=False, mode="demo_live")
    server = make_server(ledger_path=path, host="127.0.0.1", port=0, max_rows=5)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            _post(base + "/api/agent/decline", b"reason=test", content_type="application/x-www-form-urlencoded")
        assert exc_info.value.code == 415
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_shutdown_endpoint_stops_server(tmp_path: Path) -> None:
    path = tmp_path / "shutdown.sqlite"
    _populate_ledger(path, finished=False, mode="demo_live")
    server = make_server(ledger_path=path, host="127.0.0.1", port=0, max_rows=5)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        payload = json.dumps({"confirm": "SHUT DASHBOARD"}).encode("utf-8")
        req = urllib.request.Request(
            base + "/api/server/shutdown",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=3) as response:
            body = json.loads(response.read().decode("utf-8"))
            assert response.status == 200
            assert body["v"] == 1
            assert body["ok"] is True
            assert body["shutdown"] is True
        thread.join(timeout=5)
        assert not thread.is_alive()
    finally:
        try:
            server.shutdown()
        except Exception:  # noqa: BLE001 - server may already be stopped
            pass
        server.server_close()


def test_profile_selection_endpoint_returns_specs_and_updates_state(tmp_path: Path) -> None:
    path = tmp_path / "profile.sqlite"
    _populate_ledger(path, finished=False, mode="demo_live")
    server = make_server(
        ledger_path=path,
        host="127.0.0.1",
        port=0,
        max_rows=5,
        profile="config/profiles/micro_50.yaml",
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        with urllib.request.urlopen(base + "/api/state", timeout=5) as response:
            payload = json.loads(response.read().decode("utf-8"))
            spec = payload["state"]["profile_spec"]
            assert spec["key"] == "micro_50"
            assert spec["high_risk_override"] is True
            assert spec["promotion_eligible"] is False
            assert any(row["label"] == "Bot leverage cap" for row in spec["spec_rows"])
            assert payload["state"]["selected_profile"] == "micro_50"

        req = urllib.request.Request(
            base + "/api/agent/profile",
            data=json.dumps({"profile": "standard_1k"}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as response:
            payload = json.loads(response.read().decode("utf-8"))
            assert payload["ok"] is True
            assert payload["profile"]["key"] == "standard_1k"
            assert payload["profile"]["promotion_eligible"] is True
            assert payload["profile"]["high_risk_override"] is False

        with urllib.request.urlopen(base + "/api/state", timeout=5) as response:
            payload = json.loads(response.read().decode("utf-8"))
            assert payload["state"]["selected_profile"] == "standard_1k"
            assert payload["state"]["profile_spec"]["key"] == "standard_1k"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_profile_selection_endpoint_rejects_unknown_profile(tmp_path: Path) -> None:
    path = tmp_path / "profile2.sqlite"
    _populate_ledger(path, finished=False, mode="demo_live")
    server = make_server(ledger_path=path, host="127.0.0.1", port=0, max_rows=5)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        req = urllib.request.Request(
            base + "/api/agent/profile",
            data=json.dumps({"profile": "does_not_exist"}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            urllib.request.urlopen(req, timeout=5)
        assert exc_info.value.code == 400
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
