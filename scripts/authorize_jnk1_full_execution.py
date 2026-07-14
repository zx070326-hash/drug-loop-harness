"""Create JNK1 full execution authority only after both frozen gates pass exactly."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from loop_harness.v2.fep_untouched_full_execution import (  # noqa: E402
    build_untouched_full_execution_authorization,
    write_untouched_full_artifact,
)


def main() -> None:
    args = _parse_args()
    payload = build_untouched_full_execution_authorization(
        root=ROOT,
        campaign_lock_path=args.campaign_lock,
        upstream_decision_path=args.upstream_decision,
        upstream_release_lock_path=args.upstream_release_lock,
        upstream_evaluation_path=args.upstream_evaluation,
        upstream_outcome_root=args.upstream_outcome_root,
        pilot_plan_path=args.pilot_plan,
        pilot_lock_path=args.pilot_lock,
        pilot_activation_path=args.pilot_activation,
        pilot_authorization_path=args.pilot_authorization,
        pilot_verdict_path=args.pilot_verdict,
        pilot_start_paths=sorted((args.pilot_output / "starts").glob("*.json")),
        pilot_receipt_paths=sorted((args.pilot_output / "receipts").glob("*.json")),
        task_manifest_path=args.task_manifest,
        protocol_path=args.protocol,
        profile_manifest_path=args.profile_manifest,
        full_output_path=args.full_output,
    )
    sha256 = write_untouched_full_artifact(args.output, payload)
    print(
        json.dumps(
            {
                "status": "JNK1_FULL_EXECUTION_AUTHORIZED_AFTER_EXACT_DOUBLE_GATE",
                "authorization_sha256": sha256,
                "task_count": payload["schedule"]["task_count"],
                "batch_count": payload["schedule"]["batch_count"],
                "scientific_metrics_used_for_authorization": False,
            },
            indent=2,
        )
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--campaign-lock",
        type=Path,
        default=ROOT / "state/jnk1-full-campaign-lock-v2.json",
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
        "--upstream-outcome-root",
        type=Path,
        default=ROOT / "external/runs/ptp1b-paired-repair-v1/full/outcomes",
    )
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
        "--pilot-activation",
        type=Path,
        default=ROOT / "state/jnk1-pilot-activation-v1.json",
    )
    parser.add_argument(
        "--pilot-authorization",
        type=Path,
        default=ROOT / "state/jnk1-pilot-authorization-v1.json",
    )
    parser.add_argument(
        "--pilot-verdict",
        type=Path,
        default=ROOT / "state/jnk1-operational-pilot-verdict-v1.json",
    )
    parser.add_argument(
        "--pilot-output",
        type=Path,
        default=ROOT / "external/runs/jnk1-paired-repair-v1/pilot",
    )
    parser.add_argument(
        "--task-manifest",
        type=Path,
        default=ROOT / "state/jnk1-paired-repair-task-manifest-v1.json",
    )
    parser.add_argument(
        "--protocol",
        type=Path,
        default=ROOT / "state/jnk1-paired-repair-protocol-v1.json",
    )
    parser.add_argument(
        "--profile-manifest",
        type=Path,
        default=ROOT / "external/profiles/jnk1-paired-repair-v1/profile-manifest.json",
    )
    parser.add_argument(
        "--full-output",
        type=Path,
        default=ROOT / "external/runs/jnk1-paired-repair-v1/full",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "state/jnk1-full-execution-authorization-v2.json",
    )
    return parser.parse_args()


if __name__ == "__main__":
    main()
