from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


DOCKING_READINESS_STATE_SCHEMA = "docking_readiness_state.v1"


@dataclass(frozen=True)
class DockingReadinessInput:
    path: str
    artifact_exists: bool
    sha256: str | None
    schema_version: str | None
    usable: bool
    problem_codes: tuple[str, ...]


@dataclass(frozen=True)
class DockingReadinessState:
    """Compact, non-executing docking state for an agent or promotion planner."""

    schema_version: str
    generated_at: str
    engine_capability: DockingReadinessInput
    input_manifest_verification: DockingReadinessInput
    result_normalization: DockingReadinessInput
    available_engine_names: tuple[str, ...]
    future_engine_available: bool | None
    manifest_verification_status: str | None
    normalization_status: str | None
    target_id: str | None
    subject_id: str | None
    engine_name: str | None
    normalization_trust_gate_decision: str | None
    normalization_evidence_tier: str | None
    calibration_status: str | None
    calibration_mismatch_codes: tuple[str, ...]
    readiness_status: str
    recommended_action_kind: str
    requires_human_confirmation: bool
    autonomous_local_cache_execution_permitted: bool
    autonomous_cache_replay_permitted: bool
    execution_authority_granted: bool
    autonomous_execution_permitted: bool
    scientific_claim_upgrade_permitted: bool
    summary_line: str

    def to_dict(self) -> dict[str, Any]:
        return _json_ready(asdict(self))


class DockingReadinessSummarizer:
    """Join docking control artifacts without granting tool or claim authority."""

    def summarize(
        self,
        *,
        engine_capability_path: str | Path,
        input_manifest_verification_path: str | Path,
        result_normalization_path: str | Path,
        generated_at: str | None = None,
    ) -> DockingReadinessState:
        generated = generated_at or datetime.now(timezone.utc).isoformat()
        capability_payload, capability_input = _load_input(
            Path(engine_capability_path),
            expected_schema="docking_engine_capability_probe.v1",
            required_fields=("probe_status",),
        )
        manifest_payload, manifest_input = _load_input(
            Path(input_manifest_verification_path),
            expected_schema="docking_input_manifest_verification.v1",
            required_fields=("verification_status",),
        )
        normalization_payload, normalization_input = _load_input(
            Path(result_normalization_path),
            expected_schema="docking_result_normalization.v1",
            required_fields=("normalization_status",),
        )

        available_engines = _available_engines(capability_payload)
        manifest_status = _upper_string(manifest_payload, "verification_status")
        normalization_status = _upper_string(normalization_payload, "normalization_status")
        target_id = _optional_string(
            normalization_payload.get("target_id") if normalization_payload else None
        )
        subject_id = _optional_string(
            normalization_payload.get("subject_id") if normalization_payload else None
        )
        trust_gate = _trust_gate_payload(normalization_payload)
        trust_decision = _optional_string(trust_gate.get("decision")) if trust_gate else None
        evidence_tier = _optional_string(trust_gate.get("evidence_tier")) if trust_gate else None
        calibration = _calibration_payload(normalization_payload)
        calibration_status = _optional_string(calibration.get("status")) if calibration else None
        calibration_mismatches = _string_items(
            calibration.get("mismatch_codes") if calibration else None
        )
        normalized_engine = _optional_string(
            normalization_payload.get("engine_name") if normalization_payload else None
        )
        future_engine_available = (
            normalized_engine in available_engines
            if normalized_engine is not None and capability_input.usable
            else (bool(available_engines) if capability_input.usable else None)
        )

        readiness_status, action_kind, needs_human = _readiness(
            manifest_input=manifest_input,
            manifest_status=manifest_status,
            normalization_input=normalization_input,
            normalization_payload=normalization_payload,
            trust_decision=trust_decision,
            evidence_tier=evidence_tier,
            calibration_status=calibration_status,
            calibration_mismatches=calibration_mismatches,
            capability_input=capability_input,
            future_engine_available=future_engine_available,
        )
        autonomous_local_cache_execution_permitted = (
            manifest_status == "PASS"
            and capability_input.usable
            and future_engine_available is True
        )
        autonomous_cache_replay_permitted = (
            normalization_payload is not None
            and normalization_payload.get("normalization_status") == "NORMALIZED"
            and normalization_payload.get("source_verification_sha256") == manifest_input.sha256
        )
        state = DockingReadinessState(
            schema_version=DOCKING_READINESS_STATE_SCHEMA,
            generated_at=generated,
            engine_capability=capability_input,
            input_manifest_verification=manifest_input,
            result_normalization=normalization_input,
            available_engine_names=available_engines,
            future_engine_available=future_engine_available,
            manifest_verification_status=manifest_status,
            normalization_status=normalization_status,
            target_id=target_id,
            subject_id=subject_id,
            engine_name=normalized_engine,
            normalization_trust_gate_decision=trust_decision,
            normalization_evidence_tier=evidence_tier,
            calibration_status=calibration_status,
            calibration_mismatch_codes=calibration_mismatches,
            readiness_status=readiness_status,
            recommended_action_kind=action_kind,
            requires_human_confirmation=needs_human,
            autonomous_local_cache_execution_permitted=autonomous_local_cache_execution_permitted,
            autonomous_cache_replay_permitted=autonomous_cache_replay_permitted,
            execution_authority_granted=autonomous_local_cache_execution_permitted,
            autonomous_execution_permitted=autonomous_local_cache_execution_permitted,
            scientific_claim_upgrade_permitted=False,
            summary_line="",
        )
        return replace(state, summary_line=_summary_line(state))


def _load_input(
    path: Path,
    *,
    expected_schema: str,
    required_fields: tuple[str, ...],
) -> tuple[Mapping[str, Any] | None, DockingReadinessInput]:
    if not path.exists():
        return None, DockingReadinessInput(
            path=str(path),
            artifact_exists=False,
            sha256=None,
            schema_version=None,
            usable=False,
            problem_codes=("artifact_missing",),
        )
    try:
        raw = path.read_bytes()
        payload = json.loads(raw)
    except (OSError, json.JSONDecodeError):
        return None, DockingReadinessInput(
            path=str(path),
            artifact_exists=True,
            sha256=None,
            schema_version=None,
            usable=False,
            problem_codes=("artifact_unreadable",),
        )
    if not isinstance(payload, Mapping):
        return None, DockingReadinessInput(
            path=str(path),
            artifact_exists=True,
            sha256=hashlib.sha256(raw).hexdigest(),
            schema_version=None,
            usable=False,
            problem_codes=("artifact_not_object",),
        )
    schema = _optional_string(payload.get("schema_version"))
    problems: list[str] = []
    if schema != expected_schema:
        problems.append("unsupported_schema")
    if any(not _optional_string(payload.get(key)) for key in required_fields):
        problems.append("missing_required_fields")
    return payload, DockingReadinessInput(
        path=str(path),
        artifact_exists=True,
        sha256=hashlib.sha256(raw).hexdigest(),
        schema_version=schema,
        usable=not problems,
        problem_codes=tuple(problems),
    )


def _available_engines(payload: Mapping[str, Any] | None) -> tuple[str, ...]:
    if payload is None:
        return ()
    capabilities = payload.get("capabilities")
    if not isinstance(capabilities, list | tuple):
        return ()
    return tuple(
        item["name"]
        for item in capabilities
        if isinstance(item, Mapping)
        and item.get("status") == "available"
        and isinstance(item.get("name"), str)
        and item["name"]
    )


def _trust_gate_payload(payload: Mapping[str, Any] | None) -> Mapping[str, Any] | None:
    if payload is None:
        return None
    ticket = payload.get("trust_gate")
    if not isinstance(ticket, Mapping) or ticket.get("contract_type") != "TrustGateResult":
        return None
    data = ticket.get("data")
    return data if isinstance(data, Mapping) else None


def _calibration_payload(payload: Mapping[str, Any] | None) -> Mapping[str, Any] | None:
    if payload is None:
        return None
    value = payload.get("calibration_application")
    return value if isinstance(value, Mapping) else None


def _readiness(
    *,
    manifest_input: DockingReadinessInput,
    manifest_status: str | None,
    normalization_input: DockingReadinessInput,
    normalization_payload: Mapping[str, Any] | None,
    trust_decision: str | None,
    evidence_tier: str | None,
    calibration_status: str | None,
    calibration_mismatches: tuple[str, ...],
    capability_input: DockingReadinessInput,
    future_engine_available: bool | None,
) -> tuple[str, str, bool]:
    if not manifest_input.usable:
        return "docking_input_state_invalid", "repair_docking_input_manifest_state", False
    if manifest_status != "PASS":
        return "docking_input_verification_failed", "repair_docking_input_manifest", False
    if not normalization_input.usable:
        if capability_input.usable and future_engine_available is False:
            return "docking_engine_unavailable", "repair_docking_engine_environment", False
        if capability_input.usable and future_engine_available is None:
            return "docking_inputs_verified_awaiting_result", "run_declared_local_docking", False
        return "docking_result_state_missing", "record_hash_bound_docking_result", False
    if normalization_payload is None:
        return "docking_result_state_invalid", "repair_docking_result_state", False
    if normalization_payload.get("normalization_status") != "NORMALIZED":
        return "docking_result_rejected", "repair_docking_result_binding", False
    if (
        normalization_payload.get("source_verification_sha256")
        != manifest_input.sha256
    ):
        return "docking_result_verification_mismatch", "repair_docking_result_binding", False
    if trust_decision is None:
        return "docking_result_not_trust_assessed", "assess_docking_result_trust", False
    if calibration_status == "rejected" or calibration_mismatches:
        return "docking_calibration_rejected", "repair_docking_calibration_anchor", False
    if calibration_status in {None, "not_requested"} and trust_decision == "abstain":
        return "docking_proxy_cacheable_uncalibrated", "freeze_raw_docking_cache_and_compare_policies", False
    if trust_decision == "needs_promotion" and evidence_tier == "l3_binding_proxy":
        return "calibrated_proxy_needs_promotion", "prepare_higher_fidelity_followup", False
    if trust_decision in {"rejected", "conflicted", "abstain"}:
        return "docking_proxy_not_admitted", "inspect_docking_result_quality", False
    return "docking_proxy_state_unrecognized", "inspect_docking_result_trust_state", False


def _upper_string(payload: Mapping[str, Any] | None, key: str) -> str | None:
    value = _optional_string(payload.get(key)) if payload else None
    return value.upper() if value else None


def _optional_string(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _string_items(value: Any) -> tuple[str, ...]:
    if isinstance(value, str) or not isinstance(value, list | tuple):
        return ()
    return tuple(item for item in value if isinstance(item, str) and item)


def _summary_line(state: DockingReadinessState) -> str:
    return (
        "Docking readiness: "
        f"status={state.readiness_status}; action={state.recommended_action_kind}; "
        f"future_engine_available={state.future_engine_available}; "
        f"local_cache_execution={str(state.autonomous_local_cache_execution_permitted).lower()}; "
        f"cache_replay={str(state.autonomous_cache_replay_permitted).lower()}; "
        "scientific_claim_upgrade=false."
    )


def _json_ready(value: Any) -> Any:
    if isinstance(value, tuple):
        return [_json_ready(item) for item in value]
    if isinstance(value, list):
        return [_json_ready(item) for item in value]
    if isinstance(value, dict):
        return {key: _json_ready(item) for key, item in value.items()}
    return value
