from __future__ import annotations

import json
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .artifact_hash import canonical_content_sha256, json_ready
from .prospective_evaluation_protocol import (
    PROSPECTIVE_ACQUISITION_PLAN_SCHEMA,
    PROSPECTIVE_LOOP_EVALUATION_PROTOCOL_SCHEMA,
    ProspectiveLoopEvaluationProtocolVerifier,
)


PROSPECTIVE_LOOP_EVALUATION_ARM_ASSIGNMENT_SCHEMA = (
    "prospective_loop_evaluation_arm_assignment.v1"
)
PROSPECTIVE_LOOP_EVALUATION_ARM_ASSIGNMENT_VERIFICATION_SCHEMA = (
    "prospective_loop_evaluation_arm_assignment_verification.v2"
)
EXTERNAL_RANDOMIZATION_VERIFICATION_SCHEMA = "external_randomization_verification.v1"
PROSPECTIVE_ACQUISITION_APPROVAL_SCHEMA = "prospective_acquisition_approval.v1"

_INDEPENDENT_SOURCE_KINDS = {
    "public_randomness_beacon",
    "third_party_random_service",
    "institutional_randomization_service",
    "manual_lottery_with_witness",
}
_ASSIGNMENT_AUTHORITY_FIELDS = (
    "policy_ranking_permitted",
    "execution_authority_granted",
    "autonomous_execution_permitted",
    "scientific_claim_upgrade_permitted",
)
_CONTROL_AUTHORITY_FIELDS = (
    "execution_authority_granted",
    "autonomous_execution_permitted",
    "scientific_claim_upgrade_permitted",
)


@dataclass(frozen=True)
class ProspectiveLoopEvaluationArmAssignmentIssue:
    code: str
    location: str
    message: str


@dataclass(frozen=True)
class ProspectiveLoopEvaluationArmAssignmentVerification:
    """Read-only verification of external arm assignment before outcomes exist."""

    schema_version: str
    verification_status: str
    verified_at: str
    protocol_id: str | None
    assignment_set_id: str | None
    source_protocol_content_sha256: str | None
    source_prospective_acquisition_plan_content_sha256: str | None
    source_power_plan_content_sha256: str | None
    source_assignment_content_sha256: str | None
    assignment_counts_by_arm: dict[str, int]
    issue_codes: tuple[str, ...]
    issues: tuple[ProspectiveLoopEvaluationArmAssignmentIssue, ...]
    policy_ranking_permitted: bool
    execution_authority_granted: bool
    autonomous_execution_permitted: bool
    scientific_claim_upgrade_permitted: bool
    comparison_analysis_permitted: bool
    summary_line: str

    def to_dict(self) -> dict[str, Any]:
        return json_ready(asdict(self))


class ProspectiveLoopEvaluationArmAssignmentVerifier:
    """Verify a pre-outcome external arm allocation without allocating subjects."""

    def __init__(
        self,
        *,
        protocol_verifier: ProspectiveLoopEvaluationProtocolVerifier | None = None,
    ) -> None:
        self.protocol_verifier = protocol_verifier or ProspectiveLoopEvaluationProtocolVerifier()

    def verify_path(
        self,
        protocol_path: str | Path,
        prospective_acquisition_plan_path: str | Path,
        power_plan_path: str | Path,
        assignment_path: str | Path,
        randomization_verification_paths: Sequence[str | Path],
        approval_paths: Sequence[str | Path],
        *,
        verified_at: str | None = None,
    ) -> ProspectiveLoopEvaluationArmAssignmentVerification:
        protocol, protocol_issue = _read_object(Path(protocol_path), "protocol")
        plan, plan_issue = _read_object(
            Path(prospective_acquisition_plan_path),
            "prospective_acquisition_plan",
        )
        power_plan, power_plan_issue = _read_object(Path(power_plan_path), "power_plan")
        assignment, assignment_issue = _read_object(Path(assignment_path), "assignment")
        randomizations, randomization_issues = _read_objects(
            randomization_verification_paths,
            "randomization_verification",
        )
        approvals, approval_issues = _read_objects(approval_paths, "approval")
        return self._verify(
            protocol,
            plan,
            power_plan,
            assignment,
            randomizations,
            approvals,
            verified_at=verified_at,
            initial_issues=tuple(
                issue
                for issue in (
                    protocol_issue,
                    plan_issue,
                    power_plan_issue,
                    assignment_issue,
                    *randomization_issues,
                    *approval_issues,
                )
                if issue is not None
            ),
        )

    def verify(
        self,
        protocol: Mapping[str, Any],
        prospective_acquisition_plan: Mapping[str, Any],
        power_plan: Mapping[str, Any],
        assignment: Mapping[str, Any],
        randomization_verifications: Sequence[Mapping[str, Any]],
        approvals: Sequence[Mapping[str, Any]],
        *,
        verified_at: str | None = None,
    ) -> ProspectiveLoopEvaluationArmAssignmentVerification:
        return self._verify(
            protocol,
            prospective_acquisition_plan,
            power_plan,
            assignment,
            randomization_verifications,
            approvals,
            verified_at=verified_at,
            initial_issues=(),
        )

    def _verify(
        self,
        protocol: Mapping[str, Any] | None,
        prospective_acquisition_plan: Mapping[str, Any] | None,
        power_plan: Mapping[str, Any] | None,
        assignment: Mapping[str, Any] | None,
        randomization_verifications: Sequence[Mapping[str, Any]],
        approvals: Sequence[Mapping[str, Any]],
        *,
        verified_at: str | None,
        initial_issues: tuple[ProspectiveLoopEvaluationArmAssignmentIssue, ...],
    ) -> ProspectiveLoopEvaluationArmAssignmentVerification:
        timestamp = verified_at or datetime.now(timezone.utc).isoformat()
        verification_time = _parse_timestamp(timestamp, "verified_at")
        issues = list(initial_issues)
        if protocol is None:
            issues.append(_issue("protocol_unavailable", "$.protocol"))
        if prospective_acquisition_plan is None:
            issues.append(_issue("prospective_acquisition_plan_unavailable", "$.plan"))
        if power_plan is None:
            issues.append(_issue("power_plan_unavailable", "$.power_plan"))
        if assignment is None:
            issues.append(_issue("arm_assignment_unavailable", "$.assignment"))
        if protocol is not None and prospective_acquisition_plan is not None and power_plan is not None:
            protocol_verification = self.protocol_verifier.verify(
                protocol,
                prospective_acquisition_plan,
                power_plan,
                verified_at=timestamp,
            )
            if protocol_verification.verification_status != "PASS":
                issues.append(_issue("source_protocol_verification_failed", "$.protocol"))
        if (
            protocol is not None
            and prospective_acquisition_plan is not None
            and power_plan is not None
            and assignment is not None
        ):
            _validate_assignment(
                protocol,
                prospective_acquisition_plan,
                power_plan,
                assignment,
                randomization_verifications,
                approvals,
                verification_time,
                issues,
            )

        counts = _assignment_counts(assignment)
        result = ProspectiveLoopEvaluationArmAssignmentVerification(
            schema_version=PROSPECTIVE_LOOP_EVALUATION_ARM_ASSIGNMENT_VERIFICATION_SCHEMA,
            verification_status="PASS" if not issues else "FAIL",
            verified_at=timestamp,
            protocol_id=_string_or_none(protocol.get("protocol_id")) if protocol else None,
            assignment_set_id=(
                _string_or_none(assignment.get("assignment_set_id"))
                if assignment
                else None
            ),
            source_protocol_content_sha256=(
                canonical_content_sha256(protocol) if protocol is not None else None
            ),
            source_prospective_acquisition_plan_content_sha256=(
                canonical_content_sha256(prospective_acquisition_plan)
                if prospective_acquisition_plan is not None
                else None
            ),
            source_power_plan_content_sha256=(
                canonical_content_sha256(power_plan) if power_plan is not None else None
            ),
            source_assignment_content_sha256=(
                canonical_content_sha256(assignment) if assignment is not None else None
            ),
            assignment_counts_by_arm=counts,
            issue_codes=tuple(sorted({issue.code for issue in issues})),
            issues=tuple(issues),
            policy_ranking_permitted=False,
            execution_authority_granted=False,
            autonomous_execution_permitted=False,
            scientific_claim_upgrade_permitted=False,
            comparison_analysis_permitted=False,
            summary_line="",
        )
        return replace(result, summary_line=_summary_line(result))


def _validate_assignment(
    protocol: Mapping[str, Any],
    plan: Mapping[str, Any],
    power_plan: Mapping[str, Any],
    assignment: Mapping[str, Any],
    randomization_verifications: Sequence[Mapping[str, Any]],
    approvals: Sequence[Mapping[str, Any]],
    verification_time: datetime,
    issues: list[ProspectiveLoopEvaluationArmAssignmentIssue],
) -> None:
    if assignment.get("schema_version") != PROSPECTIVE_LOOP_EVALUATION_ARM_ASSIGNMENT_SCHEMA:
        issues.append(_issue("arm_assignment_schema_invalid", "$.assignment.schema_version"))
    if _string_or_none(assignment.get("assignment_set_id")) is None:
        issues.append(_issue("arm_assignment_set_id_missing", "$.assignment.assignment_set_id"))
    if assignment.get("source_protocol_content_sha256") != canonical_content_sha256(protocol):
        issues.append(_issue("arm_assignment_protocol_hash_mismatch", "$.assignment.source_protocol_content_sha256"))
    if assignment.get("source_prospective_acquisition_plan_content_sha256") != canonical_content_sha256(plan):
        issues.append(_issue("arm_assignment_plan_hash_mismatch", "$.assignment.source_prospective_acquisition_plan_content_sha256"))
    if assignment.get("source_power_plan_content_sha256") != canonical_content_sha256(power_plan):
        issues.append(_issue("arm_assignment_power_plan_hash_mismatch", "$.assignment.source_power_plan_content_sha256"))
    assigned_at = _timestamp_or_none(assignment.get("assigned_at"))
    if assigned_at is None:
        issues.append(_issue("arm_assignment_assigned_at_invalid", "$.assignment.assigned_at"))
    elif verification_time < assigned_at:
        issues.append(_issue("arm_assignment_verified_before_assignment", "$.assignment.verified_at"))
    protocol_registered_at = _timestamp_or_none(protocol.get("registered_at"))
    if (
        assigned_at is not None
        and protocol_registered_at is not None
        and assigned_at < protocol_registered_at
    ):
        issues.append(_issue("arm_assignment_before_protocol_registration", "$.assignment.assigned_at"))
    if assignment.get("outcomes_observed_at_assignment") is not False:
        issues.append(_issue("arm_assignment_outcomes_not_declared_unobserved", "$.assignment.outcomes_observed_at_assignment"))
    if assignment.get("outcome_assessor_blinded_to_arm") is not True:
        issues.append(_issue("arm_assignment_assessor_not_blinded", "$.assignment.outcome_assessor_blinded_to_arm"))
    _validate_arm_randomization(assignment.get("arm_randomization"), issues)
    _validate_human_acceptance(assignment, assigned_at, issues)
    _validate_authority_fields(assignment, issues)

    protocol_arms = _protocol_arm_ids(protocol)
    requests = _requests_by_id(plan)
    verifications = {
        canonical_content_sha256(item): item
        for item in randomization_verifications
        if isinstance(item, Mapping)
    }
    approvals_by_hash = {
        canonical_content_sha256(item): item
        for item in approvals
        if isinstance(item, Mapping)
    }
    assignments = assignment.get("assignments")
    if not isinstance(assignments, list | tuple) or not assignments:
        issues.append(_issue("arm_assignment_items_missing", "$.assignment.assignments"))
        return

    seen_ids: set[str] = set()
    seen_approvals: set[str] = set()
    seen_subjects: set[str] = set()
    counts: Counter[str] = Counter()
    for index, item in enumerate(assignments):
        location = f"$.assignment.assignments[{index}]"
        if not isinstance(item, Mapping):
            issues.append(_issue("arm_assignment_item_invalid", location))
            continue
        assignment_id = _string_or_none(item.get("assignment_id"))
        arm_id = _string_or_none(item.get("arm_id"))
        approval_id = _string_or_none(item.get("approval_id"))
        subject_id = _string_or_none(item.get("subject_id"))
        request_id = _string_or_none(item.get("request_id"))
        if assignment_id is None:
            issues.append(_issue("arm_assignment_id_missing", location))
        elif assignment_id in seen_ids:
            issues.append(_issue("arm_assignment_id_not_unique", location))
        else:
            seen_ids.add(assignment_id)
        if arm_id not in protocol_arms:
            issues.append(_issue("arm_assignment_unknown_arm", location))
        elif arm_id is not None:
            counts[arm_id] += 1
        if approval_id is None:
            issues.append(_issue("arm_assignment_approval_id_missing", location))
        elif approval_id in seen_approvals:
            issues.append(_issue("arm_assignment_approval_not_unique", location))
        else:
            seen_approvals.add(approval_id)
        if subject_id is None:
            issues.append(_issue("arm_assignment_subject_id_missing", location))
        elif subject_id in seen_subjects:
            issues.append(_issue("arm_assignment_subject_not_unique", location))
        else:
            seen_subjects.add(subject_id)
        if request_id is None:
            issues.append(_issue("arm_assignment_request_id_missing", location))

        verification_hash = _string_or_none(
            item.get("source_randomization_verification_content_sha256")
        )
        approval_hash = _string_or_none(item.get("source_approval_content_sha256"))
        verification = verifications.get(verification_hash or "")
        approval = approvals_by_hash.get(approval_hash or "")
        if verification is None:
            issues.append(_issue("arm_assignment_randomization_verification_missing", location))
        if approval is None:
            issues.append(_issue("arm_assignment_approval_artifact_missing", location))
        request = requests.get(request_id or "")
        if request is None:
            issues.append(_issue("arm_assignment_request_missing_from_plan", location))
        elif request.get("action_kind") != "send_to_experiment":
            issues.append(_issue("arm_assignment_request_not_assay", location))
        if verification is not None:
            _validate_randomization_verification(
                verification,
                plan,
                request_id,
                subject_id,
                location,
                issues,
            )
        if approval is not None:
            _validate_approval(
                approval,
                plan,
                verification_hash,
                approval_id,
                request,
                subject_id,
                assigned_at,
                location,
                issues,
            )

    if set(counts) != protocol_arms:
        issues.append(_issue("arm_assignment_roles_incomplete", "$.assignment.assignments"))
    elif max(counts.values()) - min(counts.values()) > 1:
        issues.append(_issue("arm_assignment_imbalance_exceeds_one", "$.assignment.assignments"))


def _validate_arm_randomization(
    value: Any,
    issues: list[ProspectiveLoopEvaluationArmAssignmentIssue],
) -> None:
    if not isinstance(value, Mapping):
        issues.append(_issue("arm_randomization_invalid", "$.assignment.arm_randomization"))
        return
    if value.get("source_kind") not in _INDEPENDENT_SOURCE_KINDS:
        issues.append(_issue("arm_randomization_source_not_independent", "$.assignment.arm_randomization.source_kind"))
    if _string_or_none(value.get("source_reference")) is None:
        issues.append(_issue("arm_randomization_source_reference_missing", "$.assignment.arm_randomization.source_reference"))
    if _string_or_none(value.get("allocation_algorithm")) is None:
        issues.append(_issue("arm_randomization_allocation_algorithm_missing", "$.assignment.arm_randomization.allocation_algorithm"))


def _validate_human_acceptance(
    assignment: Mapping[str, Any],
    assigned_at: datetime | None,
    issues: list[ProspectiveLoopEvaluationArmAssignmentIssue],
) -> None:
    value = assignment.get("human_acceptance")
    if not isinstance(value, Mapping):
        issues.append(_issue("arm_assignment_human_acceptance_invalid", "$.assignment.human_acceptance"))
        return
    if value.get("decision") != "accept":
        issues.append(_issue("arm_assignment_human_acceptance_not_accepted", "$.assignment.human_acceptance.decision"))
    for field in ("reviewer", "rationale"):
        if _string_or_none(value.get(field)) is None:
            issues.append(_issue(f"arm_assignment_human_acceptance_{field}_missing", f"$.assignment.human_acceptance.{field}"))
    reviewed_at = _timestamp_or_none(value.get("reviewed_at"))
    if reviewed_at is None:
        issues.append(_issue("arm_assignment_human_acceptance_reviewed_at_invalid", "$.assignment.human_acceptance.reviewed_at"))
    elif assigned_at is not None and reviewed_at > assigned_at:
        issues.append(_issue("arm_assignment_human_acceptance_after_assignment", "$.assignment.human_acceptance.reviewed_at"))


def _validate_authority_fields(
    assignment: Mapping[str, Any],
    issues: list[ProspectiveLoopEvaluationArmAssignmentIssue],
) -> None:
    for field in _ASSIGNMENT_AUTHORITY_FIELDS:
        if assignment.get(field) is not False:
            issues.append(_issue(f"arm_assignment_{field}_must_be_false", f"$.assignment.{field}"))


def _validate_randomization_verification(
    verification: Mapping[str, Any],
    plan: Mapping[str, Any],
    request_id: str | None,
    subject_id: str | None,
    location: str,
    issues: list[ProspectiveLoopEvaluationArmAssignmentIssue],
) -> None:
    if verification.get("schema_version") != EXTERNAL_RANDOMIZATION_VERIFICATION_SCHEMA:
        issues.append(_issue("arm_assignment_randomization_verification_schema_invalid", location))
    if verification.get("verification_status") != "PASS":
        issues.append(_issue("arm_assignment_randomization_verification_not_pass", location))
    if verification.get("source_plan_content_sha256") != canonical_content_sha256(plan):
        issues.append(_issue("arm_assignment_randomization_plan_hash_mismatch", location))
    if verification.get("request_id") != request_id:
        issues.append(_issue("arm_assignment_randomization_request_mismatch", location))
    if subject_id not in _string_items(verification.get("selected_subject_ids")):
        issues.append(_issue("arm_assignment_subject_not_selected_by_randomization", location))
    if verification.get("human_confirmed_acquisition_preparation_permitted") is not True:
        issues.append(_issue("arm_assignment_randomization_preparation_not_permitted", location))
    for field in _CONTROL_AUTHORITY_FIELDS:
        if verification.get(field) is not False:
            issues.append(_issue(f"arm_assignment_randomization_{field}_must_be_false", location))


def _validate_approval(
    approval: Mapping[str, Any],
    plan: Mapping[str, Any],
    verification_hash: str | None,
    approval_id: str | None,
    request: Mapping[str, Any] | None,
    subject_id: str | None,
    assigned_at: datetime | None,
    location: str,
    issues: list[ProspectiveLoopEvaluationArmAssignmentIssue],
) -> None:
    if approval.get("schema_version") != PROSPECTIVE_ACQUISITION_APPROVAL_SCHEMA:
        issues.append(_issue("arm_assignment_approval_schema_invalid", location))
    if approval.get("approval_id") != approval_id:
        issues.append(_issue("arm_assignment_approval_id_mismatch", location))
    if approval.get("source_plan_content_sha256") != canonical_content_sha256(plan):
        issues.append(_issue("arm_assignment_approval_plan_hash_mismatch", location))
    if approval.get("source_randomization_verification_content_sha256") != verification_hash:
        issues.append(_issue("arm_assignment_approval_randomization_hash_mismatch", location))
    if approval.get("decision") != "accept":
        issues.append(_issue("arm_assignment_approval_not_accepted", location))
    if approval.get("preparation_scope") != "single_subject_preparation_only":
        issues.append(_issue("arm_assignment_approval_scope_invalid", location))
    approved_budget = approval.get("approved_budget")
    if (
        isinstance(approved_budget, bool)
        or not isinstance(approved_budget, int | float)
        or approved_budget <= 0
    ):
        issues.append(_issue("arm_assignment_approval_budget_invalid", location))
    if approval.get("request_id") != (request or {}).get("request_id"):
        issues.append(_issue("arm_assignment_approval_request_mismatch", location))
    if approval.get("subject_id") != subject_id:
        issues.append(_issue("arm_assignment_approval_subject_mismatch", location))
    if approval.get("action_kind") != "send_to_experiment":
        issues.append(_issue("arm_assignment_approval_not_assay", location))
    if request is not None and approval.get("oracle_name") != request.get("oracle_name"):
        issues.append(_issue("arm_assignment_approval_oracle_mismatch", location))
    reviewed_at = _timestamp_or_none(approval.get("reviewed_at"))
    if reviewed_at is None:
        issues.append(_issue("arm_assignment_approval_reviewed_at_invalid", location))
    elif assigned_at is not None and reviewed_at > assigned_at:
        issues.append(_issue("arm_assignment_approval_after_assignment", location))
    expires_at = _timestamp_or_none(approval.get("expires_at"))
    if expires_at is None:
        issues.append(_issue("arm_assignment_approval_expiry_invalid", location))
    elif assigned_at is not None and expires_at <= assigned_at:
        issues.append(_issue("arm_assignment_approval_expired_at_assignment", location))


def _protocol_arm_ids(protocol: Mapping[str, Any]) -> set[str]:
    arms = protocol.get("arms")
    if not isinstance(arms, list | tuple):
        return set()
    return {
        arm_id
        for arm in arms
        if isinstance(arm, Mapping)
        for arm_id in (_string_or_none(arm.get("arm_id")),)
        if arm_id is not None
    }


def _requests_by_id(plan: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    requests = plan.get("requests")
    if not isinstance(requests, list | tuple):
        return {}
    return {
        request_id: request
        for request in requests
        if isinstance(request, Mapping)
        for request_id in (_string_or_none(request.get("request_id")),)
        if request_id is not None
    }


def _assignment_counts(assignment: Mapping[str, Any] | None) -> dict[str, int]:
    if assignment is None or not isinstance(assignment.get("assignments"), list | tuple):
        return {}
    return dict(
        sorted(
            Counter(
                arm_id
                for item in assignment["assignments"]
                if isinstance(item, Mapping)
                for arm_id in (_string_or_none(item.get("arm_id")),)
                if arm_id is not None
            ).items()
        )
    )


def _read_objects(
    paths: Sequence[str | Path],
    name: str,
) -> tuple[tuple[Mapping[str, Any], ...], tuple[ProspectiveLoopEvaluationArmAssignmentIssue, ...]]:
    objects: list[Mapping[str, Any]] = []
    issues: list[ProspectiveLoopEvaluationArmAssignmentIssue] = []
    for index, path in enumerate(paths):
        payload, issue = _read_object(Path(path), f"{name}_{index}")
        if payload is not None:
            objects.append(payload)
        if issue is not None:
            issues.append(issue)
    return tuple(objects), tuple(issues)


def _read_object(
    path: Path,
    name: str,
) -> tuple[Mapping[str, Any] | None, ProspectiveLoopEvaluationArmAssignmentIssue | None]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError:
        return None, _issue(f"{name}_unreadable", f"$.{name}")
    except json.JSONDecodeError:
        return None, _issue(f"{name}_invalid_json", f"$.{name}")
    if not isinstance(payload, Mapping):
        return None, _issue(f"{name}_not_object", f"$.{name}")
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


def _string_items(value: Any) -> tuple[str, ...]:
    if isinstance(value, str) or not isinstance(value, list | tuple):
        return ()
    return tuple(item for item in value if isinstance(item, str) and item)


def _issue(
    code: str,
    location: str,
) -> ProspectiveLoopEvaluationArmAssignmentIssue:
    return ProspectiveLoopEvaluationArmAssignmentIssue(
        code=code,
        location=location,
        message=code.replace("_", " ") + ".",
    )


def _summary_line(
    verification: ProspectiveLoopEvaluationArmAssignmentVerification,
) -> str:
    return (
        "Prospective loop evaluation arm-assignment verification: "
        f"status={verification.verification_status}; "
        f"assignments={sum(verification.assignment_counts_by_arm.values())}; "
        "execution_authority=false; scientific_claim_upgrade=false; "
        "comparison_analysis=false."
    )
