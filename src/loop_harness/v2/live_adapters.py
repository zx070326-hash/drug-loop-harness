from __future__ import annotations

import json
import os
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Sequence

from ..artifact_hash import json_ready
from .contracts import (
    AcquisitionAction,
    ArtifactRef,
    DispatchReceipt,
    OracleJobReceipt,
    OracleJobSpec,
    OracleJobStatus,
    OracleResult,
    OracleResultStatus,
    OutcomeDirection,
    PreflightDecision,
    ScientificOutcome,
)
from .job_runtime import JobControlError
from .model_store import ArtifactIntegrityError, ContentAddressedArtifactStore
from .state import CampaignSnapshot


@dataclass(frozen=True)
class JsonCommandProfile:
    """Fixed executable profile for one local computational oracle."""

    adapter_id: str
    adapter_version: str
    command: tuple[str, ...]
    accounted_cost: float
    timeout_seconds: float = 120.0
    cwd: str | Path | None = None
    max_output_bytes: int = 4_000_000
    target_id: str | None = None
    endpoint_id: str | None = None
    profile_artifact: ArtifactRef | None = None
    calibration_artifact: ArtifactRef | None = None
    require_calibration: bool = False

    def __post_init__(self) -> None:
        if not self.adapter_id or not self.adapter_version or not self.command:
            raise ValueError("command profile identity and command are required")
        if any(not item for item in self.command):
            raise ValueError("command profile contains an empty argument")
        if self.accounted_cost < 0 or self.timeout_seconds <= 0:
            raise ValueError("command profile cost and timeout are invalid")
        if self.max_output_bytes <= 0:
            raise ValueError("max_output_bytes must be positive")
        if self.require_calibration and self.calibration_artifact is None:
            raise ValueError("require_calibration needs a calibration_artifact")


class JsonCommandLiveAdapter:
    """Run one fixed command profile and normalize stdout into `OracleResult`.

    Terminal receipts are also kept in a small operational index beside the
    content-addressed artifacts. The index is only a recovery pointer; raw
    outputs remain verified by their content hashes.
    """

    adapter_id: str
    adapter_version: str

    def __init__(
        self,
        profile: JsonCommandProfile,
        store: ContentAddressedArtifactStore,
    ) -> None:
        self.profile = profile
        self.store = store
        self.adapter_id = profile.adapter_id
        self.adapter_version = profile.adapter_version
        self._actions: dict[str, AcquisitionAction] = {}
        self._receipts: dict[str, OracleJobReceipt] = {}
        self._input_hashes: dict[str, str] = {}
        self._bindings: dict[str, dict[str, str | None]] = {}
        self._receipt_index_path = self.store.root / "_job_receipts.json"

    def prepare(self, action: AcquisitionAction, state: CampaignSnapshot) -> OracleJobSpec:
        if abs(self.profile.accounted_cost - action.expected_cost) > 1e-9:
            raise JobControlError("command_profile_cost_does_not_match_action")
        binding = self._binding_metadata(action, state)
        job_id = f"job_{self.adapter_id}_{action.action_id}"
        manifest = self.store.put_json(
            {
                "action_id": action.action_id,
                "candidate_id": action.candidate_id,
                "oracle_id": action.oracle_id,
                "endpoint_id": action.endpoint_id,
                "smiles": state.candidates[action.candidate_id].smiles,
                "profile_binding": binding,
            },
            producer_version=self.adapter_version,
        )
        self._actions[job_id] = action
        self._input_hashes[job_id] = manifest.sha256
        self._bindings[job_id] = binding
        return OracleJobSpec(
            job_id=job_id,
            action_id=action.action_id,
            adapter_id=self.adapter_id,
            adapter_version=self.adapter_version,
            input_manifest=manifest,
            reserved_cost=action.expected_cost,
            timeout_seconds=self.profile.timeout_seconds,
        )

    def preflight(self, job: OracleJobSpec, state: CampaignSnapshot) -> PreflightDecision:
        if "{input_path}" not in self.profile.command:
            return PreflightDecision(
                job_id=job.job_id,
                allowed=False,
                reason="command_profile_missing_input_path_placeholder",
            )
        try:
            action = self._actions[job.job_id]
            self._binding_metadata(action, state)
        except (KeyError, JobControlError, ArtifactIntegrityError) as exc:
            reason = str(exc) if isinstance(exc, JobControlError) else "command_job_action_missing"
            return PreflightDecision(job_id=job.job_id, allowed=False, reason=reason)
        return PreflightDecision(job_id=job.job_id, allowed=True, reason="command_profile_ready")

    def dispatch(self, job: OracleJobSpec) -> DispatchReceipt:
        persisted = self._load_persisted_receipt(job.job_id)
        if persisted is not None:
            return DispatchReceipt(
                job_id=job.job_id,
                external_run_id=f"local:{job.job_id}",
                started_at=persisted.started_at or "",
            )
        input_path = self.store.path_for(job.input_manifest)
        command = tuple(
            str(input_path) if argument == "{input_path}" else argument
            for argument in self.profile.command
        )
        try:
            completed = subprocess.run(
                command,
                cwd=str(self.profile.cwd) if self.profile.cwd is not None else None,
                capture_output=True,
                check=False,
                timeout=self.profile.timeout_seconds,
                shell=False,
            )
        except subprocess.TimeoutExpired as exc:
            stdout = _bytes(exc.stdout)
            stderr = _bytes(exc.stderr)
            self._receipts[job.job_id] = self._receipt(
                job,
                OracleJobStatus.TIMEOUT,
                stdout,
                stderr,
                "command_timeout",
            )
        except OSError as exc:
            self._receipts[job.job_id] = self._receipt(
                job,
                OracleJobStatus.FAILED,
                b"",
                str(exc).encode("utf-8"),
                f"command_os_error:{type(exc).__name__}",
            )
        else:
            stdout = completed.stdout
            stderr = completed.stderr
            status = (
                OracleJobStatus.SUCCEEDED
                if completed.returncode == 0
                else OracleJobStatus.FAILED
            )
            failure = None if completed.returncode == 0 else f"command_exit:{completed.returncode}"
            self._receipts[job.job_id] = self._receipt(
                job,
                status,
                stdout,
                stderr,
                failure,
            )
        return DispatchReceipt(
            job_id=job.job_id,
            external_run_id=f"local:{job.job_id}",
            started_at=self._receipts[job.job_id].started_at or "",
        )

    def poll(self, job_id: str) -> OracleJobReceipt:
        self._load_persisted_receipt(job_id)
        try:
            return self._receipts[job_id]
        except KeyError as exc:
            raise JobControlError("command_job_not_dispatched") from exc

    def normalize(self, receipt: OracleJobReceipt, action: AcquisitionAction) -> OracleResult:
        self._load_persisted_receipt(receipt.job_id)
        provenance = {
            "adapter": self.adapter_id,
            "adapter_version": self.adapter_version,
            "input_hash": self._input_hashes.get(receipt.job_id, action.action_id),
        }
        provenance.update(
            {
                key: value
                for key, value in self._bindings.get(receipt.job_id, {}).items()
                if value is not None
            }
        )
        if receipt.status is not OracleJobStatus.SUCCEEDED:
            return OracleResult(
                action_id=action.action_id,
                candidate_id=action.candidate_id,
                oracle_id=action.oracle_id,
                status=(
                    OracleResultStatus.TIMEOUT
                    if receipt.status is OracleJobStatus.TIMEOUT
                    else OracleResultStatus.FAILED
                ),
                actual_cost=receipt.actual_cost,
                provenance=provenance,
                failure_reason=receipt.failure_code or receipt.status.value,
            )
        raw = self._raw_stdout(receipt)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            return OracleResult(
                action_id=action.action_id,
                candidate_id=action.candidate_id,
                oracle_id=action.oracle_id,
                status=OracleResultStatus.FAILED,
                actual_cost=receipt.actual_cost,
                provenance=provenance,
                failure_reason="malformed_json_output",
            )
        if not isinstance(payload, dict) or not isinstance(payload.get("metrics"), dict):
            return OracleResult(
                action_id=action.action_id,
                candidate_id=action.candidate_id,
                oracle_id=action.oracle_id,
                status=OracleResultStatus.FAILED,
                actual_cost=receipt.actual_cost,
                provenance=provenance,
                failure_reason="output_metrics_missing_or_invalid",
            )
        if payload.get("candidate_id", action.candidate_id) != action.candidate_id:
            return self._normalization_failure(action, receipt, "output_candidate_mismatch")
        if payload.get("oracle_id", action.oracle_id) != action.oracle_id:
            return self._normalization_failure(action, receipt, "output_oracle_mismatch")
        provenance.update(
            {
                str(key): value
                for key, value in (payload.get("provenance") or {}).items()
                if key not in {"adapter", "adapter_version", "input_hash", "output_hash"}
            }
        )
        provenance["adapter"] = self.adapter_id
        provenance["adapter_version"] = self.adapter_version
        provenance["input_hash"] = self._input_hashes.get(receipt.job_id, action.action_id)
        provenance.update(
            {
                key: value
                for key, value in self._bindings.get(receipt.job_id, {}).items()
                if value is not None
            }
        )
        provenance["output_hash"] = receipt.raw_artifacts[0].sha256
        try:
            outcome = _outcome(payload.get("outcome"), action.endpoint_id)
        except ValueError:
            return self._normalization_failure(action, receipt, "outcome_schema_invalid")
        if outcome is not None and outcome.endpoint_id != action.endpoint_id:
            return self._normalization_failure(action, receipt, "outcome_endpoint_mismatch")
        return OracleResult(
            action_id=action.action_id,
            candidate_id=action.candidate_id,
            oracle_id=action.oracle_id,
            status=OracleResultStatus.SUCCESS,
            metrics=dict(payload["metrics"]),
            uncertainty=float(payload["uncertainty"]) if payload.get("uncertainty") is not None else None,
            actual_cost=receipt.actual_cost,
            provenance=provenance,
            outcome=outcome,
        )

    def _receipt(
        self,
        job: OracleJobSpec,
        status: OracleJobStatus,
        stdout: bytes,
        stderr: bytes,
        failure_code: str | None,
    ) -> OracleJobReceipt:
        if len(stdout) > self.profile.max_output_bytes:
            stdout = b""
            status = OracleJobStatus.FAILED
            failure_code = "command_output_too_large"
        stdout_ref = self.store.put_bytes(stdout, media_type="application/json")
        if stderr:
            stderr_ref = self.store.put_bytes(stderr, media_type="text/plain")
            raw_artifacts = (stdout_ref, stderr_ref)
        else:
            raw_artifacts = (stdout_ref,)
        timestamp = _timestamp()
        receipt = OracleJobReceipt(
            job_id=job.job_id,
            status=status,
            actual_cost=self.profile.accounted_cost,
            raw_artifacts=raw_artifacts,
            started_at=timestamp,
            completed_at=timestamp,
            failure_code=failure_code,
        )
        self._persist_receipt(receipt)
        return receipt

    def _raw_stdout(self, receipt: OracleJobReceipt) -> bytes:
        if not receipt.raw_artifacts:
            return b""
        return self.store.read_bytes(receipt.raw_artifacts[0])

    def _load_persisted_receipt(self, job_id: str) -> OracleJobReceipt | None:
        if job_id in self._receipts:
            return self._receipts[job_id]
        if not self._receipt_index_path.is_file():
            return None
        try:
            index = json.loads(self._receipt_index_path.read_text(encoding="utf-8"))
            entry = index.get(job_id)
            if not isinstance(entry, dict):
                return None
            receipt = _receipt_from_dict(entry["receipt"])
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise JobControlError("command_receipt_index_invalid") from exc
        self._receipts[job_id] = receipt
        input_hash = entry.get("input_hash")
        if isinstance(input_hash, str):
            self._input_hashes[job_id] = input_hash
        binding = entry.get("binding")
        if isinstance(binding, dict):
            self._bindings[job_id] = {
                str(key): value if isinstance(value, str) else None
                for key, value in binding.items()
            }
        return receipt

    def _persist_receipt(self, receipt: OracleJobReceipt) -> None:
        index: dict = {}
        if self._receipt_index_path.is_file():
            try:
                index = json.loads(self._receipt_index_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise JobControlError("command_receipt_index_invalid") from exc
        index[receipt.job_id] = {
            "receipt": asdict(receipt),
            "input_hash": self._input_hashes.get(receipt.job_id),
            "binding": self._bindings.get(receipt.job_id, {}),
        }
        self._receipt_index_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._receipt_index_path.with_name(
            f".{self._receipt_index_path.name}.{os.getpid()}.tmp"
        )
        temporary.write_text(
            json.dumps(json_ready(index), ensure_ascii=True, sort_keys=True),
            encoding="utf-8",
        )
        os.replace(temporary, self._receipt_index_path)

    def _binding_metadata(
        self,
        action: AcquisitionAction,
        state: CampaignSnapshot,
    ) -> dict[str, str | None]:
        if self.profile.target_id is not None and state.spec.target_id != self.profile.target_id:
            raise JobControlError("command_profile_target_mismatch")
        if self.profile.endpoint_id is not None and action.endpoint_id != self.profile.endpoint_id:
            raise JobControlError("command_profile_endpoint_mismatch")
        if self.profile.profile_artifact is not None:
            self.store.read_bytes(self.profile.profile_artifact)
        if self.profile.require_calibration and self.profile.calibration_artifact is None:
            raise JobControlError("command_profile_calibration_required")
        if self.profile.calibration_artifact is not None:
            self.store.read_bytes(self.profile.calibration_artifact)
        return {
            "target_id": self.profile.target_id,
            "endpoint_id": self.profile.endpoint_id,
            "profile_artifact_sha256": (
                self.profile.profile_artifact.sha256
                if self.profile.profile_artifact is not None
                else None
            ),
            "calibration_artifact_sha256": (
                self.profile.calibration_artifact.sha256
                if self.profile.calibration_artifact is not None
                else None
            ),
        }

    def _normalization_failure(
        self,
        action: AcquisitionAction,
        receipt: OracleJobReceipt,
        reason: str,
    ) -> OracleResult:
        provenance = {
            "adapter": self.adapter_id,
            "adapter_version": self.adapter_version,
        }
        provenance.update(
            {
                key: value
                for key, value in self._bindings.get(receipt.job_id, {}).items()
                if value is not None
            }
        )
        return OracleResult(
            action_id=action.action_id,
            candidate_id=action.candidate_id,
            oracle_id=action.oracle_id,
            status=OracleResultStatus.FAILED,
            actual_cost=receipt.actual_cost,
            provenance=provenance,
            failure_reason=reason,
        )


def _outcome(payload, endpoint_id: str) -> ScientificOutcome | None:
    if not isinstance(payload, dict):
        return None
    direction = str(payload.get("direction", "unspecified"))
    try:
        outcome_direction = OutcomeDirection(direction)
    except ValueError:
        return None
    value = payload.get("value")
    return ScientificOutcome(
        endpoint_id=str(payload.get("endpoint_id", endpoint_id)),
        direction=outcome_direction,
        value=float(value) if value is not None else None,
        unit=str(payload["unit"]) if payload.get("unit") else None,
    )


def _bytes(value) -> bytes:
    if value is None:
        return b""
    return value if isinstance(value, bytes) else str(value).encode("utf-8")


def _receipt_from_dict(payload: dict) -> OracleJobReceipt:
    raw_artifacts = tuple(
        ArtifactRef(
            artifact_id=str(item["artifact_id"]),
            relative_path=str(item["relative_path"]),
            sha256=str(item["sha256"]),
            media_type=str(item["media_type"]),
            byte_size=int(item["byte_size"]),
            producer_version=str(item["producer_version"]),
        )
        for item in payload.get("raw_artifacts", ())
    )
    return OracleJobReceipt(
        job_id=str(payload["job_id"]),
        status=OracleJobStatus(str(payload["status"])),
        actual_cost=float(payload.get("actual_cost", 0.0)),
        raw_artifacts=raw_artifacts,
        started_at=str(payload["started_at"]) if payload.get("started_at") else None,
        completed_at=str(payload["completed_at"]) if payload.get("completed_at") else None,
        failure_code=str(payload["failure_code"]) if payload.get("failure_code") else None,
    )


def _timestamp() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()
