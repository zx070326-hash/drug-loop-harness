"""Build the frozen credibility companion after the primary stage decision."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from loop_harness.v2.fep_paired_repair import (  # noqa: E402
    FepRepairOutcome,
    FepRepairOutcomeStatus,
    load_paired_repair_protocol,
    load_repair_task_manifest,
)
from loop_harness.v2.fep_repair_credibility import (  # noqa: E402
    build_fep_repair_credibility_report,
    verify_fep_repair_credibility_lock,
    write_fep_repair_credibility_artifact,
)
from loop_harness.v2.fep_repair_full_execution import (  # noqa: E402
    project_full_repair_execution_state,
)


def main() -> None:
    args = _parse_args()
    lock_bytes, lock = _load_json(args.credibility_lock)
    verify_fep_repair_credibility_lock(ROOT, lock)
    state = project_full_repair_execution_state(
        root=ROOT,
        authorization_path=args.authorization,
        output_root=args.full_output,
    )
    if state["status"] != "COMPLETE_READY_FOR_PAIRED_EVALUATION":
        raise ValueError("credibility report requires the complete full cohort")
    evaluation_bytes, evaluation = _load_json(args.evaluation)
    decision_bytes, decision = _load_json(args.decision)
    if (
        decision.get("status") != "STAGE_COMPLETE"
        or decision.get("source_bindings", {}).get("evaluation_sha256")
        != hashlib.sha256(evaluation_bytes).hexdigest()
    ):
        raise ValueError("credibility report stage decision/evaluation binding mismatch")
    protocol, _ = load_paired_repair_protocol(args.protocol)
    manifest, _ = load_repair_task_manifest(args.task_manifest)
    outcome_paths = tuple(sorted((args.full_output / "outcomes").glob("*.json")))
    if len(outcome_paths) != 36:
        raise ValueError("credibility report requires exactly 36 outcome files")
    outcomes = tuple(_load_outcome(path) for path in outcome_paths)
    source_bindings = {
        "credibility_lock_sha256": hashlib.sha256(lock_bytes).hexdigest(),
        "primary_evaluation_sha256": hashlib.sha256(evaluation_bytes).hexdigest(),
        "stage_decision_sha256": hashlib.sha256(decision_bytes).hexdigest(),
        "full_outcome_set_sha256": _canonical_file_set_sha256(outcome_paths),
    }
    report = build_fep_repair_credibility_report(
        protocol=protocol,
        manifest=manifest,
        outcomes=outcomes,
        primary_evaluation=evaluation,
        stage_decision=decision,
        source_bindings=source_bindings,
    )
    report_sha256 = write_fep_repair_credibility_artifact(args.output, report)
    print(
        json.dumps(
            {
                "status": "PTP1B_REPAIR_CREDIBILITY_COMPANION_COMPLETE",
                "primary_decision_unchanged": True,
                "statistical_superiority_claim_permitted": False,
                "report_sha256": report_sha256,
                "output": str(args.output),
            },
            indent=2,
        )
    )


def _load_outcome(path: Path) -> FepRepairOutcome:
    _, payload = _load_json(path)
    if payload.get("schema_version") != "fep_repair_outcome.v1":
        raise ValueError(f"unsupported full repair outcome: {path}")
    return FepRepairOutcome(
        task_id=str(payload["task_id"]),
        status=FepRepairOutcomeStatus(str(payload["status"])),
        oracle_profile_id=str(payload["oracle_profile_id"]),
        transformation_sha256=str(payload["transformation_sha256"]),
        result_artifact_sha256=payload.get("result_artifact_sha256"),
        estimate_kcal_per_mol=payload.get("estimate_kcal_per_mol"),
        uncertainty_kcal_per_mol=payload.get("uncertainty_kcal_per_mol"),
        smallest_off_diagonal_overlap=payload.get("smallest_off_diagonal_overlap"),
        compute_seconds=float(payload["compute_seconds"]),
        actual_work_units=float(payload["actual_work_units"]),
        failure_code=payload.get("failure_code"),
    )


def _canonical_file_set_sha256(paths: tuple[Path, ...]) -> str:
    payload = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in paths
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _load_json(path: Path) -> tuple[bytes, dict]:
    data = Path(path).read_bytes()
    payload = json.loads(data.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return data, payload


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--credibility-lock",
        type=Path,
        default=ROOT / "state/ptp1b-repair-credibility-lock-v1.json",
    )
    parser.add_argument(
        "--authorization",
        type=Path,
        default=ROOT / "state/ptp1b-full-repair-execution-authorization-v1.json",
    )
    parser.add_argument(
        "--protocol",
        type=Path,
        default=ROOT / "state/ptp1b-paired-repair-protocol-v1.json",
    )
    parser.add_argument(
        "--task-manifest",
        type=Path,
        default=ROOT / "state/ptp1b-paired-repair-task-manifest-v1.json",
    )
    parser.add_argument(
        "--full-output",
        type=Path,
        default=ROOT / "external/runs/ptp1b-paired-repair-v1/full",
    )
    parser.add_argument(
        "--evaluation",
        type=Path,
        default=ROOT / "state/ptp1b-full-repair-evaluation-v1.json",
    )
    parser.add_argument(
        "--decision",
        type=Path,
        default=ROOT / "state/ptp1b-full-repair-stage-decision-v1.json",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "state/ptp1b-repair-credibility-report-v1.json",
    )
    return parser.parse_args()


if __name__ == "__main__":
    main()
