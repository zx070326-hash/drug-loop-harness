"""Run one metric-blind frozen JNK1 batch in authorized order."""

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
    from loop_harness.v2.fep_untouched_full_execution import (
        project_untouched_full_execution_state,
        verify_untouched_full_execution_authorization,
    )

    authorization = json.loads(args.authorization.read_text(encoding="utf-8-sig"))
    verify_untouched_full_execution_authorization(root, authorization)
    if authorization["execution_policy"]["maximum_batches_per_invocation"] != 1:
        raise ValueError("JNK1 batch runner requires a one-batch authorization bound")
    initial = project_untouched_full_execution_state(
        root=root,
        authorization_path=args.authorization,
        output_root=args.output_root,
    )
    if initial["status"] != "READY_FOR_NEXT_FROZEN_TASK":
        print(json.dumps(initial, indent=2))
        return
    active_batch = initial["next_batch_id"]
    while True:
        state = project_untouched_full_execution_state(
            root=root,
            authorization_path=args.authorization,
            output_root=args.output_root,
        )
        if state["status"] != "READY_FOR_NEXT_FROZEN_TASK":
            break
        if state["next_batch_id"] != active_batch:
            break
        completed = subprocess.run(
            [
                sys.executable,
                str(root / "scripts/wsl/run_openfe_jnk1_full_task.py"),
                "--project-root",
                str(root),
                "--authorization",
                str(args.authorization),
                "--task-id",
                state["next_task_id"],
                "--output-root",
                str(args.output_root),
            ],
            cwd=root,
            check=False,
            shell=False,
        )
        if completed.returncode != 0:
            break
    final = project_untouched_full_execution_state(
        root=root,
        authorization_path=args.authorization,
        output_root=args.output_root,
    )
    print(
        json.dumps(
            {
                "status": final["status"],
                "completed_batch_id": (
                    active_batch if final["next_batch_id"] != active_batch else None
                ),
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
    return parser.parse_args()


if __name__ == "__main__":
    main()
