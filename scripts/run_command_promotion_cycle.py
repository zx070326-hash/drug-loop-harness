from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
SCRIPTS = ROOT / "scripts"
for path in (SRC, SCRIPTS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from loop_harness import ExperimentReportGrader, ExperimentRunArchiver  # noqa: E402
from run_command_promotion_experiment import (  # noqa: E402
    POLICY_NAMES,
    build_experiment_payload,
)


def main() -> None:
    args = parse_args()
    report_payload = build_experiment_payload(args)
    write_json(args.report_output, report_payload)

    grade_payload = ExperimentReportGrader().grade(report_payload).to_dict()
    grade_payload["input"] = {"report": str(args.report_output)}
    write_json(args.grade_output, grade_payload)

    record = ExperimentRunArchiver().build_record(
        report_payload,
        grade_payload,
        report_path=args.report_output,
        grade_path=args.grade_output,
        notes=args.notes,
    )
    archiver = ExperimentRunArchiver()
    archiver.write_record(record, args.record_output)
    if args.archive_jsonl is not None:
        archiver.append_jsonl(record, args.archive_jsonl)

    print(f"wrote {args.report_output}")
    print(f"wrote {args.grade_output}")
    print(f"wrote {args.record_output}")
    if args.archive_jsonl is not None:
        print(f"appended {args.archive_jsonl}")

    if args.fail_on_fail and grade_payload["verdict"] == "FAIL":
        raise SystemExit(1)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run the command-backed promotion experiment, grade it, and record "
            "one durable run summary."
        ),
    )
    parser.add_argument(
        "--candidates",
        type=Path,
        default=ROOT / "examples" / "data" / "multi_fidelity_promotion_candidates.csv",
    )
    parser.add_argument(
        "--observations",
        type=Path,
        default=ROOT / "examples" / "data" / "multi_fidelity_promotion_observations.csv",
    )
    parser.add_argument(
        "--report-output",
        type=Path,
        default=ROOT
        / "examples"
        / "data"
        / "command_promotion_experiment_report.json",
    )
    parser.add_argument(
        "--grade-output",
        type=Path,
        default=ROOT
        / "examples"
        / "data"
        / "command_promotion_experiment_grade.json",
    )
    parser.add_argument(
        "--record-output",
        type=Path,
        default=ROOT
        / "examples"
        / "data"
        / "command_promotion_experiment_run_record.json",
    )
    parser.add_argument(
        "--archive-jsonl",
        type=Path,
        default=ROOT / "state" / "experiment-runs.jsonl",
        help="Append the compact run record to this JSONL archive.",
    )
    parser.add_argument(
        "--no-archive-jsonl",
        action="store_true",
        help="Write the latest run record only; do not append JSONL state.",
    )
    parser.add_argument(
        "--policies",
        choices=POLICY_NAMES,
        nargs="+",
        default=POLICY_NAMES,
    )
    parser.add_argument("--budget-limit", type=float, default=56.0)
    parser.add_argument(
        "--budget-curve",
        type=float,
        nargs="+",
        default=(48.0, 56.0, 64.0),
    )
    parser.add_argument("--max-rounds", type=int, default=7)
    parser.add_argument("--tool-timeout", type=float, default=5.0)
    parser.add_argument("--enforce-guard", action="store_true")
    parser.add_argument("--notes", default="")
    parser.add_argument("--fail-on-fail", action="store_true")
    args = parser.parse_args()
    if args.no_archive_jsonl:
        args.archive_jsonl = None
    return args


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
