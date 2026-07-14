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

from loop_harness import ExperimentHumanReviewRecorder  # noqa: E402


def main() -> None:
    args = parse_args()
    grade = read_json(args.grade)
    run_record = read_json(args.run_record) if args.run_record.exists() else {}
    grade_verdict = str(grade.get("verdict", "UNKNOWN"))
    if grade_verdict != "HUMAN" and not args.allow_non_human_grade:
        raise SystemExit(
            "Refusing to create a human-review record for a non-HUMAN grade. "
            "Use --allow-non-human-grade to override."
        )
    if (
        args.decision in {"accept", "reject"}
        and not args.rationale.strip()
        and args.decision_file is None
    ):
        raise SystemExit("accept/reject decisions require --rationale.")

    overrides = read_json(args.decision_file) if args.decision_file is not None else {}
    record = ExperimentHumanReviewRecorder().build_record(
        grade,
        run_record=run_record,
        run_id=args.run_id,
        grade_path=args.grade,
        report_path=args.report_path,
        reviewer=args.reviewer,
        default_decision=args.decision,
        default_rationale=args.rationale,
        decision_overrides=overrides,
        selected_issue_codes=tuple(args.issue_code),
        notes=args.notes,
    )
    recorder = ExperimentHumanReviewRecorder()
    recorder.write_record(record, args.output)
    if args.archive_jsonl is not None:
        recorder.append_jsonl(record, args.archive_jsonl)

    print(record.summary_line)
    print(f"wrote {args.output}")
    if args.archive_jsonl is not None:
        print(f"appended {args.archive_jsonl}")

    if args.fail_on_unresolved and not record.may_support_policy_interpretation:
        raise SystemExit(1)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Create a durable human-review decision record for a HUMAN "
            "experiment-grade verdict."
        ),
    )
    parser.add_argument(
        "--grade",
        type=Path,
        default=ROOT / "examples" / "data" / "command_promotion_experiment_grade.json",
        help="Experiment grade JSON produced by grade_command_promotion_report.py.",
    )
    parser.add_argument(
        "--run-record",
        type=Path,
        default=ROOT
        / "examples"
        / "data"
        / "command_promotion_experiment_run_record.json",
        help="Run record JSON used to link review decisions to run_id/report path.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "state" / "human-review-record.json",
    )
    parser.add_argument(
        "--archive-jsonl",
        type=Path,
        default=ROOT / "state" / "human-reviews.jsonl",
        help="Append the review record to this JSONL archive.",
    )
    parser.add_argument(
        "--no-archive-jsonl",
        action="store_true",
        help="Write the latest review record only; do not append JSONL state.",
    )
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--report-path", type=Path, default=None)
    parser.add_argument("--reviewer", default="pending_human_review")
    parser.add_argument(
        "--decision",
        choices=("accept", "reject", "defer"),
        default="defer",
        help="Decision to apply to selected HUMAN issues.",
    )
    parser.add_argument(
        "--issue-code",
        action="append",
        default=[],
        help=(
            "Apply --decision only to this issue code. Repeat for more. "
            "Unselected HUMAN issues remain deferred."
        ),
    )
    parser.add_argument(
        "--rationale",
        default="",
        help="Required for accept/reject when not using --decision-file.",
    )
    parser.add_argument(
        "--decision-file",
        type=Path,
        default=None,
        help=(
            "JSON mapping from issue code to {decision, rationale}; overrides "
            "--decision for matching issue codes."
        ),
    )
    parser.add_argument("--notes", default="")
    parser.add_argument("--allow-non-human-grade", action="store_true")
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
