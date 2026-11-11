"""Command-line entry point for the goldbot validator."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

from .manifest import load_manifest, manifest_hash
from .validator import validate_manifest, write_json_report, write_markdown_report


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
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
