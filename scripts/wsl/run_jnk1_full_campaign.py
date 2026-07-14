"""Run nine JNK1 batches through metric-blind commit gates."""

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
    from loop_harness.v2.fep_untouched_full_batch import (
        audit_untouched_full_batch_commit,
        write_untouched_full_batch_artifact,
    )
    from loop_harness.v2.fep_untouched_full_execution import (
        project_untouched_full_execution_state,
        verify_untouched_full_execution_authorization,
    )

    authorization = json.loads(args.authorization.read_text(encoding="utf-8-sig"))
    verify_untouched_full_execution_authorization(root, authorization)
    state = project_untouched_full_execution_state(
        root=root,
        authorization_path=args.authorization,
        output_root=args.output_root,
    )
    if state["succeeded_task_count"] != 0 or state["start_count"] != 0:
        raise ValueError("automatic JNK1 campaign resume is forbidden")
    for index in range(9):
        commit_path = root / f"state/jnk1-full-batch-{index}-commit-v1.json"
        if commit_path.exists():
            raise ValueError("JNK1 campaign found a pre-existing batch commit")
        completed = subprocess.run(
            [
                sys.executable,
                str(root / "scripts/wsl/run_jnk1_full_batches.py"),
                "--project-root",
                str(root),
                "--authorization",
                str(args.authorization),
                "--output-root",
                str(args.output_root),
            ],
            cwd=root,
            check=False,
            shell=False,
        )
        if completed.returncode != 0:
            raise SystemExit(completed.returncode)
        commit = audit_untouched_full_batch_commit(
            root=root,
            authorization_path=args.authorization,
            output_root=args.output_root,
            batch_index=index,
        )
        write_untouched_full_batch_artifact(commit_path, commit)
        if commit["commit_accepted"] is not True:
            raise SystemExit(1)
    final = project_untouched_full_execution_state(
        root=root,
        authorization_path=args.authorization,
        output_root=args.output_root,
    )
    if final["status"] != "COMPLETE_READY_FOR_PAIRED_EVALUATION":
        raise ValueError("JNK1 campaign did not reach the complete-cohort boundary")
    completed = subprocess.run(
        [sys.executable, str(root / "scripts/finalize_jnk1_full_execution.py")],
        cwd=root,
        check=False,
        shell=False,
    )
    if completed.returncode != 0:
        raise SystemExit(completed.returncode)
    print(
        json.dumps(
            {
                "status": "JNK1_FULL_CAMPAIGN_COMPLETE",
                "succeeded_task_count": final["succeeded_task_count"],
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
    parser.add_argument("--authorization", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    return parser.parse_args()


if __name__ == "__main__":
    main()
