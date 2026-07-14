from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .artifact_hash import canonical_content_sha256, json_ready
from .contracts import LoopState, Observation, OracleKind
from .prospective_acquisition_approval import ProspectiveAcquisitionPreflightEvaluator


PROSPECTIVE_ASSAY_ENDPOINT_POLICY_SCHEMA = "prospective_assay_endpoint_policy.v1"
PROSPECTIVE_ASSAY_RESULT_SCHEMA = "prospective_assay_result.v1"
PROSPECTIVE_ASSAY_IMPORT_SCHEMA = "prospective_assay_import.v1"
PROSPECTIVE_ASSAY_IMPORTER_VERSION = "0.1.0"
_AUTHORITY_FIELDS = (
    "execution_authority_granted",
    "autonomous_execution_permitted",
    "scientific_claim_upgrade_permitted",
)


@dataclass(frozen=True)
class ProspectiveAssayImportIssue:
    code: str
    location: str
    message: str


@dataclass(frozen=True)
class ProspectiveAssayImport:
    """A draft observation from an external assay receipt, never direct evidence admission."""

    schema_version: str
    normalized_at: str
    source_result_path: str
    source_result_sha256: str | None
    result_id: str | None
    subject_id: str | None
    oracle_name: str | None
    assay_run_id: str | None
    assay_site_id: str | None
    import_status: str
    observation: Observation | None
    issues: tuple[ProspectiveAssayImportIssue, ...]
    execution_authority_granted: bool
    autonomous_execution_permitted: bool
    scientific_claim_upgrade_permitted: bool
    trust_gate_evaluated: bool
    summary_line: str

    def to_dict(self) -> dict[str, Any]:
        return json_ready(asdict(self))


class ProspectiveAssayResultImporter:
    """Normalize one policy-bound external assay receipt into an observation draft."""

    def __init__(
        self,
        *,
        preflight_evaluator: ProspectiveAcquisitionPreflightEvaluator | None = None,
    ) -> None:
        self.preflight_evaluator = preflight_evaluator or ProspectiveAcquisitionPreflightEvaluator()

    def import_path(
        self,
        result_path: str | Path,
        endpoint_policy: Mapping[str, Any],
        protocol: Mapping[str, Any],
        plan: Mapping[str, Any],
        randomization_verification: Mapping[str, Any],
        approval: Mapping[str, Any],
        state: LoopState,
        *,
        normalized_at: str | None = None,
    ) -> ProspectiveAssayImport:
        timestamp = normalized_at or datetime.now(timezone.utc).isoformat()
        source = Path(result_path)
        result, result_hash, source_issues = _load_json_object(source)
        return self.import_result(
            result,
            endpoint_policy,
            protocol,
            plan,
            randomization_verification,
            approval,
            state,
            result_path=source,
            result_sha256=result_hash,
            source_issues=source_issues,
            normalized_at=timestamp,
        )

    def import_result(
        self,
        result: Mapping[str, Any] | None,
        endpoint_policy: Mapping[str, Any],
        protocol: Mapping[str, Any],
        plan: Mapping[str, Any],
        randomization_verification: Mapping[str, Any],
        approval: Mapping[str, Any],
        state: LoopState,
        *,
        result_path: str | Path = "",
        result_sha256: str | None = None,
        source_issues: tuple[ProspectiveAssayImportIssue, ...] = (),
        normalized_at: str | None = None,
    ) -> ProspectiveAssayImport:
        timestamp = normalized_at or datetime.now(timezone.utc).isoformat()
        source = Path(result_path)
        issues = list(source_issues)
        _validate_endpoint_policy(endpoint_policy, protocol, issues)
        if result is None:
            if not issues:
                issues.append(_issue("assay_result_missing", "$.result", "Result payload is required."))
            return _import_result(timestamp, source, result_hash, None, tuple(issues))

        if result.get("schema_version") != PROSPECTIVE_ASSAY_RESULT_SCHEMA:
            issues.append(_issue("assay_result_schema_invalid", "$.result.schema_version", "Unsupported assay result schema."))
        result_id = _required_string(result, "result_id", "$.result.result_id", issues)
        subject_id = _required_string(result, "subject_id", "$.result.subject_id", issues)
        oracle_name = _required_string(result, "oracle_name", "$.result.oracle_name", issues)
        request_id = _required_string(result, "request_id", "$.result.request_id", issues)
        input_smiles_hash = _required_sha256(result, "input_smiles_sha256", "$.result.input_smiles_sha256", issues)
        cost_spent = _nonnegative_number(result.get("cost_spent"))
        if cost_spent is None:
            issues.append(_issue("assay_result_cost_invalid", "$.result.cost_spent", "Cost must be finite and non-negative."))
        run = _required_object(result, "assay_run", "$.result.assay_run", issues)
        endpoint = _required_object(result, "endpoint", "$.result.endpoint", issues)
        controls = result.get("controls")
        source_output = _required_object(result, "source_output", "$.result.source_output", issues)
        run_id = _required_string(run, "run_id", "$.result.assay_run.run_id", issues)
        site_id = _required_string(run, "site_id", "$.result.assay_run.site_id", issues)
        assay_protocol_id = _required_string(run, "assay_protocol_id", "$.result.assay_run.assay_protocol_id", issues)
        started_at = _timestamp(run.get("started_at"))
        completed_at = _timestamp(run.get("completed_at"))
        if started_at is None:
            issues.append(_issue("assay_result_started_at_invalid", "$.result.assay_run.started_at", "Timestamp with timezone required."))
        if completed_at is None:
            issues.append(_issue("assay_result_completed_at_invalid", "$.result.assay_run.completed_at", "Timestamp with timezone required."))
        elif started_at is not None and completed_at < started_at:
            issues.append(_issue("assay_result_completion_precedes_start", "$.result.assay_run.completed_at", "Completion cannot precede start."))
        if run.get("qc_status") != "pass":
            issues.append(_issue("assay_result_qc_not_pass", "$.result.assay_run.qc_status", "Assay run QC must pass."))
        output_path = _required_string(source_output, "path", "$.result.source_output.path", issues)
        output_sha = _required_sha256(source_output, "sha256", "$.result.source_output.sha256", issues)

        plan_hash = canonical_content_sha256(plan)
        randomization_hash = canonical_content_sha256(randomization_verification)
        approval_hash = canonical_content_sha256(approval)
        policy_hash = canonical_content_sha256(endpoint_policy)
        expected_hashes = {
            "source_plan_content_sha256": plan_hash,
            "source_randomization_verification_content_sha256": randomization_hash,
            "source_approval_content_sha256": approval_hash,
            "source_endpoint_policy_content_sha256": policy_hash,
        }
        for field, expected in expected_hashes.items():
            if result.get(field) != expected:
                issues.append(_issue(f"assay_result_{field}_mismatch", f"$.result.{field}", "Source binding mismatch."))
        if request_id is not None and request_id != approval.get("request_id"):
            issues.append(_issue("assay_result_request_id_mismatch", "$.result.request_id", "Result request differs from approval."))
        if subject_id is not None and subject_id != approval.get("subject_id"):
            issues.append(_issue("assay_result_subject_id_mismatch", "$.result.subject_id", "Result subject differs from approval."))
        if oracle_name is not None and oracle_name != approval.get("oracle_name"):
            issues.append(_issue("assay_result_oracle_name_mismatch", "$.result.oracle_name", "Result oracle differs from approval."))
        if approval.get("action_kind") != "send_to_experiment":
            issues.append(_issue("assay_approval_action_invalid", "$.approval.action_kind", "Approval must be for send_to_experiment."))
        approved_budget = _nonnegative_number(approval.get("approved_budget"))
        if cost_spent is not None and approved_budget is not None and cost_spent > approved_budget:
            issues.append(_issue("assay_result_cost_exceeds_approved_budget", "$.result.cost_spent", "Result cost exceeds approved budget."))
        preflight_time = started_at.isoformat() if started_at is not None else timestamp
        preflight = self.preflight_evaluator.evaluate(plan, randomization_verification, approval, reference_time=preflight_time)
        if preflight.preflight_status != "ready_for_human_confirmed_acquisition_preparation":
            issues.append(_issue("assay_result_preflight_not_ready", "$.result.assay_run.started_at", preflight.preflight_status))
        approved_at = _timestamp(approval.get("reviewed_at"))
        if approved_at is not None and started_at is not None and started_at < approved_at:
            issues.append(_issue("assay_result_started_before_approval", "$.result.assay_run.started_at", "Assay started before approval."))

        compound = state.compounds.get(subject_id or "")
        oracle = state.oracles.get(oracle_name or "")
        if compound is None:
            issues.append(_issue("assay_result_subject_absent_from_campaign", "$.result.subject_id", "Subject absent from campaign."))
        if oracle is None:
            issues.append(_issue("assay_result_oracle_absent_from_campaign", "$.result.oracle_name", "Oracle absent from campaign."))
        elif oracle.kind is not OracleKind.ASSAY:
            issues.append(_issue("assay_result_oracle_kind_invalid", "$.result.oracle_name", "Registered oracle must be assay."))
        if compound is not None and input_smiles_hash is not None:
            if input_smiles_hash != _sha256_text(compound.smiles):
                issues.append(_issue("assay_result_input_smiles_hash_mismatch", "$.result.input_smiles_sha256", "Result input does not bind campaign compound."))

        endpoint_value = _endpoint_value(endpoint, endpoint_policy, issues)
        if assay_protocol_id is not None and assay_protocol_id != endpoint_policy.get("assay_protocol_id"):
            issues.append(_issue("assay_result_protocol_id_mismatch", "$.result.assay_run.assay_protocol_id", "Assay protocol differs from endpoint policy."))
        _validate_controls(controls, endpoint_policy, issues)
        _verify_output(source.parent, output_path, output_sha, issues)

        if issues:
            return _import_result(timestamp, source, result_sha256, result, tuple(issues), result_id, subject_id, oracle_name, run_id, site_id)

        assert subject_id is not None and oracle_name is not None and endpoint_value is not None
        output_metric_key = str(endpoint_policy["output_metric_key"])
        raw_metric_key = str(endpoint_policy["raw_metric_key"])
        endpoint_passed = _compare(
            endpoint_value,
            str(endpoint_policy["comparator"]),
            float(endpoint_policy["threshold"]),
        )
        observation = Observation(
            id=f"obs_prospective_assay_{subject_id}_{result_sha256[:12] if result_sha256 else result_id}",
            subject_id=subject_id,
            oracle_name=oracle_name,
            metrics={output_metric_key: endpoint_passed, "passed": True, raw_metric_key: endpoint_value},
            cost_spent=cost_spent or 0.0,
            provenance={
                "source": "prospective_assay_result_import",
                "adapter": "ProspectiveAssayResultImporter",
                "adapter_version": PROSPECTIVE_ASSAY_IMPORTER_VERSION,
                "domain": tuple(oracle.trust.applicability_domain) if oracle is not None else (),
                "source_result_sha256": result_sha256,
                "source_output_sha256": output_sha,
                "source_plan_content_sha256": plan_hash,
                "source_randomization_verification_content_sha256": randomization_hash,
                "source_approval_content_sha256": approval_hash,
                "source_endpoint_policy_content_sha256": policy_hash,
                "assay_run_id": run_id,
                "assay_site_id": site_id,
                "assay_protocol_id": assay_protocol_id,
                "qc_status": "pass",
            },
            notes="Normalized from a policy-bound external assay receipt. TrustGate has not evaluated this observation.",
        )
        return _import_result(timestamp, source, result_sha256, result, (), result_id, subject_id, oracle_name, run_id, site_id, observation)


def _validate_endpoint_policy(policy: Mapping[str, Any], protocol: Mapping[str, Any], issues: list[ProspectiveAssayImportIssue]) -> None:
    if policy.get("schema_version") != PROSPECTIVE_ASSAY_ENDPOINT_POLICY_SCHEMA:
        issues.append(_issue("assay_endpoint_policy_schema_invalid", "$.endpoint_policy.schema_version", "Unsupported endpoint policy schema."))
    if policy.get("source_protocol_content_sha256") != canonical_content_sha256(protocol):
        issues.append(_issue("assay_endpoint_policy_protocol_hash_mismatch", "$.endpoint_policy.source_protocol_content_sha256", "Policy does not bind protocol."))
    primary = protocol.get("primary_outcome")
    if not isinstance(primary, Mapping) or policy.get("output_metric_key") != primary.get("outcome_metric_key"):
        issues.append(_issue("assay_endpoint_policy_primary_metric_mismatch", "$.endpoint_policy.output_metric_key", "Policy output must match protocol primary metric."))
    if policy.get("oracle_name") is None or policy.get("assay_protocol_id") is None:
        issues.append(_issue("assay_endpoint_policy_identity_missing", "$.endpoint_policy", "Policy oracle and assay protocol are required."))
    if _nonempty_string(policy.get("raw_metric_key")) is None or _nonempty_string(policy.get("unit")) is None:
        issues.append(_issue("assay_endpoint_policy_endpoint_missing", "$.endpoint_policy", "Raw metric and unit are required."))
    if policy.get("comparator") not in {"at_most", "at_least"} or _finite_number(policy.get("threshold")) is None:
        issues.append(_issue("assay_endpoint_policy_threshold_invalid", "$.endpoint_policy", "Comparator and finite threshold are required."))
    controls = policy.get("required_control_ids")
    if not isinstance(controls, list | tuple) or not controls or not all(_nonempty_string(item) for item in controls):
        issues.append(_issue("assay_endpoint_policy_controls_invalid", "$.endpoint_policy.required_control_ids", "At least one named control is required."))
    acceptance = policy.get("human_acceptance")
    if not isinstance(acceptance, Mapping) or acceptance.get("decision") != "accept":
        issues.append(_issue("assay_endpoint_policy_human_acceptance_invalid", "$.endpoint_policy.human_acceptance", "Accepted human review required."))
    for field in _AUTHORITY_FIELDS:
        if policy.get(field) is not False:
            issues.append(_issue(f"assay_endpoint_policy_{field}_must_be_false", f"$.endpoint_policy.{field}", "Authority must remain false."))


def _endpoint_value(endpoint: Mapping[str, Any], policy: Mapping[str, Any], issues: list[ProspectiveAssayImportIssue]) -> float | None:
    if endpoint.get("metric_key") != policy.get("raw_metric_key"):
        issues.append(_issue("assay_result_endpoint_metric_mismatch", "$.result.endpoint.metric_key", "Raw metric differs from policy."))
    if endpoint.get("unit") != policy.get("unit"):
        issues.append(_issue("assay_result_endpoint_unit_mismatch", "$.result.endpoint.unit", "Endpoint unit differs from policy."))
    value = _finite_number(endpoint.get("value"))
    if value is None:
        issues.append(_issue("assay_result_endpoint_value_invalid", "$.result.endpoint.value", "Endpoint value must be finite."))
    return value


def _validate_controls(value: Any, policy: Mapping[str, Any], issues: list[ProspectiveAssayImportIssue]) -> None:
    if not isinstance(value, Mapping):
        issues.append(_issue("assay_result_controls_invalid", "$.result.controls", "Controls object required."))
        return
    for control_id in policy.get("required_control_ids", ()):
        if value.get(control_id) is not True:
            issues.append(_issue("assay_result_control_not_pass", f"$.result.controls.{control_id}", "Required control did not pass."))


def _compare(value: float, comparator: str, threshold: float) -> bool:
    return value <= threshold if comparator == "at_most" else value >= threshold


def _load_json_object(path: Path) -> tuple[Mapping[str, Any] | None, str | None, tuple[ProspectiveAssayImportIssue, ...]]:
    if not path.exists():
        return None, None, (_issue("assay_result_missing", "$.result", f"File does not exist: {path}"),)
    try:
        raw = path.read_bytes()
    except OSError as exc:
        return None, None, (_issue("assay_result_unreadable", "$.result", exc.__class__.__name__),)
    digest = hashlib.sha256(raw).hexdigest()
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return None, digest, (_issue("assay_result_invalid_json", "$.result", "Expected JSON object."),)
    if not isinstance(payload, Mapping):
        return None, digest, (_issue("assay_result_not_object", "$.result", "Expected JSON object."),)
    return payload, digest, ()


def _verify_output(base: Path, output_path: str | None, output_sha: str | None, issues: list[ProspectiveAssayImportIssue]) -> None:
    if output_path is None or output_sha is None:
        return
    resolved = Path(output_path)
    if not resolved.is_absolute():
        resolved = base / resolved
    if not resolved.is_file():
        issues.append(_issue("assay_result_output_missing", "$.result.source_output.path", "Raw output artifact is missing."))
        return
    try:
        actual = hashlib.sha256(resolved.read_bytes()).hexdigest()
    except OSError as exc:
        issues.append(_issue("assay_result_output_unreadable", "$.result.source_output.path", exc.__class__.__name__))
        return
    if actual != output_sha:
        issues.append(_issue("assay_result_output_sha256_mismatch", "$.result.source_output.sha256", "Raw output hash differs."))


def _import_result(normalized_at: str, source: Path, result_sha: str | None, result: Mapping[str, Any] | None, issues: tuple[ProspectiveAssayImportIssue, ...], result_id: str | None = None, subject_id: str | None = None, oracle_name: str | None = None, run_id: str | None = None, site_id: str | None = None, observation: Observation | None = None) -> ProspectiveAssayImport:
    imported = ProspectiveAssayImport(
        schema_version=PROSPECTIVE_ASSAY_IMPORT_SCHEMA,
        normalized_at=normalized_at,
        source_result_path=str(source),
        source_result_sha256=result_sha,
        result_id=result_id or _nonempty_string((result or {}).get("result_id")),
        subject_id=subject_id or _nonempty_string((result or {}).get("subject_id")),
        oracle_name=oracle_name or _nonempty_string((result or {}).get("oracle_name")),
        assay_run_id=run_id,
        assay_site_id=site_id,
        import_status="NORMALIZED" if observation is not None and not issues else "REJECTED",
        observation=observation if not issues else None,
        issues=issues,
        execution_authority_granted=False,
        autonomous_execution_permitted=False,
        scientific_claim_upgrade_permitted=False,
        trust_gate_evaluated=False,
        summary_line="",
    )
    return replace(imported, summary_line=("Prospective assay import: " f"status={imported.import_status}; trust_gate_evaluated=false; scientific_claim_upgrade=false."))


def _required_object(payload: Mapping[str, Any], field: str, location: str, issues: list[ProspectiveAssayImportIssue]) -> Mapping[str, Any]:
    value = payload.get(field)
    if not isinstance(value, Mapping):
        issues.append(_issue(f"assay_result_{field}_invalid", location, "Object required."))
        return {}
    return value


def _required_string(payload: Mapping[str, Any], field: str, location: str, issues: list[ProspectiveAssayImportIssue]) -> str | None:
    value = _nonempty_string(payload.get(field))
    if value is None:
        issues.append(_issue(f"assay_result_{field}_missing", location, "Non-empty string required."))
    return value


def _required_sha256(payload: Mapping[str, Any], field: str, location: str, issues: list[ProspectiveAssayImportIssue]) -> str | None:
    value = _required_string(payload, field, location, issues)
    if value is not None and (len(value) != 64 or any(char not in "0123456789abcdef" for char in value)):
        issues.append(_issue(f"assay_result_{field}_invalid", location, "Lowercase SHA-256 required."))
        return None
    return value


def _timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None and parsed.utcoffset() is not None else None


def _finite_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    numeric = float(value)
    return numeric if math.isfinite(numeric) else None


def _nonnegative_number(value: Any) -> float | None:
    numeric = _finite_number(value)
    return numeric if numeric is not None and numeric >= 0 else None


def _nonempty_string(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _issue(code: str, location: str, message: str) -> ProspectiveAssayImportIssue:
    return ProspectiveAssayImportIssue(code=code, location=location, message=message)
