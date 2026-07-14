"""Complete the gated v2 replication after named cache-builder processes exit.

This is a deterministic continuation runner, not a policy agent.  It waits for
the supplied builder PIDs, resumes only the frozen target caches, audits them,
finalizes every declared target-seed cell, and aggregates the complete matrix.
It fails closed after any nonzero stage and records every command receipt.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    args = _parse_args()
    workspace = Path(args.workspace).resolve()
    plan_path = _resolve(workspace, args.plan)
    plan = _read_json(plan_path)
    run_root = _resolve(workspace, args.run_root)
    run_root.mkdir(parents=True, exist_ok=True)
    receipt_path = run_root / "continuation_receipt.json"
    receipt: dict[str, Any] = {
        "schema_version": "multitarget_replication_continuation.v1",
        "started_at": _now(),
        "waited_builder_pids": list(args.wait_pid),
        "commands": [],
        "status": "RUNNING",
        "policy_ranking_permitted": False,
        "scientific_claim_upgrade_permitted": False,
    }
    _write_json(receipt_path, receipt)
    _wait_for_pids(args.wait_pid, args.poll_seconds)
    target_ids = [str(item["target_id"]) for item in plan["target_panel"]]
    seeds = [str(item) for item in plan["cohort_seeds"]]
    commands: list[tuple[str, list[str]]] = []
    for target_id in target_ids:
        target_cache_dir = f"external/cache_runs/{target_id.lower()}_multitarget_v2"
        commands.append((
            f"resume_{target_id}",
            ["scripts/build_multitarget_target_oracle_cache.py", "--target-id", target_id, "--run-dir", target_cache_dir, "--resume", "--parallel-workers", str(args.parallel_workers)],
        ))
    for target_id in target_ids:
        target_cache_dir = f"external/cache_runs/{target_id.lower()}_multitarget_v2"
        commands.append((
            f"repair_failed_batches_{target_id}",
            ["scripts/repair_multitarget_target_oracle_cache.py", "--run-dir", target_cache_dir, "--parallel-workers", str(args.parallel_workers)],
        ))
    for target_id in target_ids:
        target_cache_dir = f"external/cache_runs/{target_id.lower()}_multitarget_v2"
        commands.append((
            f"refresh_manifest_{target_id}",
            ["scripts/build_multitarget_target_oracle_cache.py", "--target-id", target_id, "--run-dir", target_cache_dir, "--resume", "--parallel-workers", str(args.parallel_workers)],
        ))
        commands.append((
            f"audit_{target_id}",
            ["scripts/audit_multitarget_target_oracle_cache.py", "--target-cache-manifest", f"{target_cache_dir}/target_oracle_cache_manifest.json", "--output", f"{target_cache_dir}/target_oracle_cache_audit.json"],
        ))
    for target_id in target_ids:
        for seed in seeds:
            commands.append((
                f"finalize_{target_id}_{seed}",
                ["scripts/finalize_multitarget_replication_cell.py", "--target-id", target_id, "--selection-seed", seed],
            ))
    commands.append(("aggregate", ["scripts/aggregate_multitarget_policy_replication.py"]))

    for ordinal, (name, command) in enumerate(commands, start=1):
        result = _run(workspace, run_root, ordinal, name, command)
        receipt["commands"].append(result)
        _write_json(receipt_path, receipt)
        if result["returncode"] != 0:
            receipt["status"] = "STOPPED_NONZERO_STAGE"
            receipt["stopped_at"] = _now()
            _write_json(receipt_path, receipt)
            raise SystemExit(result["returncode"])
    receipt["status"] = "PASS"
    receipt["completed_at"] = _now()
    _write_json(receipt_path, receipt)
    print(f"continuation PASS: commands={len(commands)}; receipt={receipt_path}")


def _wait_for_pids(pids: list[int], poll_seconds: float) -> None:
    pending = set(pids)
    while pending:
        pending = {pid for pid in pending if _pid_exists(pid)}
        if pending:
            time.sleep(poll_seconds)


def _pid_exists(pid: int) -> bool:
    if os.name == "nt":
        process_query_limited_information = 0x1000
        handle = ctypes.windll.kernel32.OpenProcess(
            process_query_limited_information,
            False,
            pid,
        )
        if not handle:
            return False
        ctypes.windll.kernel32.CloseHandle(handle)
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _run(workspace: Path, run_root: Path, ordinal: int, name: str, command: list[str]) -> dict[str, Any]:
    stdout_path = run_root / f"{ordinal:02d}_{name}.stdout.log"
    stderr_path = run_root / f"{ordinal:02d}_{name}.stderr.log"
    argv = [sys.executable, *command]
    completed = subprocess.run(argv, cwd=workspace, capture_output=True, text=True, check=False)
    stdout_path.write_text(completed.stdout, encoding="utf-8")
    stderr_path.write_text(completed.stderr, encoding="utf-8")
    return {
        "name": name,
        "argv": argv,
        "returncode": completed.returncode,
        "stdout_path": _relative(workspace, stdout_path),
        "stderr_path": _relative(workspace, stderr_path),
        "completed_at": _now(),
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", default=".")
    parser.add_argument("--plan", default="state/multitarget-policy-replication-plan.v2.json")
    parser.add_argument("--wait-pid", type=int, action="append", required=True)
    parser.add_argument("--poll-seconds", type=float, default=15.0)
    parser.add_argument("--parallel-workers", type=int, default=4)
    parser.add_argument("--run-root", default="state/multitarget-replication-v2-continuation")
    args = parser.parse_args()
    if args.poll_seconds <= 0 or args.parallel_workers <= 0:
        parser.error("--poll-seconds and --parallel-workers must be positive")
    return args


def _read_json(path: Path) -> Mapping[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _resolve(workspace: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else workspace / path


def _relative(workspace: Path, path: Path) -> str:
    return path.resolve().relative_to(workspace).as_posix()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


if __name__ == "__main__":
    main()
