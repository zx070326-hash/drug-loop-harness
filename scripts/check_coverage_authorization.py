from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from loop_harness import ActionKind, ActionRecord, CoverageApprovalGate  # noqa: E402


def main() -> None:
    args = parse_args()
    plan = read_json(args.plan)
    record = read_json(args.approval_record)
    action = ActionRecord(
        id="coverage_authorization_check",
        kind=ActionKind(args.action_kind),
        subject_ids=(args.subject_id,),
        requested_by="coverage_authorization_check",
        budget_reserved=args.budget_reserved,
        reason="approval_query_only",
        parameters={
            "coverage_request_id": args.request_id,
            "coverage_approval_id": args.approval_id,
        },
    )
    decision = CoverageApprovalGate(
        plan,
        record,
        reference_time=args.reference_time,
    ).evaluate(action)
    print(json.dumps(decision.__dict__, indent=2))
    if decision.decision != "allow":
        raise SystemExit(1)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only authorization check for one exact cache-coverage action; "
            "does not execute an oracle."
        ),
    )
    parser.add_argument("--plan", type=Path, default=ROOT / "state" / "oracle-cache-coverage-plan.json")
    parser.add_argument("--approval-record", type=Path, default=ROOT / "state" / "coverage-approval-record.json")
    parser.add_argument("--request-id", required=True)
    parser.add_argument("--approval-id", required=True)
    parser.add_argument("--subject-id", required=True)
    parser.add_argument("--action-kind", choices=tuple(item.value for item in ActionKind), required=True)
    parser.add_argument("--budget-reserved", type=float, required=True)
    parser.add_argument("--reference-time", default=None)
    return parser.parse_args()


def read_json(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise SystemExit(f"Expected JSON object at {path}.")
    return payload


if __name__ == "__main__":
    main()
