"""Read-only static HTML dashboard for saved run reports.

The dashboard is intentionally a renderer, not a live service.  It reads a JSON
report produced by the headless strategy/backtest and writes a single
self-contained HTML file.  It never imports MetaTrader5, never connects to a
broker, never places orders, and never mutates the scope.
"""

from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any


def _e(value: Any) -> str:
    return html.escape("" if value is None else str(value))


def _num(value: Any, digits: int = 2, default: str = "n/a") -> str:
    try:
        return f"{float(value):,.{digits}f}"
    except (TypeError, ValueError):
        return default


def _pct(value: Any, digits: int = 2) -> str:
    try:
        return f"{float(value):.{digits}f}%"
    except (TypeError, ValueError):
        return "n/a"


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _equity_svg(report: dict[str, Any]) -> str:
    trades = [trade for trade in _list(report.get("trades")) if isinstance(trade, dict)]
    initial = float(report.get("initial_equity") or 0.0)
    points = [initial]
    for trade in trades:
        try:
            points.append(points[-1] + float(trade.get("pnl") or 0.0))
        except (TypeError, ValueError):
            points.append(points[-1])
    if len(points) < 2:
        return '<p class="muted">No closed trades; no equity curve to plot.</p>'
    width, height, pad = 720, 180, 12
    low, high = min(points), max(points)
    span = high - low if high > low else 1.0
    coords: list[str] = []
    for index, value in enumerate(points):
        x = pad + (width - 2 * pad) * index / (len(points) - 1)
        y = height - pad - (height - 2 * pad) * (value - low) / span
        coords.append(f"{x:.1f},{y:.1f}")
    polyline = " ".join(coords)
    return (
        f'<svg viewBox="0 0 {width} {height}" role="img" '
        f'aria-label="Equity curve from closed trades">'
        f'<rect x="0" y="0" width="{width}" height="{height}" fill="#0f172a" rx="8"/>'
        f'<polyline fill="none" stroke="#38bdf8" stroke-width="3" points="{polyline}"/>'
        f"</svg>"
    )


def _risk_rows(risk: dict[str, Any]) -> str:
    labels = [
        ("account_model", "Account model"),
        ("leverage_cap", "Leverage cap"),
        ("max_notional_exposure_ratio", "Notional exposure cap"),
        ("per_trade_equity_pct", "Base risk per trade"),
        ("min_lot_risk_cap_pct", "Minimum-lot risk cap"),
        ("daily_loss_halt_pct", "Daily loss halt"),
        ("max_drawdown_flatten_pct", "Max drawdown flatten/halt"),
        ("contract_size_oz", "Contract size (oz/lot)"),
        ("min_lot", "Minimum lot"),
    ]
    rows = []
    for key, label in labels:
        value = risk.get(key)
        if key in {"per_trade_equity_pct", "min_lot_risk_cap_pct", "daily_loss_halt_pct", "max_drawdown_flatten_pct"}:
            rendered = _pct(value)
        elif key in {"leverage_cap", "max_notional_exposure_ratio", "contract_size_oz", "min_lot"}:
            rendered = _num(value, 2 if key == "min_lot" else 1)
        else:
            rendered = _e(value)
        rows.append(f"<tr><th>{_e(label)}</th><td>{rendered}</td></tr>")
    return "\n".join(rows)


def _trades_table(trades: list[Any]) -> str:
    if not trades:
        return '<p class="muted">No closed trades in this report.</p>'
    rows = []
    for trade in trades:
        if not isinstance(trade, dict):
            continue
        pnl = float(trade.get("pnl") or 0.0)
        css = "pos" if pnl >= 0 else "neg"
        rows.append(
            "<tr>"
            f"<td>{_e(trade.get('trade_id'))}</td>"
            f"<td>{_e(trade.get('side'))}</td>"
            f"<td>{_num(trade.get('volume'), 2)}</td>"
            f"<td>{_num(trade.get('entry_price'), 2)}</td>"
            f"<td>{_num(trade.get('exit_price'), 2)}</td>"
            f"<td class='{css}'>{_num(pnl, 2)}</td>"
            f"<td>{_e(trade.get('exit_reason'))}</td>"
            "</tr>"
        )
    return (
        "<table><thead><tr><th>ID</th><th>Side</th><th>Lots</th><th>Entry</th>"
        "<th>Exit</th><th>PnL</th><th>Exit reason</th></tr></thead><tbody>"
        + "\n".join(rows)
        + "</tbody></table>"
    )


def _decisions_table(decisions: list[Any], max_decisions: int) -> str:
    if not decisions:
        return '<p class="muted">No decisions recorded.</p>'
    rows = []
    for decision in decisions[-max_decisions:]:
        if not isinstance(decision, dict):
            continue
        fundamental = _dict(decision.get("fundamental"))
        technical = _dict(decision.get("technical"))
        overseer = _dict(decision.get("overseer"))
        reasons = "; ".join(str(item) for item in _list(decision.get("reasons")))
        rows.append(
            "<tr>"
            f"<td>{_e(decision.get('as_of_utc'))}</td>"
            f"<td><strong>{_e(decision.get('decision_type'))}</strong></td>"
            f"<td>{_e(decision.get('side'))}</td>"
            f"<td>{_num(decision.get('combined_score'), 3)}</td>"
            f"<td>{_num(fundamental.get('score'), 3)}</td>"
            f"<td>{_num(technical.get('score'), 3)}</td>"
            f"<td>{_e(overseer.get('decision'))}</td>"
            f"<td class='reasons'>{_e(reasons)}</td>"
            "</tr>"
        )
    return (
        "<table><thead><tr><th>As of (UTC)</th><th>Decision</th><th>Side</th>"
        "<th>Combined</th><th>Fundamental</th><th>Technical</th><th>Overseer</th>"
        "<th>Reasons</th></tr></thead><tbody>"
        + "\n".join(rows)
        + "</tbody></table>"
    )


def _walkforward_section(report: dict[str, Any]) -> str:
    walk_forward = _dict(report.get("walk_forward"))
    folds = [fold for fold in _list(report.get("folds")) if isinstance(fold, dict)]
    baselines = _dict(report.get("baselines"))
    if not walk_forward and not folds and not baselines:
        return ""
    parts: list[str] = ["<h2>Walk-forward / out-of-sample</h2>"]
    if walk_forward:
        parts.append(
            '<p class="muted">'
            f"mode <strong>{_e(walk_forward.get('mode'))}</strong> · "
            f"train {_num(walk_forward.get('train_bars'), 0)} bars · "
            f"test {_num(walk_forward.get('test_bars'), 0)} bars · "
            f"step {_num(walk_forward.get('step_bars'), 0)} bars"
            "</p>"
        )
    if baselines:
        rows = [
            ("Buy and hold", _pct(baselines.get("buy_and_hold_return_pct"))),
            (
                "Simple trend (fast EMA > slow EMA)",
                _pct(baselines.get("simple_trend_return_pct")),
            ),
            ("Random entry mean", _pct(baselines.get("random_entry_mean_return_pct"))),
            ("Random entry median", _pct(baselines.get("random_entry_median_return_pct"))),
            ("Random entry p05", _pct(baselines.get("random_entry_p05_return_pct"))),
            ("Random entry p95", _pct(baselines.get("random_entry_p95_return_pct"))),
        ]
        body = "\n".join(
            f"<tr><th>{_e(label)}</th><td>{value}</td></tr>" for label, value in rows
        )
        parts.append(
            "<h3>Baseline controls</h3>"
            f"<table><tbody>{body}</tbody></table>"
            f'<p class="muted">{_e(baselines.get("note"))}</p>'
        )
    if folds:
        rows = []
        for fold in folds:
            metrics = _dict(fold.get("metrics"))
            rows.append(
                "<tr>"
                f"<td>{_num(fold.get('fold_id'), 0)}</td>"
                f"<td>{_e(fold.get('test_start_utc'))}</td>"
                f"<td>{_e(fold.get('test_end_utc'))}</td>"
                f"<td>{_num(metrics.get('trade_count'), 0)}</td>"
                f"<td>{_pct(metrics.get('total_return_pct'))}</td>"
                f"<td>{_pct(metrics.get('max_drawdown_pct'))}</td>"
                "</tr>"
            )
        parts.append(
            "<h3>Folds</h3>"
            "<table><thead><tr><th>Fold</th><th>Test start (UTC)</th>"
            "<th>Test end (UTC)</th><th>Trades</th><th>Return</th>"
            "<th>Max drawdown</th></tr></thead><tbody>"
            + "\n".join(rows)
            + "</tbody></table>"
        )
    return "\n".join(parts)


def build_dashboard(
    report: dict[str, Any],
    output_path: str | Path,
    *,
    max_decisions: int = 200,
) -> Path:
    """Render a saved run report to a self-contained HTML file."""
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    metrics = _dict(report.get("metrics"))
    risk = _dict(report.get("risk_summary"))
    data_quality = _dict(report.get("data_quality"))
    trades = _list(report.get("trades"))
    decisions = _list(report.get("decisions"))
    high_risk = bool(report.get("high_risk_override", False))
    promotion = bool(report.get("promotion_eligible", True))
    profile = str(report.get("account_profile") or "base")
    profile_description = str(report.get("account_profile_description") or "")
    banner_class = "danger" if high_risk else "ok"
    banner_text = (
        "HIGH RISK OVERRIDE — not promotion-eligible. One normal stop can be a "
        "double-digit account loss. Use a micro-lot broker for a risk-preserving account."
        if high_risk
        else "Risk profile is within the standard promotion-eligible limits."
    )
    if not promotion and not high_risk:
        banner_text += " This profile is not promotion-eligible."

    cards = [
        ("Initial equity", _num(report.get("initial_equity"), 2)),
        ("Final equity", _num(report.get("final_equity"), 2)),
        ("Total return", _pct(metrics.get("total_return_pct"))),
        ("Max drawdown", _pct(metrics.get("max_drawdown_pct"))),
        ("Trades", _num(metrics.get("trade_count"), 0)),
        ("Win rate", _pct(metrics.get("win_rate_pct"))),
        ("Profit factor", _num(metrics.get("profit_factor"), 2)),
        ("Expectancy", _num(metrics.get("expectancy"), 2)),
        ("Sharpe (daily)", _num(metrics.get("sharpe_daily_annualized"), 2)),
        ("Sortino (daily)", _num(metrics.get("sortino_daily_annualized"), 2)),
        ("Max entry exposure", _pct(metrics.get("max_entry_exposure_pct"))),
    ]
    card_html = "\n".join(
        f'<div class="card"><div class="label">{_e(label)}</div>'
        f'<div class="value">{value}</div></div>'
        for label, value in cards
    )
    quality_rows = "\n".join(
        f"<tr><th>{_e(key.replace('_', ' ').title())}</th><td>{_e(value)}</td></tr>"
        for key, value in data_quality.items()
    )
    doc = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Gold Quantamental Dashboard — {_e(report.get('run_id'))}</title>
<style>
:root {{ color-scheme: dark; }}
* {{ box-sizing: border-box; }}
body {{ margin: 0; padding: 24px; background: #020617; color: #e2e8f0;
       font: 14px/1.45 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }}
h1 {{ margin: 0 0 4px; font-size: 26px; }}
h2 {{ margin: 28px 0 10px; font-size: 17px; color: #bae6fd; }}
h3 {{ margin: 18px 0 8px; font-size: 15px; color: #7dd3fc; }}
.muted {{ color: #94a3b8; }}
.banner {{ margin: 18px 0; padding: 14px 16px; border-radius: 10px; font-weight: 600; }}
.banner.danger {{ background: #450a0a; border: 1px solid #dc2626; color: #fecaca; }}
.banner.ok {{ background: #052e16; border: 1px solid #16a34a; color: #bbf7d0; }}
.meta {{ color: #94a3b8; }}
.grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
         gap: 10px; margin-top: 14px; }}
.card {{ background: #0f172a; border: 1px solid #1e293b; border-radius: 10px; padding: 12px; }}
.card .label {{ color: #94a3b8; font-size: 12px; text-transform: uppercase; letter-spacing: .04em; }}
.card .value {{ font-size: 20px; font-weight: 700; margin-top: 4px; }}
table {{ width: 100%; border-collapse: collapse; background: #0f172a; border-radius: 10px;
         overflow: hidden; }}
th, td {{ padding: 8px 10px; text-align: left; border-bottom: 1px solid #1e293b;
          vertical-align: top; }}
thead th {{ background: #111c33; color: #bae6fd; font-size: 12px; text-transform: uppercase; }}
tbody tr:last-child td, tbody tr:last-child th {{ border-bottom: none; }}
td.pos {{ color: #86efac; }} td.neg {{ color: #fca5a5; }}
td.reasons {{ color: #cbd5e1; font-size: 12px; }}
.two {{ display: grid; grid-template-columns: 1fr 1fr; gap: 18px; }}
@media (max-width: 800px) {{ .two {{ grid-template-columns: 1fr; }} }}
.footer {{ margin-top: 28px; padding-top: 14px; border-top: 1px solid #1e293b;
           color: #94a3b8; font-size: 12px; }}
.badge {{ display: inline-block; padding: 2px 8px; border-radius: 999px; font-size: 12px;
          background: #1e293b; color: #bae6fd; }}
</style>
</head>
<body>
<h1>Gold Quantamental Dashboard</h1>
<div class="meta">
  scope <strong>{_e(report.get('scope_id'))}</strong> ·
  run <strong>{_e(report.get('run_id'))}</strong> ·
  profile <span class="badge">{_e(profile)}</span> ·
  manifest <code>{_e(report.get('manifest_hash'))}</code>
</div>
<div class="banner {banner_class}">{_e(banner_text)}</div>
{('<p class="muted">' + _e(profile_description) + '</p>') if profile_description else ''}
<div class="grid">{card_html}</div>
<p class="muted">Sharpe/Sortino are annualized from daily realized trade PnL; they show n/a until
at least 20 trading days have closed trades. Exposure is entry notional / initial equity.</p>

<h2>Equity curve (closed trades)</h2>
{_equity_svg(report)}

{_walkforward_section(report)}

<div class="two">
  <section>
    <h2>Risk and account model</h2>
    <table><tbody>{_risk_rows(risk)}</tbody></table>
  </section>
  <section>
    <h2>Data quality</h2>
    <table><tbody>{quality_rows or '<tr><td class="muted">No data-quality fields.</td></tr>'}</tbody></table>
    <h2>Decision counts</h2>
    <table><tbody>
      <tr><th>Total decisions</th><td>{_num(report.get('decision_count'), 0)}</td></tr>
      <tr><th>Entry decisions</th><td>{_num(metrics.get('entry_decisions'), 0)}</td></tr>
      <tr><th>Exit decisions</th><td>{_num(metrics.get('exit_decisions'), 0)}</td></tr>
    </tbody></table>
  </section>
</div>

<h2>Trades</h2>
{_trades_table(trades)}

<h2>Decisions (last {_num(max_decisions, 0)})</h2>
{_decisions_table(decisions, max_decisions)}

<div class="footer">
  Read-only dashboard. Generated from a saved JSON run report. It does not connect to MT5,
  does not place orders, and cannot change the scope. Synthetic fixtures are not profitability
  evidence; promotion requires the demo/OOS gates in AGENTS.md.
</div>
</body>
</html>
"""
    path.write_text(doc, encoding="utf-8")
    return path


def build_dashboard_from_file(
    report_path: str | Path,
    output_path: str | Path,
    *,
    max_decisions: int = 200,
) -> Path:
    """Load a saved JSON report and render it."""
    path = Path(report_path)
    if not path.exists():
        raise FileNotFoundError(f"run report not found: {path}")
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid run report JSON: {path}: {exc}") from exc
    if not isinstance(report, dict):
        raise ValueError(f"run report must be a JSON object: {path}")
    return build_dashboard(report, output_path, max_decisions=max_decisions)
