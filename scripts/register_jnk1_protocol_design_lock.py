"""Freeze the JNK1 paired-repair design before charges or profile construction."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from loop_harness.v2.fep_untouched_protocol import (  # noqa: E402
    build_untouched_protocol_design_lock,
    write_untouched_protocol_design_lock,
)


IMPLEMENTATION_FILES = (
    "src/loop_harness/v2/fep_untouched_protocol.py",
    "scripts/wsl/audit_jnk1_charge_repeatability.py",
    "scripts/wsl/build_jnk1_paired_repair_inputs.py",
    "scripts/register_jnk1_protocol_design_lock.py",
    "scripts/register_jnk1_paired_repair_protocol.py",
    "scripts/build_jnk1_paired_repair_task_manifest.py",
    "tests/test_fep_untouched_protocol.py",
)


def main() -> None:
    args = _parse_args()
    payload = build_untouched_protocol_design_lock(
        root=ROOT,
        qualification_lock_path=args.qualification_lock,
        qualification_path=args.qualification,
        confirmation_contract_path=args.contract,
        input_audit_path=args.input_audit,
        charge_audit_output_path=args.charge_audit_output,
        profile_output_path=args.profile_output,
        run_output_path=args.run_output,
        implementation_files=IMPLEMENTATION_FILES,
        snapshot_path=args.snapshot,
    )
    sha256 = write_untouched_protocol_design_lock(args.output, payload)
    print(
        json.dumps(
            {
                "status": "JNK1_PROTOCOL_DESIGN_LOCKED_PRE_CHARGE_AND_PRE_PROFILE",
                "selected_edge_count": len(payload["selected_edge_ids"]),
                "expected_full_task_count": payload["profile_design"][
                    "expected_full_task_count"
                ],
                "design_lock_sha256": sha256,
                "snapshot_sha256": payload["implementation_snapshot"]["sha256"],
                "simulation_execution_authority_granted": False,
            },
            indent=2,
        )
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--qualification-lock",
        type=Path,
        default=ROOT / "state" / "jnk1-untouched-qualification-lock-v1.json",
    )
    parser.add_argument(
        "--qualification",
        type=Path,
        default=ROOT / "state" / "jnk1-untouched-target-qualification-v1.json",
    )
    parser.add_argument(
        "--contract",
        type=Path,
        default=ROOT / "state" / "ptp1b-untouched-confirmation-contract-v1.json",
    )
    parser.add_argument(
        "--input-audit",
        type=Path,
        default=ROOT / "state" / "jnk1-untouched-input-audit-v1.json",
    )
    parser.add_argument(
        "--charge-audit-output",
        type=Path,
        default=ROOT / "state" / "jnk1-charge-repeatability-audit-v1.json",
    )
    parser.add_argument(
        "--profile-output",
        type=Path,
        default=ROOT / "external" / "profiles" / "jnk1-paired-repair-v1",
    )
    parser.add_argument(
        "--run-output",
        type=Path,
        default=ROOT / "external" / "runs" / "jnk1-paired-repair-v1",
    )
    parser.add_argument(
        "--snapshot",
        type=Path,
        default=ROOT / "state" / "jnk1-protocol-design-snapshot-v1.zip",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "state" / "jnk1-protocol-design-lock-v1.json",
    )
    return parser.parse_args()


if __name__ == "__main__":
    main()
