from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from loop_harness import CoverageConsumptionLedger, CoverageRuntimeReceiptAuditor  # noqa: E402


def main() -> None:
    args = parse_args()
    report = read_json(args.runtime_report)
    plan = read_json(args.plan)
    approval = read_json(args.approval_record)
    ledger_problem = None
    try:
        ledger_records = CoverageConsumptionLedger().records(args.ledger)
    except (OSError, ValueError) as exc:
        ledger_records = ()
        ledger_problem = str(exc)

    audit = CoverageRuntimeReceiptAuditor().audit(
        report,
        plan,
        approval,
        ledger_records,
        ledger_problem=ledger_problem,
    )
    payload = audit.to_dict()
    payload["input"] = {
        "runtime_report": str(args.runtime_report),
        "plan": str(args.plan),
        "approval_record": str(args.approval_record),
        "consumption_ledger": str(args.ledger),
        "read_only": True,
        "live_oracle_calls": 0,
    }
    write_json(args.output, payload)
    print(audit.summary_line)
    print(f"wrote {args.output}")
    if audit.audit_status != "PASS":
        raise SystemExit(1)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only cross-artifact audit of a coverage runtime report and "
            "its durable consumption receipts. This command never runs an oracle."
        ),
    )
    parser.add_argument(
        "runtime_report",
        type=Path,
        help="JSON output from run_coverage_runtime.py.",
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
        "--output",
        type=Path,
        default=ROOT / "state" / "coverage-runtime-receipt-audit.json",
    )
    return parser.parse_args()


def read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise SystemExit(f"Expected JSON object at {path}.")
    return payload


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
