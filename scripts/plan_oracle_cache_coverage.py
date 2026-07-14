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

from loop_harness import OracleCacheCoveragePlanner  # noqa: E402


def main() -> None:
    args = parse_args()
    report_path = resolve_report_path(args)
    plan = OracleCacheCoveragePlanner().plan_file(
        report_path,
        include_budget_curve=not args.no_budget_curve,
        max_source_locations=args.max_source_locations,
    )
    write_json(args.output, plan.to_dict())
    print(plan.summary_line)
    for request in plan.requests:
        subject = request.subject_id or "<unknown_subject>"
        oracle = request.oracle_name or "<unmapped_oracle>"
        print(
            f"- {subject} -> {oracle}: occurrences={request.occurrence_count}; "
            f"status={request.request_status}"
        )
    print(f"wrote {args.output}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Compile recorded missing observations into a human-approval-gated "
            "oracle-cache coverage plan. This command never executes an oracle."
        ),
    )
    parser.add_argument(
        "report",
        nargs="?",
        type=Path,
        help="Promotion report JSON. Mutually exclusive with --run-record.",
    )
    parser.add_argument(
        "--run-record",
        type=Path,
        default=None,
        help="Experiment run record JSON whose report_path selects the report.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "state" / "oracle-cache-coverage-plan.json",
        help="Read-only, approval-gated coverage plan JSON.",
    )
    parser.add_argument("--no-budget-curve", action="store_true")
    parser.add_argument(
        "--max-source-locations",
        type=int,
        default=12,
        help="Maximum trace locations retained for each distinct request.",
    )
    return parser.parse_args()


def resolve_report_path(args: argparse.Namespace) -> Path:
    if args.report is not None and args.run_record is not None:
        raise SystemExit("Provide a report path or --run-record, not both.")
    if args.report is not None:
        return args.report
    if args.run_record is not None:
        record = read_json(args.run_record)
        report_path = record.get("report_path")
        if not isinstance(report_path, str) or not report_path:
            raise SystemExit(f"Run record has no usable report_path: {args.run_record}")
        return Path(report_path)
    return ROOT / "examples" / "data" / "command_promotion_experiment_report.json"


def read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise SystemExit(f"Expected JSON object at {path}.")
    return payload


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
