from __future__ import annotations

import json
import math
from collections.abc import Mapping
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .artifact_hash import canonical_content_sha256, json_ready
from .prospective_evaluation_power_plan import (
    PROSPECTIVE_LOOP_EVALUATION_POWER_PLAN_SCHEMA,
    ProspectiveLoopEvaluationPowerPlanVerifier,
)
from .prospective_policy_runtime import policy_from_spec, policy_runtime_manifest


PROSPECTIVE_LOOP_EVALUATION_PROTOCOL_SCHEMA = (
    "prospective_loop_evaluation_protocol.v5"
)
PROSPECTIVE_LOOP_EVALUATION_VERIFICATION_SCHEMA = (
    "prospective_loop_evaluation_verification.v2"
)
PROSPECTIVE_ACQUISITION_PLAN_SCHEMA = "prospective_acquisition_plan.v1"

_ARM_ROLES = ("baseline", "credibility_aware")
_REQUIRED_SECONDARY_METRICS = {
    "prospective_promotion_precision",
    "cost_per_assay_validated_candidate",
    "proxy_reversal_rate",
    "unresolved_credibility_rate",
}
_PRIMARY_OUTCOME_METRIC_ID = "prospective_assay_validated_candidate_rate"
_PRIMARY_DENOMINATOR_KIND = "randomized_candidate_promotion_episodes"
_ADAPTATION_FIELDS = (
    "online_rethresholding_permitted",
    "policy_parameter_updates_permitted",
    "arm_switching_permitted",
    "outcome_dependent_early_stopping_permitted",
)
_AUTHORITY_FIELDS = (
    "policy_ranking_permitted",
    "execution_authority_granted",
    "autonomous_execution_permitted",
    "scientific_claim_upgrade_permitted",
)


@dataclass(frozen=True)
class ProspectiveEvaluationArm:
    arm_id: str
    role: str
    policy_spec: dict[str, Any]
    policy_spec_content_sha256: str
    policy_runtime_manifest: dict[str, Any]
    policy_runtime_content_sha256: str


@dataclass(frozen=True)
class ProspectiveLoopEvaluationProtocol:
    """A frozen study declaration, never an execution or claim permission."""

    schema_version: str
    protocol_id: str
    registered_at: str
    target_population_id: str
    study_scope: dict[str, Any]
    prospective_split: dict[str, Any]
    source_prospective_acquisition_plan_content_sha256: str
    source_power_plan_content_sha256: str
    source_power_plan_id: str
    arms: tuple[ProspectiveEvaluationArm, ...]
    shared_budget: dict[str, Any]
    primary_outcome: dict[str, Any]
    secondary_metric_ids: tuple[str, ...]
    analysis_plan: dict[str, Any]
    adaptation_controls: dict[str, Any]
    bias_controls: dict[str, Any]
    human_acceptance: dict[str, Any]
    policy_ranking_permitted: bool
    execution_authority_granted: bool
    autonomous_execution_permitted: bool
    scientific_claim_upgrade_permitted: bool
    summary_line: str

    def to_dict(self) -> dict[str, Any]:
        return json_ready(asdict(self))


@dataclass(frozen=True)
class ProspectiveLoopEvaluationProtocolIssue:
    code: str
    location: str
    message: str


@dataclass(frozen=True)
class ProspectiveLoopEvaluationVerification:
    schema_version: str
    verification_status: str
    verified_at: str
    source_protocol_path: str
    source_protocol_content_sha256: str | None
    source_prospective_acquisition_plan_path: str
    source_prospective_acquisition_plan_content_sha256: str | None
    source_power_plan_path: str
    source_power_plan_content_sha256: str | None
    protocol_id: str | None
    target_population_id: str | None
    issue_codes: tuple[str, ...]
    issues: tuple[ProspectiveLoopEvaluationProtocolIssue, ...]
    policy_ranking_permitted: bool
    execution_authority_granted: bool
    autonomous_execution_permitted: bool
    scientific_claim_upgrade_permitted: bool
    summary_line: str

    def to_dict(self) -> dict[str, Any]:
        return json_ready(asdict(self))


class ProspectiveLoopEvaluationProtocolRegistrar:
    """Freeze a human-reviewed loop-study declaration against a candidate pool plan."""

    def register(
        self,
        declaration: Mapping[str, Any],
        prospective_acquisition_plan: Mapping[str, Any],
        power_plan: Mapping[str, Any],
        *,
        registered_at: str | None = None,
    ) -> ProspectiveLoopEvaluationProtocol:
        if not isinstance(declaration, Mapping):
            raise ValueError("evaluation declaration must be an object")
        timestamp = registered_at or datetime.now(timezone.utc).isoformat()
        _parse_timestamp(timestamp, "registered_at")
        arms = _registered_arms(declaration.get("arms"))
        protocol = ProspectiveLoopEvaluationProtocol(
            schema_version=PROSPECTIVE_LOOP_EVALUATION_PROTOCOL_SCHEMA,
            protocol_id=_required_string(declaration, "protocol_id"),
            registered_at=timestamp,
            target_population_id=_required_string(declaration, "target_population_id"),
            study_scope=_required_object(declaration, "study_scope"),
            prospective_split=_required_object(declaration, "prospective_split"),
            source_prospective_acquisition_plan_content_sha256=canonical_content_sha256(
                prospective_acquisition_plan
            ),
            source_power_plan_content_sha256=canonical_content_sha256(power_plan),
            source_power_plan_id=_required_string(power_plan, "power_plan_id"),
            arms=arms,
            shared_budget=_required_object(declaration, "shared_budget"),
            primary_outcome=_required_object(declaration, "primary_outcome"),
            secondary_metric_ids=_required_string_items(
                declaration,
                "secondary_metric_ids",
            ),
            analysis_plan=_required_object(declaration, "analysis_plan"),
            adaptation_controls=_required_object(declaration, "adaptation_controls"),
            bias_controls=_required_object(declaration, "bias_controls"),
            human_acceptance=_required_object(declaration, "human_acceptance"),
            policy_ranking_permitted=False,
            execution_authority_granted=False,
            autonomous_execution_permitted=False,
            scientific_claim_upgrade_permitted=False,
            summary_line="",
        )
        issues = _validation_issues(
            protocol.to_dict(), prospective_acquisition_plan, power_plan
        )
        if issues:
            codes = ", ".join(issue.code for issue in issues)
            raise ValueError(f"evaluation declaration is invalid: {codes}")
        return replace(protocol, summary_line=_protocol_summary_line(protocol))


class ProspectiveLoopEvaluationProtocolVerifier:
    """Read-only verifier for a frozen prospective loop-study protocol."""

    def verify_path(
        self,
        protocol_path: str | Path,
        prospective_acquisition_plan_path: str | Path,
        power_plan_path: str | Path,
        *,
        verified_at: str | None = None,
    ) -> ProspectiveLoopEvaluationVerification:
        protocol_source = Path(protocol_path)
        plan_source = Path(prospective_acquisition_plan_path)
        power_plan_source = Path(power_plan_path)
        timestamp = verified_at or datetime.now(timezone.utc).isoformat()
        protocol, protocol_issue = _read_object(protocol_source, "protocol")
        plan, plan_issue = _read_object(plan_source, "prospective_acquisition_plan")
        power_plan, power_plan_issue = _read_object(power_plan_source, "power_plan")
        issues = tuple(
            issue
            for issue in (protocol_issue, plan_issue, power_plan_issue)
            if issue is not None
        )
        return self._verification(
            protocol,
            plan,
            power_plan,
            protocol_path=protocol_source,
            plan_path=plan_source,
            power_plan_path=power_plan_source,
            verified_at=timestamp,
            initial_issues=issues,
        )

    def verify(
        self,
        protocol: Mapping[str, Any],
        prospective_acquisition_plan: Mapping[str, Any],
        power_plan: Mapping[str, Any],
        *,
        verified_at: str | None = None,
    ) -> ProspectiveLoopEvaluationVerification:
        timestamp = verified_at or datetime.now(timezone.utc).isoformat()
        return self._verification(
            protocol,
            prospective_acquisition_plan,
            power_plan,
            protocol_path=Path("<in-memory>"),
            plan_path=Path("<in-memory>"),
            power_plan_path=Path("<in-memory>"),
            verified_at=timestamp,
            initial_issues=(),
        )

    def _verification(
        self,
        protocol: Mapping[str, Any] | None,
        prospective_acquisition_plan: Mapping[str, Any] | None,
        power_plan: Mapping[str, Any] | None,
        *,
        protocol_path: Path,
        plan_path: Path,
        power_plan_path: Path,
        verified_at: str,
        initial_issues: tuple[ProspectiveLoopEvaluationProtocolIssue, ...],
    ) -> ProspectiveLoopEvaluationVerification:
        _parse_timestamp(verified_at, "verified_at")
        issues = list(initial_issues)
        if protocol is None:
            issues.append(_issue("protocol_unavailable", "$.protocol"))
        if prospective_acquisition_plan is None:
            issues.append(_issue("prospective_acquisition_plan_unavailable", "$.plan"))
        if power_plan is None:
            issues.append(_issue("power_plan_unavailable", "$.power_plan"))
        if protocol is not None and prospective_acquisition_plan is not None and power_plan is not None:
            issues.extend(_validation_issues(protocol, prospective_acquisition_plan, power_plan))
        result = ProspectiveLoopEvaluationVerification(
            schema_version=PROSPECTIVE_LOOP_EVALUATION_VERIFICATION_SCHEMA,
            verification_status="PASS" if not issues else "FAIL",
            verified_at=verified_at,
            source_protocol_path=str(protocol_path),
            source_protocol_content_sha256=(
                canonical_content_sha256(protocol) if protocol is not None else None
            ),
            source_prospective_acquisition_plan_path=str(plan_path),
            source_prospective_acquisition_plan_content_sha256=(
                canonical_content_sha256(prospective_acquisition_plan)
                if prospective_acquisition_plan is not None
                else None
            ),
            source_power_plan_path=str(power_plan_path),
            source_power_plan_content_sha256=(
                canonical_content_sha256(power_plan) if power_plan is not None else None
            ),
            protocol_id=_optional_string(protocol.get("protocol_id")) if protocol else None,
            target_population_id=(
                _optional_string(protocol.get("target_population_id"))
                if protocol
                else None
            ),
            issue_codes=tuple(sorted({issue.code for issue in issues})),
            issues=tuple(issues),
            policy_ranking_permitted=False,
            execution_authority_granted=False,
            autonomous_execution_permitted=False,
            scientific_claim_upgrade_permitted=False,
            summary_line="",
        )
        return replace(result, summary_line=_verification_summary_line(result))


def _registered_arms(value: Any) -> tuple[ProspectiveEvaluationArm, ...]:
    if not isinstance(value, list | tuple):
        raise ValueError("arms must be a list")
    arms: list[ProspectiveEvaluationArm] = []
    for index, item in enumerate(value):
        if not isinstance(item, Mapping):
            raise ValueError(f"arms[{index}] must be an object")
        arm_id = _required_string(item, "arm_id")
        role = _required_string(item, "role")
        policy_spec = _required_object(item, "policy_spec")
        try:
            runtime_manifest = policy_runtime_manifest(policy_spec)
        except ValueError:
            runtime_manifest = {}
        arms.append(
            ProspectiveEvaluationArm(
                arm_id=arm_id,
                role=role,
                policy_spec=policy_spec,
                policy_spec_content_sha256=canonical_content_sha256(policy_spec),
                policy_runtime_manifest=runtime_manifest,
                policy_runtime_content_sha256=canonical_content_sha256(runtime_manifest),
            )
        )
    return tuple(arms)


def _validation_issues(
    protocol: Mapping[str, Any],
    prospective_acquisition_plan: Mapping[str, Any],
    power_plan: Mapping[str, Any],
) -> list[ProspectiveLoopEvaluationProtocolIssue]:
    issues: list[ProspectiveLoopEvaluationProtocolIssue] = []
    _validate_acquisition_plan(prospective_acquisition_plan, issues)
    _validate_protocol_identity(protocol, prospective_acquisition_plan, power_plan, issues)
    _validate_power_plan(protocol, power_plan, issues)
    _validate_study_scope(protocol.get("study_scope"), issues)
    _validate_split(protocol.get("prospective_split"), issues)
    _validate_arms(protocol.get("arms"), issues)
    _validate_shared_budget(protocol.get("shared_budget"), issues)
    _validate_outcomes(protocol, issues)
    _validate_analysis(protocol.get("analysis_plan"), issues)
    _validate_adaptation_controls(protocol.get("adaptation_controls"), issues)
    _validate_bias_controls(protocol.get("bias_controls"), issues)
    _validate_human_acceptance(protocol, issues)
    _validate_authority_fields(protocol, issues)
    return issues


def _validate_acquisition_plan(
    plan: Mapping[str, Any],
    issues: list[ProspectiveLoopEvaluationProtocolIssue],
) -> None:
    if plan.get("schema_version") != PROSPECTIVE_ACQUISITION_PLAN_SCHEMA:
        issues.append(_issue("prospective_acquisition_plan_schema_invalid", "$.plan.schema_version"))
    if _optional_string(plan.get("target_population_id")) is None:
        issues.append(_issue("prospective_acquisition_plan_population_missing", "$.plan.target_population_id"))
    if plan.get("external_randomization_method") != "independent_external_random_sampling":
        issues.append(_issue("prospective_acquisition_plan_randomization_method_invalid", "$.plan.external_randomization_method"))
    if plan.get("agent_subject_selection_permitted") is not False:
        issues.append(_issue("prospective_acquisition_plan_agent_selection_not_disabled", "$.plan.agent_subject_selection_permitted"))
    requests = plan.get("requests")
    if not isinstance(requests, list | tuple) or not requests:
        issues.append(_issue("prospective_acquisition_plan_requests_missing", "$.plan.requests"))
        return
    for index, request in enumerate(requests):
        location = f"$.plan.requests[{index}]"
        if not isinstance(request, Mapping):
            issues.append(_issue("prospective_acquisition_plan_request_invalid", location))
            continue
        if request.get("external_randomization_required") is not True:
            issues.append(_issue("prospective_acquisition_request_randomization_not_required", location))
        if request.get("human_approval_required") is not True:
            issues.append(_issue("prospective_acquisition_request_human_approval_not_required", location))


def _validate_protocol_identity(
    protocol: Mapping[str, Any],
    plan: Mapping[str, Any],
    power_plan: Mapping[str, Any],
    issues: list[ProspectiveLoopEvaluationProtocolIssue],
) -> None:
    if protocol.get("schema_version") != PROSPECTIVE_LOOP_EVALUATION_PROTOCOL_SCHEMA:
        issues.append(_issue("protocol_schema_invalid", "$.protocol.schema_version"))
    if _optional_string(protocol.get("protocol_id")) is None:
        issues.append(_issue("protocol_id_missing", "$.protocol.protocol_id"))
    if _timestamp_or_none(protocol.get("registered_at")) is None:
        issues.append(_issue("protocol_registered_at_invalid", "$.protocol.registered_at"))
    if _optional_string(protocol.get("target_population_id")) is None:
        issues.append(_issue("protocol_target_population_missing", "$.protocol.target_population_id"))
    elif protocol.get("target_population_id") != plan.get("target_population_id"):
        issues.append(_issue("protocol_target_population_mismatch", "$.protocol.target_population_id"))
    if (
        protocol.get("source_prospective_acquisition_plan_content_sha256")
        != canonical_content_sha256(plan)
    ):
        issues.append(_issue("protocol_acquisition_plan_content_hash_mismatch", "$.protocol.source_prospective_acquisition_plan_content_sha256"))
    if protocol.get("source_power_plan_content_sha256") != canonical_content_sha256(power_plan):
        issues.append(_issue("protocol_power_plan_content_hash_mismatch", "$.protocol.source_power_plan_content_sha256"))
    if protocol.get("source_power_plan_id") != power_plan.get("power_plan_id"):
        issues.append(_issue("protocol_power_plan_id_mismatch", "$.protocol.source_power_plan_id"))


def _validate_power_plan(
    protocol: Mapping[str, Any],
    power_plan: Mapping[str, Any],
    issues: list[ProspectiveLoopEvaluationProtocolIssue],
) -> None:
    power_plan_verification = ProspectiveLoopEvaluationPowerPlanVerifier().verify(power_plan)
    if power_plan_verification.verification_status != "PASS":
        issues.append(_issue("protocol_power_plan_artifact_verification_failed", "$.power_plan"))
    if power_plan.get("schema_version") != PROSPECTIVE_LOOP_EVALUATION_POWER_PLAN_SCHEMA:
        issues.append(_issue("protocol_power_plan_schema_invalid", "$.power_plan.schema_version"))
    if power_plan.get("readiness_status") != "READY_FOR_PROTOCOL_REGISTRATION":
        issues.append(_issue("protocol_power_plan_not_feasible", "$.power_plan.readiness_status"))
    if power_plan.get("protocol_design_feasible") is not True:
        issues.append(_issue("protocol_power_plan_design_not_feasible", "$.power_plan.protocol_design_feasible"))
    if power_plan.get("target_population_id") != protocol.get("target_population_id"):
        issues.append(_issue("protocol_power_plan_population_mismatch", "$.power_plan.target_population_id"))
    if _optional_string(power_plan.get("planned_assay_site_id")) is None:
        issues.append(_issue("protocol_power_plan_assay_site_missing", "$.power_plan.planned_assay_site_id"))

    primary = protocol.get("primary_outcome")
    if not isinstance(primary, Mapping):
        return
    expected = {
        "primary_outcome_metric_id": primary.get("metric_id"),
        "primary_outcome_validation_oracle_kind": primary.get("validation_oracle_kind"),
        "primary_outcome_metric_key": primary.get("outcome_metric_key"),
        "primary_outcome_positive_value": primary.get("positive_outcome_value"),
        "primary_outcome_denominator_kind": primary.get("denominator_kind"),
    }
    for field, expected_value in expected.items():
        if power_plan.get(field) != expected_value:
            issues.append(_issue(f"protocol_power_plan_{field}_mismatch", f"$.power_plan.{field}"))

    protocol_budget = protocol.get("shared_budget")
    protocol_per_arm_budget = (
        _positive_finite(protocol_budget.get("per_arm_experiment_budget_limit"))
        if isinstance(protocol_budget, Mapping)
        else None
    )
    planned_budget = _positive_finite(power_plan.get("declared_per_arm_experiment_budget_limit"))
    if protocol_per_arm_budget is None or planned_budget is None or not math.isclose(
        protocol_per_arm_budget,
        planned_budget,
        rel_tol=0.0,
        abs_tol=1e-9,
    ):
        issues.append(_issue("protocol_power_plan_per_arm_budget_mismatch", "$.power_plan.declared_per_arm_experiment_budget_limit"))

    plan_time = _timestamp_or_none(power_plan.get("planned_at"))
    registered_at = _timestamp_or_none(protocol.get("registered_at"))
    if plan_time is None:
        issues.append(_issue("protocol_power_plan_planned_at_invalid", "$.power_plan.planned_at"))
    elif registered_at is not None and plan_time > registered_at:
        issues.append(_issue("protocol_power_plan_after_registration", "$.power_plan.planned_at"))
    acceptance = power_plan.get("human_acceptance")
    if not isinstance(acceptance, Mapping) or acceptance.get("decision") != "accept":
        issues.append(_issue("protocol_power_plan_human_acceptance_invalid", "$.power_plan.human_acceptance"))
    else:
        accepted_at = _timestamp_or_none(acceptance.get("reviewed_at"))
        if accepted_at is None:
            issues.append(_issue("protocol_power_plan_human_acceptance_time_invalid", "$.power_plan.human_acceptance.reviewed_at"))
        elif registered_at is not None and accepted_at > registered_at:
            issues.append(_issue("protocol_power_plan_human_acceptance_after_registration", "$.power_plan.human_acceptance.reviewed_at"))
    for field in _AUTHORITY_FIELDS:
        if power_plan.get(field) is not False:
            issues.append(_issue(f"protocol_power_plan_{field}_must_be_false", f"$.power_plan.{field}"))


def _validate_split(
    value: Any,
    issues: list[ProspectiveLoopEvaluationProtocolIssue],
) -> None:
    if not isinstance(value, Mapping):
        issues.append(_issue("prospective_split_invalid", "$.protocol.prospective_split"))
        return
    if _optional_string(value.get("split_id")) is None:
        issues.append(_issue("prospective_split_id_missing", "$.protocol.prospective_split.split_id"))
    if value.get("split_kind") != "prospective":
        issues.append(_issue("prospective_split_kind_invalid", "$.protocol.prospective_split.split_kind"))
    if value.get("outcomes_observed_after_registration") is not True:
        issues.append(_issue("prospective_outcomes_not_declared_post_registration", "$.protocol.prospective_split.outcomes_observed_after_registration"))


def _validate_study_scope(
    value: Any,
    issues: list[ProspectiveLoopEvaluationProtocolIssue],
) -> None:
    if not isinstance(value, Mapping):
        issues.append(_issue("study_scope_invalid", "$.protocol.study_scope"))
        return
    expected = {
        "evaluation_unit": "candidate_promotion_episode",
        "policy_scope": "promotion_policy_only",
        "candidate_population_allocation": (
            "independent_external_randomization_from_fixed_candidate_pools"
        ),
    }
    for field, expected_value in expected.items():
        if value.get(field) != expected_value:
            issues.append(_issue(f"study_scope_{field}_invalid", f"$.protocol.study_scope.{field}"))


def _validate_arms(
    value: Any,
    issues: list[ProspectiveLoopEvaluationProtocolIssue],
) -> None:
    if not isinstance(value, list | tuple) or len(value) != len(_ARM_ROLES):
        issues.append(_issue("evaluation_arms_invalid", "$.protocol.arms"))
        return
    arm_ids: list[str] = []
    roles: list[str] = []
    policy_hashes: list[str] = []
    for index, arm in enumerate(value):
        location = f"$.protocol.arms[{index}]"
        if not isinstance(arm, Mapping):
            issues.append(_issue("evaluation_arm_invalid", location))
            continue
        arm_id = _optional_string(arm.get("arm_id"))
        role = _optional_string(arm.get("role"))
        policy_spec = arm.get("policy_spec")
        if arm_id is None:
            issues.append(_issue("evaluation_arm_id_missing", location))
        else:
            arm_ids.append(arm_id)
        if role not in _ARM_ROLES:
            issues.append(_issue("evaluation_arm_role_invalid", location))
        else:
            roles.append(role)
        if not isinstance(policy_spec, Mapping):
            issues.append(_issue("evaluation_arm_policy_spec_invalid", location))
            continue
        if _optional_string(policy_spec.get("policy_kind")) is None:
            issues.append(_issue("evaluation_arm_policy_kind_missing", location))
        if not isinstance(policy_spec.get("parameters"), Mapping):
            issues.append(_issue("evaluation_arm_policy_parameters_invalid", location))
        else:
            try:
                policy_from_spec(policy_spec)
            except ValueError:
                issues.append(_issue("evaluation_arm_policy_not_replayable", location))
            else:
                runtime_manifest = policy_runtime_manifest(policy_spec)
                runtime_hash = canonical_content_sha256(runtime_manifest)
                if arm.get("policy_runtime_manifest") != runtime_manifest:
                    issues.append(_issue("evaluation_arm_policy_runtime_manifest_mismatch", location))
                if arm.get("policy_runtime_content_sha256") != runtime_hash:
                    issues.append(_issue("evaluation_arm_policy_runtime_hash_mismatch", location))
        policy_hash = canonical_content_sha256(policy_spec)
        if arm.get("policy_spec_content_sha256") != policy_hash:
            issues.append(_issue("evaluation_arm_policy_hash_mismatch", location))
        else:
            policy_hashes.append(policy_hash)
    if len(set(arm_ids)) != len(_ARM_ROLES):
        issues.append(_issue("evaluation_arm_ids_not_unique", "$.protocol.arms"))
    if set(roles) != set(_ARM_ROLES):
        issues.append(_issue("evaluation_arm_roles_incomplete", "$.protocol.arms"))
    if len(policy_hashes) == len(_ARM_ROLES) and len(set(policy_hashes)) != len(_ARM_ROLES):
        issues.append(_issue("evaluation_arms_not_distinct", "$.protocol.arms"))


def _validate_shared_budget(
    value: Any,
    issues: list[ProspectiveLoopEvaluationProtocolIssue],
) -> None:
    if not isinstance(value, Mapping):
        issues.append(_issue("shared_budget_invalid", "$.protocol.shared_budget"))
        return
    if value.get("budget_currency") != "declared_cost_units":
        issues.append(_issue("shared_budget_currency_invalid", "$.protocol.shared_budget.budget_currency"))
    for field in ("compute_budget_limit", "experiment_budget_limit"):
        if _positive_finite(value.get(field)) is None:
            issues.append(_issue(f"shared_budget_{field}_invalid", f"$.protocol.shared_budget.{field}"))
    if _nonnegative_finite(value.get("human_review_budget_limit")) is None:
        issues.append(_issue("shared_budget_human_review_budget_limit_invalid", "$.protocol.shared_budget.human_review_budget_limit"))
    per_arm_limit = _positive_finite(value.get("per_arm_experiment_budget_limit"))
    experiment_limit = _positive_finite(value.get("experiment_budget_limit"))
    if per_arm_limit is None:
        issues.append(_issue("shared_budget_per_arm_experiment_budget_limit_invalid", "$.protocol.shared_budget.per_arm_experiment_budget_limit"))
    elif experiment_limit is not None and not math.isclose(
        experiment_limit,
        per_arm_limit * len(_ARM_ROLES),
        rel_tol=0.0,
        abs_tol=1e-9,
    ):
        issues.append(_issue("shared_budget_per_arm_experiment_budget_not_equal_allocation", "$.protocol.shared_budget.per_arm_experiment_budget_limit"))


def _validate_outcomes(
    protocol: Mapping[str, Any],
    issues: list[ProspectiveLoopEvaluationProtocolIssue],
) -> None:
    primary = protocol.get("primary_outcome")
    if not isinstance(primary, Mapping):
        issues.append(_issue("primary_outcome_invalid", "$.protocol.primary_outcome"))
    else:
        if primary.get("metric_id") != _PRIMARY_OUTCOME_METRIC_ID:
            issues.append(_issue("primary_outcome_metric_invalid", "$.protocol.primary_outcome.metric_id"))
        if primary.get("validation_oracle_kind") != "assay":
            issues.append(_issue("primary_outcome_not_assay_validated", "$.protocol.primary_outcome.validation_oracle_kind"))
        for field in ("numerator_definition", "denominator_definition"):
            if _optional_string(primary.get(field)) is None:
                issues.append(_issue(f"primary_outcome_{field}_missing", f"$.protocol.primary_outcome.{field}"))
        if _optional_string(primary.get("outcome_metric_key")) is None:
            issues.append(_issue("primary_outcome_metric_key_missing", "$.protocol.primary_outcome.outcome_metric_key"))
        if primary.get("denominator_kind") != _PRIMARY_DENOMINATOR_KIND:
            issues.append(_issue("primary_outcome_denominator_kind_invalid", "$.protocol.primary_outcome.denominator_kind"))
        if not isinstance(primary.get("positive_outcome_value"), bool):
            issues.append(_issue("primary_outcome_positive_value_invalid", "$.protocol.primary_outcome.positive_outcome_value"))
    metrics = protocol.get("secondary_metric_ids")
    if not isinstance(metrics, list | tuple) or not all(
        _optional_string(metric) is not None for metric in metrics
    ):
        issues.append(_issue("secondary_metrics_invalid", "$.protocol.secondary_metric_ids"))
        return
    metric_set = set(metrics)
    if len(metric_set) != len(metrics):
        issues.append(_issue("secondary_metrics_not_unique", "$.protocol.secondary_metric_ids"))
    if not _REQUIRED_SECONDARY_METRICS.issubset(metric_set):
        issues.append(_issue("secondary_metrics_incomplete", "$.protocol.secondary_metric_ids"))


def _validate_analysis(
    value: Any,
    issues: list[ProspectiveLoopEvaluationProtocolIssue],
) -> None:
    if not isinstance(value, Mapping):
        issues.append(_issue("analysis_plan_invalid", "$.protocol.analysis_plan"))
        return
    expected = {
        "primary_analysis": "effect_size_with_confidence_interval",
        "confidence_interval_method": "newcombe_wilson_95",
        "missing_outcome_handling": "report_missing_separately_not_negative",
        "multiple_comparison_handling": "primary_confirmatory_secondary_exploratory",
    }
    for field, expected_value in expected.items():
        if value.get(field) != expected_value:
            issues.append(_issue(f"analysis_plan_{field}_invalid", f"$.protocol.analysis_plan.{field}"))


def _validate_adaptation_controls(
    value: Any,
    issues: list[ProspectiveLoopEvaluationProtocolIssue],
) -> None:
    if not isinstance(value, Mapping):
        issues.append(_issue("adaptation_controls_invalid", "$.protocol.adaptation_controls"))
        return
    for field in _ADAPTATION_FIELDS:
        if value.get(field) is not False:
            issues.append(_issue(f"adaptation_control_{field}_must_be_false", f"$.protocol.adaptation_controls.{field}"))


def _validate_bias_controls(
    value: Any,
    issues: list[ProspectiveLoopEvaluationProtocolIssue],
) -> None:
    if not isinstance(value, Mapping):
        issues.append(_issue("bias_controls_invalid", "$.protocol.bias_controls"))
        return
    expected = {
        "arm_assignment_method": "independent_external_randomization",
        "assignment_before_outcomes_observed": True,
        "outcome_assessor_blinded_to_arm": True,
        "all_randomized_subjects_reported": True,
    }
    for field, expected_value in expected.items():
        if value.get(field) != expected_value:
            issues.append(_issue(f"bias_control_{field}_invalid", f"$.protocol.bias_controls.{field}"))


def _validate_human_acceptance(
    protocol: Mapping[str, Any],
    issues: list[ProspectiveLoopEvaluationProtocolIssue],
) -> None:
    value = protocol.get("human_acceptance")
    if not isinstance(value, Mapping):
        issues.append(_issue("human_acceptance_invalid", "$.protocol.human_acceptance"))
        return
    if value.get("decision") != "accept":
        issues.append(_issue("human_acceptance_not_accepted", "$.protocol.human_acceptance.decision"))
    for field in ("reviewer", "rationale"):
        if _optional_string(value.get(field)) is None:
            issues.append(_issue(f"human_acceptance_{field}_missing", f"$.protocol.human_acceptance.{field}"))
    reviewed_at = _timestamp_or_none(value.get("reviewed_at"))
    registered_at = _timestamp_or_none(protocol.get("registered_at"))
    if reviewed_at is None:
        issues.append(_issue("human_acceptance_reviewed_at_invalid", "$.protocol.human_acceptance.reviewed_at"))
    elif registered_at is not None and reviewed_at > registered_at:
        issues.append(_issue("human_acceptance_after_registration", "$.protocol.human_acceptance.reviewed_at"))


def _validate_authority_fields(
    protocol: Mapping[str, Any],
    issues: list[ProspectiveLoopEvaluationProtocolIssue],
) -> None:
    for field in _AUTHORITY_FIELDS:
        if protocol.get(field) is not False:
            issues.append(_issue(f"protocol_{field}_must_be_false", f"$.protocol.{field}"))


def _read_object(
    path: Path,
    name: str,
) -> tuple[Mapping[str, Any] | None, ProspectiveLoopEvaluationProtocolIssue | None]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError:
        return None, _issue(f"{name}_unreadable", f"$.{name}")
    except json.JSONDecodeError:
        return None, _issue(f"{name}_invalid_json", f"$.{name}")
    if not isinstance(payload, Mapping):
        return None, _issue(f"{name}_not_object", f"$.{name}")
    return payload, None


def _required_string(payload: Mapping[str, Any], field: str) -> str:
    value = _optional_string(payload.get(field))
    if value is None:
        raise ValueError(f"{field} must be a non-empty string")
    return value


def _required_object(payload: Mapping[str, Any], field: str) -> dict[str, Any]:
    value = payload.get(field)
    if not isinstance(value, Mapping):
        raise ValueError(f"{field} must be an object")
    return dict(value)


def _required_string_items(payload: Mapping[str, Any], field: str) -> tuple[str, ...]:
    value = payload.get(field)
    if not isinstance(value, list | tuple) or not value:
        raise ValueError(f"{field} must be a non-empty list")
    items = tuple(_optional_string(item) for item in value)
    if any(item is None for item in items):
        raise ValueError(f"{field} must contain non-empty strings")
    return tuple(item for item in items if item is not None)


def _optional_string(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _parse_timestamp(value: str, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{field} must be ISO-8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field} must include timezone")
    return parsed


def _timestamp_or_none(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return _parse_timestamp(value, "timestamp")
    except ValueError:
        return None


def _positive_finite(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    result = float(value)
    return result if math.isfinite(result) and result > 0 else None


def _nonnegative_finite(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    result = float(value)
    return result if math.isfinite(result) and result >= 0 else None


def _issue(code: str, location: str) -> ProspectiveLoopEvaluationProtocolIssue:
    return ProspectiveLoopEvaluationProtocolIssue(
        code=code,
        location=location,
        message=code.replace("_", " ") + ".",
    )


def _protocol_summary_line(protocol: ProspectiveLoopEvaluationProtocol) -> str:
    return (
        "Prospective loop evaluation protocol registered: "
        f"protocol_id={protocol.protocol_id}; arms={len(protocol.arms)}; "
        "policy_ranking=false; execution_authority=false; "
        "scientific_claim_upgrade=false."
    )


def _verification_summary_line(
    result: ProspectiveLoopEvaluationVerification,
) -> str:
    return (
        "Prospective loop evaluation protocol verification: "
        f"status={result.verification_status}; issues={len(result.issues)}; "
        "policy_ranking=false; execution_authority=false; "
        "scientific_claim_upgrade=false."
    )
