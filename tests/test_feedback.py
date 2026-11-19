"""Tests for the dashboard feedback store (goldbot/feedback.py)."""

from __future__ import annotations

import base64
from pathlib import Path

import pytest

from goldbot import feedback


def _db(tmp_path: Path) -> Path:
    return tmp_path / "feedback.sqlite"


def test_create_and_get_item_roundtrip(tmp_path: Path) -> None:
    item = feedback.create_item(
        {
            "type": "bug",
            "severity": "high",
            "title": "Kill switch button misaligns",
            "description": "Button overflows its container",
            "steps": "1. Open dashboard\n2. Look at header",
            "expected": "Button fits",
            "actual": "Button clipped",
            "recommended": "Reduce padding",
        },
        db_path=_db(tmp_path),
    )
    assert item["id"] >= 1
    assert item["status"] == "new"
    assert item["type_label"] == "Bug"
    assert item["severity_label"] == "High"
    fetched = feedback.get_item(item["id"], db_path=_db(tmp_path))
    assert fetched is not None
    assert fetched["title"] == "Kill switch button misaligns"
    assert fetched["attachments"] == []


def test_create_requires_title(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        feedback.create_item({"title": "   "}, db_path=_db(tmp_path))


def test_unknown_type_and_severity_fall_back_to_defaults(tmp_path: Path) -> None:
    item = feedback.create_item({"type": "bogus", "severity": "bogus", "title": "x"}, db_path=_db(tmp_path))
    assert item["type"] == "bug"
    assert item["severity"] == "medium"


def test_list_items_sorts_by_severity_rank(tmp_path: Path) -> None:
    db = _db(tmp_path)
    feedback.create_item({"title": "low one", "severity": "low"}, db_path=db)
    feedback.create_item({"title": "crit one", "severity": "critical"}, db_path=db)
    feedback.create_item({"title": "high one", "severity": "high"}, db_path=db)
    items = feedback.list_items(sort="severity", descending=False, db_path=db)
    assert [i["severity"] for i in items] == ["critical", "high", "low"]


def test_done_items_sink_to_the_bottom(tmp_path: Path) -> None:
    db = _db(tmp_path)
    open_item = feedback.create_item({"title": "open", "severity": "low"}, db_path=db)
    done_item = feedback.create_item({"title": "done", "severity": "critical"}, db_path=db)
    feedback.update_item(done_item["id"], {"status": "done"}, db_path=db)
    by_created = feedback.list_items(sort="created", descending=True, db_path=db)
    assert [i["title"] for i in by_created] == ["open", "done"]
    by_severity = feedback.list_items(sort="severity", descending=False, db_path=db)
    assert [i["title"] for i in by_severity] == ["open", "done"]


def test_list_items_filters_by_status_and_type(tmp_path: Path) -> None:
    db = _db(tmp_path)
    a = feedback.create_item({"title": "a", "type": "bug"}, db_path=db)
    feedback.create_item({"title": "b", "type": "improvement"}, db_path=db)
    feedback.update_item(a["id"], {"status": "done"}, db_path=db)
    done = feedback.list_items(status="done", db_path=db)
    assert [i["title"] for i in done] == ["a"]
    improvements = feedback.list_items(item_type="improvement", db_path=db)
    assert [i["title"] for i in improvements] == ["b"]


def test_update_item_changes_fields_and_status(tmp_path: Path) -> None:
    db = _db(tmp_path)
    item = feedback.create_item({"title": "before"}, db_path=db)
    updated = feedback.update_item(item["id"], {"title": "after", "status": "accepted"}, db_path=db)
    assert updated is not None
    assert updated["title"] == "after"
    assert updated["status"] == "accepted"
    assert updated["status_label"] == "Triaged · Accepted (queued)"


def test_update_item_rejects_unknown_status(tmp_path: Path) -> None:
    db = _db(tmp_path)
    item = feedback.create_item({"title": "x"}, db_path=db)
    with pytest.raises(ValueError):
        feedback.update_item(item["id"], {"status": "whatever"}, db_path=db)


def test_update_missing_item_returns_none(tmp_path: Path) -> None:
    assert feedback.update_item(9999, {"title": "x"}, db_path=_db(tmp_path)) is None


def test_delete_item(tmp_path: Path) -> None:
    db = _db(tmp_path)
    item = feedback.create_item({"title": "gone"}, db_path=db)
    assert feedback.delete_item(item["id"], db_path=db) is True
    assert feedback.get_item(item["id"], db_path=db) is None
    assert feedback.delete_item(item["id"], db_path=db) is False


def test_save_and_resolve_screenshot(tmp_path: Path) -> None:
    png = base64.b64encode(b"\x89PNG\r\n\x1a\nfakepng").decode("ascii")
    name = feedback.save_screenshot("shot.png", png, "image/png", assets_dir=tmp_path / "assets")
    assert name.endswith(".png")
    resolved = feedback.asset_path(name, assets_dir=tmp_path / "assets")
    assert resolved is not None and resolved.is_file()


def test_save_screenshot_rejects_bad_type_and_traversal(tmp_path: Path) -> None:
    png = base64.b64encode(b"data").decode("ascii")
    with pytest.raises(ValueError):
        feedback.save_screenshot("evil.exe", png, "application/x-msdownload", assets_dir=tmp_path / "assets")
    assert feedback.asset_path("../outside.png", assets_dir=tmp_path / "assets") is None


def test_to_markdown_contains_all_sections(tmp_path: Path) -> None:
    item = feedback.create_item(
        {
            "type": "bug",
            "severity": "critical",
            "title": "Dashboard 500s on /api/state",
            "description": "Server returns 500",
            "steps": "1. Start dashboard",
            "expected": "200 OK",
            "actual": "500",
            "recommended": "Catch the exception",
            "attachments": ["shot.png"],
        },
        db_path=_db(tmp_path),
    )
    md = feedback.to_markdown(item)
    assert md.startswith(f"# [Bug] #{item['id']} Dashboard 500s")
    assert "**Severity:** Critical" in md
    assert "## Description" in md
    assert "## Steps to reproduce" in md
    assert "## Expected" in md
    assert "## Actual" in md
    assert "## Recommended fix" in md
    assert "- shot.png" in md
