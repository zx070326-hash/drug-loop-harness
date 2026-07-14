"""Continue frozen PTP1B batches through metric-blind commit gates."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


def main() -> None:
    args = _parse_args()
    root = args.project_root.resolve()
    sys.path.insert(0, str(root / "src"))
    from loop_harness.v2.fep_repair_batch_commit import (
        audit_full_repair_batch_commit,
        validate_full_repair_batch_commit_audit,
        write_full_repair_batch_commit_audit,
    )
    from loop_harness.v2.fep_repair_campaign import verify_fep_repair_campaign_lock
    from loop_harness.v2.fep_repair_full_execution import (
        project_full_repair_execution_state,
    )

    campaign_lock = json.loads(args.campaign_lock.read_text(encoding="utf-8"))
    verify_fep_repair_campaign_lock(root, campaign_lock)
    initial_commit_path = root / "state/ptp1b-full-repair-batch-0-commit-v1.json"
    initial_commit = json.loads(initial_commit_path.read_text(encoding="utf-8"))
    validate_full_repair_batch_commit_audit(initial_commit)
    fresh_initial = audit_full_repair_batch_commit(
        root=root,
        authorization_path=args.authorization,
        output_root=args.output_root,
        batch_index=0,
    )
    if initial_commit != fresh_initial or initial_commit.get("commit_accepted") is not True:
        raise ValueError("batch 0 lacks a current accepted commit")
    state = project_full_repair_execution_state(
        root=root,
        authorization_path=args.authorization,
        output_root=args.output_root,
    )
    if state["succeeded_task_count"] != 4 or state["next_batch_id"] is None:
        raise ValueError("campaign must start exactly at the batch 0 commit boundary")
    for batch_index in range(1, 9):
        later_commit = root / f"state/ptp1b-full-repair-batch-{batch_index}-commit-v1.json"
        if later_commit.exists():
            raise ValueError("automatic campaign resume is forbidden")
        completed = subprocess.run(
            [
                sys.executable,
                str(root / "scripts/wsl/run_ptp1b_full_repair_batches.py"),
                "--project-root",
                str(root),
                "--authorization",
                str(args.authorization),
                "--output-root",
                str(args.output_root),
                "--max-batches",
                "1",
            ],
            cwd=root,
            check=False,
            shell=False,
        )
        if completed.returncode != 0:
            raise SystemExit(completed.returncode)
        commit = audit_full_repair_batch_commit(
            root=root,
            authorization_path=args.authorization,
            output_root=args.output_root,
            batch_index=batch_index,
        )
        commit_path = root / f"state/ptp1b-full-repair-batch-{batch_index}-commit-v1.json"
        write_full_repair_batch_commit_audit(commit_path, commit)
        if commit["commit_accepted"] is not True:
            raise SystemExit(1)

    state = project_full_repair_execution_state(
        root=root,
        authorization_path=args.authorization,
        output_root=args.output_root,
    )
    if state["status"] != "COMPLETE_READY_FOR_PAIRED_EVALUATION":
        raise ValueError("campaign completed batches without reaching frozen release state")
    completed = subprocess.run(
        [sys.executable, str(root / "scripts/finalize_ptp1b_full_repair_stage.py")],
        cwd=root,
        check=False,
        shell=False,
    )
    if completed.returncode != 0:
        raise SystemExit(completed.returncode)
    print(
        json.dumps(
            {
                "status": "PTP1B_FULL_REPAIR_CAMPAIGN_COMPLETE",
                "succeeded_task_count": state["succeeded_task_count"],
                "paired_evaluator_released_after_complete_cohort": True,
                "controller_scientific_metrics_accessed": False,
                "scientific_claim_upgrade_permitted": False,
            },
            indent=2,
        )
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--campaign-lock", type=Path, required=True)
    parser.add_argument("--authorization", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    return parser.parse_args()


if __name__ == "__main__":
    main()
