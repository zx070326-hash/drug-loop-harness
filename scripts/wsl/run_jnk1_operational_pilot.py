"""Run the exact authorized JNK1 pilot serially and stop on first failure."""

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
    from loop_harness.v2.fep_untouched_pilot import (
        verify_untouched_pilot_authorization,
    )

    plan, _, authorization = verify_untouched_pilot_authorization(
        root=root,
        plan_path=args.pilot_plan,
        lock_path=args.pilot_lock,
        authorization_path=args.authorization,
        upstream_decision_path=args.upstream_decision,
    )
    task_by_id = {task["task_id"]: task for task in plan["tasks"]}
    runner = Path(__file__).with_name("run_jnk1_pilot_task.py")
    for task_id in authorization["authorized_task_ids_in_order"]:
        if task_id not in task_by_id:
            raise ValueError("authorized JNK1 task is absent from pilot plan")
        completed = subprocess.run(
            [
                sys.executable,
                str(runner),
                "--project-root",
                str(root),
                "--pilot-plan",
                str(args.pilot_plan),
                "--pilot-lock",
                str(args.pilot_lock),
                "--authorization",
                str(args.authorization),
                "--upstream-decision",
                str(args.upstream_decision),
                "--task-id",
                task_id,
                "--output-root",
                str(args.output_root),
            ],
            check=False,
        )
        if completed.returncode != 0:
            raise SystemExit(completed.returncode)
    print(
        json.dumps(
            {
                "status": "JNK1_OPERATIONAL_PILOT_ALL_TASKS_TERMINAL",
                "task_count": 4,
                "scientific_evaluation_performed": False,
            },
            indent=2,
        )
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--pilot-plan", type=Path, required=True)
    parser.add_argument("--pilot-lock", type=Path, required=True)
    parser.add_argument("--authorization", type=Path, required=True)
    parser.add_argument("--upstream-decision", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    return parser.parse_args()


if __name__ == "__main__":
    main()
