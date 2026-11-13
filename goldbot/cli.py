"""Command-line entry point for the goldbot validator and M2 strategy core."""

from __future__ import annotations

import argparse
import copy
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Sequence

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
from .manifest import load_manifest, manifest_hash
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
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
