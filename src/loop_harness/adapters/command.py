from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from ..benchmark import DEFAULT_ACTION_ORACLE_MAP
from ..contracts import ActionKind, ActionRecord, LoopState, Observation
from ..driver import ActionExecutor, ExecutorObservation


@dataclass(frozen=True)
class LocalCommandSpec:
    """Fixed local command used as a tool-like executor boundary."""

    action_kind: ActionKind
    command: tuple[str, ...]
    oracle_name: str | None = None
    cwd: str | Path | None = None
    timeout_seconds: float = 30.0


class LocalCommandExecutor(ActionExecutor):
    """Run fixed local commands that emit normalized observation JSON.

    This adapter is intentionally narrow. It does not accept arbitrary shell
    strings, and it treats malformed tool output as an executor result rather
    than scientific evidence.
    """

    def __init__(
        self,
        specs: Mapping[ActionKind, LocalCommandSpec],
        *,
        action_oracle_map: Mapping[ActionKind, str] | None = None,
    ) -> None:
        self.specs = dict(specs)
        self.action_oracle_map = dict(action_oracle_map or DEFAULT_ACTION_ORACLE_MAP)

    def execute(
        self,
        action: ActionRecord,
        state: LoopState,
    ) -> tuple[ExecutorObservation, ...]:
        spec = self.specs.get(action.kind)
        oracle_name = (
            spec.oracle_name
            if spec is not None and spec.oracle_name is not None
            else self.action_oracle_map.get(action.kind)
        )
        if spec is None:
            return tuple(
                _result(
                    subject_id,
                    oracle_name,
                    "missing",
                    "no_command_spec",
                )
                for subject_id in action.subject_ids
            )

        results: list[ExecutorObservation] = []
        for subject_id in action.subject_ids:
            results.append(self._execute_subject(action, spec, subject_id, oracle_name))
        return tuple(results)

    def _execute_subject(
        self,
        action: ActionRecord,
        spec: LocalCommandSpec,
        subject_id: str,
        oracle_name: str | None,
    ) -> ExecutorObservation:
        command = tuple(
            _format_arg(arg, action=action, subject_id=subject_id, oracle_name=oracle_name)
            for arg in spec.command
        )
        try:
            completed = subprocess.run(
                command,
                cwd=str(spec.cwd) if spec.cwd is not None else None,
                check=False,
                capture_output=True,
                text=True,
                timeout=spec.timeout_seconds,
            )
        except subprocess.TimeoutExpired:
            return _result(subject_id, oracle_name, "timeout", "command_timeout")
        except OSError as exc:
            return _result(
                subject_id,
                oracle_name,
                "error",
                f"command_os_error:{exc.__class__.__name__}",
            )

        if completed.returncode != 0:
            return _result(
                subject_id,
                oracle_name,
                "error",
                f"command_failed:{completed.returncode}",
            )

        return _parse_observation_stdout(
            completed.stdout,
            subject_id=subject_id,
            oracle_name=oracle_name,
        )


def _parse_observation_stdout(
    stdout: str,
    *,
    subject_id: str,
    oracle_name: str | None,
) -> ExecutorObservation:
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError:
        return _result(subject_id, oracle_name, "invalid", "malformed_json_output")

    if not isinstance(payload, Mapping):
        return _result(subject_id, oracle_name, "invalid", "output_not_object")

    missing = tuple(
        key for key in ("id", "subject_id", "oracle_name", "metrics") if key not in payload
    )
    if missing:
        return _result(
            subject_id,
            oracle_name,
            "invalid",
            f"missing_output_fields:{','.join(missing)}",
        )
    metrics = payload["metrics"]
    provenance = payload.get("provenance", {})
    if not isinstance(metrics, Mapping):
        return _result(subject_id, oracle_name, "invalid", "metrics_not_object")
    if not isinstance(provenance, Mapping):
        return _result(subject_id, oracle_name, "invalid", "provenance_not_object")

    observation = Observation(
        id=str(payload["id"]),
        subject_id=str(payload["subject_id"]),
        oracle_name=str(payload["oracle_name"]),
        metrics=dict(metrics),
        uncertainty=(
            float(payload["uncertainty"])
            if payload.get("uncertainty") is not None
            else None
        ),
        cost_spent=float(payload.get("cost_spent", 0.0)),
        provenance=dict(provenance),
        notes=str(payload.get("notes", "")),
    )
    return ExecutorObservation(
        subject_id=subject_id,
        oracle_name=oracle_name,
        status="observed",
        reason="command_observation_emitted",
        observation=observation,
    )


def _format_arg(
    arg: str,
    *,
    action: ActionRecord,
    subject_id: str,
    oracle_name: str | None,
) -> str:
    values: dict[str, Any] = {
        "action_id": action.id,
        "action_kind": action.kind.value,
        "oracle_name": oracle_name or "",
        "subject_id": subject_id,
    }
    for key, value in action.parameters.items():
        if isinstance(value, str | int | float | bool):
            values[key] = value
    return arg.format(**values)


def _result(
    subject_id: str,
    oracle_name: str | None,
    status: str,
    reason: str,
) -> ExecutorObservation:
    return ExecutorObservation(
        subject_id=subject_id,
        oracle_name=oracle_name,
        status=status,
        reason=reason,
    )
