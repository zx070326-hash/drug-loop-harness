from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from loop_harness import CoverageApprovalConsumptionReconciler  # noqa: E402


def main() -> None:
    args = parse_args()
    plan = read_json(args.plan)
    approval = read_json(args.approval_record)
    result = CoverageApprovalConsumptionReconciler().reconcile_ledger(
        plan,
        approval,
        args.ledger,
        reference_time=args.reference_time,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result.to_dict(), indent=2) + "\n", encoding="utf-8")
    print(result.summary_line)
    print(f"wrote {args.output}")
    if args.fail_on_inconsistent and result.reconciliation_status != "consistent":
        raise SystemExit(1)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only reconciliation of coverage approval, consumption, and "
            "evidence-admission records."
        ),
    )
    parser.add_argument("--plan", type=Path, default=ROOT / "state" / "oracle-cache-coverage-plan.json")
    parser.add_argument("--approval-record", type=Path, default=ROOT / "state" / "coverage-approval-record.json")
    parser.add_argument("--ledger", type=Path, default=ROOT / "state" / "coverage-consumptions.jsonl")
    parser.add_argument("--output", type=Path, default=ROOT / "state" / "coverage-consumption-reconciliation.json")
    parser.add_argument("--reference-time", default=None)
    parser.add_argument("--fail-on-inconsistent", action="store_true")
    return parser.parse_args()


def read_json(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise SystemExit(f"Expected JSON object at {path}.")
    return payload


if __name__ == "__main__":
    main()
