"""Command-line entry point for the goldbot validator and M2 strategy core."""

from __future__ import annotations

import argparse
import copy
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Sequence

from .contracts import utc_now
from .backtest import BacktestRunner
from .dashboard import build_dashboard, build_dashboard_from_file
from .data import (
    BarSeries,
    MT5DataFeed,
    aggregate_bars,
    load_macro_directory,
    make_synthetic_bars,
    make_synthetic_macro_store,
)
from .agent import run_agent_loop
from .demo import run_demo_order, run_demo_preflight
from .ledger import Ledger
from .live_dashboard import serve_live_dashboard
from .manifest import load_manifest, manifest_hash
from .paper import PaperRuntime, replay_times_from_bars
from .profiles import load_manifest_with_profile
from .validator import validate_manifest, write_json_report, write_markdown_report
from .walkforward import WalkForwardRunner


def _load_manifest_for_run(args: argparse.Namespace) -> dict:
    manifest = load_manifest_with_profile(
        args.manifest,
        getattr(args, "account_profile", None),
    )
    if manifest.get("profile_high_risk_override"):
        print(
            "WARNING: account profile "
            f"{manifest.get('active_account_profile')} is a HIGH RISK OVERRIDE; "
            "it is not promotion-eligible.",
            file=sys.stderr,
        )
    return manifest


def _cmd_validate(args: argparse.Namespace) -> int:
    report = validate_manifest(
        args.manifest,
        offline=args.offline,
        no_mt5=args.no_mt5,
        timeout=args.timeout,
        project_root=args.project_root,
    )
    if args.report:
        write_json_report(report, args.report)
    if args.markdown:
        write_markdown_report(report, args.markdown)
    if args.json:
        print(json.dumps(report.to_dict(), indent=2, ensure_ascii=False, default=str))
    else:
        print(f"scope: {report.scope_id}")
        print(f"manifest: {report.manifest_path}")
        print(f"manifest_hash: {report.manifest_hash}")
        print(f"overall: {report.overall_status}")
        print(f"m1: {report.m1_status}")
        print(f"runtime: {report.runtime_status}")
        print(
            "counts: "
            + ", ".join(f"{key}={value}" for key, value in report.counts.items())
        )
        if report.required_failures:
            print("required failures:")
            for result in report.required_failures:
                print(f"  - {result.item_id}: {result.detail}")
        if report.runtime_blockers:
            print("runtime blockers:")
            for result in report.runtime_blockers:
                print(f"  - {result.item_id}: {result.detail}")
        if report.human_judgment_items:
            print("human judgment:")
            for result in report.human_judgment_items:
                print(f"  - {result.item_id}: {result.detail}")
    if report.overall_status == "FAIL":
        return 1
    if report.overall_status == "PARTIAL":
        return 1 if args.strict else 2
    return 0


def _cmd_hash_manifest(args: argparse.Namespace) -> int:
    manifest = load_manifest(args.manifest)
    digest = manifest_hash(manifest)
    print(digest)
    if args.write_calibration:
        path = Path(args.write_calibration)
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                data = {}
        else:
            data = {}
        data.setdefault("artifact_version", 1)
        data["scope_id"] = manifest.get("scope_id")
        data["manifest_hash"] = digest
        data.setdefault("status", "not_fitted")
        data.setdefault("fit_window", None)
        data.setdefault("metrics", {})
        data.setdefault(
            "notes",
            "Updated by goldbot hash-manifest. No overseer parameters are fitted yet.",
        )
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"wrote calibration artifact: {path}")
    return 0


def _build_run_inputs(args: argparse.Namespace):
    manifest = _load_manifest_for_run(args)
    macro_store = None
    if args.macro_dir:
        macro_store = load_macro_directory(args.macro_dir)
    if args.source == "synthetic":
        start = datetime(2026, 1, 5, tzinfo=timezone.utc)
        m15 = make_synthetic_bars(
            start_utc=start,
            count=args.bars,
            drift_per_bar=args.drift,
            amplitude=args.amplitude,
        )
        h4 = aggregate_bars(m15, "H4", 4)
        as_of = m15[-1].time_utc + timedelta(minutes=15)
        if macro_store is None:
            macro_store = make_synthetic_macro_store(as_of_utc=as_of, days=args.macro_days)
        return manifest, BarSeries(m15, "M15"), BarSeries(h4, "H4"), macro_store

    feed = MT5DataFeed(symbol=str(manifest.get("market", {}).get("symbol", "XAUUSD")))
    try:
        account = feed.connect()
        m15_bars, h4_bars = feed.fetch_both(
            execution_count=args.bars,
            context_count=args.context_bars,
        )
        if not m15_bars:
            raise RuntimeError("MT5 returned no M15 bars")
        as_of = m15_bars[-1].time_utc + timedelta(minutes=15)
        if macro_store is None:
            macro_store = make_synthetic_macro_store(as_of_utc=as_of, days=args.macro_days)
        if account:
            print(
                f"mt5 account: {account.get('login')} {account.get('server')} "
                f"equity={account.get('equity')}",
                file=sys.stderr,
            )
        return manifest, BarSeries(m15_bars, "M15"), BarSeries(h4_bars, "H4"), macro_store
    finally:
        feed.close()


def _cmd_run(args: argparse.Namespace) -> int:
    try:
        manifest, execution, context, macro_store = _build_run_inputs(args)
        runner = BacktestRunner(
            manifest=manifest,
            execution_bars=execution,
            context_bars=context,
            macro_store=macro_store,
            initial_equity=args.equity,
        )
        result = runner.run()
    except Exception as exc:  # noqa: BLE001 - CLI must report, not hide, the failure
        print(f"run failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    result_payload = result.to_dict()
    if args.report:
        path = Path(args.report)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(result_payload, indent=2, ensure_ascii=False, default=str) + "\n",
            encoding="utf-8",
        )
        print(f"wrote run report: {path}")
    if args.dashboard:
        dashboard_path = build_dashboard(result_payload, args.dashboard)
        print(f"wrote read-only dashboard: {dashboard_path}")
    if args.json:
        print(
            json.dumps(
                {
                    "run_id": result.run_id,
                    "scope_id": result.scope_id,
                    "manifest_hash": result.manifest_hash,
                    "account_profile": result.account_profile,
                    "high_risk_override": result.high_risk_override,
                    "promotion_eligible": result.promotion_eligible,
                    "metrics": result.metrics,
                    "trades": [trade.to_dict() for trade in result.trades],
                },
                indent=2,
                ensure_ascii=False,
                default=str,
            )
        )
        return 0
    print(f"scope: {result.scope_id}")
    print(f"run_id: {result.run_id}")
    print(f"manifest_hash: {result.manifest_hash}")
    print(
        f"account_profile: {result.account_profile} "
        f"(high_risk={result.high_risk_override}, "
        f"promotion_eligible={result.promotion_eligible})"
    )
    print(f"bars: M15={len(execution)} H4={len(context)}")
    print(f"initial_equity: {result.initial_equity:.2f}")
    print(f"final_equity: {result.final_equity:.2f}")
    print(f"metrics: {json.dumps(result.metrics, ensure_ascii=False)}")
    for trade in result.trades:
        print(
            f"trade {trade.trade_id}: {trade.side} {trade.volume:.2f} "
            f"{trade.entry_price:.2f} -> {trade.exit_price:.2f} "
            f"pnl={trade.pnl:.2f} reason={trade.exit_reason}"
        )
    if args.show_decisions > 0:
        for decision in result.decisions[-args.show_decisions :]:
            print(
                f"decision {decision.as_of_utc.isoformat()} {decision.decision_type} "
                f"score={decision.combined_score:+.3f} reasons={decision.reasons}"
            )
    return 0


def _cmd_paper(args: argparse.Namespace) -> int:
    """Run the paper/dry-run execution path and persist everything to SQLite."""
    try:
        manifest, execution, context, macro_store = _build_run_inputs(args)
        with Ledger(args.ledger) as ledger:
            runtime = PaperRuntime(
                manifest=manifest,
                ledger=ledger,
                execution_bars=execution,
                context_bars=context,
                macro_store=macro_store,
                initial_equity=args.equity,
            )
            times = replay_times_from_bars(
                execution,
                replay_bars=args.replay_bars,
            )
            if not times:
                raise RuntimeError("no replay decision times were generated")
            result = runtime.run_replay(times)
    except Exception as exc:  # noqa: BLE001 - CLI must report, not hide, the failure
        print(f"paper failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    payload = result.to_dict()
    if args.report:
        path = Path(args.report)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False, default=str) + "\n",
            encoding="utf-8",
        )
        print(f"wrote paper report: {path}")
    if args.dashboard:
        dashboard_path = build_dashboard(payload, args.dashboard)
        print(f"wrote read-only paper dashboard: {dashboard_path}")
    if args.json:
        print(
            json.dumps(
                {
                    "run_id": result.run_id,
                    "mode": result.mode,
                    "scope_id": result.scope_id,
                    "manifest_hash": result.manifest_hash,
                    "account_profile": result.account_profile,
                    "high_risk_override": result.high_risk_override,
                    "promotion_eligible": result.promotion_eligible,
                    "ledger_path": result.ledger_path,
                    "decision_count": len(result.decisions),
                    "order_count": len(result.orders),
                    "fill_count": len(result.fills),
                    "trade_count": len(result.trades),
                    "final_equity": result.final_equity,
                    "metrics": result.metrics,
                    "trades": [trade.to_dict() for trade in result.trades],
                },
                indent=2,
                ensure_ascii=False,
                default=str,
            )
        )
        return 0

    print("mode: paper_replay (no broker orders were sent)")
    print(f"scope: {result.scope_id}")
    print(f"run_id: {result.run_id}")
    print(f"manifest_hash: {result.manifest_hash}")
    print(
        f"account_profile: {result.account_profile} "
        f"(high_risk={result.high_risk_override}, "
        f"promotion_eligible={result.promotion_eligible})"
    )
    print(f"ledger: {result.ledger_path}")
    print(f"bars: M15={len(execution)} H4={len(context)}")
    print(f"replay_decisions: {len(result.decisions)}")
    print(f"orders: {len(result.orders)} fills: {len(result.fills)}")
    print(f"initial_equity: {result.initial_equity:.2f}")
    print(f"final_equity: {result.final_equity:.2f}")
    print(f"metrics: {json.dumps(result.metrics, ensure_ascii=False)}")
    for trade in result.trades:
        print(
            f"paper trade {trade.trade_id}: {trade.side} {trade.volume:.2f} "
            f"{trade.entry_price:.2f} -> {trade.exit_price:.2f} "
            f"pnl={trade.pnl:.2f} reason={trade.exit_reason}"
        )
    if args.show_decisions > 0:
        for decision in result.decisions[-args.show_decisions :]:
            print(
                f"decision {decision.as_of_utc.isoformat()} {decision.decision_type} "
                f"score={decision.combined_score:+.3f} reasons={decision.reasons}"
            )
    return 0


def _cmd_demo_preflight(args: argparse.Namespace) -> int:
    """Read-only demo-account preflight; never calls order_send."""
    try:
        manifest = _load_manifest_for_run(args)
        result = run_demo_preflight(
            manifest=manifest,
            macro_dir=args.macro_dir,
            ledger_path=args.ledger,
            bars=args.bars,
            context_bars=args.context_bars,
            broker_utc_offset_minutes=args.broker_utc_offset_minutes,
            deviation_points=args.deviation_points,
            magic=args.magic,
            expected_login=args.expected_login,
            broker_offset_confirmed=args.confirm_broker_offset,
        )
    except Exception as exc:  # noqa: BLE001 - CLI must report, not hide, the failure
        print(f"demo preflight failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    payload = result.to_dict()
    if args.report:
        path = Path(args.report)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False, default=str) + "\n",
            encoding="utf-8",
        )
        print(f"wrote demo preflight report: {path}")
    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False, default=str))
        return 0 if result.status != "FAIL" else 2

    print("mode: demo_preflight (no broker orders were sent)")
    print(f"status: {result.status}")
    print(
        f"account: {result.account.get('login')} {result.account.get('server')} "
        f"equity={result.account.get('equity')} "
        f"trade_mode={result.account.get('trade_mode')}"
    )
    print(
        "terminal: "
        f"connected={result.terminal.get('connected')} "
        f"trade_allowed={result.terminal.get('trade_allowed')} "
        f"tradeapi_disabled={result.terminal.get('tradeapi_disabled')}"
    )
    print(
        f"symbol: {result.symbol.get('name')} "
        f"trade_mode={result.symbol.get('trade_mode')} "
        f"volume_min={result.symbol.get('volume_min')} "
        f"filling_mode={result.symbol.get('filling_mode')}"
    )
    if result.decision is not None:
        print(
            f"decision: {result.decision.decision_type} "
            f"as_of={result.decision.as_of_utc.isoformat()} "
            f"score={result.decision.combined_score:+.3f}"
        )
        print(f"reasons: {result.decision.reasons}")
    for item in result.items:
        print(f"[{item.status}] {item.name}: {item.detail}")
    if result.order_request is not None:
        print(f"order_request: {json.dumps(result.order_request, ensure_ascii=False)}")
    if result.order_check is not None:
        print(
            "order_check: "
            f"retcode={result.order_check.get('retcode')!r} "
            f"comment={result.order_check.get('comment')!r} "
            f"margin={result.order_check.get('margin')!r}"
        )
    print(f"ledger: {result.ledger_path}")
    return 0 if result.status != "FAIL" else 2


def _cmd_demo_send(args: argparse.Namespace) -> int:
    """Guarded one-shot demo order. Refuses unless every explicit guard is present."""
    if not args.send:
        print("refusing to send: --send is required", file=sys.stderr)
        return 2
    if args.confirm_order != "SEND DEMO ORDER":
        print(
            "refusing to send: --confirm-order must be exactly 'SEND DEMO ORDER'",
            file=sys.stderr,
        )
        return 2
    if not args.confirm_broker_offset:
        print(
            "refusing to send: --confirm-broker-offset is required after human calibration",
            file=sys.stderr,
        )
        return 2
    if args.expected_login is None:
        print("refusing to send: --expected-login is required", file=sys.stderr)
        return 2
    try:
        manifest = _load_manifest_for_run(args)
        result = run_demo_order(
            manifest=manifest,
            macro_dir=args.macro_dir,
            ledger_path=args.ledger,
            expected_login=args.expected_login,
            broker_utc_offset_minutes=args.broker_utc_offset_minutes,
            broker_offset_confirmed=args.confirm_broker_offset,
            send=args.send,
            confirm_phrase=args.confirm_order,
            bars=args.bars,
            context_bars=args.context_bars,
            override_blocks=args.override_blocks,
            deviation_points=args.deviation_points,
            magic=args.magic,
        )
    except Exception as exc:  # noqa: BLE001 - CLI must report, not hide, the failure
        print(f"demo order failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    payload = result.to_dict()
    if args.report:
        path = Path(args.report)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False, default=str) + "\n",
            encoding="utf-8",
        )
        print(f"wrote demo order report: {path}")
    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False, default=str))
        return 0 if (result.sent or not result.preflight.send_ready) else 2

    print("mode: demo_order")
    print(f"status: {result.status}")
    print(f"preflight_status: {result.preflight.status}")
    print(f"sent: {result.sent}")
    print(
        f"account: {result.preflight.account.get('login')} "
        f"{result.preflight.account.get('server')} "
        f"equity={result.preflight.account.get('equity')}"
    )
    if result.preflight.decision is not None:
        print(
            f"decision: {result.preflight.decision.decision_type} "
            f"as_of={result.preflight.decision.as_of_utc.isoformat()} "
            f"score={result.preflight.decision.combined_score:+.3f}"
        )
    for item in result.preflight.items:
        print(f"[{item.status}] {item.name}: {item.detail}")
    if result.preflight.order_request is not None:
        print(
            "order_request: "
            f"{json.dumps(result.preflight.order_request, ensure_ascii=False)}"
        )
    if result.send_result is not None:
        print(
            "send_result: "
            f"retcode={result.send_result.get('retcode')!r} "
            f"order={result.send_result.get('order')!r} "
            f"deal={result.send_result.get('deal')!r} "
            f"price={result.send_result.get('price')!r} "
            f"volume={result.send_result.get('volume')!r} "
            f"comment={result.send_result.get('comment')!r}"
        )
    print(f"ledger: {result.ledger_path}")
    return 0 if (result.sent or not result.preflight.send_ready) else 2


def _cmd_agent(args: argparse.Namespace) -> int:
    """Run the guarded agent loop. Never sends orders."""
    try:
        manifest = _load_manifest_for_run(args)
        return run_agent_loop(
            manifest=manifest,
            macro_dir=args.macro_dir,
            ledger_path=args.ledger,
            broker_utc_offset_minutes=args.broker_utc_offset_minutes,
            bars=args.bars,
            context_bars=args.context_bars,
            interval_seconds=args.interval_seconds,
            expected_login=args.expected_login,
            stop_file=args.stop_file,
            max_iterations=args.max_iterations,
        )
    except Exception as exc:  # noqa: BLE001 - CLI must report, not crash
        print(f"agent failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


def _cmd_decline(args: argparse.Namespace) -> int:
    """Log a human decline decision to the ledger."""
    try:
        with Ledger(args.ledger) as ledger:
            ledger.log_event(
                level="INFO",
                component="launcher",
                event_type="decline_opportunity",
                message=args.reason,
                payload={"reason": args.reason},
            )
    except Exception as exc:  # noqa: BLE001
        print(f"decline failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    print("decline logged")
    return 0


def _cmd_close_position(args: argparse.Namespace) -> int:
    """Close one MT5 position by ticket and record the outcome in the ledger."""
    from .data import MT5DataFeed
    feed = MT5DataFeed(symbol=args.symbol, broker_utc_offset_minutes=args.broker_utc_offset_minutes)
    try:
        feed.connect()
        result = feed.close_position(
            ticket=int(args.ticket),
            symbol=args.symbol,
            comment=args.reason[:24],
        )
    except Exception as exc:  # noqa: BLE001 - report the failure, don't crash
        print(f"close failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    retcode = int(result.get("retcode", -1))
    print(f"close_result: retcode={retcode} comment={result.get('comment')!r} order={result.get('order')!r} deal={result.get('deal')!r} price={result.get('price')!r}")

    exit_price = float(result.get("price") or 0.0)
    if exit_price <= 0:
        exit_price = 0.0

    try:
        with Ledger(args.ledger) as ledger:
            row = ledger.connection.execute(
                """
                SELECT id, position_id, side, volume, open_price, stop_loss, take_profit
                FROM positions
                WHERE broker_position_ticket = ? OR position_id = ?
                LIMIT 1
                """,
                (int(args.ticket), f"broker-{int(args.ticket)}"),
            ).fetchone()
            if row is None:
                open_price = exit_price
                pnl = 0.0
                ledger.connection.execute(
                    """
                    INSERT INTO positions
                        (position_id, symbol, side, volume, open_price, open_time_utc,
                         stop_loss, take_profit, close_price, close_time_utc, close_reason,
                         realized_pnl, broker_position_ticket, state, raw_position_json)
                    VALUES (?, ?, 'LONG', 0.0, ?, ?, NULL, NULL, ?, ?, ?, 0.0, ?, 'DEMO_CLOSED', '{}')
                    """,
                    (
                        f"broker-{int(args.ticket)}",
                        args.symbol,
                        open_price,
                        utc_now().isoformat(),
                        exit_price,
                        utc_now().isoformat(),
                        args.reason,
                        int(args.ticket),
                    ),
                )
            else:
                side = str(row["side"]).upper()
                direction = 1 if side == "LONG" else -1
                pnl = (exit_price - float(row["open_price"])) * float(row["volume"]) * 100.0 * direction
                ledger.connection.execute(
                    """
                    UPDATE positions
                    SET close_price = ?, close_time_utc = ?, close_reason = ?,
                        realized_pnl = ?, state = 'DEMO_CLOSED'
                    WHERE id = ?
                    """,
                    (exit_price, utc_now().isoformat(), args.reason, pnl, int(row["id"])),
                )
            ledger.connection.commit()
            print(f"ledger: closed-position recorded; realized_pnl={pnl:.2f}")
    except Exception as exc:  # noqa: BLE001 - close already happened; log but don't fail
        print(f"ledger update failed: {type(exc).__name__}: {exc}", file=sys.stderr)

    return 0 if retcode in {10008, 10009} else 2


def _cmd_walkforward(args: argparse.Namespace) -> int:
    try:
        manifest, execution, context, macro_store = _build_run_inputs(args)
        runner = WalkForwardRunner(
            manifest=manifest,
            execution_bars=execution,
            context_bars=context,
            macro_store=macro_store,
            initial_equity=args.equity,
            train_bars=args.train_bars,
            test_bars=args.test_bars,
            step_bars=args.step_bars,
            mode=args.mode,
            random_runs=args.random_runs,
            baseline_fast=args.baseline_fast,
            baseline_slow=args.baseline_slow,
        )
        result = runner.run()
    except Exception as exc:  # noqa: BLE001 - CLI must report, not hide, the failure
        print(f"walkforward failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    result_payload = result.to_dict()
    if args.report:
        path = Path(args.report)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(result_payload, indent=2, ensure_ascii=False, default=str) + "\n",
            encoding="utf-8",
        )
        print(f"wrote walk-forward report: {path}")
    if args.dashboard:
        dashboard_path = build_dashboard(result_payload, args.dashboard)
        print(f"wrote read-only dashboard: {dashboard_path}")
    if args.json:
        print(
            json.dumps(
                {
                    "run_id": result.run_id,
                    "scope_id": result.scope_id,
                    "manifest_hash": result.manifest_hash,
                    "account_profile": result.account_profile,
                    "high_risk_override": result.high_risk_override,
                    "promotion_eligible": result.promotion_eligible,
                    "walk_forward": result.config.to_dict(),
                    "metrics": result.metrics,
                    "baselines": result.baselines,
                    "folds": [fold.to_dict() for fold in result.folds],
                },
                indent=2,
                ensure_ascii=False,
                default=str,
            )
        )
        return 0
    print(f"scope: {result.scope_id}")
    print(f"run_id: {result.run_id}")
    print(f"manifest_hash: {result.manifest_hash}")
    print(
        f"account_profile: {result.account_profile} "
        f"(high_risk={result.high_risk_override}, "
        f"promotion_eligible={result.promotion_eligible})"
    )
    print(f"walk_forward: {json.dumps(result.config.to_dict(), ensure_ascii=False)}")
    print(f"bars: M15={len(execution)} H4={len(context)}")
    print(f"initial_equity: {result.initial_equity:.2f}")
    print(f"final_equity: {result.final_equity:.2f}")
    print(f"metrics: {json.dumps(result.metrics, ensure_ascii=False)}")
    print(f"baselines: {json.dumps(result.baselines, ensure_ascii=False)}")
    if args.show_folds:
        for fold in result.folds:
            metrics = fold.metrics
            print(
                f"fold {fold.fold.fold_id}: "
                f"test={fold.fold.test_start_utc.isoformat()}..{fold.fold.test_end_utc.isoformat()} "
                f"trades={metrics.get('trade_count')} "
                f"return={metrics.get('total_return_pct')}% "
                f"drawdown={metrics.get('max_drawdown_pct')}%"
            )
    return 0


def _cmd_reconnect_drill(args: argparse.Namespace) -> int:
    """Simulate an MT5 disconnect/reconnect; record evidence in the ledger."""
    from .drill import run_reconnect_drill

    try:
        result = run_reconnect_drill(
            ledger_path=args.ledger,
            symbol=args.symbol,
            broker_utc_offset_minutes=args.broker_utc_offset_minutes,
        )
    except Exception as exc:  # noqa: BLE001 - surface, don't crash
        print(f"reconnect-drill failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    if args.json_output:
        print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False, default=str))
    else:
        print(f"reconnect drill verdict={result.verdict} incident={result.incident_id}")
        for check in result.checks:
            print(f"  [{check.status:7s}] {check.name}: {check.detail}")
    return 0 if result.ok else 1


def _cmd_calendar(args: argparse.Namespace) -> int:
    """Print the Tier-1 macro calendar and current blackout status."""
    from .econ_calendar import all_events, blackout_at, upcoming
    from .contracts import utc_now

    now = utc_now()
    blocked, event, minutes_until = blackout_at(now)

    if args.json_output:
        payload = {
            "now_utc": now.isoformat(),
            "blackout": blocked,
            "next_or_active": event.to_dict() if event else None,
            "minutes_until_end" if blocked else "minutes_until_blackout": minutes_until,
            "events": [e.to_dict() for e in upcoming(now, args.horizon_days)],
        }
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        print(f"now: {now.isoformat()}")
        if blocked and event is not None:
            print(f"BLACKOUT ACTIVE: {event.label} (until {event.blackout_end_utc.isoformat()})")
        else:
            print(f"no blackout active; next event in {minutes_until:.0f} minutes" if minutes_until else "no upcoming events")
        print(f"upcoming ({args.horizon_days}d):")
        for e in upcoming(now, args.horizon_days):
            print(f"  {e.blackout_start_utc.isoformat()}  [{e.category:>3s}]  {e.label}")
    return 0


def _cmd_news_stress(args: argparse.Namespace) -> int:
    """Replay a long entry after each major event; report stop-hits and gaps."""
    from .news_stress import run_news_stress

    try:
        report = run_news_stress(
            source=args.source,
            bars=args.bars,
            broker_utc_offset_minutes=args.broker_utc_offset_minutes,
        )
    except Exception as exc:  # noqa: BLE001 - surface, don't crash
        print(f"news-stress failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    if not report.get("ok", True):
        print(f"news-stress failed: {report.get('error')}", file=sys.stderr)
        return 2

    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")
        print(f"news-stress report written: {out_path}")
    if args.json_output:
        print(json.dumps(report, indent=2, ensure_ascii=False, default=str))
    else:
        print(f"news-stress: source={report['source']} events={report['summary']['events']} stops hit={report['summary']['stop_hits']} rate={report['summary']['stop_hit_rate_pct']}%")
        for case in report["cases"][:20]:
            print(f"  {case['label']:55s} entry={case['entry']} stop={case['stop']} hit={case['stop_hit']} gap%={case['gap_pct']}")
        if len(report["cases"]) > 20:
            print(f"  ... and {len(report['cases']) - 20} more (see {args.output or '--json-output'})")
    return 0 if report.get("source") == "mt5" else 1


def _cmd_explain(args: argparse.Namespace) -> int:
    """Reconstruct a decision (or the latest) from the ledger."""
    from .traceability import reconstruct, render_markdown

    report = reconstruct(
        ledger_path=args.ledger,
        decision_id=args.decision_id,
        latest=args.latest,
    )
    if not report.get("found"):
        print(f"explain: {report.get('error', 'not found')}", file=sys.stderr)
        return 1

    markdown = render_markdown(report)
    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(markdown, encoding="utf-8")
        print(f"explain written: {out_path}")
    if args.json_output:
        print(json.dumps(report, indent=2, ensure_ascii=False, default=str))
    else:
        print(markdown)
    return 0


def _cmd_spread_sample(args: argparse.Namespace) -> int:
    """Sample bid/ask for N seconds and append to reports/spread_history.csv."""
    from .spread import CSV_PATH, append_samples, sample_once
    from .data import MT5DataFeed
    import time

    feed = MT5DataFeed(
        symbol=args.symbol, broker_utc_offset_minutes=args.broker_utc_offset_minutes
    )
    try:
        feed.connect()
        samples = []
        deadline = time.monotonic() + args.seconds
        while time.monotonic() < deadline:
            sample = sample_once(feed)
            if sample is not None:
                samples.append(sample)
            time.sleep(args.interval_seconds)
    except Exception as exc:  # noqa: BLE001 - surface, don't crash
        print(f"spread-sample failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    finally:
        try:
            feed.close()
        except Exception:  # noqa: BLE001 - best effort
            pass

    if not samples:
        print("spread-sample: no ticks captured (market may be closed)", file=sys.stderr)
        return 1
    count = append_samples(samples, Path(args.csv))
    print(f"spread-sample: appended {count} samples to {args.csv}")
    return 0


def _cmd_spread_report(args: argparse.Namespace) -> int:
    """Print spread percentiles overall and per hour-of-day."""
    from .spread import report

    payload = report(Path(args.csv))
    if args.json_output:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        print(f"spread-report: samples={payload['samples']}")
        overall = payload.get("overall", {})
        if overall.get("n"):
            print(
                f"  overall: min={overall['min']:.1f} p50={overall['p50']:.1f} "
                f"p90={overall['p90']:.1f} p99={overall['p99']:.1f} max={overall['max']:.1f} points"
            )
            for row in payload.get("fill_probability_buckets", {}).get("rows", []):
                print(f"    {row['bucket']:>6s}: {row['count']:4d} samples ({row['pct_of_samples']}%)")
            for hour, stats in payload.get("by_hour_utc", {}).items():
                if stats.get("n"):
                    print(f"  hour {hour:>2}: n={stats['n']} p50={stats['p50']:.1f} p99={stats['p99']:.1f} max={stats['max']:.1f}")
        else:
            print("  no samples yet; run `goldbot spread-sample` first")
    return 0


def _cmd_tick_backtest(args: argparse.Namespace) -> int:
    """Measure bar-open spread and adverse trough on recent ticks; no orders."""
    from .tick_backtest import run_tick_backtest

    try:
        report = run_tick_backtest(
            bars=args.bars,
            broker_utc_offset_minutes=args.broker_utc_offset_minutes,
        )
    except Exception as exc:  # noqa: BLE001 - surface, don't crash
        print(f"tick-backtest failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            json.dumps(report, indent=2, ensure_ascii=False, default=str) + "\n",
            encoding="utf-8",
        )
        print(f"tick-backtest written: {out_path}")
    if args.json_output:
        print(json.dumps(report, indent=2, ensure_ascii=False, default=str))
    else:
        if not report.get("ok"):
            print(f"tick-backtest failed: {report.get('error')}", file=sys.stderr)
            return 2
        summary = report["summary"]
        print(
            f"tick-backtest: bars={summary['bars_measured']}/{summary['bars_available']} "
            f"spread@open p50={summary['spread_at_open'].get('p50')} p99={summary['spread_at_open'].get('p99')} "
            f"over_limit={summary['bars_over_limit']} ({summary['bars_over_limit_pct']}%) "
            f"max_trough={summary['max_trough_points']} pts"
        )
    return 0


def _cmd_exec_quality(args: argparse.Namespace) -> int:
    """One-screen execution-quality summary for the current account/ledger."""
    from .econ_calendar import blackout_at, upcoming
    from .contracts import utc_now

    now = utc_now()
    blocked, event, minutes_until = blackout_at(now)
    payload: dict[str, Any] = {
        "now_utc": now.isoformat(),
        "blackout": blocked,
        "active_event": event.to_dict() if blocked and event else None,
        "minutes_until_blackout": minutes_until if not blocked else None,
        "minutes_until_blackout_end": minutes_until if blocked else None,
        "upcoming": [e.to_dict() for e in upcoming(now, 14)],
    }
    if Path("reports/news_stress.json").exists():
        try:
            stress = json.loads(Path("reports/news_stress.json").read_text(encoding="utf-8"))
            payload["news_stress"] = stress.get("summary")
        except Exception:  # noqa: BLE001 - best effort
            payload["news_stress"] = None
    if Path("reports/spread_history.csv").exists():
        from .spread import report as spread_report

        payload["spread_history"] = spread_report(Path("reports/spread_history.csv"))
    if Path("reports/tick_backtest.json").exists():
        try:
            tick = json.loads(Path("reports/tick_backtest.json").read_text(encoding="utf-8"))
            payload["tick_backtest"] = {
                "generated_at_utc": tick.get("generated_at_utc"),
                "window": tick.get("window"),
                "summary": tick.get("summary"),
            }
        except Exception:  # noqa: BLE001 - best effort
            payload["tick_backtest"] = None
    if args.json_output:
        print(json.dumps(payload, indent=2, ensure_ascii=False, default=str))
    else:
        if blocked and event:
            print(f"BLACKOUT ACTIVE: {event.label} — entries blocked until {event.blackout_end_utc.isoformat()}")
        else:
            print(f"entries allowed; next event in {minutes_until:.0f} minutes" if minutes_until else "no upcoming events")
        if payload.get("news_stress"):
            s = payload["news_stress"]
            print(f"news stress: {s['stop_hits']}/{s['events']} stops hit ({s['stop_hit_rate_pct']}%)")
        if payload.get("spread_history"):
            sp = payload["spread_history"]
            print(f"spread samples: {sp['samples']} overall p50={sp['overall'].get('p50')} p99={sp['overall'].get('p99')}")
        if payload.get("tick_backtest"):
            tb = payload["tick_backtest"]
            s = tb.get("summary", {})
            print(f"tick backtest window: {tb.get('window', {}).get('from')} -> bars={s.get('bars_measured')}")
    return 0


def _cmd_sell_review(args: argparse.Namespace) -> int:
    """Print the SELL-side dossier from the ledger: exits, reasons, vetoes."""
    from .sell_review import run_sell_review

    report = run_sell_review(ledger_path=args.ledger)
    if not report.get("ok"):
        print(f"sell-review failed: {report.get('error')}", file=sys.stderr)
        return 2

    if args.json_output:
        print(json.dumps(report, indent=2, ensure_ascii=False, default=str))
    else:
        print(f"sell-review: exits={report['summary']['exits']} fills={report['exit_fill_count']}")
        for exit_row in report["exits"][:20]:
            print(f"  {exit_row['as_of_utc']} reason={exit_row['exit_reason']} priority={exit_row['exit_priority']}")
        if report["summary"]["exits"] == 0:
            print("  (no exits recorded yet — nothing to review)")
    return 0


def _cmd_surprise(args: argparse.Namespace) -> int:
    """Report owner-maintained economic surprises (consensus vs actual)."""
    from .surprise import report

    payload = report(Path(args.csv))
    if not payload["csv_exists"]:
        print(
            f"surprise: no data file at {payload['csv_path']}\n"
            "create it with columns: release_date,event,forecast,actual\n"
            "(owner maintains this file monthly; there is no free ToS-clean consensus feed)"
        )
        return 0
    if args.json_output:
        print(json.dumps(payload, indent=2, ensure_ascii=False, default=str))
    else:
        print(f"surprise: {payload['count']} observations")
        summary = payload["summary"]
        if summary.get("last"):
            last = summary["last"]
            print(
                f"  last: {last['release_date']} {last['event']} "
                f"forecast={last['forecast']} actual={last['actual']} "
                f"surprise={last['surprise']} ({last['surprise_pct']}%)"
            )
    return 0


def _cmd_risk_corr(args: argparse.Namespace) -> int:
    """Gold vs USD trailing correlation on H4 returns (read-only)."""
    from .risk_corr import run_risk_corr

    try:
        report = run_risk_corr(
            bars=args.bars,
            broker_utc_offset_minutes=args.broker_utc_offset_minutes,
        )
    except Exception as exc:  # noqa: BLE001 - surface, don't crash
        print(f"risk-corr failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    if args.json_output:
        print(json.dumps(report, indent=2, ensure_ascii=False, default=str))
    else:
        status = report.get("status")
        if status == "unavailable":
            print(f"risk-corr: unavailable — {report.get('note')}")
        elif status == "error":
            print(f"risk-corr: error — {report.get('error')}", file=sys.stderr)
        else:
            print(f"risk-corr: symbol={report.get('symbol')} bars={report.get('bars')} rho={report.get('rho')}")
            if report.get("warning"):
                print(f"  FLAG: {report['warning']}")
    return 0 if report.get("ok", True) else 1


def _cmd_fill_quality(args: argparse.Namespace) -> int:
    """Summarize fill quality vs MT5 deal history (read-only)."""
    from .fill_quality import run_fill_quality

    report = run_fill_quality(ledger_path=args.ledger)
    if not report.get("ok"):
        print(f"fill-quality failed: {report.get('error')}", file=sys.stderr)
        return 2

    if args.json_output:
        print(json.dumps(report, indent=2, ensure_ascii=False, default=str))
    else:
        summary = report["summary"]
        slip = summary["slippage_points"]
        print(
            f"fill-quality: fills={summary['fills']} with_deal={summary['fills_with_deal_ticket']} "
            f"broker_deals={summary['broker_deals_matched_prefix']}"
        )
        print(
            f"  slippage_points n={slip['n']} min={slip['min']} mean={slip['mean']} max={slip['max']}"
        )
        if slip["n"] == 0:
            print("  (no slippage recorded; older fills predate the slippage tracker)")
    return 0


def _cmd_health(args: argparse.Namespace) -> int:
    """Read-only health check: halt flag, MT5 probe, ledger activity age."""
    from .health import run_health

    try:
        report = run_health(ledger_path=args.ledger, skip_mt5=args.skip_mt5)
    except Exception as exc:  # noqa: BLE001 - surface, don't crash
        print(f"health failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    if args.json_output:
        print(json.dumps(report.to_dict(), indent=2, ensure_ascii=False, default=str))
    else:
        print(f"health: {report.verdict}")
        print(f"  halt_flag={report.halt_flag} ({report.halt_flag_path})")
        print(f"  ledger_exists={report.ledger_exists}")
        if report.last_activity_utc:
            print(f"  last_activity={report.last_activity_utc.isoformat()} age={report.last_activity_age_seconds:.0f}s")
        else:
            print("  last_activity=None (ledger has no runs/snapshots logged)")
        print(f"  mt5={report.mt5.get('status')}")
        if report.mt5.get("status") == "up":
            print(f"  account login={report.mt5.get('login')} trade_allowed={report.mt5.get('trade_allowed')}")
        elif report.mt5.get("error"):
            print(f"  mt5 error={report.mt5.get('error')}")
    return 0 if report.ok else 1


def _cmd_backup(args: argparse.Namespace) -> int:
    """Timestamped, integrity-checked backup of the execution ledger."""
    from .backup import run_backup

    result = run_backup(
        ledger_path=args.ledger,
        out_dir=args.out_dir,
        keep=args.keep,
    )
    if args.json_output:
        print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False, default=str))
    else:
        if result.ok:
            print(f"backup ok: {result.destination} ({result.size_bytes} bytes; integrity={result.integrity})")
            print(f"  backups kept: {result.kept}")
        else:
            print(f"backup FAILED: {result.detail}", file=sys.stderr)
    return 0 if result.ok else 1


def _cmd_export_parquet(args: argparse.Namespace) -> int:
    """Export ledger tables to Parquet under reports/parquet/."""
    from .parquet_export import run_export

    try:
        result = run_export(ledger_path=args.ledger, out_dir=args.out_dir)
    except Exception as exc:  # noqa: BLE001 - surface, don't crash
        print(f"export-parquet failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    if args.json_output:
        print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False, default=str))
    else:
        if not result.ok:
            print(f"export-parquet FAILED: {result.detail}", file=sys.stderr)
        else:
            print(f"export-parquet ok: {result.out_dir}")
            for table, count in sorted(result.written.items()):
                print(f"  {table}: {count} rows")
    return 0 if result.ok else 1


def _cmd_refresh_macro(args: argparse.Namespace) -> int:
    """Download FRED macro inputs via the free public endpoint; report staleness."""
    from .refresh import refresh_macro, stale_limits
    from .manifest import load_manifest

    try:
        result = refresh_macro(manifest_path=args.manifest, macro_dir=args.macro_dir)
        manifest = load_manifest(args.manifest)
        limits = stale_limits(manifest)
    except Exception as exc:  # noqa: BLE001 - surface the blocker, don't crash
        print(f"refresh-macro failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    if args.ledger:
        try:
            with Ledger(args.ledger) as ledger:
                ledger.log_event(
                    level="INFO" if result.ok else "WARNING",
                    component="refresh_macro",
                    event_type="refresh_macro",
                    message=("macro refresh OK" if result.ok else "macro refresh FAILED"),
                    correlation_id=None,
                    payload=result.to_dict(),
                    logged_at_utc=result.generated_at_utc,
                )
        except Exception as exc:  # noqa: BLE001 - never fail a download over logging
            print(f"warning: could not log refresh_macro to ledger: {exc}", file=sys.stderr)

    payload = json.dumps(result.to_dict(), indent=2, ensure_ascii=False, default=str)
    if args.json_output:
        print(payload)
    else:
        print(f"macro refresh {'OK' if result.ok else 'FAILED'} -> {result.macro_dir}")
        for series in result.series:
            age = series.get("age_days")
            age_text = f"{age}d old" if age is not None else "unknown age"
            print(
                f"  {series['series_id']:9s} {series['status']:6s} "
                f"latest={series.get('latest_date')} ({age_text}) "
                f"rows={series.get('rows')} changed={series.get('changed')} {series.get('errors') or ''}"
            )
        print(f"  enforced stale_after_days (manifest, unchanged): {limits}")
        print("  NOTE: this command never widens staleness limits and never edits the manifest.")
    return 0 if result.ok else 1


def _cmd_wgc_check(args: argparse.Namespace) -> int:
    """Reconfirm the monthly WGC/IMF XLSX (mechanical checks only; records evidence)."""
    from .wgc import run_wgc_check

    try:
        report = run_wgc_check(wgc_dir=args.wgc_dir, ledger_path=args.ledger)
    except Exception as exc:  # noqa: BLE001 - surface, don't crash
        print(f"wgc-check failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    if args.json_output:
        print(json.dumps(report, indent=2, ensure_ascii=False, default=str))
    else:
        print(f"WGC reconfirm: {report['status']}")
        print(f"  file: {report.get('file')}")
        if report.get("inspection"):
            insp = report["inspection"]
            print(f"  rows={insp.get('rows')} sheets={insp.get('sheets')} sha256={(insp.get('sha256') or '')[:16]}")
        if report.get("review_file"):
            review_insp = report.get("review_inspection") or {}
            print(
                f"  review file: {report['review_file']} "
                f"(sha256={(review_insp.get('sha256') or 'missing')[:16] if review_insp else 'missing'}) "
                f"tallies={report.get('tallies_with_review')}"
            )
        if report.get("age_days") is not None:
            print(f"  age: {report['age_days']} days")
        if report.get("ledger_warning"):
            print(f"  warning: {report['ledger_warning']}")
        print(f"  {report.get('detail')}")
    return 0 if report["status"] == "pass" else 1


def _cmd_reconcile(args: argparse.Namespace) -> int:
    """Compare the ledger against the MT5 broker. Read-only; never sends orders."""
    from .reconcile import run_reconcile

    try:
        report = run_reconcile(
            ledger_path=args.ledger,
            symbol=args.symbol,
            broker_utc_offset_minutes=args.broker_utc_offset_minutes,
        )
    except Exception as exc:  # noqa: BLE001 - surface the blocker, don't crash
        print(f"reconcile failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    payload = json.dumps(report.to_dict(), indent=2, ensure_ascii=False, default=str)
    if args.json_output:
        print(payload)
    else:
        print(
            f"verdict={report.verdict} ledger_open={report.ledger_open_count} mt5_open={report.mt5_open_count}"
        )
        if report.mt5_error:
            print(f"mt5_error={report.mt5_error}")
        for ticket in report.ledger_only:
            print(f"ledger_only ticket={ticket}")
        for ticket in report.mt5_only:
            print(f"mt5_only ticket={ticket}")
        for item in report.mismatches:
            print(f"mismatch ticket={item['ticket']}: {'; '.join(item['problems'])}")
        print(
            f"deals={report.deals_verdict} ledger_fills={report.ledger_fill_count} "
            f"mt5_deals={report.mt5_deal_count} matched={len(report.deals_matched)}"
        )
        if report.deals_error:
            print(f"deals_error={report.deals_error}")
        for ticket in report.deals_ledger_only:
            print(f"deals_ledger_only ticket={ticket}")
        for ticket in report.deals_broker_only:
            print(f"deals_broker_only ticket={ticket}")
        for item in report.deal_mismatches:
            print(f"deal_mismatch ticket={item['ticket']}: {'; '.join(item['problems'])}")
        if report.ok:
            print("ledger and broker agree on open positions and fills")
    return 0 if report.ok else 1


def _cmd_kill_switch(args: argparse.Namespace) -> int:
    """Flatten every XAUUSD position and halt the bot. Demo-only, phrase-guarded."""
    from .killswitch import CONFIRMATION_PHRASE, run_kill_switch, run_kill_switch_drill

    try:
        if args.drill:
            result = run_kill_switch_drill(
                ledger_path=args.ledger,
                expected_login=args.expected_login,
                confirm_flatten=args.confirm_flatten,
                symbol=args.symbol,
                broker_utc_offset_minutes=args.broker_utc_offset_minutes,
            )
        else:
            result = run_kill_switch(
                ledger_path=args.ledger,
                expected_login=args.expected_login,
                confirm_flatten=args.confirm_flatten,
                symbol=args.symbol,
                broker_utc_offset_minutes=args.broker_utc_offset_minutes,
            )
    except Exception as exc:  # noqa: BLE001 - guards must surface loudly
        print(f"kill switch refused/failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    payload = json.dumps(result.to_dict(), indent=2, ensure_ascii=False, default=str)
    if args.json_output:
        print(payload)
    else:
        mode = "kill-switch drill" if args.drill else "kill switch"
        print(
            f"{mode}: positions_before={result.positions_before} "
            f"closed={len(result.closed)} failed={len(result.failed)} "
            f"halted={result.halted} incident={result.incident_id}"
        )
        print(f"halt flag: {result.halt_file}")
        if args.drill:
            print("drill mode: NO orders were sent and the halt flag was NOT armed")
        if result.failed:
            print("WARNING: some positions failed to close; investigate MT5 immediately")
    return 0 if result.ok else 1


def _cmd_broker_specs(args: argparse.Namespace) -> int:
    """Capture FBS-Demo XAUUSD symbol spec and write reports/broker_specs.json."""
    try:
        feed = MT5DataFeed(symbol=args.symbol, broker_utc_offset_minutes=args.broker_utc_offset_minutes)
        feed.connect()
        try:
            info = feed._mt5.symbol_info(args.symbol)
        finally:
            feed.close()
    except Exception as exc:  # noqa: BLE001 - CLI must report, not crash
        print(f"broker-specs failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    spec = info._asdict()
    allowed = [
        "name", "description", "exchange", "category", "path",
        "digits", "point", "currency_base", "currency_margin", "currency_profit",
        "trade_tick_size", "trade_tick_value", "trade_tick_value_profit", "trade_tick_value_loss",
        "trade_contract_size",
        "volume_min", "volume_step", "volume_limit", "volume_max",
        "trade_stops_level", "trade_freeze_level",
        "swap_long", "swap_short", "swap_mode", "filling_mode", "trade_mode",
        "spread", "spread_float", "margin_hedged", "margin_initial", "margin_maintenance",
        "expiration_time",
    ]
    specs = {k: spec.get(k) for k in allowed}
    specs["_captured_at_utc"] = utc_now().isoformat()
    specs["_source"] = "MT5 symbol_info()"

    report_path = Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(specs, indent=2, ensure_ascii=False, default=str) + "\n",
        encoding="utf-8",
    )
    print(f"wrote broker specs: {report_path}")
    print(json.dumps(specs, indent=2, ensure_ascii=False, default=str))
    return 0


def _cmd_demo_param(args: argparse.Namespace) -> int:
    try:
        manifest = _load_manifest_for_run(args)
        start = datetime(2026, 1, 5, tzinfo=timezone.utc)
        m15 = make_synthetic_bars(
            start_utc=start,
            count=args.bars,
            drift_per_bar=args.drift,
            amplitude=args.amplitude,
        )
        h4 = aggregate_bars(m15, "H4", 4)
        execution = BarSeries(m15, "M15")
        context = BarSeries(h4, "H4")
        as_of = m15[-1].time_utc + timedelta(minutes=15)
        macro_store = make_synthetic_macro_store(as_of_utc=as_of, days=args.macro_days)
        baseline = BacktestRunner(
            manifest=manifest,
            execution_bars=execution,
            context_bars=context,
            macro_store=macro_store,
            initial_equity=args.equity,
        ).run()
        changed_manifest = copy.deepcopy(manifest)
        changed_manifest.setdefault("strategy", {}).setdefault("fusion", {})["entry_threshold"] = args.changed_threshold
        changed = BacktestRunner(
            manifest=changed_manifest,
            execution_bars=execution,
            context_bars=context,
            macro_store=macro_store,
            initial_equity=args.equity,
        ).run()
    except Exception as exc:  # noqa: BLE001
        print(f"demo failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    baseline_entries = int(baseline.metrics["entry_decisions"])
    changed_entries = int(changed.metrics["entry_decisions"])
    print(f"account_profile: {manifest.get('active_account_profile', 'base')}")
    print(f"baseline entry_threshold={manifest['strategy']['fusion']['entry_threshold']}")
    print(f"  entries={baseline_entries} trades={baseline.metrics['trade_count']} "
          f"final_equity={baseline.final_equity:.2f}")
    print(f"changed entry_threshold={args.changed_threshold}")
    print(f"  entries={changed_entries} trades={changed.metrics['trade_count']} "
          f"final_equity={changed.final_equity:.2f}")
    if baseline_entries == changed_entries:
        print(
            "WARNING: the manifest change did not alter entry behavior on this fixture; "
            "choose a different parameter or fixture.",
            file=sys.stderr,
        )
        return 1
    print("manifest parameter change altered behavior without a code edit")
    return 0


def _cmd_dashboard(args: argparse.Namespace) -> int:
    try:
        output = build_dashboard_from_file(
            args.report,
            args.output,
            max_decisions=args.max_decisions,
        )
    except Exception as exc:  # noqa: BLE001 - CLI must report, not hide, the failure
        print(f"dashboard failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    print(f"wrote read-only dashboard: {output}")
    return 0


def _cmd_dashboard_live(args: argparse.Namespace) -> int:
    try:
        serve_live_dashboard(
            ledger_path=args.ledger,
            host=args.host,
            port=args.port,
            refresh_seconds=args.refresh_seconds,
            max_rows=args.max_rows,
            profile=args.profile,
            macro_dir=args.macro_dir,
            manifest_rel=args.manifest_rel,
            broker_utc_offset_minutes=args.broker_utc_offset_minutes,
            expected_login=args.expected_login,
            interval_seconds=args.interval_seconds,
            bars=args.bars,
            context_bars=args.context_bars,
            open_browser=args.open,
        )
    except Exception as exc:  # noqa: BLE001 - CLI must report, not hide, the failure
        print(f"dashboard-live failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="goldbot", description="Gold quantamental bot tools")
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate = subparsers.add_parser("validate", help="validate a scope manifest")
    validate.add_argument("--manifest", required=True, help="path to scope YAML")
    validate.add_argument("--report", help="write JSON report to this path")
    validate.add_argument("--markdown", help="write Markdown report to this path")
    validate.add_argument("--offline", action="store_true", help="skip network checks")
    validate.add_argument("--no-mt5", action="store_true", help="skip MT5 preflight")
    validate.add_argument("--timeout", type=float, default=30.0, help="HTTP timeout in seconds")
    validate.add_argument("--project-root", help="project root for relative file checks")
    validate.add_argument("--strict", action="store_true", help="treat runtime blockers as failures")
    validate.add_argument("--json", action="store_true", help="print the full JSON report")
    validate.set_defaults(func=_cmd_validate)

    hash_cmd = subparsers.add_parser("hash-manifest", help="print or write the manifest hash")
    hash_cmd.add_argument("--manifest", required=True, help="path to scope YAML")
    hash_cmd.add_argument(
        "--write-calibration",
        help="update/create a calibration JSON artifact with the current manifest hash",
    )
    hash_cmd.set_defaults(func=_cmd_hash_manifest)

    run = subparsers.add_parser("run", help="run the headless strategy core")
    run.add_argument("--manifest", required=True, help="path to scope YAML")
    run.add_argument(
        "--account-profile",
        help="explicit account-profile YAML overlay, e.g. config/profiles/micro_100.yaml",
    )
    run.add_argument("--source", choices=["synthetic", "mt5"], default="synthetic")
    run.add_argument("--macro-dir", help="local macro CSV/XLSX directory; synthetic if omitted")
    run.add_argument("--bars", type=int, default=1200, help="M15 bars to load")
    run.add_argument("--context-bars", type=int, default=500, help="H4 bars to load from MT5")
    run.add_argument("--macro-days", type=int, default=520, help="synthetic macro days")
    run.add_argument("--equity", type=float, default=10000.0, help="backtest starting equity")
    run.add_argument("--drift", type=float, default=0.12, help="synthetic M15 drift per bar")
    run.add_argument("--amplitude", type=float, default=6.0, help="synthetic M15 wave amplitude")
    run.add_argument("--report", help="write full JSON run report")
    run.add_argument("--dashboard", help="write a read-only HTML dashboard from the run")
    run.add_argument("--show-decisions", type=int, default=10)
    run.add_argument("--json", action="store_true", help="print summary JSON")
    run.set_defaults(func=_cmd_run)

    paper = subparsers.add_parser(
        "paper",
        help="replay closed bars through the strategy and persist paper executions",
    )
    paper.add_argument("--manifest", required=True, help="path to scope YAML")
    paper.add_argument(
        "--account-profile",
        help="explicit account-profile YAML overlay, e.g. config/profiles/micro_100.yaml",
    )
    paper.add_argument("--source", choices=["synthetic", "mt5"], default="synthetic")
    paper.add_argument("--macro-dir", help="local macro CSV/XLSX directory; synthetic if omitted")
    paper.add_argument("--bars", type=int, default=1600, help="M15 bars to load")
    paper.add_argument("--context-bars", type=int, default=500, help="H4 bars to load from MT5")
    paper.add_argument("--macro-days", type=int, default=520, help="synthetic macro days")
    paper.add_argument("--equity", type=float, default=10000.0, help="paper starting equity")
    paper.add_argument("--drift", type=float, default=0.12, help="synthetic M15 drift per bar")
    paper.add_argument("--amplitude", type=float, default=6.0, help="synthetic M15 wave amplitude")
    paper.add_argument(
        "--replay-bars",
        type=int,
        default=0,
        help="replay the last N closed bars; 0 = only the latest closed bar",
    )
    paper.add_argument(
        "--ledger",
        default="reports/goldbot.ledger.sqlite",
        help="SQLite ledger path (single writer)",
    )
    paper.add_argument("--report", help="write full JSON paper report")
    paper.add_argument("--dashboard", help="write a read-only HTML dashboard from the paper run")
    paper.add_argument("--show-decisions", type=int, default=10)
    paper.add_argument("--json", action="store_true", help="print summary JSON")
    paper.set_defaults(func=_cmd_paper)

    specs = subparsers.add_parser(
        "broker-specs",
        help="capture FBS-Demo XAUUSD symbol spec from MT5",
    )
    specs.add_argument("--symbol", required=True, help="symbol to capture, e.g. XAUUSD")
    specs.add_argument(
        "--broker-utc-offset-minutes",
        type=int,
        default=120,
        help="broker server offset from UTC",
    )
    specs.add_argument(
        "--report",
        default="reports/broker_specs.json",
        help="write JSON report to this path",
    )
    specs.set_defaults(func=_cmd_broker_specs)

    wf = subparsers.add_parser(
        "walkforward",
        help="run non-overlapping walk-forward / out-of-sample folds",
    )
    wf.add_argument("--manifest", required=True, help="path to scope YAML")
    wf.add_argument(
        "--account-profile",
        help="explicit account-profile YAML overlay, e.g. config/profiles/micro_100.yaml",
    )
    wf.add_argument("--source", choices=["synthetic", "mt5"], default="synthetic")
    wf.add_argument("--macro-dir", help="local macro CSV/XLSX directory; synthetic if omitted")
    wf.add_argument("--bars", type=int, default=1600, help="M15 bars to load")
    wf.add_argument("--context-bars", type=int, default=500, help="H4 bars to load from MT5")
    wf.add_argument("--macro-days", type=int, default=520, help="synthetic macro days")
    wf.add_argument("--equity", type=float, default=10000.0, help="backtest starting equity")
    wf.add_argument("--drift", type=float, default=0.12, help="synthetic M15 drift per bar")
    wf.add_argument("--amplitude", type=float, default=6.0, help="synthetic M15 wave amplitude")
    wf.add_argument("--train-bars", type=int, default=600, help="bars before each test window")
    wf.add_argument("--test-bars", type=int, default=200, help="bars in each test window")
    wf.add_argument(
        "--step-bars",
        type=int,
        default=None,
        help="bars to advance between folds; defaults to test-bars",
    )
    wf.add_argument(
        "--mode",
        choices=["rolling", "expanding"],
        default="rolling",
        help="rolling keeps a fixed train size; expanding keeps all history",
    )
    wf.add_argument("--random-runs", type=int, default=200, help="random-entry control runs")
    wf.add_argument("--baseline-fast", type=int, default=50, help="simple-trend fast EMA")
    wf.add_argument("--baseline-slow", type=int, default=200, help="simple-trend slow EMA")
    wf.add_argument("--report", help="write full JSON walk-forward report")
    wf.add_argument("--dashboard", help="write a read-only HTML dashboard from the run")
    wf.add_argument("--show-folds", action="store_true", help="print one line per fold")
    wf.add_argument("--json", action="store_true", help="print summary JSON")
    wf.set_defaults(func=_cmd_walkforward)

    preflight = subparsers.add_parser(
        "demo-preflight",
        help="read-only demo-account preflight; never submits an order",
    )
    preflight.add_argument("--manifest", required=True, help="path to scope YAML")
    preflight.add_argument(
        "--account-profile",
        help="explicit account-profile YAML overlay, e.g. config/profiles/standard_1k.yaml",
    )
    preflight.add_argument(
        "--macro-dir",
        required=True,
        help="local macro CSV/XLSX directory (real data required; synthetic is refused)",
    )
    preflight.add_argument(
        "--ledger",
        default="reports/demo.preflight.ledger.sqlite",
        help="SQLite ledger for the preflight decision",
    )
    preflight.add_argument("--bars", type=int, default=1600, help="M15 bars to load")
    preflight.add_argument(
        "--context-bars", type=int, default=500, help="H4 bars to load from MT5"
    )
    preflight.add_argument(
        "--broker-utc-offset-minutes",
        type=int,
        default=0,
        help="broker server offset from UTC; calibration is a human item",
    )
    preflight.add_argument(
        "--confirm-broker-offset",
        action="store_true",
        help="confirm the broker UTC offset is human-calibrated",
    )
    preflight.add_argument(
        "--deviation-points",
        type=int,
        default=20,
        help="max slippage/deviation for the would-be market order",
    )
    preflight.add_argument(
        "--magic", type=int, default=20261114, help="MT5 magic number for the bot"
    )
    preflight.add_argument(
        "--expected-login",
        type=int,
        help="refuse if the connected MT5 account login differs",
    )
    preflight.add_argument("--report", help="write full JSON preflight report")
    preflight.add_argument("--json", action="store_true", help="print summary JSON")
    preflight.set_defaults(func=_cmd_demo_preflight)

    order = subparsers.add_parser(
        "demo-send",
        help="guarded one-shot demo order; requires explicit send and confirmation",
    )
    order.add_argument("--manifest", required=True, help="path to scope YAML")
    order.add_argument(
        "--account-profile",
        help="explicit account-profile YAML overlay, e.g. config/profiles/standard_1k.yaml",
    )
    order.add_argument(
        "--macro-dir",
        required=True,
        help="local macro CSV/XLSX directory (real data required; synthetic is refused)",
    )
    order.add_argument(
        "--ledger",
        default="reports/demo.execution.ledger.sqlite",
        help="SQLite ledger for the demo decision and broker order",
    )
    order.add_argument("--bars", type=int, default=1600, help="M15 bars to load")
    order.add_argument(
        "--context-bars", type=int, default=500, help="H4 bars to load from MT5"
    )
    order.add_argument(
        "--broker-utc-offset-minutes",
        type=int,
        required=True,
        help="human-calibrated broker server offset from UTC",
    )
    order.add_argument(
        "--confirm-broker-offset",
        action="store_true",
        help="confirm the broker UTC offset is human-calibrated",
    )
    order.add_argument(
        "--deviation-points",
        type=int,
        default=20,
        help="max slippage/deviation for the market order",
    )
    order.add_argument(
        "--magic", type=int, default=20261114, help="MT5 magic number for the bot"
    )
    order.add_argument(
        "--expected-login",
        type=int,
        required=True,
        help="required demo account login",
    )
    order.add_argument(
        "--send",
        action="store_true",
        help="required explicit send switch; without it the command refuses",
    )
    order.add_argument(
        "--confirm-order",
        help="required confirmation phrase: SEND DEMO ORDER",
    )
    order.add_argument(
        "--override-blocks",
        action="store_true",
        help="TESTING ONLY: bypass macro/fusion gates and place a bare 0.01-lot demo order",
    )
    order.add_argument("--report", help="write full JSON demo-order report")
    order.add_argument("--json", action="store_true", help="print summary JSON")
    order.set_defaults(func=_cmd_demo_send)

    agent_cmd = subparsers.add_parser(
        "agent",
        help="run the guarded agent loop (connect MT5, evaluate strategy, write ledger)",
    )
    agent_cmd.add_argument("--manifest", required=True, help="path to scope YAML")
    agent_cmd.add_argument(
        "--account-profile",
        help="explicit account-profile YAML overlay, e.g. config/profiles/standard_1k.yaml",
    )
    agent_cmd.add_argument(
        "--macro-dir",
        required=True,
        help="local macro CSV/XLSX directory (real data required)",
    )
    agent_cmd.add_argument("--ledger", default="reports/agent.ledger.sqlite", help="SQLite ledger for the agent")
    agent_cmd.add_argument("--broker-utc-offset-minutes", type=int, default=120, help="broker server offset from UTC")
    agent_cmd.add_argument("--bars", type=int, default=1600, help="M15 bars to load")
    agent_cmd.add_argument("--context-bars", type=int, default=500, help="H4 bars to load")
    agent_cmd.add_argument("--interval-seconds", type=float, default=15.0, help="seconds between agent evaluations")
    agent_cmd.add_argument("--expected-login", type=int, required=True, help="required demo account login")
    agent_cmd.add_argument("--stop-file", help="agent exits when this file exists")
    agent_cmd.add_argument("--max-iterations", type=int, help="stop after N iterations (testing)")
    agent_cmd.set_defaults(func=_cmd_agent)

    decline = subparsers.add_parser(
        "decline",
        help="log a human decision to decline the current opportunity",
    )
    decline.add_argument("--ledger", required=True, help="SQLite ledger path")
    decline.add_argument("--reason", required=True, help="reason for declining")
    decline.set_defaults(func=_cmd_decline)

    close_pos = subparsers.add_parser(
        "close-position",
        help="MT5 close one open position by ticket; records the close in the ledger",
    )
    close_pos.add_argument("--ticket", required=True, type=int, help="MT5 position ticket to close")
    close_pos.add_argument("--symbol", default="XAUUSD", help="symbol of the position")
    close_pos.add_argument("--reason", default="operator request", help="human reason to record")
    close_pos.add_argument(
        "--broker-utc-offset-minutes",
        type=int,
        default=120,
        help="broker server offset from UTC (human-confirmed)",
    )
    close_pos.add_argument(
        "--ledger",
        required=True,
        help="SQLite ledger path to update",
    )
    close_pos.add_argument("--report", help="write full JSON report")
    close_pos.add_argument("--json", action="store_true", help="print summary JSON")
    close_pos.set_defaults(func=_cmd_close_position)

    calendar_cmd = subparsers.add_parser(
        "calendar",
        help="print Tier-1 macro calendar with blackout status (FOMC/CPI/NFP/PCE/auctions)",
    )
    calendar_cmd.add_argument("--horizon-days", type=int, default=14)
    calendar_cmd.add_argument("--json-output", action="store_true")
    calendar_cmd.set_defaults(func=_cmd_calendar)

    stress = subparsers.add_parser(
        "news-stress",
        help="replay a long after each major event; report stop-hits and gaps (read-only)",
    )
    stress.add_argument("--source", choices=["mt5", "synthetic"], default="mt5")
    stress.add_argument("--bars", type=int, default=1600)
    stress.add_argument("--broker-utc-offset-minutes", type=int, default=120)
    stress.add_argument("--output", default=None, help="write full JSON report here")
    stress.add_argument("--json-output", action="store_true")
    stress.set_defaults(func=_cmd_news_stress)

    explain = subparsers.add_parser(
        "explain",
        help="reconstruct a decision (from the ledger) for human review; never re-runs the strategy",
    )
    explain.add_argument("--ledger", default="reports/demo.execution.ledger.sqlite")
    explain.add_argument("--decision-id", default=None, help="exact id or unique prefix")
    explain.add_argument("--latest", action="store_true", help="use the most recent decision")
    explain.add_argument("--output", default=None, help="write Markdown report here")
    explain.add_argument("--json-output", action="store_true")
    explain.set_defaults(func=_cmd_explain)

    sample = subparsers.add_parser(
        "spread-sample",
        help="sample bid/ask for N seconds into reports/spread_history.csv (append)",
    )
    sample.add_argument("--symbol", default="XAUUSD")
    sample.add_argument("--seconds", type=int, default=60)
    sample.add_argument("--interval-seconds", type=float, default=1.0)
    sample.add_argument("--csv", default="reports/spread_history.csv")
    sample.add_argument("--broker-utc-offset-minutes", type=int, default=120)
    sample.set_defaults(func=_cmd_spread_sample)

    spread_report = subparsers.add_parser(
        "spread-report",
        help="print spread percentiles overall and per hour-of-day (UTC)",
    )
    spread_report.add_argument("--csv", default="reports/spread_history.csv")
    spread_report.add_argument("--json-output", action="store_true")
    spread_report.set_defaults(func=_cmd_spread_report)

    tick_cmd = subparsers.add_parser(
        "tick-backtest",
        help="measure bar-open spread and adverse trough on recent ticks; no orders (read-only)",
    )
    tick_cmd.add_argument("--bars", type=int, default=400)
    tick_cmd.add_argument("--broker-utc-offset-minutes", type=int, default=120)
    tick_cmd.add_argument("--output", default=None)
    tick_cmd.add_argument("--json-output", action="store_true")
    tick_cmd.set_defaults(func=_cmd_tick_backtest)

    sr = subparsers.add_parser(
        "sell-review",
        help="SELL-side dossier from the ledger: exits, reasons, vetoes, fills",
    )
    sr.add_argument("--ledger", default="reports/demo.execution.ledger.sqlite")
    sr.add_argument("--json-output", action="store_true")
    sr.set_defaults(func=_cmd_sell_review)

    eq = subparsers.add_parser(
        "exec-quality",
        help="one-screen execution-quality summary: blackout, news-stress, spread, tick check",
    )
    eq.add_argument("--json-output", action="store_true")
    eq.set_defaults(func=_cmd_exec_quality)

    surp = subparsers.add_parser(
        "surprise",
        help="report owner-maintained consensus-vs-actual surprises (data/manual/econ/surprises.csv)",
    )
    surp.add_argument("--csv", default="data/manual/econ/surprises.csv")
    surp.add_argument("--json-output", action="store_true")
    surp.set_defaults(func=_cmd_surprise)

    rcorr = subparsers.add_parser(
        "risk-corr",
        help="gold vs USD trailing correlation on H4 returns (read-only)",
    )
    rcorr.add_argument("--bars", type=int, default=400)
    rcorr.add_argument("--broker-utc-offset-minutes", type=int, default=120)
    rcorr.add_argument("--json-output", action="store_true")
    rcorr.set_defaults(func=_cmd_risk_corr)

    fq = subparsers.add_parser(
        "fill-quality",
        help="slippage/RTT and MT5-deal cross-check from the ledger (read-only)",
    )
    fq.add_argument("--ledger", default="reports/demo.execution.ledger.sqlite")
    fq.add_argument("--json-output", action="store_true")
    fq.set_defaults(func=_cmd_fill_quality)

    health = subparsers.add_parser(
        "health",
        help="read-only health probe: halt flag, ledger activity age, MT5 connection (no orders placed)",
    )
    health.add_argument("--ledger", default="reports/demo.execution.ledger.sqlite")
    health.add_argument("--skip-mt5", action="store_true", help="only ledger/halt checks; do not probe MT5")
    health.add_argument("--json-output", action="store_true")
    health.set_defaults(func=_cmd_health)

    backup = subparsers.add_parser(
        "backup",
        help="timestamped, integrity-checked backup of the execution ledger",
    )
    backup.add_argument("--ledger", default="reports/demo.execution.ledger.sqlite")
    backup.add_argument("--out-dir", default="reports/backups")
    backup.add_argument("--keep", type=int, default=30, help="keep this many newest backups (default 30; 0 keeps all)")
    backup.add_argument("--json-output", action="store_true")
    backup.set_defaults(func=_cmd_backup)

    parquet_cmd = subparsers.add_parser(
        "export-parquet",
        help="export ledger tables to Parquet under reports/parquet/ (read-only)",
    )
    parquet_cmd.add_argument("--ledger", default="reports/demo.execution.ledger.sqlite")
    parquet_cmd.add_argument("--out-dir", default="reports/parquet")
    parquet_cmd.add_argument("--json-output", action="store_true")
    parquet_cmd.set_defaults(func=_cmd_export_parquet)

    refresh_macro_cmd = subparsers.add_parser(
        "refresh-macro",
        help="re-download FRED macro CSVs (free public endpoint) and report staleness; never edits the manifest",
    )
    refresh_macro_cmd.add_argument("--manifest", default="config/scope.gold.v1.yaml")
    refresh_macro_cmd.add_argument("--macro-dir", default="data/raw/macro")
    refresh_macro_cmd.add_argument("--json-output", action="store_true")
    refresh_macro_cmd.add_argument(
        "--ledger",
        default=None,
        help="optional execution ledger; logs refresh_macro as evidence (best-effort, non-fatal)",
    )
    refresh_macro_cmd.set_defaults(func=_cmd_refresh_macro)

    recon_drill = subparsers.add_parser(
        "reconnect-drill",
        help="simulate MT5 disconnect/reconnect; writes drill evidence to the ledger",
    )
    recon_drill.add_argument("--ledger", required=True, help="execution ledger SQLite path")
    recon_drill.add_argument("--symbol", default="XAUUSD")
    recon_drill.add_argument("--broker-utc-offset-minutes", type=int, default=120)
    recon_drill.add_argument("--json-output", action="store_true")
    recon_drill.set_defaults(func=_cmd_reconnect_drill)

    wgc_check = subparsers.add_parser(
        "wgc-check",
        help="reconfirm the manual WGC/IMF XLSX (mechanical checks; logs evidence to the ledger)",
    )
    wgc_check.add_argument("--wgc-dir", default="data/manual/wgc")
    wgc_check.add_argument("--ledger", default=None, help="execution ledger for evidence logs (best-effort)")
    wgc_check.add_argument("--json-output", action="store_true")
    wgc_check.set_defaults(func=_cmd_wgc_check)

    recon = subparsers.add_parser(
        "reconcile",
        help="read-only compare of ledger vs MT5 open positions; exits non-zero on mismatch",
    )
    recon.add_argument("--ledger", required=True, help="execution ledger SQLite path")
    recon.add_argument("--symbol", default="XAUUSD")
    recon.add_argument("--broker-utc-offset-minutes", type=int, default=120)
    recon.add_argument("--json-output", action="store_true", help="print full JSON report")
    recon.set_defaults(func=_cmd_reconcile)

    kill = subparsers.add_parser(
        "kill-switch",
        help="EMERGENCY: flatten all XAUUSD positions and halt the bot (demo only, phrase-guarded)",
    )
    kill.add_argument("--ledger", required=True, help="execution ledger SQLite path")
    kill.add_argument("--symbol", default="XAUUSD")
    kill.add_argument("--broker-utc-offset-minutes", type=int, default=120)
    kill.add_argument("--expected-login", required=True, type=int, help="demo account login; must match connected account")
    kill.add_argument(
        "--confirm-flatten",
        required=True,
        help="must be exactly 'FLATTEN AND HALT'",
    )
    kill.add_argument("--json-output", action="store_true", help="print full JSON report")
    kill.add_argument(
        "--drill",
        action="store_true",
        help="prove the guard chain only: no orders sent, halt flag NOT armed, evidence recorded",
    )
    kill.set_defaults(func=_cmd_kill_switch)

    demo = subparsers.add_parser(
        "demo-param",
        help="show that a manifest parameter change alters behavior without a code edit",
    )
    demo.add_argument("--manifest", required=True, help="path to scope YAML")
    demo.add_argument(
        "--account-profile",
        help="explicit account-profile YAML overlay, e.g. config/profiles/micro_100.yaml",
    )
    demo.add_argument("--bars", type=int, default=1200)
    demo.add_argument("--macro-days", type=int, default=520)
    demo.add_argument("--equity", type=float, default=10000.0)
    demo.add_argument("--drift", type=float, default=0.12)
    demo.add_argument("--amplitude", type=float, default=6.0)
    demo.add_argument("--changed-threshold", type=float, default=0.99)
    demo.set_defaults(func=_cmd_demo_param)

    dash = subparsers.add_parser(
        "dashboard",
        help="render a saved JSON run report to a read-only HTML dashboard",
    )
    dash.add_argument("--report", required=True, help="path to a saved run report JSON")
    dash.add_argument("--output", required=True, help="path for the generated HTML file")
    dash.add_argument("--max-decisions", type=int, default=200)
    dash.set_defaults(func=_cmd_dashboard)

    live = subparsers.add_parser(
        "dashboard-live",
        help="serve a read-only live dashboard from a SQLite ledger on loopback",
    )
    live.add_argument(
        "--ledger",
        default="reports/demo.execution.ledger.sqlite",
        help="path to the SQLite ledger (read-only; never created by the dashboard)",
    )
    live.add_argument(
        "--host",
        default="127.0.0.1",
        help="loopback host to bind (127.0.0.1 or localhost)",
    )
    live.add_argument("--port", type=int, default=8010)
    live.add_argument("--refresh-seconds", type=float, default=2.0)
    live.add_argument("--max-rows", type=int, default=25)
    live.add_argument(
        "--profile",
        default="config/profiles/micro_50.yaml",
        help="account profile used by the agent and guarded demo order",
    )
    live.add_argument(
        "--macro-dir",
        default="data/raw/macro",
        help="local macro CSV/XLSX directory used by the agent",
    )
    live.add_argument(
        "--manifest-rel",
        default="config/scope.gold.v1.yaml",
        help="manifest path relative to the project root",
    )
    live.add_argument(
        "--broker-utc-offset-minutes",
        type=int,
        default=120,
        help="broker server offset from UTC (human-confirmed)",
    )
    live.add_argument(
        "--expected-login",
        type=int,
        default=106806652,
        help="required demo account login",
    )
    live.add_argument(
        "--interval-seconds",
        type=float,
        default=15.0,
        help="seconds between agent evaluations",
    )
    live.add_argument("--bars", type=int, default=1600, help="M15 bars for the agent")
    live.add_argument(
        "--context-bars", type=int, default=500, help="H4 bars for the agent"
    )
    live.add_argument(
        "--open",
        action="store_true",
        help="open the dashboard URL in the default browser after startup",
    )
    live.set_defaults(func=_cmd_dashboard_live)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
