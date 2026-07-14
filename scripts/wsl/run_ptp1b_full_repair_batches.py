"""Run metric-blind frozen PTP1B repair batches in authorized order."""

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
    from loop_harness.v2.fep_repair_full_execution import (
        project_full_repair_execution_state,
        verify_full_repair_execution_authorization,
    )

    authorization = json.loads(args.authorization.read_text(encoding="utf-8"))
    verify_full_repair_execution_authorization(root, authorization)
    maximum = int(authorization["execution_policy"]["maximum_batches_per_invocation"])
    if args.max_batches < 1 or args.max_batches > maximum:
        raise ValueError(f"max-batches must be between 1 and {maximum}")
    runner = root / "scripts/wsl/run_openfe_full_repair_task.py"
    initial = project_full_repair_execution_state(
        root=root,
        authorization_path=args.authorization,
        output_root=args.output_root,
    )
    if initial["status"] != "READY_FOR_NEXT_FROZEN_TASK":
        print(json.dumps(initial, indent=2))
        return

    completed_batches = 0
    active_batch = initial["next_batch_id"]
    while completed_batches < args.max_batches:
        state = project_full_repair_execution_state(
            root=root,
            authorization_path=args.authorization,
            output_root=args.output_root,
        )
        if state["status"] != "READY_FOR_NEXT_FROZEN_TASK":
            print(json.dumps(state, indent=2))
            break
        if state["next_batch_id"] != active_batch:
            completed_batches += 1
            if completed_batches >= args.max_batches:
                break
            active_batch = state["next_batch_id"]
        command = [
            sys.executable,
            str(runner),
            "--project-root",
            str(root),
            "--authorization",
            str(args.authorization),
            "--task-id",
            state["next_task_id"],
            "--output-root",
            str(args.output_root),
        ]
        completed = subprocess.run(command, cwd=root, check=False, shell=False)
        if completed.returncode != 0:
            break

    final = project_full_repair_execution_state(
        root=root,
        authorization_path=args.authorization,
        output_root=args.output_root,
    )
    print(
        json.dumps(
            {
                "status": final["status"],
                "completed_batches_this_invocation": completed_batches,
                "succeeded_task_count": final["succeeded_task_count"],
                "next_task_id": final["next_task_id"],
                "next_batch_id": final["next_batch_id"],
                "scientific_metrics_accessed": False,
            },
            indent=2,
        )
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--authorization", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--max-batches", type=int, default=1)
    return parser.parse_args()


if __name__ == "__main__":
    main()
