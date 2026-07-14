from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from loop_harness import ProspectiveLoopEvaluationOutcomeReportVerifier  # noqa: E402


def main() -> None:
    args = parse_args()
    verification = ProspectiveLoopEvaluationOutcomeReportVerifier().verify_path(
        args.protocol,
        args.prospective_acquisition_plan,
        args.power_plan,
        args.assignment,
        args.assignment_verification,
        args.assay_endpoint_policy,
        args.outcome_report,
        args.consumption_ledger,
        args.campaign_store,
        randomization_verification_paths=args.randomization_verification,
        approval_paths=args.approval,
        admission_commit_directories=args.admission_commit_directory,
        policy_decision_receipt_paths=args.policy_decision_receipt,
        policy_decision_campaign_store_paths=args.policy_decision_campaign_store,
        verified_at=args.verified_at,
    )
    write_json(args.output, verification.to_dict())
    print(verification.summary_line)
    print(f"wrote {args.output}")
    if verification.verification_status != "PASS":
        raise SystemExit(2)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only verifier for a prospective loop evaluation outcome report. "
            "It never executes an experiment, ranks policies, computes the "
            "predeclared analysis, or upgrades a scientific claim."
        ),
    )
    parser.add_argument("protocol", type=Path)
    parser.add_argument("--prospective-acquisition-plan", type=Path, required=True)
    parser.add_argument("--power-plan", type=Path, required=True)
    parser.add_argument("--assignment", type=Path, required=True)
    parser.add_argument("--assignment-verification", type=Path, required=True)
    parser.add_argument("--randomization-verification", type=Path, action="append", required=True)
    parser.add_argument("--approval", type=Path, action="append", required=True)
    parser.add_argument("--assay-endpoint-policy", type=Path, required=True)
    parser.add_argument("--outcome-report", type=Path, required=True)
    parser.add_argument("--consumption-ledger", type=Path, required=True)
    parser.add_argument("--campaign-store", type=Path, required=True)
    parser.add_argument("--admission-commit-directory", type=Path, action="append", default=[])
    parser.add_argument("--policy-decision-receipt", type=Path, action="append", default=[])
    parser.add_argument("--policy-decision-campaign-store", type=Path, action="append", default=[])
    parser.add_argument("--verified-at")
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "state" / "prospective-loop-outcome-verification.json",
    )
    return parser.parse_args()


def write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
