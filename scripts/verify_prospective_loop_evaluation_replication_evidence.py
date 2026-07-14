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

from loop_harness import ProspectiveLoopEvaluationReplicationEvidenceVerifier  # noqa: E402


def main() -> None:
    args = parse_args()
    evidence = ProspectiveLoopEvaluationReplicationEvidenceVerifier().verify_path(
        args.source_protocol,
        args.source_power_plan,
        args.source_assignment,
        args.source_outcome_verification,
        args.source_analysis,
        args.replication_protocol,
        args.replication_power_plan,
        args.replication_assignment,
        args.replication_outcome_verification,
        args.replication_analysis,
        args.replication_design_verification,
        evaluated_at=args.evaluated_at,
    )
    write_json(args.output, evidence.to_dict())
    print(evidence.summary_line)
    print(f"wrote {args.output}")
    if evidence.verification_status != "PASS":
        raise SystemExit(2)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only verifier for completed prospective replication evidence. "
            "It never selects a policy winner, executes work, or upgrades claims."
        )
    )
    parser.add_argument("--source-protocol", type=Path, required=True)
    parser.add_argument("--source-power-plan", type=Path, required=True)
    parser.add_argument("--source-assignment", type=Path, required=True)
    parser.add_argument("--source-outcome-verification", type=Path, required=True)
    parser.add_argument("--source-analysis", type=Path, required=True)
    parser.add_argument("--replication-protocol", type=Path, required=True)
    parser.add_argument("--replication-power-plan", type=Path, required=True)
    parser.add_argument("--replication-assignment", type=Path, required=True)
    parser.add_argument("--replication-outcome-verification", type=Path, required=True)
    parser.add_argument("--replication-analysis", type=Path, required=True)
    parser.add_argument("--replication-design-verification", type=Path, required=True)
    parser.add_argument("--evaluated-at")
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "state" / "prospective-loop-replication-evidence.json",
    )
    return parser.parse_args()


def write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
