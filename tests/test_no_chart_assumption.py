"""Guard test: the bot must never require any chart to be open or focused.

The market data feed goes through the MT5 Python API. No code path may call
a chart-related MT5 API (chart_open/chart_select/...) or pin execution to a
specific chart object. This keeps the bot headless and chart-independent.
"""

import ast
from pathlib import Path

FORBIDDEN = {
    "chart_open",
    "chart_close",
    "chart_select",
    "chart_navigate",
    "chart_window_handle",
    "chart_first_visible_bar",
    "chart_navigate_down",
    "chart_navigate_up",
    "chart_navigate_first",
    "chart_navigate_last",
    "chart_scale",
    "chart_properties",
    "chart_indicators",
    "chart_objects",
    "chart_screenshot",
    "chart_set_subset",
    "chart_set_visible_range",
    "chart_find",
    "ChartSetSymbolPeriod",
}


def _forbidden_calls_in(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8").lstrip("\ufeff"))
    hits = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in FORBIDDEN:
            hits.append((path, node.attr, node.lineno))
        elif isinstance(node, ast.Name) and node.id in FORBIDDEN:
            hits.append((path, node.id, node.lineno))
    return hits


def test_no_chart_api_is_called_anywhere():
    root = Path(__file__).resolve().parents[1] / "goldbot"
    offenders = []
    for path in sorted(root.glob("*.py")):
        offenders.extend(_forbidden_calls_in(path))
    assert not offenders, "forbidden chart API call(s): " + "; ".join(
        f"{p.name}:{lineno} {name}" for p, name, lineno in offenders
    )
