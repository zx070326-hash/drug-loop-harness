"""Evaluate a complete PTP1B paired repair outcome cohort."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from loop_harness.v2.fep_paired_repair import (  # noqa: E402
    FepPairedRepairEvaluator,
    FepRepairOutcome,
    FepRepairOutcomeStatus,
    load_paired_repair_protocol,
    load_repair_task_manifest,
    write_repair_evaluation,
)


def main() -> None:
    args = _parse_args()
    protocol, _ = load_paired_repair_protocol(args.protocol)
    manifest, _ = load_repair_task_manifest(args.task_manifest)
    outcomes = tuple(_load_outcome(path) for path in sorted(args.outcomes.glob("*.json")))
    evaluation = FepPairedRepairEvaluator(protocol).evaluate(manifest, outcomes)
    sha256 = write_repair_evaluation(args.output, evaluation)
    payload = evaluation.to_dict()
    print(
        json.dumps(
            {
                "status": payload["status"],
                "go_passed": payload["go_passed"],
                "task_count": payload["task_count"],
                "succeeded_task_count": payload.get("succeeded_task_count", 0),
                "evaluation_sha256": sha256,
                "output": str(args.output),
            },
            indent=2,
        )
    )


def _load_outcome(path: Path) -> FepRepairOutcome:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != "fep_repair_outcome.v1":
        raise ValueError(f"unsupported repair outcome: {path}")
    return FepRepairOutcome(
        task_id=str(payload["task_id"]),
        status=FepRepairOutcomeStatus(str(payload["status"])),
        oracle_profile_id=str(payload["oracle_profile_id"]),
        transformation_sha256=str(payload["transformation_sha256"]),
        result_artifact_sha256=payload.get("result_artifact_sha256"),
        estimate_kcal_per_mol=payload.get("estimate_kcal_per_mol"),
        uncertainty_kcal_per_mol=payload.get("uncertainty_kcal_per_mol"),
        smallest_off_diagonal_overlap=payload.get(
            "smallest_off_diagonal_overlap"
        ),
        compute_seconds=float(payload["compute_seconds"]),
        actual_work_units=float(payload["actual_work_units"]),
        failure_code=payload.get("failure_code"),
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--task-manifest", type=Path, required=True)
    parser.add_argument("--outcomes", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


if __name__ == "__main__":
    main()
