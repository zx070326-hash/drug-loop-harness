from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .artifact_hash import canonical_content_sha256, json_ready
from .prospective_evaluation_assignment import (
    PROSPECTIVE_LOOP_EVALUATION_ARM_ASSIGNMENT_SCHEMA,
)
from .prospective_evaluation_protocol import (
    ProspectiveLoopEvaluationProtocolVerifier,
)


PROSPECTIVE_LOOP_EVALUATION_REPLICATION_DESIGN_DECLARATION_SCHEMA = (
    "prospective_loop_evaluation_replication_design_declaration.v1"
)
PROSPECTIVE_LOOP_EVALUATION_REPLICATION_DESIGN_VERIFICATION_SCHEMA = (
    "prospective_loop_evaluation_replication_design_verification.v1"
)
_AUTHORITY_FIELDS = (
    "policy_ranking_permitted",
    "execution_authority_granted",
    "autonomous_execution_permitted",
    "scientific_claim_upgrade_permitted",
)
_PRIMARY_OUTCOME_FIELDS = (
    "primary_outcome_metric_id",
    "primary_outcome_validation_oracle_kind",
    "primary_outcome_metric_key",
    "primary_outcome_positive_value",
    "primary_outcome_denominator_kind",
)


@dataclass(frozen=True)
class ProspectiveLoopEvaluationReplicationDesignIssue:
    code: str
    location: str
    message: str


@dataclass(frozen=True)
class ProspectiveLoopEvaluationReplicationDesignVerification:
    """Read-only proof that an independent replication was predeclared in time."""

    schema_version: str
    verification_status: str
    replication_design_status: str
    verified_at: str
    replication_design_id: str | None
    source_protocol_content_sha256: str | None
    source_assignment_content_sha256: str | None
    replication_protocol_content_sha256: str | None
    source_target_population_id: str | None
    replication_target_population_id: str | None
    source_planned_assay_site_id: str | None
    replication_planned_assay_site_id: str | None
    independent_cohort_predeclared: bool
    independent_assay_site_predeclared: bool
    policy_intervention_matched: bool
    policy_ranking_permitted: bool
    execution_authority_granted: bool
    autonomous_execution_permitted: bool
    scientific_claim_upgrade_permitted: bool
    issue_codes: tuple[str, ...]
    issues: tuple[ProspectiveLoopEvaluationReplicationDesignIssue, ...]
    summary_line: str

    def to_dict(self) -> dict[str, Any]:
        return json_ready(asdict(self))


class ProspectiveLoopEvaluationReplicationDesignVerifier:
    """Check a second protocol was independently registered before source allocation."""

    def __init__(
        self,
        *,
        protocol_verifier: ProspectiveLoopEvaluationProtocolVerifier | None = None,
    ) -> None:
        self.protocol_verifier = protocol_verifier or ProspectiveLoopEvaluationProtocolVerifier()

    def verify_path(
        self,
        source_protocol_path: str | Path,
        source_acquisition_plan_path: str | Path,
        source_power_plan_path: str | Path,
        source_assignment_path: str | Path,
        replication_protocol_path: str | Path,
        replication_acquisition_plan_path: str | Path,
        replication_power_plan_path: str | Path,
        declaration_path: str | Path,
        *,
        verified_at: str | None = None,
    ) -> ProspectiveLoopEvaluationReplicationDesignVerification:
        sources = (
            (Path(source_protocol_path), "source_protocol"),
            (Path(source_acquisition_plan_path), "source_acquisition_plan"),
            (Path(source_power_plan_path), "source_power_plan"),
            (Path(source_assignment_path), "source_assignment"),
            (Path(replication_protocol_path), "replication_protocol"),
            (Path(replication_acquisition_plan_path), "replication_acquisition_plan"),
            (Path(replication_power_plan_path), "replication_power_plan"),
            (Path(declaration_path), "replication_design_declaration"),
        )
        payloads: list[Mapping[str, Any] | None] = []
        issues: list[ProspectiveLoopEvaluationReplicationDesignIssue] = []
        for path, name in sources:
            payload, issue = _read_object(path, name)
            payloads.append(payload)
            if issue is not None:
                issues.append(issue)
        return self._verify(
            *payloads,
            verified_at=verified_at,
            initial_issues=tuple(issues),
        )

    def verify(
        self,
        source_protocol: Mapping[str, Any],
        source_acquisition_plan: Mapping[str, Any],
        source_power_plan: Mapping[str, Any],
        source_assignment: Mapping[str, Any],
        replication_protocol: Mapping[str, Any],
        replication_acquisition_plan: Mapping[str, Any],
        replication_power_plan: Mapping[str, Any],
        declaration: Mapping[str, Any],
        *,
        verified_at: str | None = None,
    ) -> ProspectiveLoopEvaluationReplicationDesignVerification:
        return self._verify(
            source_protocol,
            source_acquisition_plan,
            source_power_plan,
            source_assignment,
            replication_protocol,
            replication_acquisition_plan,
            replication_power_plan,
            declaration,
            verified_at=verified_at,
            initial_issues=(),
        )

    def _verify(
        self,
        source_protocol: Mapping[str, Any] | None,
        source_acquisition_plan: Mapping[str, Any] | None,
        source_power_plan: Mapping[str, Any] | None,
        source_assignment: Mapping[str, Any] | None,
        replication_protocol: Mapping[str, Any] | None,
        replication_acquisition_plan: Mapping[str, Any] | None,
        replication_power_plan: Mapping[str, Any] | None,
        declaration: Mapping[str, Any] | None,
        *,
        verified_at: str | None,
        initial_issues: tuple[ProspectiveLoopEvaluationReplicationDesignIssue, ...],
    ) -> ProspectiveLoopEvaluationReplicationDesignVerification:
        timestamp = verified_at or datetime.now(timezone.utc).isoformat()
        _parse_timestamp(timestamp, "verified_at")
        issues = list(initial_issues)
        values = {
            "source_protocol": source_protocol,
            "source_acquisition_plan": source_acquisition_plan,
            "source_power_plan": source_power_plan,
            "source_assignment": source_assignment,
            "replication_protocol": replication_protocol,
            "replication_acquisition_plan": replication_acquisition_plan,
            "replication_power_plan": replication_power_plan,
            "replication_design_declaration": declaration,
        }
        for name, value in values.items():
            if value is None:
                issues.append(_issue(f"replication_design_{name}_unavailable", f"$.{name}"))

        if all(value is not None for value in values.values()):
            assert source_protocol is not None
            assert source_acquisition_plan is not None
            assert source_power_plan is not None
            assert source_assignment is not None
            assert replication_protocol is not None
            assert replication_acquisition_plan is not None
            assert replication_power_plan is not None
            assert declaration is not None
            _validate_protocol(
                self.protocol_verifier,
                source_protocol,
                source_acquisition_plan,
                source_power_plan,
                "source",
                issues,
            )
            _validate_protocol(
                self.protocol_verifier,
                replication_protocol,
                replication_acquisition_plan,
                replication_power_plan,
                "replication",
                issues,
            )
            _validate_declaration(declaration, source_assignment, issues)
            _validate_source_assignment(
                source_protocol,
                source_acquisition_plan,
                source_power_plan,
                source_assignment,
                issues,
            )
            _validate_independence(
                source_protocol,
                source_power_plan,
                source_assignment,
                replication_protocol,
                replication_power_plan,
                declaration,
                issues,
            )

        source_target = _string_or_none(
            source_protocol.get("target_population_id") if source_protocol else None
        )
        replication_target = _string_or_none(
            replication_protocol.get("target_population_id") if replication_protocol else None
        )
        source_site = _string_or_none(
            source_power_plan.get("planned_assay_site_id") if source_power_plan else None
        )
        replication_site = _string_or_none(
            replication_power_plan.get("planned_assay_site_id")
            if replication_power_plan
            else None
        )
        policy_matched = (
            source_protocol is not None
            and replication_protocol is not None
            and _arms_by_role(source_protocol) == _arms_by_role(replication_protocol)
        )
        result = ProspectiveLoopEvaluationReplicationDesignVerification(
            schema_version=PROSPECTIVE_LOOP_EVALUATION_REPLICATION_DESIGN_VERIFICATION_SCHEMA,
            verification_status="PASS" if not issues else "FAIL",
            replication_design_status=(
                "PREDECLARED_INDEPENDENT_REPLICATION_DESIGN"
                if not issues
                else "FAIL"
            ),
            verified_at=timestamp,
            replication_design_id=(
                _string_or_none(declaration.get("replication_design_id"))
                if declaration
                else None
            ),
            source_protocol_content_sha256=(
                canonical_content_sha256(source_protocol) if source_protocol else None
            ),
            source_assignment_content_sha256=(
                canonical_content_sha256(source_assignment) if source_assignment else None
            ),
            replication_protocol_content_sha256=(
                canonical_content_sha256(replication_protocol)
                if replication_protocol
                else None
            ),
            source_target_population_id=source_target,
            replication_target_population_id=replication_target,
            source_planned_assay_site_id=source_site,
            replication_planned_assay_site_id=replication_site,
            independent_cohort_predeclared=(
                source_target is not None
                and replication_target is not None
                and source_target != replication_target
                and source_power_plan is not None
                and source_power_plan.get("replication_cohort_id") == replication_target
            ),
            independent_assay_site_predeclared=(
                source_site is not None
                and replication_site is not None
                and source_site != replication_site
            ),
            policy_intervention_matched=policy_matched,
            policy_ranking_permitted=False,
            execution_authority_granted=False,
            autonomous_execution_permitted=False,
            scientific_claim_upgrade_permitted=False,
            issue_codes=tuple(sorted({issue.code for issue in issues})),
            issues=tuple(issues),
            summary_line="",
        )
        return replace(result, summary_line=_summary_line(result))


def _validate_protocol(
    verifier: ProspectiveLoopEvaluationProtocolVerifier,
    protocol: Mapping[str, Any],
    acquisition_plan: Mapping[str, Any],
    power_plan: Mapping[str, Any],
    label: str,
    issues: list[ProspectiveLoopEvaluationReplicationDesignIssue],
) -> None:
    verification = verifier.verify(protocol, acquisition_plan, power_plan)
    if verification.verification_status != "PASS":
        issues.append(_issue(f"replication_design_{label}_protocol_verification_failed", f"$.{label}_protocol"))


def _validate_declaration(
    declaration: Mapping[str, Any],
    source_assignment: Mapping[str, Any],
    issues: list[ProspectiveLoopEvaluationReplicationDesignIssue],
) -> None:
    if declaration.get("schema_version") != PROSPECTIVE_LOOP_EVALUATION_REPLICATION_DESIGN_DECLARATION_SCHEMA:
        issues.append(_issue("replication_design_declaration_schema_invalid", "$.replication_design_declaration.schema_version"))
    if _string_or_none(declaration.get("replication_design_id")) is None:
        issues.append(_issue("replication_design_id_missing", "$.replication_design_declaration.replication_design_id"))
    declared_at = _timestamp_or_none(declaration.get("declared_at"))
    assigned_at = _timestamp_or_none(source_assignment.get("assigned_at"))
    if declared_at is None:
        issues.append(_issue("replication_design_declared_at_invalid", "$.replication_design_declaration.declared_at"))
    elif assigned_at is not None and declared_at > assigned_at:
        issues.append(_issue("replication_design_declared_after_source_assignment", "$.replication_design_declaration.declared_at"))
    acceptance = declaration.get("human_acceptance")
    if not isinstance(acceptance, Mapping) or acceptance.get("decision") != "accept":
        issues.append(_issue("replication_design_human_acceptance_invalid", "$.replication_design_declaration.human_acceptance"))
    else:
        for field in ("reviewer", "rationale"):
            if _string_or_none(acceptance.get(field)) is None:
                issues.append(_issue(f"replication_design_human_acceptance_{field}_missing", f"$.replication_design_declaration.human_acceptance.{field}"))
        reviewed_at = _timestamp_or_none(acceptance.get("reviewed_at"))
        if reviewed_at is None:
            issues.append(_issue("replication_design_human_acceptance_time_invalid", "$.replication_design_declaration.human_acceptance.reviewed_at"))
        elif declared_at is not None and reviewed_at > declared_at:
            issues.append(_issue("replication_design_human_acceptance_after_declaration", "$.replication_design_declaration.human_acceptance.reviewed_at"))
    for field in _AUTHORITY_FIELDS:
        if declaration.get(field) is not False:
            issues.append(_issue(f"replication_design_{field}_must_be_false", f"$.replication_design_declaration.{field}"))


def _validate_source_assignment(
    protocol: Mapping[str, Any],
    acquisition_plan: Mapping[str, Any],
    power_plan: Mapping[str, Any],
    assignment: Mapping[str, Any],
    issues: list[ProspectiveLoopEvaluationReplicationDesignIssue],
) -> None:
    if assignment.get("schema_version") != PROSPECTIVE_LOOP_EVALUATION_ARM_ASSIGNMENT_SCHEMA:
        issues.append(_issue("replication_design_source_assignment_schema_invalid", "$.source_assignment.schema_version"))
    if assignment.get("source_protocol_content_sha256") != canonical_content_sha256(protocol):
        issues.append(_issue("replication_design_source_assignment_protocol_hash_mismatch", "$.source_assignment.source_protocol_content_sha256"))
    if assignment.get("source_prospective_acquisition_plan_content_sha256") != canonical_content_sha256(acquisition_plan):
        issues.append(_issue("replication_design_source_assignment_plan_hash_mismatch", "$.source_assignment.source_prospective_acquisition_plan_content_sha256"))
    if assignment.get("source_power_plan_content_sha256") != canonical_content_sha256(power_plan):
        issues.append(_issue("replication_design_source_assignment_power_plan_hash_mismatch", "$.source_assignment.source_power_plan_content_sha256"))
    if assignment.get("outcomes_observed_at_assignment") is not False:
        issues.append(_issue("replication_design_source_assignment_outcomes_not_unobserved", "$.source_assignment.outcomes_observed_at_assignment"))
    if _timestamp_or_none(assignment.get("assigned_at")) is None:
        issues.append(_issue("replication_design_source_assignment_time_invalid", "$.source_assignment.assigned_at"))


def _validate_independence(
    source_protocol: Mapping[str, Any],
    source_power_plan: Mapping[str, Any],
    source_assignment: Mapping[str, Any],
    replication_protocol: Mapping[str, Any],
    replication_power_plan: Mapping[str, Any],
    declaration: Mapping[str, Any],
    issues: list[ProspectiveLoopEvaluationReplicationDesignIssue],
) -> None:
    for field in (
        "independent_replication_cohort_required",
        "independent_assay_site_required",
        "external_arm_randomization_required",
    ):
        if source_power_plan.get(field) is not True:
            issues.append(_issue(f"replication_design_source_{field}_missing", f"$.source_power_plan.{field}"))
        if replication_power_plan.get(field) is not True:
            issues.append(_issue(f"replication_design_replication_{field}_missing", f"$.replication_power_plan.{field}"))
    source_target = source_protocol.get("target_population_id")
    replication_target = replication_protocol.get("target_population_id")
    if source_target == replication_target:
        issues.append(_issue("replication_design_cohort_not_independent", "$.replication_protocol.target_population_id"))
    if source_power_plan.get("replication_cohort_id") != replication_target:
        issues.append(_issue("replication_design_cohort_not_predeclared", "$.replication_protocol.target_population_id"))
    source_site = source_power_plan.get("planned_assay_site_id")
    replication_site = replication_power_plan.get("planned_assay_site_id")
    if _string_or_none(source_site) is None or _string_or_none(replication_site) is None:
        issues.append(_issue("replication_design_assay_site_missing", "$.power_plan.planned_assay_site_id"))
    elif source_site == replication_site:
        issues.append(_issue("replication_design_assay_site_not_independent", "$.replication_power_plan.planned_assay_site_id"))
    for field in _PRIMARY_OUTCOME_FIELDS:
        if source_power_plan.get(field) != replication_power_plan.get(field):
            issues.append(_issue(f"replication_design_primary_outcome_{field}_mismatch", f"$.replication_power_plan.{field}"))
    if _arms_by_role(source_protocol) != _arms_by_role(replication_protocol):
        issues.append(_issue("replication_design_policy_intervention_mismatch", "$.replication_protocol.arms"))
    source_assigned_at = _timestamp_or_none(source_assignment.get("assigned_at"))
    replication_registered_at = _timestamp_or_none(replication_protocol.get("registered_at"))
    declared_at = _timestamp_or_none(declaration.get("declared_at"))
    if replication_registered_at is None:
        issues.append(_issue("replication_design_replication_protocol_time_invalid", "$.replication_protocol.registered_at"))
    elif source_assigned_at is not None and replication_registered_at > source_assigned_at:
        issues.append(_issue("replication_design_protocol_registered_after_source_assignment", "$.replication_protocol.registered_at"))
    if declared_at is not None and replication_registered_at is not None and declared_at < replication_registered_at:
        issues.append(_issue("replication_design_declared_before_replication_protocol", "$.replication_design_declaration.declared_at"))


def _arms_by_role(protocol: Mapping[str, Any]) -> dict[str, tuple[str, str]]:
    arms = protocol.get("arms")
    if not isinstance(arms, list | tuple):
        return {}
    result: dict[str, tuple[str, str]] = {}
    for arm in arms:
        if not isinstance(arm, Mapping):
            continue
        role = _string_or_none(arm.get("role"))
        spec_hash = _string_or_none(arm.get("policy_spec_content_sha256"))
        runtime_hash = _string_or_none(arm.get("policy_runtime_content_sha256"))
        if role is not None and spec_hash is not None and runtime_hash is not None:
            result[role] = (spec_hash, runtime_hash)
    return result


def _read_object(
    path: Path,
    name: str,
) -> tuple[Mapping[str, Any] | None, ProspectiveLoopEvaluationReplicationDesignIssue | None]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError:
        return None, _issue(f"replication_design_{name}_unreadable", f"$.{name}")
    except json.JSONDecodeError:
        return None, _issue(f"replication_design_{name}_invalid_json", f"$.{name}")
    if not isinstance(payload, Mapping):
        return None, _issue(f"replication_design_{name}_not_object", f"$.{name}")
    return payload, None


def _parse_timestamp(value: str, field: str) -> datetime:
    try:
        timestamp = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{field} must be ISO-8601") from exc
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError(f"{field} must include timezone")
    return timestamp


def _timestamp_or_none(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return _parse_timestamp(value, "timestamp")
    except ValueError:
        return None


def _string_or_none(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _issue(code: str, location: str) -> ProspectiveLoopEvaluationReplicationDesignIssue:
    return ProspectiveLoopEvaluationReplicationDesignIssue(
        code=code,
        location=location,
        message=code.replace("_", " ") + ".",
    )


def _summary_line(result: ProspectiveLoopEvaluationReplicationDesignVerification) -> str:
    return (
        "Prospective loop evaluation replication design: "
        f"verification={result.verification_status}; status={result.replication_design_status}; "
        "execution_authority=false; policy_ranking=false; scientific_claim_upgrade=false."
    )
