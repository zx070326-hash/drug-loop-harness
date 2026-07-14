from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import os
import re
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from .artifact_hash import canonical_content_sha256, json_ready
from .contracts import LoopState, OracleKind
from .prospective_acquisition_approval import ProspectiveAcquisitionPreflightEvaluator


ADMET_AI_EXECUTION_PROFILE_SCHEMA = "admet_ai_execution_profile.v1"
PROSPECTIVE_ADMET_EXECUTION_INTENT_SCHEMA = "prospective_admet_execution_intent.v1"
PROSPECTIVE_ADMET_EXECUTION_RECEIPT_SCHEMA = "prospective_admet_execution_receipt.v1"
PROSPECTIVE_ADMET_EXECUTION_AUDIT_SCHEMA = "prospective_admet_execution_audit.v1"

INTENT_FILENAME = "execution-intent.json"
RECEIPT_FILENAME = "execution-receipt.json"
INPUT_FILENAME = "input.csv"
OUTPUT_FILENAME = "predictions.csv"
VERSION_STDOUT_FILENAME = "version.stdout"
VERSION_STDERR_FILENAME = "version.stderr"
PREDICT_STDOUT_FILENAME = "predict.stdout"
PREDICT_STDERR_FILENAME = "predict.stderr"

EXECUTION_SUCCEEDED = "SUCCEEDED"
EXECUTION_BLOCKED_PREFLIGHT = "BLOCKED_PREFLIGHT"
EXECUTION_BLOCKED_PROFILE = "BLOCKED_PROFILE"
EXECUTION_BLOCKED_CONTROL = "BLOCKED_CONTROL"
EXECUTION_UNAVAILABLE = "UNAVAILABLE"
EXECUTION_TIMED_OUT = "TIMED_OUT"
EXECUTION_FAILED = "FAILED"
AUDIT_PASS_CONTROL = "PASS_CONTROL"
AUDIT_FAIL = "FAIL"
AUDIT_INCOMPLETE_REQUIRES_INCIDENT_REVIEW = "INCOMPLETE_REQUIRES_INCIDENT_REVIEW"

_ATTEMPT_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,119}")


@dataclass(frozen=True)
class ProspectiveAdmetExecutionIssue:
    code: str
    location: str
    message: str


@dataclass(frozen=True)
class AdmetAiExecutionProfile:
    """Fixed local ADMET-AI command and model-manifest identity for one attempt."""

    schema_version: str
    profile_id: str
    predict_command_prefix: tuple[str, ...]
    version_command: tuple[str, ...]
    expected_version: str
    model_name: str
    model_version: str
    endpoint_set_id: str
    model_manifest_path: str
    model_manifest_sha256: str
    accounted_cost_per_attempt: float
    cost_accounting_basis: str
    timeout_seconds: float
    version_timeout_seconds: float

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> AdmetAiExecutionProfile:
        if payload.get("schema_version") != ADMET_AI_EXECUTION_PROFILE_SCHEMA:
            raise ValueError("Unsupported ADMET-AI execution profile schema")
        profile = cls(
            schema_version=ADMET_AI_EXECUTION_PROFILE_SCHEMA,
            profile_id=_required_string(payload, "profile_id"),
            predict_command_prefix=_command(payload.get("predict_command_prefix"), "predict_command_prefix"),
            version_command=_command(payload.get("version_command"), "version_command"),
            expected_version=_required_string(payload, "expected_version"),
            model_name=_required_string(payload, "model_name"),
            model_version=_required_string(payload, "model_version"),
            endpoint_set_id=_required_string(payload, "endpoint_set_id"),
            model_manifest_path=_required_string(payload, "model_manifest_path"),
            model_manifest_sha256=_required_sha256(payload, "model_manifest_sha256"),
            accounted_cost_per_attempt=_nonnegative_number(
                payload.get("accounted_cost_per_attempt"),
                "accounted_cost_per_attempt",
            ),
            cost_accounting_basis=_required_string(payload, "cost_accounting_basis"),
            timeout_seconds=_positive_seconds(payload.get("timeout_seconds"), "timeout_seconds"),
            version_timeout_seconds=_positive_seconds(
                payload.get("version_timeout_seconds"), "version_timeout_seconds"
            ),
        )
        return profile

    def to_dict(self) -> dict[str, Any]:
        return json_ready(asdict(self))


@dataclass(frozen=True)
class ProspectiveAdmetExecutionIntent:
    """Durable pre-execution request. Its existence never proves a call occurred."""

    schema_version: str
    attempt_id: str
    source_plan_content_sha256: str
    source_randomization_verification_content_sha256: str
    source_approval_content_sha256: str
    source_profile_content_sha256: str
    request_id: str
    subject_id: str
    oracle_name: str
    candidate_smiles_sha256: str
    attempt_directory: str
    input_csv_path: str
    input_csv_sha256: str
    expected_output_filename: str
    execution_reviewer: str
    execution_rationale: str
    execution_confirmed_at: str
    human_execution_confirmed: bool
    execution_authority_granted: bool
    autonomous_execution_permitted: bool
    scientific_claim_upgrade_permitted: bool
    summary_line: str

    def to_dict(self) -> dict[str, Any]:
        return json_ready(asdict(self))


@dataclass(frozen=True)
class ProspectiveAdmetExecutionReceipt:
    """Raw local command result or failure evidence; never an ADMET observation."""

    schema_version: str
    attempt_id: str
    execution_status: str
    source_intent_content_sha256: str
    source_profile_content_sha256: str
    profile_id: str | None
    request_id: str | None
    subject_id: str | None
    oracle_name: str | None
    candidate_smiles_sha256: str | None
    accounted_cost_spent: float | None
    cost_accounting_basis: str | None
    input_csv_path: str
    input_csv_sha256: str | None
    raw_output_path: str | None
    raw_output_sha256: str | None
    version_stdout_path: str | None
    version_stdout_sha256: str | None
    version_stderr_path: str | None
    version_stderr_sha256: str | None
    predict_stdout_path: str | None
    predict_stdout_sha256: str | None
    predict_stderr_path: str | None
    predict_stderr_sha256: str | None
    version_exit_code: int | None
    predict_exit_code: int | None
    preflight_status: str | None
    external_command_invoked: bool
    started_at: str
    completed_at: str
    result_normalization_performed: bool
    trust_gate_evaluated: bool
    execution_authority_granted: bool
    autonomous_execution_permitted: bool
    scientific_claim_upgrade_permitted: bool
    issues: tuple[ProspectiveAdmetExecutionIssue, ...]
    summary_line: str

    def to_dict(self) -> dict[str, Any]:
        return json_ready(asdict(self))


@dataclass(frozen=True)
class ProspectiveAdmetExecutionAudit:
    """Read-only provenance audit for a local ADMET-AI execution attempt."""

    schema_version: str
    audit_status: str
    attempt_id: str | None
    execution_status: str | None
    external_command_invoked: bool | None
    result_normalization_performed: bool | None
    issue_codes: tuple[str, ...]
    summary_line: str

    def to_dict(self) -> dict[str, Any]:
        return json_ready(asdict(self))


class ProspectiveAdmetExecutionRunner:
    """Human-confirmed local ADMET-AI invocation producing raw artifacts only."""

    def __init__(
        self,
        *,
        preflight_evaluator: ProspectiveAcquisitionPreflightEvaluator | None = None,
    ) -> None:
        self.preflight_evaluator = preflight_evaluator or ProspectiveAcquisitionPreflightEvaluator()

    def run(
        self,
        profile_payload: Mapping[str, Any],
        plan: Mapping[str, Any],
        randomization_verification: Mapping[str, Any],
        approval: Mapping[str, Any],
        state: LoopState,
        attempt_root: str | Path,
        *,
        execution_reviewer: str,
        execution_rationale: str,
        execution_confirmed_at: str | None = None,
        attempt_id: str | None = None,
    ) -> tuple[ProspectiveAdmetExecutionIntent, ProspectiveAdmetExecutionReceipt]:
        started_at = execution_confirmed_at or datetime.now(timezone.utc).isoformat()
        _validate_human_confirmation(execution_reviewer, execution_rationale, started_at)
        selected_attempt_id = attempt_id or f"prospective_admet_attempt_{uuid4().hex}"
        _validate_attempt_id(selected_attempt_id)
        target = Path(attempt_root).resolve() / selected_attempt_id
        if target.exists():
            raise ValueError(f"Execution attempt directory already exists: {target}")
        target.mkdir(parents=True, exist_ok=False)

        profile_hash = canonical_content_sha256(profile_payload)
        request_id = _string_or_none(approval.get("request_id"))
        subject_id = _string_or_none(approval.get("subject_id"))
        oracle_name = _string_or_none(approval.get("oracle_name"))
        compound = state.compounds.get(subject_id or "")
        smiles = compound.smiles if compound is not None else ""
        input_bytes = _input_csv(subject_id or "", smiles)
        input_path = target / INPUT_FILENAME
        _atomic_write_bytes(input_path, input_bytes)
        intent = ProspectiveAdmetExecutionIntent(
            schema_version=PROSPECTIVE_ADMET_EXECUTION_INTENT_SCHEMA,
            attempt_id=selected_attempt_id,
            source_plan_content_sha256=canonical_content_sha256(plan),
            source_randomization_verification_content_sha256=canonical_content_sha256(
                randomization_verification
            ),
            source_approval_content_sha256=canonical_content_sha256(approval),
            source_profile_content_sha256=profile_hash,
            request_id=request_id or "unknown_request",
            subject_id=subject_id or "unknown_subject",
            oracle_name=oracle_name or "unknown_oracle",
            candidate_smiles_sha256=_sha256_text(smiles),
            attempt_directory=str(target),
            input_csv_path=str(input_path),
            input_csv_sha256=_sha256_bytes(input_bytes),
            expected_output_filename=OUTPUT_FILENAME,
            execution_reviewer=execution_reviewer,
            execution_rationale=execution_rationale,
            execution_confirmed_at=started_at,
            human_execution_confirmed=True,
            execution_authority_granted=False,
            autonomous_execution_permitted=False,
            scientific_claim_upgrade_permitted=False,
            summary_line="",
        )
        intent = replace(intent, summary_line=_intent_summary_line(intent))
        _atomic_write_json(target / INTENT_FILENAME, intent.to_dict())
        execution_started_at = datetime.now(timezone.utc).isoformat()

        issues: list[ProspectiveAdmetExecutionIssue] = []
        profile: AdmetAiExecutionProfile | None = None
        try:
            profile = AdmetAiExecutionProfile.from_mapping(profile_payload)
            _validate_profile_manifest(profile)
        except (OSError, ValueError) as exc:
            issues.append(_issue("profile_validation_failed", "$.profile", exc.__class__.__name__))
            return intent, _receipt(
                intent,
                status=EXECUTION_BLOCKED_PROFILE,
                profile=None,
                input_path=input_path,
                started_at=execution_started_at,
                issues=issues,
            )

        control_issues = _validate_control_bindings(intent, approval, state, profile)
        if control_issues:
            return intent, _receipt(
                intent,
                status=EXECUTION_BLOCKED_CONTROL,
                profile=profile,
                input_path=input_path,
                started_at=execution_started_at,
                issues=control_issues,
            )
        preflight = self.preflight_evaluator.evaluate(
            plan,
            randomization_verification,
            approval,
            reference_time=execution_started_at,
        )
        if preflight.preflight_status != "ready_for_human_confirmed_acquisition_preparation":
            return intent, _receipt(
                intent,
                status=EXECUTION_BLOCKED_PREFLIGHT,
                profile=profile,
                input_path=input_path,
                started_at=execution_started_at,
                preflight_status=preflight.preflight_status,
                issues=[
                    _issue(
                        "prospective_preflight_not_ready",
                        "$.preflight",
                        preflight.preflight_status,
                    )
                ],
            )

        version = _run_command(
            profile.version_command,
            timeout_seconds=profile.version_timeout_seconds,
        )
        version_paths = _write_process_artifacts(target, "version", version)
        if version.status == "timeout":
            return intent, _receipt(
                intent,
                status=EXECUTION_UNAVAILABLE,
                profile=profile,
                input_path=input_path,
                started_at=execution_started_at,
                preflight_status=preflight.preflight_status,
                version=version,
                version_paths=version_paths,
                issues=[_issue("provider_version_command_timed_out", "$.version_command", "timeout")],
            )
        if version.status == "os_error" or version.exit_code != 0:
            return intent, _receipt(
                intent,
                status=EXECUTION_UNAVAILABLE,
                profile=profile,
                input_path=input_path,
                started_at=execution_started_at,
                preflight_status=preflight.preflight_status,
                version=version,
                version_paths=version_paths,
                issues=[_issue("provider_version_command_failed", "$.version_command", version.status)],
            )
        version_text = version.stdout.decode("utf-8", errors="replace")
        if profile.expected_version not in version_text:
            return intent, _receipt(
                intent,
                status=EXECUTION_UNAVAILABLE,
                profile=profile,
                input_path=input_path,
                started_at=execution_started_at,
                preflight_status=preflight.preflight_status,
                version=version,
                version_paths=version_paths,
                issues=[
                    _issue(
                        "provider_version_mismatch",
                        "$.version_command",
                        profile.expected_version,
                    )
                ],
            )

        output_path = target / OUTPUT_FILENAME
        command = (
            *profile.predict_command_prefix,
            "--data_path",
            str(input_path),
            "--save_path",
            str(output_path),
            "--smiles_column",
            "smiles",
        )
        prediction = _run_command(command, timeout_seconds=profile.timeout_seconds)
        prediction_paths = _write_process_artifacts(target, "predict", prediction)
        if prediction.status == "timeout":
            return intent, _receipt(
                intent,
                status=EXECUTION_TIMED_OUT,
                profile=profile,
                input_path=input_path,
                started_at=execution_started_at,
                preflight_status=preflight.preflight_status,
                version=version,
                version_paths=version_paths,
                prediction=prediction,
                prediction_paths=prediction_paths,
                external_command_invoked=True,
                issues=[_issue("admet_predict_timed_out", "$.predict_command", "timeout")],
            )
        if prediction.status == "os_error" or prediction.exit_code != 0:
            return intent, _receipt(
                intent,
                status=EXECUTION_FAILED,
                profile=profile,
                input_path=input_path,
                started_at=execution_started_at,
                preflight_status=preflight.preflight_status,
                version=version,
                version_paths=version_paths,
                prediction=prediction,
                prediction_paths=prediction_paths,
                external_command_invoked=True,
                issues=[_issue("admet_predict_failed", "$.predict_command", prediction.status)],
            )
        if not output_path.is_file() or not output_path.read_bytes():
            return intent, _receipt(
                intent,
                status=EXECUTION_FAILED,
                profile=profile,
                input_path=input_path,
                started_at=execution_started_at,
                preflight_status=preflight.preflight_status,
                version=version,
                version_paths=version_paths,
                prediction=prediction,
                prediction_paths=prediction_paths,
                external_command_invoked=True,
                issues=[_issue("admet_predict_output_missing_or_empty", "$.raw_output", "missing")],
            )
        return intent, _receipt(
            intent,
            status=EXECUTION_SUCCEEDED,
            profile=profile,
            input_path=input_path,
            raw_output_path=output_path,
            started_at=execution_started_at,
            preflight_status=preflight.preflight_status,
            version=version,
            version_paths=version_paths,
            prediction=prediction,
            prediction_paths=prediction_paths,
            external_command_invoked=True,
        )


class ProspectiveAdmetExecutionAuditor:
    """Verify control-chain and artifact integrity without rerunning a provider."""

    def audit(
        self,
        intent: Mapping[str, Any],
        receipt: Mapping[str, Any] | None,
        profile_payload: Mapping[str, Any],
        plan: Mapping[str, Any],
        randomization_verification: Mapping[str, Any],
        approval: Mapping[str, Any],
        state: LoopState,
    ) -> ProspectiveAdmetExecutionAudit:
        issues: list[str] = []
        attempt_id = _string_or_none(intent.get("attempt_id"))
        if intent.get("schema_version") != PROSPECTIVE_ADMET_EXECUTION_INTENT_SCHEMA:
            issues.append("execution_intent_schema_invalid")
        expected = {
            "source_plan_content_sha256": canonical_content_sha256(plan),
            "source_randomization_verification_content_sha256": canonical_content_sha256(
                randomization_verification
            ),
            "source_approval_content_sha256": canonical_content_sha256(approval),
            "source_profile_content_sha256": canonical_content_sha256(profile_payload),
        }
        for field, value in expected.items():
            if intent.get(field) != value:
                issues.append(f"execution_intent_{field}_mismatch")
        subject_id = _string_or_none(intent.get("subject_id"))
        compound = state.compounds.get(subject_id or "")
        if compound is None:
            issues.append("execution_intent_subject_absent_from_campaign")
        elif intent.get("candidate_smiles_sha256") != _sha256_text(compound.smiles):
            issues.append("execution_intent_smiles_hash_mismatch")
        try:
            profile = AdmetAiExecutionProfile.from_mapping(profile_payload)
            _validate_profile_manifest(profile)
        except (OSError, ValueError):
            profile = None
            issues.append("execution_profile_invalid_or_manifest_mismatch")
        if receipt is None:
            return _audit(
                AUDIT_INCOMPLETE_REQUIRES_INCIDENT_REVIEW,
                attempt_id,
                None,
                None,
                ("execution_receipt_missing_unknown_external_state", *issues),
            )
        status = _string_or_none(receipt.get("execution_status"))
        if receipt.get("schema_version") != PROSPECTIVE_ADMET_EXECUTION_RECEIPT_SCHEMA:
            issues.append("execution_receipt_schema_invalid")
        if receipt.get("source_intent_content_sha256") != canonical_content_sha256(intent):
            issues.append("execution_receipt_intent_hash_mismatch")
        if receipt.get("source_profile_content_sha256") != canonical_content_sha256(profile_payload):
            issues.append("execution_receipt_profile_hash_mismatch")
        expected_receipt = {
            "attempt_id": intent.get("attempt_id"),
            "request_id": intent.get("request_id"),
            "subject_id": intent.get("subject_id"),
            "oracle_name": intent.get("oracle_name"),
            "candidate_smiles_sha256": intent.get("candidate_smiles_sha256"),
            "input_csv_path": intent.get("input_csv_path"),
            "input_csv_sha256": intent.get("input_csv_sha256"),
        }
        for field, value in expected_receipt.items():
            if receipt.get(field) != value:
                issues.append(f"execution_receipt_{field}_mismatch")
        if profile is not None and receipt.get("profile_id") != profile.profile_id:
            issues.append("execution_receipt_profile_id_mismatch")
        if profile is not None and receipt.get("external_command_invoked") is True:
            if receipt.get("accounted_cost_spent") != profile.accounted_cost_per_attempt:
                issues.append("execution_receipt_accounted_cost_mismatch")
            if receipt.get("cost_accounting_basis") != profile.cost_accounting_basis:
                issues.append("execution_receipt_cost_basis_mismatch")
        approved_budget = _nonnegative_number_or_none(approval.get("approved_budget"))
        receipt_cost = _nonnegative_number_or_none(receipt.get("accounted_cost_spent"))
        if receipt_cost is None:
            issues.append("execution_receipt_accounted_cost_invalid")
        elif receipt.get("external_command_invoked") is True and (
            approved_budget is None or receipt_cost > approved_budget
        ):
            issues.append("execution_receipt_accounted_cost_exceeds_approved_budget")
        if any(
            receipt.get(field) is not False
            for field in (
                "result_normalization_performed",
                "trust_gate_evaluated",
                "execution_authority_granted",
                "autonomous_execution_permitted",
                "scientific_claim_upgrade_permitted",
            )
        ):
            issues.append("execution_receipt_authority_or_normalization_flag_invalid")
        input_path = _path_or_none(receipt.get("input_csv_path"))
        _verify_artifact(input_path, receipt.get("input_csv_sha256"), "input_csv", issues, required=True)
        if status == EXECUTION_SUCCEEDED:
            if receipt.get("external_command_invoked") is not True:
                issues.append("successful_receipt_command_invocation_missing")
            output_path = _path_or_none(receipt.get("raw_output_path"))
            _verify_artifact(output_path, receipt.get("raw_output_sha256"), "raw_output", issues, required=True)
            if receipt.get("predict_exit_code") != 0:
                issues.append("successful_receipt_predict_exit_code_invalid")
            if receipt.get("version_exit_code") != 0:
                issues.append("successful_receipt_version_exit_code_invalid")
            if receipt.get("preflight_status") != "ready_for_human_confirmed_acquisition_preparation":
                issues.append("successful_receipt_preflight_status_invalid")
            _verify_artifact(
                _path_or_none(receipt.get("version_stdout_path")),
                receipt.get("version_stdout_sha256"),
                "version_stdout",
                issues,
                required=True,
            )
            _verify_artifact(
                _path_or_none(receipt.get("predict_stdout_path")),
                receipt.get("predict_stdout_sha256"),
                "predict_stdout",
                issues,
                required=True,
            )
        elif receipt.get("raw_output_path") is not None:
            _verify_artifact(
                _path_or_none(receipt.get("raw_output_path")),
                receipt.get("raw_output_sha256"),
                "raw_output",
                issues,
                required=False,
            )
        if status not in {
            EXECUTION_SUCCEEDED,
            EXECUTION_BLOCKED_PREFLIGHT,
            EXECUTION_BLOCKED_PROFILE,
            EXECUTION_BLOCKED_CONTROL,
            EXECUTION_UNAVAILABLE,
            EXECUTION_TIMED_OUT,
            EXECUTION_FAILED,
        }:
            issues.append("execution_receipt_status_invalid")
        _validate_receipt_timestamps(receipt, issues)
        return _audit(
            AUDIT_PASS_CONTROL if not issues else AUDIT_FAIL,
            attempt_id,
            status,
            receipt.get("external_command_invoked") if isinstance(receipt.get("external_command_invoked"), bool) else None,
            tuple(sorted(set(issues))),
        )


@dataclass(frozen=True)
class _ProcessResult:
    status: str
    exit_code: int | None
    stdout: bytes
    stderr: bytes


def _run_command(command: Sequence[str], *, timeout_seconds: float) -> _ProcessResult:
    try:
        completed = subprocess.run(
            tuple(command),
            check=False,
            capture_output=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        return _ProcessResult(
            status="timeout",
            exit_code=None,
            stdout=exc.stdout or b"",
            stderr=exc.stderr or b"",
        )
    except OSError as exc:
        return _ProcessResult(
            status="os_error",
            exit_code=None,
            stdout=b"",
            stderr=exc.__class__.__name__.encode("utf-8"),
        )
    return _ProcessResult(
        status="completed" if completed.returncode == 0 else "nonzero_exit",
        exit_code=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )


def _write_process_artifacts(
    target: Path,
    name: str,
    result: _ProcessResult,
) -> dict[str, tuple[str | None, str | None]]:
    stdout_path = target / (VERSION_STDOUT_FILENAME if name == "version" else PREDICT_STDOUT_FILENAME)
    stderr_path = target / (VERSION_STDERR_FILENAME if name == "version" else PREDICT_STDERR_FILENAME)
    _atomic_write_bytes(stdout_path, result.stdout)
    _atomic_write_bytes(stderr_path, result.stderr)
    return {
        "stdout": (str(stdout_path), _sha256_bytes(result.stdout)),
        "stderr": (str(stderr_path), _sha256_bytes(result.stderr)),
    }


def _receipt(
    intent: ProspectiveAdmetExecutionIntent,
    *,
    status: str,
    profile: AdmetAiExecutionProfile | None,
    input_path: Path,
    started_at: str,
    preflight_status: str | None = None,
    version: _ProcessResult | None = None,
    version_paths: Mapping[str, tuple[str | None, str | None]] | None = None,
    prediction: _ProcessResult | None = None,
    prediction_paths: Mapping[str, tuple[str | None, str | None]] | None = None,
    raw_output_path: Path | None = None,
    external_command_invoked: bool = False,
    issues: Sequence[ProspectiveAdmetExecutionIssue] = (),
) -> ProspectiveAdmetExecutionReceipt:
    raw_output = raw_output_path.read_bytes() if raw_output_path is not None and raw_output_path.is_file() else None
    receipt = ProspectiveAdmetExecutionReceipt(
        schema_version=PROSPECTIVE_ADMET_EXECUTION_RECEIPT_SCHEMA,
        attempt_id=intent.attempt_id,
        execution_status=status,
        source_intent_content_sha256=canonical_content_sha256(intent.to_dict()),
        source_profile_content_sha256=intent.source_profile_content_sha256,
        profile_id=profile.profile_id if profile is not None else None,
        request_id=intent.request_id,
        subject_id=intent.subject_id,
        oracle_name=intent.oracle_name,
        candidate_smiles_sha256=intent.candidate_smiles_sha256,
        accounted_cost_spent=(
            profile.accounted_cost_per_attempt
            if profile is not None and external_command_invoked
            else 0.0
        ),
        cost_accounting_basis=(
            profile.cost_accounting_basis
            if profile is not None and external_command_invoked
            else "no_prediction_command_invoked"
        ),
        input_csv_path=str(input_path),
        input_csv_sha256=_sha256_bytes(input_path.read_bytes()) if input_path.is_file() else None,
        raw_output_path=str(raw_output_path) if raw_output is not None else None,
        raw_output_sha256=_sha256_bytes(raw_output) if raw_output is not None else None,
        version_stdout_path=_artifact_path(version_paths, "stdout"),
        version_stdout_sha256=_artifact_hash(version_paths, "stdout"),
        version_stderr_path=_artifact_path(version_paths, "stderr"),
        version_stderr_sha256=_artifact_hash(version_paths, "stderr"),
        predict_stdout_path=_artifact_path(prediction_paths, "stdout"),
        predict_stdout_sha256=_artifact_hash(prediction_paths, "stdout"),
        predict_stderr_path=_artifact_path(prediction_paths, "stderr"),
        predict_stderr_sha256=_artifact_hash(prediction_paths, "stderr"),
        version_exit_code=version.exit_code if version is not None else None,
        predict_exit_code=prediction.exit_code if prediction is not None else None,
        preflight_status=preflight_status,
        external_command_invoked=external_command_invoked,
        started_at=started_at,
        completed_at=_completion_timestamp(started_at),
        result_normalization_performed=False,
        trust_gate_evaluated=False,
        execution_authority_granted=False,
        autonomous_execution_permitted=False,
        scientific_claim_upgrade_permitted=False,
        issues=tuple(issues),
        summary_line="",
    )
    receipt = replace(receipt, summary_line=_receipt_summary_line(receipt))
    directory = input_path.parent
    _atomic_write_json(directory / RECEIPT_FILENAME, receipt.to_dict())
    return receipt


def _validate_profile_manifest(profile: AdmetAiExecutionProfile) -> None:
    manifest = Path(profile.model_manifest_path).resolve()
    raw = manifest.read_bytes()
    if _sha256_bytes(raw) != profile.model_manifest_sha256:
        raise ValueError("model_manifest_sha256 mismatch")


def _validate_control_bindings(
    intent: ProspectiveAdmetExecutionIntent,
    approval: Mapping[str, Any],
    state: LoopState,
    profile: AdmetAiExecutionProfile,
) -> list[ProspectiveAdmetExecutionIssue]:
    issues: list[ProspectiveAdmetExecutionIssue] = []
    if approval.get("action_kind") != "predict_admet":
        issues.append(_issue("approval_not_predict_admet", "$.approval.action_kind", "predict_admet required"))
    if approval.get("request_id") != intent.request_id:
        issues.append(_issue("intent_request_id_mismatch", "$.intent.request_id", "approval mismatch"))
    if approval.get("subject_id") != intent.subject_id:
        issues.append(_issue("intent_subject_id_mismatch", "$.intent.subject_id", "approval mismatch"))
    if approval.get("oracle_name") != intent.oracle_name:
        issues.append(_issue("intent_oracle_name_mismatch", "$.intent.oracle_name", "approval mismatch"))
    oracle = state.oracles.get(intent.oracle_name)
    if oracle is None:
        issues.append(_issue("execution_oracle_absent_from_campaign", "$.oracle_name", intent.oracle_name))
    elif oracle.kind is not OracleKind.ADMET:
        issues.append(_issue("execution_oracle_kind_not_admet", "$.oracle_name", oracle.kind.value))
    elif oracle.version is not None and oracle.version != profile.model_version:
        issues.append(_issue("execution_model_version_mismatch", "$.profile.model_version", profile.model_version))
    if intent.subject_id not in state.compounds:
        issues.append(_issue("execution_subject_absent_from_campaign", "$.subject_id", intent.subject_id))
    try:
        confirmed = _parse_timestamp(intent.execution_confirmed_at)
        reviewed = _parse_timestamp(_required_string(approval, "reviewed_at"))
        if confirmed < reviewed:
            issues.append(
                _issue(
                    "execution_confirmation_before_approval",
                    "$.execution_confirmed_at",
                    "before approval reviewed_at",
                )
            )
    except ValueError:
        issues.append(
            _issue(
                "execution_confirmation_or_approval_timestamp_invalid",
                "$.execution_confirmed_at",
                "timezone-aware timestamps required",
            )
        )
    if approval.get("decision") == "accept":
        approved_budget = _nonnegative_number_or_none(approval.get("approved_budget"))
        if approved_budget is None:
            issues.append(
                _issue(
                    "execution_approval_budget_invalid",
                    "$.approval.approved_budget",
                    "non-negative approved budget required",
                )
            )
        elif profile.accounted_cost_per_attempt > approved_budget:
            issues.append(
                _issue(
                    "execution_accounted_cost_exceeds_approved_budget",
                    "$.profile.accounted_cost_per_attempt",
                    str(profile.accounted_cost_per_attempt),
                )
            )
    return issues


def _input_csv(subject_id: str, smiles: str) -> bytes:
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=("subject_id", "smiles"), lineterminator="\n")
    writer.writeheader()
    writer.writerow({"subject_id": subject_id, "smiles": smiles})
    return stream.getvalue().encode("utf-8")


def _verify_artifact(
    path: Path | None,
    digest: Any,
    name: str,
    issues: list[str],
    *,
    required: bool,
) -> None:
    if path is None or not isinstance(digest, str):
        if required:
            issues.append(f"execution_receipt_{name}_reference_missing")
        return
    if not path.is_file():
        issues.append(f"execution_receipt_{name}_missing")
        return
    if _sha256_bytes(path.read_bytes()) != digest:
        issues.append(f"execution_receipt_{name}_sha256_mismatch")


def _path_or_none(value: Any) -> Path | None:
    return Path(value) if isinstance(value, str) and value else None


def _artifact_path(
    paths: Mapping[str, tuple[str | None, str | None]] | None,
    key: str,
) -> str | None:
    return paths[key][0] if paths is not None and key in paths else None


def _artifact_hash(
    paths: Mapping[str, tuple[str | None, str | None]] | None,
    key: str,
) -> str | None:
    return paths[key][1] if paths is not None and key in paths else None


def _audit(
    status: str,
    attempt_id: str | None,
    execution_status: str | None,
    external_command_invoked: bool | None,
    issues: Sequence[str],
) -> ProspectiveAdmetExecutionAudit:
    audit = ProspectiveAdmetExecutionAudit(
        schema_version=PROSPECTIVE_ADMET_EXECUTION_AUDIT_SCHEMA,
        audit_status=status,
        attempt_id=attempt_id,
        execution_status=execution_status,
        external_command_invoked=external_command_invoked,
        result_normalization_performed=False,
        issue_codes=tuple(issues),
        summary_line="",
    )
    return replace(audit, summary_line=_audit_summary_line(audit))


def _atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    _atomic_write_bytes(path, (json.dumps(payload, indent=2) + "\n").encode("utf-8"))


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _required_string(payload: Mapping[str, Any], field: str) -> str:
    value = _string_or_none(payload.get(field))
    if value is None:
        raise ValueError(f"{field} must be a non-empty string")
    return value


def _required_sha256(payload: Mapping[str, Any], field: str) -> str:
    value = _required_string(payload, field)
    if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise ValueError(f"{field} must be a lowercase SHA-256 digest")
    return value


def _command(value: Any, field: str) -> tuple[str, ...]:
    if not isinstance(value, list | tuple) or not value or not all(
        isinstance(item, str) and item for item in value
    ):
        raise ValueError(f"{field} must be a non-empty string array")
    return tuple(value)


def _positive_seconds(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value) or value <= 0:
        raise ValueError(f"{field} must be a positive finite number")
    return float(value)


def _nonnegative_number(value: Any, field: str) -> float:
    parsed = _nonnegative_number_or_none(value)
    if parsed is None:
        raise ValueError(f"{field} must be a non-negative finite number")
    return parsed


def _nonnegative_number_or_none(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value) or value < 0:
        return None
    return float(value)


def _validate_human_confirmation(reviewer: str, rationale: str, timestamp: str) -> None:
    if not reviewer.strip():
        raise ValueError("execution reviewer must be non-empty")
    if not rationale.strip():
        raise ValueError("execution rationale must be non-empty")
    try:
        _parse_timestamp(timestamp)
    except ValueError as exc:
        raise ValueError("execution confirmation timestamp must be timezone-aware ISO-8601") from exc


def _validate_attempt_id(value: str) -> None:
    if value in {".", ".."} or not _ATTEMPT_ID_PATTERN.fullmatch(value):
        raise ValueError("attempt_id may contain only letters, digits, dot, underscore, and hyphen")


def _sha256_text(value: str) -> str:
    return _sha256_bytes(value.encode("utf-8"))


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _string_or_none(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _parse_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timezone required")
    return parsed


def _completion_timestamp(started_at: str) -> str:
    started = _parse_timestamp(started_at)
    return max(started, datetime.now(timezone.utc)).isoformat()


def _validate_receipt_timestamps(receipt: Mapping[str, Any], issues: list[str]) -> None:
    try:
        started = _parse_timestamp(_required_string(receipt, "started_at"))
        completed = _parse_timestamp(_required_string(receipt, "completed_at"))
    except ValueError:
        issues.append("execution_receipt_timestamp_invalid")
        return
    if completed < started:
        issues.append("execution_receipt_completion_precedes_start")


def _issue(code: str, location: str, message: str) -> ProspectiveAdmetExecutionIssue:
    return ProspectiveAdmetExecutionIssue(code=code, location=location, message=message)


def _intent_summary_line(intent: ProspectiveAdmetExecutionIntent) -> str:
    return (
        "Prospective ADMET execution intent: "
        f"attempt_id={intent.attempt_id}; human_execution_confirmed=true; "
        "autonomous_execution=false; scientific_claim_upgrade=false."
    )


def _receipt_summary_line(receipt: ProspectiveAdmetExecutionReceipt) -> str:
    return (
        "Prospective ADMET execution receipt: "
        f"attempt_id={receipt.attempt_id}; status={receipt.execution_status}; "
        f"external_command_invoked={receipt.external_command_invoked}; "
        "result_normalization=false; scientific_claim_upgrade=false."
    )


def _audit_summary_line(audit: ProspectiveAdmetExecutionAudit) -> str:
    return (
        "Prospective ADMET execution audit: "
        f"status={audit.audit_status}; issues={len(audit.issue_codes)}; "
        "scientific_claim_upgrade=false."
    )
