"""Unit tests for the pure ledger-vs-broker comparison (no MT5 required)."""

from goldbot.reconcile import compare_position_sets


def _ledger_position(ticket, volume=0.01, sl=0.0, tp=0.0, state="DEMO_OPEN"):
    return {
        "position_id": f"broker-{ticket}",
        "broker_position_ticket": ticket,
        "volume": volume,
        "stop_loss": sl,
        "take_profit": tp,
        "state": state,
    }


def _mt5_position(ticket, volume=0.01, sl=0.0, tp=0.0):
    return {
        "ticket": ticket,
        "volume": volume,
        "sl": sl,
        "tp": tp,
    }


def test_exact_match():
    result = compare_position_sets(
        [_ledger_position(111, volume=0.01, sl=3800.0, tp=3900.0)],
        [_mt5_position(111, volume=0.01, sl=3800.0, tp=3900.0)],
    )
    assert result["matched"] == ["111"]
    assert result["ledger_only"] == []
    assert result["mt5_only"] == []
    assert result["mismatches"] == []


def test_ledger_only_position_is_flagged():
    result = compare_position_sets(
        [_ledger_position(111), _ledger_position(222)],
        [_mt5_position(111)],
    )
    assert result["matched"] == ["111"]
    assert result["ledger_only"] == ["222"]
    assert result["mt5_only"] == []


def test_broker_only_position_is_flagged():
    result = compare_position_sets(
        [_ledger_position(111)],
        [_mt5_position(111), _mt5_position(333)],
    )
    assert result["matched"] == ["111"]
    assert result["mt5_only"] == ["333"]


def test_stop_loss_mismatch_is_reported_not_silently_accepted():
    result = compare_position_sets(
        [_ledger_position(111, sl=3800.0)],
        [_mt5_position(111, sl=3790.0)],
    )
    assert result["matched"] == []
    assert len(result["mismatches"]) == 1
    assert "stop_loss" in result["mismatches"][0]["problems"][0]


def test_volume_mismatch_is_reported():
    result = compare_position_sets(
        [_ledger_position(111, volume=0.02)],
        [_mt5_position(111, volume=0.01)],
    )
    assert len(result["mismatches"]) == 1
    assert "volume" in result["mismatches"][0]["problems"][0]


def test_empty_on_both_sides_is_a_match():
    result = compare_position_sets([], [])
    assert result["matched"] == []
    assert result["ledger_only"] == []
    assert result["mt5_only"] == []
    assert result["mismatches"] == []
