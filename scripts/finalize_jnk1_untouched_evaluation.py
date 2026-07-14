"""Run the frozen JNK1 evaluator only after complete-cohort release."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from loop_harness.v2.fep_untouched_evaluation import (  # noqa: E402
    finalize_untouched_evaluation,
)


def main() -> None:
    args = _parse_args()
    decision, primary_sha256, report_sha256, decision_sha256 = (
        finalize_untouched_evaluation(
            root=ROOT,
            evaluator_lock_path=args.evaluator_lock,
            authorization_path=args.authorization,
            completion_path=args.completion,
            commit_paths=[
                ROOT / f"state/jnk1-full-batch-{index}-commit-v1.json"
                for index in range(9)
            ],
            full_output_path=args.full_output,
            primary_evaluation_path=args.primary_evaluation,
            credibility_report_path=args.credibility_report,
            decision_path=args.decision,
        )
    )
    print(
        json.dumps(
            {
                "status": decision["status"],
                "decision": decision["decision"],
                "primary_evaluation_status": decision["primary_evaluation_status"],
                "go_passed": decision["go_passed"],
                "primary_evaluation_sha256": primary_sha256,
                "credibility_report_sha256": report_sha256,
                "decision_sha256": decision_sha256,
                "accuracy_endpoint_evaluated": False,
                "scientific_claim_upgrade_permitted": False,
            },
            indent=2,
        )
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--evaluator-lock",
        type=Path,
        default=ROOT / "state/jnk1-evaluator-lock-v1.json",
    )
    parser.add_argument(
        "--authorization",
        type=Path,
        default=ROOT / "state/jnk1-full-execution-authorization-v2.json",
    )
    parser.add_argument(
        "--completion",
        type=Path,
        default=ROOT / "state/jnk1-full-execution-complete-v1.json",
    )
    parser.add_argument(
        "--full-output",
        type=Path,
        default=ROOT / "external/runs/jnk1-paired-repair-v1/full",
    )
    parser.add_argument(
        "--primary-evaluation",
        type=Path,
        default=ROOT / "state/jnk1-primary-repair-evaluation-v1.json",
    )
    parser.add_argument(
        "--credibility-report",
        type=Path,
        default=ROOT / "state/jnk1-repair-credibility-report-v1.json",
    )
    parser.add_argument(
        "--decision",
        type=Path,
        default=ROOT / "state/jnk1-untouched-confirmation-decision-v1.json",
    )
    return parser.parse_args()


if __name__ == "__main__":
    main()
