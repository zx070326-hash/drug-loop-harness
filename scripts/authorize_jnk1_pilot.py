"""Resolve JNK1 pilot activation from the complete frozen PTP1B decision."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from loop_harness.v2.fep_untouched_pilot import (  # noqa: E402
    resolve_untouched_pilot_activation,
    write_pilot_artifact,
)


def main() -> None:
    args = _parse_args()
    activation, authorization = resolve_untouched_pilot_activation(
        root=ROOT,
        pilot_plan_path=args.pilot_plan,
        pilot_lock_path=args.pilot_lock,
        upstream_decision_path=args.upstream_decision,
        upstream_release_lock_path=args.upstream_release_lock,
        upstream_evaluation_path=args.upstream_evaluation,
        upstream_outcome_root=args.upstream_outcomes,
        pilot_output_path=args.pilot_output,
    )
    authorization_sha256 = None
    if authorization is not None:
        authorization_sha256 = write_pilot_artifact(
            args.authorization_output, authorization
        )
    activation_sha256 = write_pilot_artifact(args.activation_output, activation)
    print(
        json.dumps(
            {
                "status": activation["status"],
                "pilot_execution_authority_granted": activation[
                    "pilot_execution_authority_granted"
                ],
                "activation_sha256": activation_sha256,
                "authorization_sha256": authorization_sha256,
            },
            indent=2,
        )
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--pilot-plan",
        type=Path,
        default=ROOT / "state/jnk1-operational-pilot-plan-v1.json",
    )
    parser.add_argument(
        "--pilot-lock",
        type=Path,
        default=ROOT / "state/jnk1-conditional-pilot-lock-v1.json",
    )
    parser.add_argument(
        "--upstream-decision",
        type=Path,
        default=ROOT / "state/ptp1b-full-repair-stage-decision-v1.json",
    )
    parser.add_argument(
        "--upstream-release-lock",
        type=Path,
        default=ROOT / "state/ptp1b-full-repair-release-lock-v1.json",
    )
    parser.add_argument(
        "--upstream-evaluation",
        type=Path,
        default=ROOT / "state/ptp1b-full-repair-evaluation-v1.json",
    )
    parser.add_argument(
        "--upstream-outcomes",
        type=Path,
        default=ROOT / "external/runs/ptp1b-paired-repair-v1/full/outcomes",
    )
    parser.add_argument(
        "--pilot-output",
        type=Path,
        default=ROOT / "external/runs/jnk1-paired-repair-v1/pilot",
    )
    parser.add_argument(
        "--activation-output",
        type=Path,
        default=ROOT / "state/jnk1-pilot-activation-v1.json",
    )
    parser.add_argument(
        "--authorization-output",
        type=Path,
        default=ROOT / "state/jnk1-pilot-authorization-v1.json",
    )
    return parser.parse_args()


if __name__ == "__main__":
    main()
