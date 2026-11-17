"""Unit tests for the reconnect-drill check evaluator (no MT5 required)."""

from goldbot.drill import evaluate_drill_checks


def _position(ticket, volume=0.01):
    return {"ticket": ticket, "volume": volume, "sl": 0.0, "tp": 0.0}


def _ledger_row(ticket, volume=0.01):
    return {
        "position_id": f"broker-{ticket}",
        "broker_position_ticket": ticket,
        "volume": volume,
        "stop_loss": 0.0,
        "take_profit": 0.0,
        "state": "DEMO_OPEN",
    }


def _checks(before, after, ledger_open, first_ok=True, reconnect_ok=True, orders_before=3, orders_after=3):
    return evaluate_drill_checks(
        first_session_ok=first_ok,
        reconnect_ok=reconnect_ok,
        positions_before=before,
        positions_after=after,
        ledger_open=ledger_open,
        orders_before=orders_before,
        orders_after=orders_after,
    )


def test_all_checks_pass_on_stable_state():
    checks = _checks([_position(1)], [_position(1)], [_ledger_row(1)])
    assert all(check.status == "PASS" for check in checks)


def test_first_session_failure_blocks_drill():
    checks = _checks([], [], [], first_ok=False)
    assert checks[0].status == "BLOCKED"


def test_reconnect_failure_blocks_drill():
    checks = _checks([], [], [], reconnect_ok=False)
    assert checks[1].status == "BLOCKED"


def test_positions_appearing_across_reconnect_fails():
    checks = _checks([], [_position(9)], [])
    by_name = {check.name: check for check in checks}
    assert by_name["positions_stable_across_reconnect"].status == "FAIL"


def test_ledger_broker_mismatch_fails():
    checks = _checks(
        [_position(1)],
        [_position(1)],
        [_ledger_row(1), _ledger_row(2)],
    )
    by_name = {check.name: check for check in checks}
    assert by_name["ledger_matches_broker_after_reconnect"].status == "FAIL"


def test_orders_changing_during_drill_fails():
    checks = _checks([], [], [], orders_before=3, orders_after=4)
    by_name = {check.name: check for check in checks}
    assert by_name["order_ledger_stable"].status == "FAIL"
