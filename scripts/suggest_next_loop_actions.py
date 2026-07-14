from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from loop_harness import ReviewAwarePlanningAdvisor  # noqa: E402


def main() -> None:
    args = parse_args()
    plan = ReviewAwarePlanningAdvisor().plan_from_files(
        args.run_summary,
        args.review_summary,
        coverage_runtime_summary_path=args.coverage_runtime_summary,
        docking_readiness_summary_path=args.docking_readiness_summary,
        evidence_gap_summary_path=args.evidence_gap_summary,
        admet_prospective_drift_report_path=args.admet_drift_report,
    )
    write_json(args.output, plan.to_dict())
    print(plan.summary_line)
    for hint in plan.hints:
        codes = ",".join(hint.related_issue_codes)
        print(
            f"- [{hint.priority}] {codes}: {hint.action_kind} "
            f"-> {hint.suggested_artifact}"
        )
    print(f"wrote {args.output}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Create advisory next-loop actions from compact experiment and "
            "human-review state. This command never executes tools or upgrades claims."
        ),
    )
    parser.add_argument(
        "--run-summary",
        type=Path,
        default=ROOT / "state" / "experiment-runs-summary.json",
        help="Hot-state artifact produced by summarize_experiment_runs.py.",
    )
    parser.add_argument(
        "--review-summary",
        type=Path,
        default=ROOT / "state" / "human-reviews-summary.json",
        help="Hot-state artifact produced by summarize_human_reviews.py.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "state" / "next-loop-plan.json",
        help="JSON advisory plan for the next manual loop turn.",
    )
    parser.add_argument(
        "--coverage-runtime-summary",
        type=Path,
        default=None,
        help=(
            "Optional coverage-runtime hot state from "
            "summarize_coverage_runtime_archives.py."
        ),
    )
    parser.add_argument(
        "--docking-readiness-summary",
        type=Path,
        default=None,
        help=(
            "Optional non-executing docking state from "
            "summarize_docking_readiness.py."
        ),
    )
    parser.add_argument(
        "--evidence-gap-summary",
        type=Path,
        default=None,
        help=(
            "Optional candidate-level evidence gap state from "
            "compose_evidence_gaps.py."
        ),
    )
    parser.add_argument(
        "--admet-drift-report",
        type=Path,
        default=None,
        help=(
            "Optional read-only report from monitor_admet_calibration_drift.py. "
            "It may add advisory calibration diagnostics only."
        ),
    )
    return parser.parse_args()


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
