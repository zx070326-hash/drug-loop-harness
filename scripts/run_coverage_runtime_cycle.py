from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from loop_harness import CoverageRuntimeArchiver  # noqa: E402


def main() -> None:
    args = parse_args()
    run_local_runtime(args)
    audit_completed = run_receipt_audit(args)
    if audit_completed.returncode != 0:
        print("receipt audit failed; coverage runtime was not archived.")
        raise SystemExit(audit_completed.returncode or 1)

    runtime_report = read_json(args.runtime_output)
    receipt_audit = read_json(args.audit_output)
    record = CoverageRuntimeArchiver().build_record(
        runtime_report,
        receipt_audit,
        runtime_report_path=args.runtime_output,
        receipt_audit_path=args.audit_output,
        notes=args.notes,
    )
    archiver = CoverageRuntimeArchiver()
    archiver.write_record(record, args.record_output)
    if args.archive_jsonl is not None:
        archiver.append_jsonl(record, args.archive_jsonl)

    print(f"wrote {args.record_output}")
    if args.archive_jsonl is not None:
        print(f"appended {args.archive_jsonl}")


def run_local_runtime(args: argparse.Namespace) -> None:
    command = [
        sys.executable,
        str(ROOT / "scripts" / "run_coverage_runtime.py"),
        "--plan",
        str(args.plan),
        "--approval-record",
        str(args.approval_record),
        "--ledger",
        str(args.ledger),
        "--candidates",
        str(args.candidates),
        "--observations",
        *(str(path) for path in args.observations),
        "--output",
        str(args.runtime_output),
    ]
    for request_id in args.request_id:
        command.extend(("--request-id", request_id))
    if args.budget_reserved is not None:
        command.extend(("--budget-reserved", str(args.budget_reserved)))
    if args.reference_time is not None:
        command.extend(("--reference-time", args.reference_time))
    subprocess.run(command, check=True)


def run_receipt_audit(args: argparse.Namespace) -> subprocess.CompletedProcess[bytes]:
    command = [
        sys.executable,
        str(ROOT / "scripts" / "audit_coverage_runtime_receipts.py"),
        str(args.runtime_output),
        "--plan",
        str(args.plan),
        "--approval-record",
        str(args.approval_record),
        "--ledger",
        str(args.ledger),
        "--output",
        str(args.audit_output),
    ]
    return subprocess.run(command, check=False)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run local cache coverage, audit durable receipts, and archive only "
            "a passing control-plane record. This command never runs a live oracle."
        ),
    )
    parser.add_argument(
        "--plan",
        type=Path,
        default=ROOT / "state" / "oracle-cache-coverage-plan.json",
    )
    parser.add_argument(
        "--approval-record",
        type=Path,
        default=ROOT / "state" / "coverage-approval-record.json",
    )
    parser.add_argument(
        "--ledger",
        type=Path,
        default=ROOT / "state" / "coverage-consumptions.jsonl",
    )
    parser.add_argument(
        "--candidates",
        type=Path,
        default=ROOT / "examples" / "data" / "multi_fidelity_promotion_candidates.csv",
    )
    parser.add_argument(
        "--observations",
        type=Path,
        nargs="+",
        default=(
            ROOT / "examples" / "data" / "multi_fidelity_promotion_observations.csv",
        ),
    )
    parser.add_argument("--request-id", action="append", default=[])
    parser.add_argument("--budget-reserved", type=float, default=None)
    parser.add_argument("--reference-time", default=None)
    parser.add_argument(
        "--runtime-output",
        type=Path,
        default=ROOT / "state" / "coverage-runtime-report.json",
    )
    parser.add_argument(
        "--audit-output",
        type=Path,
        default=ROOT / "state" / "coverage-runtime-receipt-audit.json",
    )
    parser.add_argument(
        "--record-output",
        type=Path,
        default=ROOT / "state" / "coverage-runtime-record.json",
    )
    parser.add_argument(
        "--archive-jsonl",
        type=Path,
        default=ROOT / "state" / "coverage-runtime-runs.jsonl",
    )
    parser.add_argument("--no-archive-jsonl", action="store_true")
    parser.add_argument("--notes", default="")
    args = parser.parse_args()
    if args.no_archive_jsonl:
        args.archive_jsonl = None
    return args


def read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise SystemExit(f"Expected JSON object at {path}.")
    return payload


if __name__ == "__main__":
    main()
