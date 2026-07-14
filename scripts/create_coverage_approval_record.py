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

from loop_harness import CoverageApprovalRecorder  # noqa: E402


def main() -> None:
    args = parse_args()
    plan = read_json(args.plan)
    if (
        args.decision in {"accept", "reject"}
        and not args.rationale.strip()
        and args.decision_file is None
    ):
        raise SystemExit("accept/reject decisions require --rationale.")
    if args.decision == "accept" and args.decision_file is None:
        if args.approved_budget is None or args.expires_at is None:
            raise SystemExit("accept decisions require --approved-budget and --expires-at.")

    overrides = read_json(args.decision_file) if args.decision_file else {}
    recorder = CoverageApprovalRecorder()
    record = recorder.build_record(
        plan,
        plan_path=args.plan,
        reviewer=args.reviewer,
        default_decision=args.decision,
        default_rationale=args.rationale,
        default_approved_budget=args.approved_budget,
        default_expires_at=args.expires_at,
        decision_overrides=overrides,
        selected_request_ids=tuple(args.request_id),
        notes=args.notes,
    )
    recorder.write_record(record, args.output)
    if args.archive_jsonl is not None:
        recorder.append_jsonl(record, args.archive_jsonl)

    print(record.summary_line)
    print(f"wrote {args.output}")
    if args.archive_jsonl is not None:
        print(f"appended {args.archive_jsonl}")
    if args.fail_on_unresolved and record.review_status not in {"authorized", "rejected"}:
        raise SystemExit(1)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Create a human budget-approval record for exact oracle-cache "
            "coverage requests. This command never executes an oracle."
        ),
    )
    parser.add_argument(
        "--plan",
        type=Path,
        default=ROOT / "state" / "oracle-cache-coverage-plan.json",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "state" / "coverage-approval-record.json",
    )
    parser.add_argument(
        "--archive-jsonl",
        type=Path,
        default=ROOT / "state" / "coverage-approvals.jsonl",
    )
    parser.add_argument("--no-archive-jsonl", action="store_true")
    parser.add_argument("--reviewer", default="pending_human_budget_approval")
    parser.add_argument(
        "--decision",
        choices=("accept", "reject", "defer"),
        default="defer",
    )
    parser.add_argument(
        "--request-id",
        action="append",
        default=[],
        help=(
            "Apply --decision only to this request id. Repeat to select more; "
            "unselected requests remain deferred."
        ),
    )
    parser.add_argument("--rationale", default="")
    parser.add_argument("--approved-budget", type=float, default=None)
    parser.add_argument(
        "--expires-at",
        default=None,
        help="ISO-8601 timestamp with timezone; required for accept.",
    )
    parser.add_argument(
        "--decision-file",
        type=Path,
        default=None,
        help=(
            "JSON mapping request_id to {decision, rationale, approved_budget, "
            "expires_at}; overrides default flags."
        ),
    )
    parser.add_argument("--notes", default="")
    parser.add_argument("--fail-on-unresolved", action="store_true")
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
