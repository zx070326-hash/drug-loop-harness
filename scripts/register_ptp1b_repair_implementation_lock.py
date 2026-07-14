"""Freeze PTP1B paired-repair code and inputs before any pilot outcome."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from loop_harness.v2.fep_repair_execution import (  # noqa: E402
    build_repair_implementation_lock,
    write_json_artifact,
)


IMPLEMENTATION_FILES = (
    "src/loop_harness/v2/__init__.py",
    "src/loop_harness/v2/fep_paired_repair.py",
    "src/loop_harness/v2/fep_repair_execution.py",
    "scripts/wsl/build_ptp1b_paired_repair_inputs.py",
    "scripts/wsl/analyze_ptp1b_repair_mechanism.py",
    "scripts/wsl/audit_ptp1b_charge_repeatability.py",
    "scripts/wsl/run_openfe_repair_task.py",
    "scripts/supersede_ptp1b_paired_repair_preoutcome.py",
    "scripts/register_ptp1b_paired_repair_protocol.py",
    "scripts/build_ptp1b_paired_repair_task_manifest.py",
    "scripts/evaluate_ptp1b_paired_repair.py",
    "scripts/register_ptp1b_repair_pilot.py",
    "scripts/register_ptp1b_repair_implementation_lock.py",
    "scripts/finalize_ptp1b_repair_pilot.py",
    "tests/test_fep_paired_repair.py",
    "tests/test_fep_repair_execution.py",
)

SCIENTIFIC_INPUT_FILES = (
    "state/ptp1b-risk-action-plan-v1.json",
    "state/ptp1b-risk-action-stage-decision-v1.json",
    "state/ptp1b-repair-mechanism-audit-v1.json",
    "state/ptp1b-charge-repeatability-audit-v1.json",
    "state/ptp1b-paired-repair-preoutcome-supersession-v1.json",
    "state/openfe-wsl-environment-v1.json",
    "state/ptp1b-paired-repair-protocol-v1.json",
    "state/ptp1b-paired-repair-task-manifest-v1.json",
    "state/ptp1b-paired-repair-pilot-manifest-v1.json",
    "external/runs/ptp1b-paired-repair-v1/inputs/profile-manifest.json",
    "external/runs/ptp1b-paired-repair-v1/inputs/transformations/control/complex_20669_23466.json",
    "external/runs/ptp1b-paired-repair-v1/inputs/transformations/control/solvent_20669_23466.json",
    "external/runs/ptp1b-paired-repair-v1/inputs/transformations/control/complex_23466_23330.json",
    "external/runs/ptp1b-paired-repair-v1/inputs/transformations/control/solvent_23466_23330.json",
    "external/runs/ptp1b-paired-repair-v1/inputs/transformations/control/complex_23473_23466.json",
    "external/runs/ptp1b-paired-repair-v1/inputs/transformations/control/solvent_23473_23466.json",
    "external/runs/ptp1b-paired-repair-v1/inputs/transformations/repair/complex_20669_23466.json",
    "external/runs/ptp1b-paired-repair-v1/inputs/transformations/repair/solvent_20669_23466.json",
    "external/runs/ptp1b-paired-repair-v1/inputs/transformations/repair/complex_23466_23330.json",
    "external/runs/ptp1b-paired-repair-v1/inputs/transformations/repair/solvent_23466_23330.json",
    "external/runs/ptp1b-paired-repair-v1/inputs/transformations/repair/complex_23473_23466.json",
    "external/runs/ptp1b-paired-repair-v1/inputs/transformations/repair/solvent_23473_23466.json",
    "external/runs/ptp1b-paired-repair-v1/inputs/transformations/pilot_control/complex_20669_23466.json",
    "external/runs/ptp1b-paired-repair-v1/inputs/transformations/pilot_control/solvent_20669_23466.json",
    "external/runs/ptp1b-paired-repair-v1/inputs/transformations/pilot_control/complex_23466_23330.json",
    "external/runs/ptp1b-paired-repair-v1/inputs/transformations/pilot_control/solvent_23466_23330.json",
    "external/runs/ptp1b-paired-repair-v1/inputs/transformations/pilot_control/complex_23473_23466.json",
    "external/runs/ptp1b-paired-repair-v1/inputs/transformations/pilot_control/solvent_23473_23466.json",
    "external/runs/ptp1b-paired-repair-v1/inputs/transformations/pilot_repair/complex_20669_23466.json",
    "external/runs/ptp1b-paired-repair-v1/inputs/transformations/pilot_repair/solvent_20669_23466.json",
    "external/runs/ptp1b-paired-repair-v1/inputs/transformations/pilot_repair/complex_23466_23330.json",
    "external/runs/ptp1b-paired-repair-v1/inputs/transformations/pilot_repair/solvent_23466_23330.json",
    "external/runs/ptp1b-paired-repair-v1/inputs/transformations/pilot_repair/complex_23473_23466.json",
    "external/runs/ptp1b-paired-repair-v1/inputs/transformations/pilot_repair/solvent_23473_23466.json",
)


def main() -> None:
    args = _parse_args()
    payload = build_repair_implementation_lock(
        root=ROOT,
        pilot_manifest_path=args.pilot_manifest,
        implementation_files=IMPLEMENTATION_FILES,
        scientific_input_files=SCIENTIFIC_INPUT_FILES,
        snapshot_path=args.snapshot,
        pilot_output_path=args.pilot_output,
    )
    sha256 = write_json_artifact(args.output, payload)
    print(
        json.dumps(
            {
                "status": "PTP1B_PAIRED_REPAIR_IMPLEMENTATION_LOCKED_PRE_OUTCOME",
                "implementation_file_count": len(payload["implementation_sha256"]),
                "scientific_input_file_count": len(payload["scientific_input_sha256"]),
                "snapshot_sha256": payload["snapshot"]["sha256"],
                "lock_sha256": sha256,
                "output": str(args.output),
            },
            indent=2,
        )
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--pilot-manifest",
        type=Path,
        default=ROOT / "state/ptp1b-paired-repair-pilot-manifest-v1.json",
    )
    parser.add_argument(
        "--snapshot",
        type=Path,
        default=ROOT / "state/ptp1b-paired-repair-implementation-snapshot-v1.zip",
    )
    parser.add_argument(
        "--pilot-output",
        type=Path,
        default=ROOT / "external/runs/ptp1b-paired-repair-v1/pilot",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "state/ptp1b-paired-repair-implementation-lock-v1.json",
    )
    return parser.parse_args()


if __name__ == "__main__":
    main()
