"""Audit WSL process liveness without reading full-repair scientific metrics."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


def main() -> None:
    args = _parse_args()
    root = args.project_root.resolve()
    sys.path.insert(0, str(root / "src"))
    from loop_harness.v2.fep_repair_full_execution import (
        project_full_repair_execution_state,
        write_full_repair_artifact,
    )

    state = project_full_repair_execution_state(
        root=root,
        authorization_path=args.authorization,
        output_root=args.output_root,
    )
    task_id = state.get("unresolved_started_task_id")
    pid = state.get("unresolved_runner_pid")
    if task_id is None:
        liveness = {
            "status": "NO_UNRESOLVED_STARTED_TASK",
            "runner_pid_alive": None,
            "runner_command_identity_matches": None,
            "latest_task_artifact_mtime": None,
        }
    else:
        process = Path(f"/proc/{pid}")
        command = ""
        alive = process.is_dir()
        if alive:
            command = (process / "cmdline").read_bytes().replace(b"\x00", b" ").decode(
                "utf-8", errors="replace"
            )
        command_matches = (
            alive
            and "run_openfe_full_repair_task.py" in command
            and task_id in command
        )
        task_root = args.output_root.resolve() / "tasks" / task_id
        artifacts = [path for path in task_root.rglob("*") if path.is_file()]
        latest_mtime = max((path.stat().st_mtime for path in artifacts), default=None)
        liveness = {
            "status": (
                "RUNNER_LIVE_AWAIT_TERMINAL_RECEIPT"
                if command_matches
                else "RUNNER_NOT_LIVE_RECOVERY_AUDIT_REQUIRED"
            ),
            "runner_pid_alive": alive,
            "runner_command_identity_matches": command_matches,
            "runner_command": command if command_matches else None,
            "latest_task_artifact_mtime": (
                datetime.fromtimestamp(latest_mtime, timezone.utc).isoformat()
                if latest_mtime is not None
                else None
            ),
        }
    payload = {
        "schema_version": "fep_repair_full_liveness_audit.v1",
        "audited_at": datetime.now(timezone.utc).isoformat(),
        "execution_state_status": state["status"],
        "task_id": task_id,
        "runner_pid": pid,
        **liveness,
        "authority": {
            "continue_authority_granted": False,
            "recovery_authority_granted": False,
            "termination_authority_granted": False,
        },
        "scientific_controls": {
            "scientific_metric_files_read": False,
            "scientific_metrics_accessed": False,
            "outcome_dependent_decision_permitted": False,
            "scientific_claim_upgrade_permitted": False,
        },
    }
    sha256 = write_full_repair_artifact(
        args.output, payload, refuse_different=False
    )
    print(json.dumps({**payload, "audit_sha256": sha256}, indent=2))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--authorization", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


if __name__ == "__main__":
    main()
