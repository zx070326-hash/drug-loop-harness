from __future__ import annotations

import shutil
import subprocess
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from typing import Any, Sequence


KNOWN_DOCKING_ENGINES = ("vina", "smina", "qvina2", "gnina")
DOCKING_ENGINE_CAPABILITY_PROBE_SCHEMA = "docking_engine_capability_probe.v1"


@dataclass(frozen=True)
class DockingEngineCapabilitySpec:
    """Fixed no-input version command for one local docking engine."""

    name: str
    command: tuple[str, ...]
    timeout_seconds: float = 10.0


@dataclass(frozen=True)
class DockingEngineCapability:
    name: str
    command: tuple[str, ...]
    resolved_executable: str | None
    status: str
    return_code: int | None
    reported_version: str | None
    stderr_summary: str | None


@dataclass(frozen=True)
class DockingEngineCapabilityProbeResult:
    schema_version: str
    probed_at: str
    capabilities: tuple[DockingEngineCapability, ...]
    available_engine_count: int
    probe_status: str
    docking_execution_permitted: bool
    autonomous_execution_permitted: bool
    scientific_claim_upgrade_permitted: bool
    summary_line: str

    def to_dict(self) -> dict[str, Any]:
        return _json_ready(asdict(self))


class DockingEngineCapabilityProbe:
    """Probe only local engine availability; never pass scientific inputs."""

    def probe(
        self,
        specs: Sequence[DockingEngineCapabilitySpec],
        *,
        probed_at: str | None = None,
    ) -> DockingEngineCapabilityProbeResult:
        if not specs:
            raise ValueError("At least one docking-engine capability spec is required.")
        timestamp = probed_at or datetime.now(timezone.utc).isoformat()
        capabilities = tuple(self._probe_spec(spec) for spec in specs)
        available_count = sum(item.status == "available" for item in capabilities)
        result = DockingEngineCapabilityProbeResult(
            schema_version=DOCKING_ENGINE_CAPABILITY_PROBE_SCHEMA,
            probed_at=timestamp,
            capabilities=capabilities,
            available_engine_count=available_count,
            probe_status="COMPLETE",
            docking_execution_permitted=False,
            autonomous_execution_permitted=False,
            scientific_claim_upgrade_permitted=False,
            summary_line="",
        )
        return replace(
            result,
            summary_line=(
                "Docking engine capability probe: "
                f"available={available_count}/{len(capabilities)}; "
                "docking_execution=false; scientific_claim_upgrade=false."
            ),
        )

    def _probe_spec(
        self,
        spec: DockingEngineCapabilitySpec,
    ) -> DockingEngineCapability:
        if not spec.name:
            raise ValueError("Docking engine name must not be empty.")
        if not spec.command:
            raise ValueError("Docking engine command must not be empty.")
        if spec.timeout_seconds <= 0:
            raise ValueError("Docking engine timeout_seconds must be positive.")
        executable = shutil.which(spec.command[0])
        if executable is None:
            return DockingEngineCapability(
                name=spec.name,
                command=spec.command,
                resolved_executable=None,
                status="unavailable",
                return_code=None,
                reported_version=None,
                stderr_summary=None,
            )
        command = (executable, *spec.command[1:])
        try:
            completed = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
                timeout=spec.timeout_seconds,
            )
        except subprocess.TimeoutExpired:
            return DockingEngineCapability(
                name=spec.name,
                command=spec.command,
                resolved_executable=executable,
                status="timeout",
                return_code=None,
                reported_version=None,
                stderr_summary=None,
            )
        except OSError as exc:
            return DockingEngineCapability(
                name=spec.name,
                command=spec.command,
                resolved_executable=executable,
                status="probe_os_error",
                return_code=None,
                reported_version=None,
                stderr_summary=exc.__class__.__name__,
            )
        if completed.returncode != 0:
            return DockingEngineCapability(
                name=spec.name,
                command=spec.command,
                resolved_executable=executable,
                status="version_command_failed",
                return_code=completed.returncode,
                reported_version=None,
                stderr_summary=_summarize(completed.stderr),
            )
        reported = _summarize(completed.stdout) or _summarize(completed.stderr)
        return DockingEngineCapability(
            name=spec.name,
            command=spec.command,
            resolved_executable=executable,
            status="available",
            return_code=completed.returncode,
            reported_version=reported,
            stderr_summary=_summarize(completed.stderr),
        )


def default_docking_engine_specs(
    *,
    engines: Sequence[str] = KNOWN_DOCKING_ENGINES,
    timeout_seconds: float = 10.0,
) -> tuple[DockingEngineCapabilitySpec, ...]:
    return tuple(
        DockingEngineCapabilitySpec(
            name=engine,
            command=(engine, "--version"),
            timeout_seconds=timeout_seconds,
        )
        for engine in engines
    )


def _summarize(value: str, *, limit: int = 500) -> str | None:
    normalized = " ".join(value.split())
    if not normalized:
        return None
    return normalized[:limit]


def _json_ready(value: Any) -> Any:
    if isinstance(value, tuple):
        return [_json_ready(item) for item in value]
    if isinstance(value, list):
        return [_json_ready(item) for item in value]
    if isinstance(value, dict):
        return {key: _json_ready(item) for key, item in value.items()}
    return value
