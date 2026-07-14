from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from loop_harness import ExperimentReportGrader  # noqa: E402


def main() -> None:
    args = parse_args()
    payload = json.loads(args.report.read_text(encoding="utf-8"))
    grade = ExperimentReportGrader().grade(payload).to_dict()
    grade["input"] = {"report": str(args.report)}

    text = json.dumps(grade, indent=2)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
        print(f"wrote {args.output}")
    else:
        print(text)

    if args.fail_on_fail and grade["verdict"] == "FAIL":
        raise SystemExit(1)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Grade a command-backed promotion experiment report.",
    )
    parser.add_argument(
        "report",
        type=Path,
        nargs="?",
        default=ROOT
        / "examples"
        / "data"
        / "command_promotion_experiment_report.json",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT
        / "examples"
        / "data"
        / "command_promotion_experiment_grade.json",
    )
    parser.add_argument(
        "--fail-on-fail",
        action="store_true",
        help="Exit nonzero only when the grader verdict is FAIL.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    main()
