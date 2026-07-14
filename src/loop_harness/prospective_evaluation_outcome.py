from __future__ import annotations

import json
import math
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .artifact_hash import canonical_content_sha256, json_ready
from .contracts import LoopState, OracleKind
from .prospective_evaluation_assignment import (
    PROSPECTIVE_LOOP_EVALUATION_ARM_ASSIGNMENT_VERIFICATION_SCHEMA,
    ProspectiveLoopEvaluationArmAssignmentVerifier,
)
from .prospective_assay_import import PROSPECTIVE_ASSAY_ENDPOINT_POLICY_SCHEMA
from .prospective_admission_commit_evidence import (
    PROSPECTIVE_ADMISSION_COMMIT_EVIDENCE_SCHEMA,
    PROSPECTIVE_ADMISSION_COMMIT_EVIDENCE_SET_SCHEMA,
    ProspectiveAdmissionCommitEvidenceSetBuilder,
)
from .prospective_policy_fidelity import (
    PROSPECTIVE_POLICY_FIDELITY_SCHEMA,
    PROSPECTIVE_POLICY_FIDELITY_SET_SCHEMA,
    ProspectivePolicyFidelitySetBuilder,
)
from .prospective_acquisition_consumption import (
    PROSPECTIVE_ACQUISITION_CONSUMPTION_SCHEMA,
)
from .store import CampaignStore, event_to_dict


PROSPECTIVE_LOOP_EVALUATION_OUTCOME_REPORT_SCHEMA = (
    "prospective_loop_evaluation_outcome_report.v1"
)
PROSPECTIVE_LOOP_EVALUATION_OUTCOME_VERIFICATION_SCHEMA = (
    "prospective_loop_evaluation_outcome_verification.v8"
)
_OUTCOME_STATUSES = {
    "admitted_assay_outcome",
    "invalid_or_unresolved_assay_outcome",
    "missing_outcome",
}
_REPORT_AUTHORITY_FIELDS = (
    "policy_ranking_permitted",
    "execution_authority_granted",
    "autonomous_execution_permitted",
    "scientific_claim_upgrade_permitted",
)
_COMMIT_EVIDENCE_AUTHORITY_FIELDS = _REPORT_AUTHORITY_FIELDS[1:]


@dataclass(frozen=True)
class ProspectiveLoopEvaluationOutcomeIssue:
    code: str
    location: str
    message: str


@dataclass(frozen=True)
class ProspectiveLoopEvaluationArmOutcomeCount:
    arm_id: str
    randomized_episode_count: int
    admitted_assay_outcome_count: int
    invalid_or_unresolved_outcome_count: int
    missing_outcome_count: int
    primary_positive_count: int
    primary_negative_count: int
    declared_experiment_budget_limit: float | None
    actual_experiment_cost_spent: float


@dataclass(frozen=True)
class ProspectiveLoopEvaluationOutcomeVerification:
    """Read-only outcome-report validation; never a policy ranking or claim."""

    schema_version: str
    verification_status: str
    outcome_report_status: str
    verified_at: str
    protocol_id: str | None
    report_id: str | None
    source_protocol_content_sha256: str | None
    source_power_plan_content_sha256: str | None
    source_assay_endpoint_policy_content_sha256: str | None
    source_admission_commit_evidence_content_sha256: str | None
    source_policy_fidelity_content_sha256: str | None
    source_outcome_report_content_sha256: str | None
    source_assignment_content_sha256: str | None
    source_randomization_verification_content_sha256s: tuple[str, ...]
    source_approval_content_sha256s: tuple[str, ...]
    source_campaign_content_sha256: str | None
    source_consumption_ledger_content_sha256: str | None
    arm_outcomes: tuple[ProspectiveLoopEvaluationArmOutcomeCount, ...]
    issue_codes: tuple[str, ...]
    issues: tuple[ProspectiveLoopEvaluationOutcomeIssue, ...]
    policy_ranking_permitted: bool
    execution_authority_granted: bool
    autonomous_execution_permitted: bool
    scientific_claim_upgrade_permitted: bool
    predeclared_analysis_computed: bool
    summary_line: str

    def to_dict(self) -> dict[str, Any]:
        return json_ready(asdict(self))


class ProspectiveLoopEvaluationOutcomeReportVerifier:
    """Fail closed unless every randomized episode has a trustworthy outcome status."""

    def __init__(
        self,
        *,
        commit_evidence_set_builder: ProspectiveAdmissionCommitEvidenceSetBuilder | None = None,
        policy_fidelity_set_builder: ProspectivePolicyFidelitySetBuilder | None = None,
    ) -> None:
        self.commit_evidence_set_builder = (
            commit_evidence_set_builder or ProspectiveAdmissionCommitEvidenceSetBuilder()
        )
        self.policy_fidelity_set_builder = (
            policy_fidelity_set_builder or ProspectivePolicyFidelitySetBuilder()
        )

    def verify_path(
        self,
        protocol_path: str | Path,
        prospective_acquisition_plan_path: str | Path,
        power_plan_path: str | Path,
        assignment_path: str | Path,
        assignment_verification_path: str | Path,
        assay_endpoint_policy_path: str | Path,
        outcome_report_path: str | Path,
        consumption_ledger_path: str | Path,
        campaign_store_path: str | Path,
        *,
        randomization_verification_paths: Sequence[str | Path],
        approval_paths: Sequence[str | Path],
        admission_commit_directories: Sequence[str | Path] = (),
        policy_decision_receipt_paths: Sequence[str | Path] = (),
        policy_decision_campaign_store_paths: Sequence[str | Path] = (),
        verified_at: str | None = None,
    ) -> ProspectiveLoopEvaluationOutcomeVerification:
        protocol, protocol_issue = _read_object(Path(protocol_path), "protocol")
        plan, plan_issue = _read_object(
            Path(prospective_acquisition_plan_path), "prospective_acquisition_plan"
        )
        power_plan, power_plan_issue = _read_object(Path(power_plan_path), "power_plan")
        assignment, assignment_issue = _read_object(Path(assignment_path), "assignment")
        assignment_verification, assignment_verification_issue = _read_object(
            Path(assignment_verification_path), "assignment_verification"
        )
        randomizations, randomization_issues = _read_objects(
            randomization_verification_paths,
            "randomization_verification",
        )
        approvals, approval_issues = _read_objects(approval_paths, "approval")
        assay_endpoint_policy, assay_endpoint_policy_issue = _read_object(
            Path(assay_endpoint_policy_path), "assay_endpoint_policy"
        )
        report, report_issue = _read_object(Path(outcome_report_path), "outcome_report")
        ledger, ledger_issue = _read_jsonl(Path(consumption_ledger_path))
        store, store_issue = _read_campaign_store(Path(campaign_store_path))
        commit_evidence_set = self.commit_evidence_set_builder.build_paths(
            list(admission_commit_directories)
        ).to_dict()
        policy_fidelity_set = self.policy_fidelity_set_builder.build_paths(
            protocol_path,
            assignment_path,
            assignment_verification_path,
            list(policy_decision_receipt_paths),
            list(policy_decision_campaign_store_paths),
        ).to_dict()
        return self._verify(
            protocol,
            plan,
            power_plan,
            assignment,
            assignment_verification,
            randomizations,
            approvals,
            assay_endpoint_policy,
            report,
            ledger,
            store,
            commit_evidence_set,
            policy_fidelity_set,
            verified_at=verified_at,
            initial_issues=tuple(
                issue
                for issue in (
                    protocol_issue,
                    plan_issue,
                    power_plan_issue,
                    assignment_issue,
                    assignment_verification_issue,
                    *randomization_issues,
                    *approval_issues,
                    assay_endpoint_policy_issue,
                    report_issue,
                    ledger_issue,
                    store_issue,
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
        assignment_verification: Mapping[str, Any],
        randomization_verifications: Sequence[Mapping[str, Any]],
        approvals: Sequence[Mapping[str, Any]],
        assay_endpoint_policy: Mapping[str, Any],
        outcome_report: Mapping[str, Any],
        consumption_ledger: Sequence[Mapping[str, Any]],
        campaign_store: CampaignStore,
        *,
        admission_commit_evidence_set: Mapping[str, Any],
        policy_fidelity_set: Mapping[str, Any],
        verified_at: str | None = None,
    ) -> ProspectiveLoopEvaluationOutcomeVerification:
        return self._verify(
            protocol,
            prospective_acquisition_plan,
            power_plan,
            assignment,
            assignment_verification,
            randomization_verifications,
            approvals,
            assay_endpoint_policy,
            outcome_report,
            consumption_ledger,
            campaign_store,
            admission_commit_evidence_set,
            policy_fidelity_set,
            verified_at=verified_at,
            initial_issues=(),
        )

    def _verify(
        self,
        protocol: Mapping[str, Any] | None,
        plan: Mapping[str, Any] | None,
        power_plan: Mapping[str, Any] | None,
        assignment: Mapping[str, Any] | None,
        assignment_verification: Mapping[str, Any] | None,
        randomization_verifications: Sequence[Mapping[str, Any]],
        approvals: Sequence[Mapping[str, Any]],
        assay_endpoint_policy: Mapping[str, Any] | None,
        outcome_report: Mapping[str, Any] | None,
        consumption_ledger: Sequence[Mapping[str, Any]] | None,
        campaign_store: CampaignStore | None,
        admission_commit_evidence_set: Mapping[str, Any] | None,
        policy_fidelity_set: Mapping[str, Any] | None,
        *,
        verified_at: str | None,
        initial_issues: tuple[ProspectiveLoopEvaluationOutcomeIssue, ...],
    ) -> ProspectiveLoopEvaluationOutcomeVerification:
        timestamp = verified_at or datetime.now(timezone.utc).isoformat()
        verification_time = _parse_timestamp(timestamp, "verified_at")
        issues = list(initial_issues)
        for name, value in (
            ("protocol", protocol),
            ("prospective_acquisition_plan", plan),
            ("power_plan", power_plan),
            ("assignment", assignment),
            ("assignment_verification", assignment_verification),
            ("assay_endpoint_policy", assay_endpoint_policy),
            ("outcome_report", outcome_report),
            ("consumption_ledger", consumption_ledger),
            ("campaign_store", campaign_store),
            ("admission_commit_evidence_set", admission_commit_evidence_set),
            ("policy_fidelity_set", policy_fidelity_set),
        ):
            if value is None:
                issues.append(_issue(f"{name}_unavailable", f"$.{name}"))
        state: LoopState | None = None
        campaign_hash: str | None = None
        if campaign_store is not None:
            try:
                state = campaign_store.rebuild()
                campaign_hash = _campaign_hash(campaign_store)
            except (TypeError, ValueError):
                issues.append(_issue("campaign_store_not_rebuildable", "$.campaign_store"))
        if all(
            value is not None
            for value in (
                protocol,
                plan,
                power_plan,
                assignment,
                assignment_verification,
                randomization_verifications,
                approvals,
                assay_endpoint_policy,
                outcome_report,
                admission_commit_evidence_set,
                policy_fidelity_set,
            )
        ) and consumption_ledger is not None and state is not None and campaign_hash is not None:
            _validate_outcome_report(
                protocol,
                plan,
                power_plan,
                assignment,
                assignment_verification,
                randomization_verifications,
                approvals,
                assay_endpoint_policy,
                outcome_report,
                consumption_ledger,
                state,
                campaign_hash,
                admission_commit_evidence_set,
                policy_fidelity_set,
                verification_time,
                issues,
            )

        arm_outcomes = _arm_outcomes(
            protocol,
            assignment,
            outcome_report,
            consumption_ledger,
        )
        outcome_report_status = _outcome_report_status(issues, arm_outcomes)
        result = ProspectiveLoopEvaluationOutcomeVerification(
            schema_version=PROSPECTIVE_LOOP_EVALUATION_OUTCOME_VERIFICATION_SCHEMA,
            verification_status="PASS" if not issues else "FAIL",
            outcome_report_status=outcome_report_status,
            verified_at=timestamp,
            protocol_id=_string_or_none(protocol.get("protocol_id")) if protocol else None,
            report_id=(
                _string_or_none(outcome_report.get("report_id")) if outcome_report else None
            ),
            source_protocol_content_sha256=(
                canonical_content_sha256(protocol) if protocol is not None else None
            ),
            source_power_plan_content_sha256=(
                canonical_content_sha256(power_plan) if power_plan is not None else None
            ),
            source_assay_endpoint_policy_content_sha256=(
                canonical_content_sha256(assay_endpoint_policy)
                if assay_endpoint_policy is not None
                else None
            ),
            source_admission_commit_evidence_content_sha256=(
                _string_or_none(
                    admission_commit_evidence_set.get(
                        "source_evidence_content_sha256"
                    )
                )
                if admission_commit_evidence_set is not None
                else None
            ),
            source_policy_fidelity_content_sha256=(
                _string_or_none(policy_fidelity_set.get("source_fidelity_content_sha256"))
                if policy_fidelity_set is not None
                else None
            ),
            source_outcome_report_content_sha256=(
                canonical_content_sha256(outcome_report)
                if outcome_report is not None
                else None
            ),
            source_assignment_content_sha256=(
                canonical_content_sha256(assignment) if assignment is not None else None
            ),
            source_randomization_verification_content_sha256s=tuple(
                sorted(canonical_content_sha256(item) for item in randomization_verifications)
            ),
            source_approval_content_sha256s=tuple(
                sorted(canonical_content_sha256(item) for item in approvals)
            ),
            source_campaign_content_sha256=campaign_hash,
            source_consumption_ledger_content_sha256=(
                canonical_content_sha256(consumption_ledger)
                if consumption_ledger is not None
                else None
            ),
            arm_outcomes=arm_outcomes,
            issue_codes=tuple(sorted({issue.code for issue in issues})),
            issues=tuple(issues),
            policy_ranking_permitted=False,
            execution_authority_granted=False,
            autonomous_execution_permitted=False,
            scientific_claim_upgrade_permitted=False,
            predeclared_analysis_computed=False,
            summary_line="",
        )
        return replace(result, summary_line=_summary_line(result))


def _validate_outcome_report(
    protocol: Mapping[str, Any],
    plan: Mapping[str, Any],
    power_plan: Mapping[str, Any],
    assignment: Mapping[str, Any],
    assignment_verification: Mapping[str, Any],
    randomization_verifications: Sequence[Mapping[str, Any]],
    approvals: Sequence[Mapping[str, Any]],
    assay_endpoint_policy: Mapping[str, Any],
    report: Mapping[str, Any],
    ledger: Sequence[Mapping[str, Any]],
    state: LoopState,
    campaign_hash: str,
    admission_commit_evidence_set: Mapping[str, Any],
    policy_fidelity_set: Mapping[str, Any],
    verification_time: datetime,
    issues: list[ProspectiveLoopEvaluationOutcomeIssue],
) -> None:
    _validate_sources(
        protocol,
        plan,
        power_plan,
        assignment,
        assignment_verification,
        assay_endpoint_policy,
        report,
        ledger,
        campaign_hash,
        admission_commit_evidence_set,
        policy_fidelity_set,
        issues,
    )
    _validate_assignment_verification(
        protocol,
        plan,
        power_plan,
        assignment,
        assignment_verification,
        randomization_verifications,
        approvals,
        issues,
    )
    _validate_assay_endpoint_policy(protocol, assay_endpoint_policy, issues)
    evidence_by_consumption = _validate_commit_evidence(
        admission_commit_evidence_set,
        ledger,
        issues,
    )
    fidelity_by_assignment = _validate_policy_fidelity(
        policy_fidelity_set,
        protocol,
        assignment,
        assignment_verification,
        issues,
    )
    report_time = _timestamp_or_none(report.get("reported_at"))
    if report_time is None:
        issues.append(_issue("outcome_report_reported_at_invalid", "$.outcome_report.reported_at"))
    assignment_time = _timestamp_or_none(assignment.get("assigned_at"))
    if report_time is not None and assignment_time is not None and report_time < assignment_time:
        issues.append(_issue("outcome_report_before_assignment", "$.outcome_report.reported_at"))
    if report_time is not None and verification_time < report_time:
        issues.append(_issue("outcome_verification_before_report", "$.verified_at"))
    for index, record in enumerate(ledger):
        consumed_at = _timestamp_or_none(record.get("consumed_at"))
        if consumed_at is not None and verification_time < consumed_at:
            issues.append(_issue("outcome_verification_before_consumption", f"$.consumption_ledger[{index}].consumed_at"))
    _validate_human_acceptance(report, report_time, issues)
    _validate_authority_fields(report, issues)

    assignments = _assignments_by_id(assignment)
    reports = report.get("outcomes")
    if not isinstance(reports, list | tuple):
        issues.append(_issue("outcome_report_outcomes_invalid", "$.outcome_report.outcomes"))
        return
    ledger_by_id = _ledger_by_consumption_id(ledger)
    if len(ledger_by_id) != len(ledger):
        issues.append(
            _issue(
                "outcome_report_consumption_ledger_duplicate_or_invalid_ids",
                "$.consumption_ledger",
            )
        )
    seen_assignment_ids: set[str] = set()
    seen_consumption_ids: set[str] = set()
    for index, item in enumerate(reports):
        location = f"$.outcome_report.outcomes[{index}]"
        if not isinstance(item, Mapping):
            issues.append(_issue("outcome_report_item_invalid", location))
            continue
        assignment_id = _string_or_none(item.get("assignment_id"))
        if assignment_id is None:
            issues.append(_issue("outcome_report_assignment_id_missing", location))
            continue
        if assignment_id in seen_assignment_ids:
            issues.append(_issue("outcome_report_assignment_id_not_unique", location))
            continue
        seen_assignment_ids.add(assignment_id)
        assignment_item = assignments.get(assignment_id)
        if assignment_item is None:
            issues.append(_issue("outcome_report_assignment_not_registered", location))
            continue
        status = item.get("outcome_status")
        if status not in _OUTCOME_STATUSES:
            issues.append(_issue("outcome_report_status_invalid", location))
            continue
        matching_consumptions = _consumptions_for_assignment(ledger, assignment_item)
        if status == "missing_outcome":
            _validate_missing_outcome(item, matching_consumptions, location, issues)
            continue
        consumption_id = _string_or_none(item.get("consumption_id"))
        if consumption_id is not None:
            if consumption_id in seen_consumption_ids:
                issues.append(_issue("outcome_report_consumption_reused", location))
            seen_consumption_ids.add(consumption_id)
        if len(matching_consumptions) != 1:
            issues.append(
                _issue(
                    "outcome_report_assignment_consumption_count_invalid",
                    location,
                )
            )
        _validate_consumed_outcome(
            protocol,
            plan,
            power_plan,
            assignment_item,
            assay_endpoint_policy,
            evidence_by_consumption,
            fidelity_by_assignment,
            item,
            ledger_by_id,
            state,
            assignment_time,
            report_time,
            location,
            issues,
        )
    if set(assignments) != seen_assignment_ids:
        issues.append(_issue("outcome_report_randomized_assignments_incomplete", "$.outcome_report.outcomes"))
    _validate_experiment_budget(
        protocol,
        assignments,
        reports,
        ledger_by_id,
        issues,
    )


def _validate_sources(
    protocol: Mapping[str, Any],
    plan: Mapping[str, Any],
    power_plan: Mapping[str, Any],
    assignment: Mapping[str, Any],
    assignment_verification: Mapping[str, Any],
    assay_endpoint_policy: Mapping[str, Any],
    report: Mapping[str, Any],
    ledger: Sequence[Mapping[str, Any]],
    campaign_hash: str,
    admission_commit_evidence_set: Mapping[str, Any],
    policy_fidelity_set: Mapping[str, Any],
    issues: list[ProspectiveLoopEvaluationOutcomeIssue],
) -> None:
    expected = {
        "source_protocol_content_sha256": canonical_content_sha256(protocol),
        "source_prospective_acquisition_plan_content_sha256": canonical_content_sha256(plan),
        "source_power_plan_content_sha256": canonical_content_sha256(power_plan),
        "source_assignment_content_sha256": canonical_content_sha256(assignment),
        "source_assignment_verification_content_sha256": canonical_content_sha256(assignment_verification),
        "source_assay_endpoint_policy_content_sha256": canonical_content_sha256(assay_endpoint_policy),
        "source_admission_commit_evidence_content_sha256": admission_commit_evidence_set.get(
            "source_evidence_content_sha256"
        ),
        "source_policy_fidelity_content_sha256": policy_fidelity_set.get(
            "source_fidelity_content_sha256"
        ),
        "source_campaign_content_sha256": campaign_hash,
        "source_consumption_ledger_content_sha256": canonical_content_sha256(ledger),
    }
    if report.get("schema_version") != PROSPECTIVE_LOOP_EVALUATION_OUTCOME_REPORT_SCHEMA:
        issues.append(_issue("outcome_report_schema_invalid", "$.outcome_report.schema_version"))
    if _string_or_none(report.get("report_id")) is None:
        issues.append(_issue("outcome_report_id_missing", "$.outcome_report.report_id"))
    for field, expected_value in expected.items():
        if report.get(field) != expected_value:
            issues.append(_issue(f"outcome_report_{field}_mismatch", f"$.outcome_report.{field}"))


def _validate_assay_endpoint_policy(
    protocol: Mapping[str, Any],
    policy: Mapping[str, Any],
    issues: list[ProspectiveLoopEvaluationOutcomeIssue],
) -> None:
    if policy.get("schema_version") != PROSPECTIVE_ASSAY_ENDPOINT_POLICY_SCHEMA:
        issues.append(_issue("outcome_report_endpoint_policy_schema_invalid", "$.assay_endpoint_policy.schema_version"))
    if policy.get("source_protocol_content_sha256") != canonical_content_sha256(protocol):
        issues.append(_issue("outcome_report_endpoint_policy_protocol_hash_mismatch", "$.assay_endpoint_policy.source_protocol_content_sha256"))
    primary = protocol.get("primary_outcome")
    if not isinstance(primary, Mapping) or policy.get("output_metric_key") != primary.get("outcome_metric_key"):
        issues.append(_issue("outcome_report_endpoint_policy_primary_metric_mismatch", "$.assay_endpoint_policy.output_metric_key"))
    if _string_or_none(policy.get("oracle_name")) is None or _string_or_none(policy.get("assay_protocol_id")) is None:
        issues.append(_issue("outcome_report_endpoint_policy_identity_missing", "$.assay_endpoint_policy"))
    acceptance = policy.get("human_acceptance")
    if not isinstance(acceptance, Mapping) or acceptance.get("decision") != "accept":
        issues.append(_issue("outcome_report_endpoint_policy_human_acceptance_invalid", "$.assay_endpoint_policy.human_acceptance"))
    for field in _REPORT_AUTHORITY_FIELDS:
        if policy.get(field) is not False:
            issues.append(_issue(f"outcome_report_endpoint_policy_{field}_must_be_false", f"$.assay_endpoint_policy.{field}"))


def _validate_commit_evidence(
    evidence_set: Mapping[str, Any],
    ledger: Sequence[Mapping[str, Any]],
    issues: list[ProspectiveLoopEvaluationOutcomeIssue],
) -> dict[str, tuple[Mapping[str, Any], ...]]:
    if evidence_set.get("schema_version") != PROSPECTIVE_ADMISSION_COMMIT_EVIDENCE_SET_SCHEMA:
        issues.append(_issue("outcome_report_commit_evidence_set_schema_invalid", "$.admission_commit_evidence_set.schema_version"))
    if evidence_set.get("verification_status") != "PASS":
        issues.append(_issue("outcome_report_commit_evidence_set_not_pass", "$.admission_commit_evidence_set.verification_status"))
    evidence_items = evidence_set.get("evidence")
    if not isinstance(evidence_items, list | tuple):
        issues.append(_issue("outcome_report_commit_evidence_set_evidence_invalid", "$.admission_commit_evidence_set.evidence"))
        return {}
    if evidence_set.get("source_evidence_content_sha256") != canonical_content_sha256(evidence_items):
        issues.append(_issue("outcome_report_commit_evidence_set_source_hash_mismatch", "$.admission_commit_evidence_set.source_evidence_content_sha256"))
    for field in _COMMIT_EVIDENCE_AUTHORITY_FIELDS:
        if evidence_set.get(field) is not False:
            issues.append(_issue(f"outcome_report_commit_evidence_set_{field}_must_be_false", "$.admission_commit_evidence_set"))
    ledger_by_id = _ledger_by_consumption_id(ledger)
    grouped: dict[str, list[Mapping[str, Any]]] = {}
    for index, evidence in enumerate(evidence_items):
        location = f"$.admission_commit_evidence[{index}]"
        if not isinstance(evidence, Mapping):
            issues.append(_issue("outcome_report_commit_evidence_item_invalid", location))
            continue
        if evidence.get("schema_version") != PROSPECTIVE_ADMISSION_COMMIT_EVIDENCE_SCHEMA:
            issues.append(_issue("outcome_report_commit_evidence_schema_invalid", location))
            continue
        if evidence.get("verification_status") != "PASS":
            issues.append(_issue("outcome_report_commit_evidence_not_pass", location))
            continue
        for field in _COMMIT_EVIDENCE_AUTHORITY_FIELDS:
            if evidence.get(field) is not False:
                issues.append(_issue(f"outcome_report_commit_evidence_{field}_must_be_false", location))
        consumption_id = _string_or_none(evidence.get("consumption_id"))
        if consumption_id is None:
            issues.append(_issue("outcome_report_commit_evidence_consumption_id_missing", location))
            continue
        ledger_record = ledger_by_id.get(consumption_id)
        if ledger_record is None:
            issues.append(_issue("outcome_report_commit_evidence_consumption_not_in_ledger", location))
            continue
        if evidence.get("source_consumption_content_sha256") != canonical_content_sha256(ledger_record):
            issues.append(_issue("outcome_report_commit_evidence_consumption_hash_mismatch", location))
        grouped.setdefault(consumption_id, []).append(evidence)
    return {key: tuple(value) for key, value in grouped.items()}


def _validate_policy_fidelity(
    fidelity_set: Mapping[str, Any],
    protocol: Mapping[str, Any],
    assignment: Mapping[str, Any],
    assignment_verification: Mapping[str, Any],
    issues: list[ProspectiveLoopEvaluationOutcomeIssue],
) -> dict[str, tuple[Mapping[str, Any], ...]]:
    if fidelity_set.get("schema_version") != PROSPECTIVE_POLICY_FIDELITY_SET_SCHEMA:
        issues.append(_issue("outcome_report_policy_fidelity_set_schema_invalid", "$.policy_fidelity_set.schema_version"))
    if fidelity_set.get("verification_status") != "PASS":
        issues.append(_issue("outcome_report_policy_fidelity_set_not_pass", "$.policy_fidelity_set.verification_status"))
    items = fidelity_set.get("fidelity")
    if not isinstance(items, list | tuple):
        issues.append(_issue("outcome_report_policy_fidelity_set_items_invalid", "$.policy_fidelity_set.fidelity"))
        return {}
    if fidelity_set.get("source_fidelity_content_sha256") != canonical_content_sha256(items):
        issues.append(_issue("outcome_report_policy_fidelity_set_source_hash_mismatch", "$.policy_fidelity_set.source_fidelity_content_sha256"))
    for field in _REPORT_AUTHORITY_FIELDS:
        if fidelity_set.get(field) is not False:
            issues.append(_issue(f"outcome_report_policy_fidelity_set_{field}_must_be_false", "$.policy_fidelity_set"))
    grouped: dict[str, list[Mapping[str, Any]]] = {}
    for index, item in enumerate(items):
        location = f"$.policy_fidelity_set.fidelity[{index}]"
        if not isinstance(item, Mapping):
            issues.append(_issue("outcome_report_policy_fidelity_item_invalid", location))
            continue
        if item.get("schema_version") != PROSPECTIVE_POLICY_FIDELITY_SCHEMA:
            issues.append(_issue("outcome_report_policy_fidelity_schema_invalid", location))
            continue
        if item.get("verification_status") != "PASS":
            issues.append(_issue("outcome_report_policy_fidelity_not_pass", location))
            continue
        for field in _REPORT_AUTHORITY_FIELDS:
            if item.get(field) is not False:
                issues.append(_issue(f"outcome_report_policy_fidelity_{field}_must_be_false", location))
        assignment_id = _string_or_none(item.get("assignment_id"))
        if assignment_id is None:
            issues.append(_issue("outcome_report_policy_fidelity_assignment_id_missing", location))
            continue
        grouped.setdefault(assignment_id, []).append(item)
        if item.get("source_protocol_content_sha256") != canonical_content_sha256(protocol):
            issues.append(_issue("outcome_report_policy_fidelity_protocol_hash_mismatch", location))
        if item.get("source_assignment_content_sha256") != canonical_content_sha256(assignment):
            issues.append(_issue("outcome_report_policy_fidelity_assignment_hash_mismatch", location))
        if item.get("source_assignment_verification_content_sha256") != canonical_content_sha256(assignment_verification):
            issues.append(_issue("outcome_report_policy_fidelity_assignment_verification_hash_mismatch", location))
        arms = protocol.get("arms")
        arm = next(
            (
                candidate
                for candidate in (arms if isinstance(arms, list | tuple) else ())
                if isinstance(candidate, Mapping) and candidate.get("arm_id") == item.get("arm_id")
            ),
            None,
        )
        if not isinstance(arm, Mapping) or item.get("source_policy_runtime_content_sha256") != arm.get("policy_runtime_content_sha256"):
            issues.append(_issue("outcome_report_policy_fidelity_runtime_hash_mismatch", location))
    return {key: tuple(value) for key, value in grouped.items()}


def _validate_assignment_verification(
    protocol: Mapping[str, Any],
    plan: Mapping[str, Any],
    power_plan: Mapping[str, Any],
    assignment: Mapping[str, Any],
    verification: Mapping[str, Any],
    randomization_verifications: Sequence[Mapping[str, Any]],
    approvals: Sequence[Mapping[str, Any]],
    issues: list[ProspectiveLoopEvaluationOutcomeIssue],
) -> None:
    if verification.get("schema_version") != PROSPECTIVE_LOOP_EVALUATION_ARM_ASSIGNMENT_VERIFICATION_SCHEMA:
        issues.append(_issue("assignment_verification_schema_invalid", "$.assignment_verification.schema_version"))
    if verification.get("verification_status") != "PASS":
        issues.append(_issue("assignment_verification_not_pass", "$.assignment_verification.verification_status"))
    if verification.get("source_protocol_content_sha256") != canonical_content_sha256(protocol):
        issues.append(_issue("assignment_verification_protocol_hash_mismatch", "$.assignment_verification.source_protocol_content_sha256"))
    if verification.get("source_prospective_acquisition_plan_content_sha256") != canonical_content_sha256(plan):
        issues.append(_issue("assignment_verification_plan_hash_mismatch", "$.assignment_verification.source_prospective_acquisition_plan_content_sha256"))
    if verification.get("source_power_plan_content_sha256") != canonical_content_sha256(power_plan):
        issues.append(_issue("assignment_verification_power_plan_hash_mismatch", "$.assignment_verification.source_power_plan_content_sha256"))
    if verification.get("source_assignment_content_sha256") != canonical_content_sha256(assignment):
        issues.append(_issue("assignment_verification_assignment_hash_mismatch", "$.assignment_verification.source_assignment_content_sha256"))
    if verification.get("comparison_analysis_permitted") is not False:
        issues.append(_issue("assignment_verification_analysis_authority_invalid", "$.assignment_verification.comparison_analysis_permitted"))
    for field in _REPORT_AUTHORITY_FIELDS:
        if verification.get(field) is not False:
            issues.append(_issue(f"assignment_verification_{field}_must_be_false", f"$.assignment_verification.{field}"))
    verified_at = _string_or_none(verification.get("verified_at"))
    if verified_at is None:
        issues.append(_issue("assignment_verification_verified_at_invalid", "$.assignment_verification.verified_at"))
        return
    try:
        recomputed = ProspectiveLoopEvaluationArmAssignmentVerifier().verify(
            protocol,
            plan,
            power_plan,
            assignment,
            randomization_verifications,
            approvals,
            verified_at=verified_at,
        ).to_dict()
    except ValueError:
        issues.append(_issue("assignment_verification_recomputation_unavailable", "$.assignment_verification"))
        return
    if canonical_content_sha256(recomputed) != canonical_content_sha256(verification):
        issues.append(_issue("assignment_verification_recomputation_mismatch", "$.assignment_verification"))


def _validate_consumed_outcome(
    protocol: Mapping[str, Any],
    plan: Mapping[str, Any],
    power_plan: Mapping[str, Any],
    assignment: Mapping[str, Any],
    assay_endpoint_policy: Mapping[str, Any],
    evidence_by_consumption: Mapping[str, Sequence[Mapping[str, Any]]],
    fidelity_by_assignment: Mapping[str, Sequence[Mapping[str, Any]]],
    report_item: Mapping[str, Any],
    ledger_by_id: Mapping[str, Mapping[str, Any]],
    state: LoopState,
    assignment_time: datetime | None,
    report_time: datetime | None,
    location: str,
    issues: list[ProspectiveLoopEvaluationOutcomeIssue],
) -> None:
    consumption_id = _string_or_none(report_item.get("consumption_id"))
    consumption = ledger_by_id.get(consumption_id or "")
    if consumption is None:
        issues.append(_issue("outcome_report_consumption_missing_from_ledger", location))
        return
    matching_fidelity = fidelity_by_assignment.get(
        _string_or_none(report_item.get("assignment_id")) or "",
        (),
    )
    if len(matching_fidelity) == 0:
        issues.append(_issue("outcome_report_policy_fidelity_missing", location))
    elif len(matching_fidelity) != 1:
        issues.append(_issue("outcome_report_policy_fidelity_not_unique", location))
    else:
        _validate_policy_fidelity_binding(
            matching_fidelity[0],
            assignment,
            consumption,
            location,
            issues,
        )
    matching_evidence = evidence_by_consumption.get(consumption_id or "", ())
    if len(matching_evidence) == 0:
        issues.append(_issue("outcome_report_commit_evidence_missing", location))
    elif len(matching_evidence) != 1:
        issues.append(_issue("outcome_report_commit_evidence_not_unique", location))
    else:
        _validate_commit_evidence_binding(
            matching_evidence[0],
            consumption,
            assay_endpoint_policy,
            location,
            issues,
        )
    if consumption.get("schema_version") != PROSPECTIVE_ACQUISITION_CONSUMPTION_SCHEMA:
        issues.append(_issue("outcome_report_consumption_schema_invalid", location))
    expected = {
        "source_plan_content_sha256": canonical_content_sha256(plan),
        "source_randomization_verification_content_sha256": assignment.get(
            "source_randomization_verification_content_sha256"
        ),
        "source_approval_content_sha256": assignment.get(
            "source_approval_content_sha256"
        ),
        "approval_id": assignment.get("approval_id"),
        "request_id": assignment.get("request_id"),
        "subject_id": assignment.get("subject_id"),
        "action_kind": "send_to_experiment",
    }
    for field, expected_value in expected.items():
        if consumption.get(field) != expected_value:
            issues.append(_issue(f"outcome_report_consumption_{field}_mismatch", location))
    observation_id = _string_or_none(consumption.get("observation_id"))
    ticket_id = _string_or_none(consumption.get("trust_gate_result_id"))
    observation = state.observations.get(observation_id or "")
    ticket = state.trust_gate_results.get(ticket_id or "")
    if observation is None or ticket is None:
        issues.append(_issue("outcome_report_campaign_evidence_missing", location))
        return
    oracle = state.oracles.get(observation.oracle_name)
    if oracle is None or oracle.kind is not OracleKind.ASSAY:
        issues.append(_issue("outcome_report_observation_not_assay", location))
    _validate_assay_observation_provenance(
        observation,
        assay_endpoint_policy,
        power_plan,
        location,
        issues,
    )
    if (
        observation.subject_id != consumption.get("subject_id")
        or observation.oracle_name != consumption.get("oracle_name")
    ):
        issues.append(_issue("outcome_report_observation_consumption_mismatch", location))
    if (
        ticket.observation_id != observation.id
        or ticket.subject_id != observation.subject_id
        or ticket.oracle_name != observation.oracle_name
    ):
        issues.append(_issue("outcome_report_trust_gate_binding_mismatch", location))
    if consumption.get("trust_gate_decision") != ticket.decision.value:
        issues.append(_issue("outcome_report_trust_gate_decision_mismatch", location))
    expected_tier = ticket.evidence_tier.value if ticket.evidence_tier else None
    if consumption.get("evidence_tier") != expected_tier:
        issues.append(_issue("outcome_report_evidence_tier_mismatch", location))
    if consumption.get("evidence_admitted") is not ticket.admits_evidence():
        issues.append(_issue("outcome_report_evidence_admission_mismatch", location))
    if consumption.get("actual_cost_spent") != observation.cost_spent:
        issues.append(_issue("outcome_report_actual_cost_mismatch", location))
    actual_cost = consumption.get("actual_cost_spent")
    if _nonnegative_finite_number(actual_cost) is None:
        issues.append(_issue("outcome_report_actual_cost_invalid", location))
    approved_budget = consumption.get("approved_budget")
    if (
        isinstance(approved_budget, bool)
        or not isinstance(approved_budget, int | float)
        or approved_budget <= 0
        or observation.cost_spent > approved_budget
    ):
        issues.append(_issue("outcome_report_consumption_budget_invalid", location))
    consumed_at = _timestamp_or_none(consumption.get("consumed_at"))
    if consumed_at is None:
        issues.append(_issue("outcome_report_consumed_at_invalid", location))
    elif assignment_time is not None and consumed_at <= assignment_time:
        issues.append(_issue("outcome_report_outcome_not_after_assignment", location))
    elif report_time is not None and consumed_at > report_time:
        issues.append(_issue("outcome_report_before_consumption", location))
    status = report_item.get("outcome_status")
    metric_key = _string_or_none(
        (protocol.get("primary_outcome") or {}).get("outcome_metric_key")
        if isinstance(protocol.get("primary_outcome"), Mapping)
        else None
    )
    metric_value = observation.metrics.get(metric_key or "")
    reported_value = report_item.get("primary_outcome_value")
    if status == "admitted_assay_outcome":
        if consumption.get("evidence_admitted") is not True or not ticket.admits_evidence():
            issues.append(_issue("outcome_report_admitted_status_without_admitted_evidence", location))
        if not isinstance(metric_value, bool) or reported_value is not metric_value:
            issues.append(_issue("outcome_report_primary_value_mismatch", location))
    else:
        if consumption.get("evidence_admitted") is True and ticket.admits_evidence() and isinstance(metric_value, bool):
            issues.append(_issue("outcome_report_valid_admitted_outcome_marked_unresolved", location))
        if reported_value is not None:
            issues.append(_issue("outcome_report_unresolved_outcome_has_primary_value", location))
        if _string_or_none(report_item.get("rationale")) is None:
            issues.append(_issue("outcome_report_unresolved_outcome_rationale_missing", location))


def _validate_assay_observation_provenance(
    observation: Any,
    policy: Mapping[str, Any],
    power_plan: Mapping[str, Any],
    location: str,
    issues: list[ProspectiveLoopEvaluationOutcomeIssue],
) -> None:
    provenance = observation.provenance
    if provenance.get("source_endpoint_policy_content_sha256") != canonical_content_sha256(policy):
        issues.append(_issue("outcome_report_assay_policy_provenance_mismatch", location))
    if observation.oracle_name != policy.get("oracle_name"):
        issues.append(_issue("outcome_report_assay_oracle_policy_mismatch", location))
    if provenance.get("assay_protocol_id") != policy.get("assay_protocol_id"):
        issues.append(_issue("outcome_report_assay_protocol_provenance_mismatch", location))
    if _string_or_none(provenance.get("assay_run_id")) is None:
        issues.append(_issue("outcome_report_assay_run_provenance_missing", location))
    if _string_or_none(provenance.get("assay_site_id")) is None:
        issues.append(_issue("outcome_report_assay_site_provenance_missing", location))
    elif provenance.get("assay_site_id") != power_plan.get("planned_assay_site_id"):
        issues.append(_issue("outcome_report_assay_site_not_predeclared", location))
    if provenance.get("qc_status") != "pass":
        issues.append(_issue("outcome_report_assay_qc_not_pass", location))


def _validate_commit_evidence_binding(
    evidence: Mapping[str, Any],
    consumption: Mapping[str, Any],
    policy: Mapping[str, Any],
    location: str,
    issues: list[ProspectiveLoopEvaluationOutcomeIssue],
) -> None:
    expected = {
        "source_consumption_content_sha256": canonical_content_sha256(consumption),
        "observation_id": consumption.get("observation_id"),
        "trust_gate_result_id": consumption.get("trust_gate_result_id"),
        "source_endpoint_policy_content_sha256": canonical_content_sha256(policy),
    }
    for field, expected_value in expected.items():
        if evidence.get(field) != expected_value:
            issues.append(_issue(f"outcome_report_commit_evidence_{field}_mismatch", location))
    for field in ("source_result_sha256", "source_output_sha256"):
        if _string_or_none(evidence.get(field)) is None:
            issues.append(_issue(f"outcome_report_commit_evidence_{field}_missing", location))


def _validate_policy_fidelity_binding(
    fidelity: Mapping[str, Any],
    assignment: Mapping[str, Any],
    consumption: Mapping[str, Any],
    location: str,
    issues: list[ProspectiveLoopEvaluationOutcomeIssue],
) -> None:
    for field in ("assignment_id", "arm_id", "subject_id"):
        if fidelity.get(field) != assignment.get(field):
            issues.append(_issue(f"outcome_report_policy_fidelity_{field}_mismatch", location))
    selected = fidelity.get("selected_decision")
    if not isinstance(selected, Mapping):
        issues.append(_issue("outcome_report_policy_fidelity_selected_decision_invalid", location))
        return
    if selected.get("subject_id") != assignment.get("subject_id"):
        issues.append(_issue("outcome_report_policy_fidelity_selected_subject_mismatch", location))
    if selected.get("to_action") != consumption.get("action_kind"):
        issues.append(_issue("outcome_report_policy_fidelity_selected_action_mismatch", location))


def _validate_missing_outcome(
    item: Mapping[str, Any],
    matching_consumptions: Sequence[Mapping[str, Any]],
    location: str,
    issues: list[ProspectiveLoopEvaluationOutcomeIssue],
) -> None:
    if item.get("consumption_id") is not None:
        issues.append(_issue("outcome_report_missing_outcome_has_consumption", location))
    if item.get("primary_outcome_value") is not None:
        issues.append(_issue("outcome_report_missing_outcome_has_primary_value", location))
    if _string_or_none(item.get("rationale")) is None:
        issues.append(_issue("outcome_report_missing_outcome_rationale_missing", location))
    if matching_consumptions:
        issues.append(_issue("outcome_report_missing_outcome_has_consumption_evidence", location))


def _validate_experiment_budget(
    protocol: Mapping[str, Any],
    assignments: Mapping[str, Mapping[str, Any]],
    reports: Sequence[Any],
    ledger_by_id: Mapping[str, Mapping[str, Any]],
    issues: list[ProspectiveLoopEvaluationOutcomeIssue],
) -> None:
    per_arm_limit = _per_arm_experiment_budget_limit(protocol)
    total_limit = _positive_number(
        (protocol.get("shared_budget") or {}).get("experiment_budget_limit")
        if isinstance(protocol.get("shared_budget"), Mapping)
        else None
    )
    if per_arm_limit is None or total_limit is None:
        issues.append(_issue("outcome_report_declared_experiment_budget_invalid", "$.protocol.shared_budget"))
        return
    costs_by_arm: Counter[str] = Counter()
    seen_consumption_ids: set[str] = set()
    for item in reports:
        if not isinstance(item, Mapping):
            continue
        assignment = assignments.get(_string_or_none(item.get("assignment_id")) or "")
        consumption_id = _string_or_none(item.get("consumption_id"))
        if assignment is None or consumption_id is None or consumption_id in seen_consumption_ids:
            continue
        consumption = ledger_by_id.get(consumption_id)
        if consumption is None:
            continue
        actual_cost = _nonnegative_finite_number(consumption.get("actual_cost_spent"))
        if actual_cost is None:
            continue
        seen_consumption_ids.add(consumption_id)
        arm_id = _string_or_none(assignment.get("arm_id"))
        if arm_id is not None:
            costs_by_arm[arm_id] += actual_cost
    for arm_id, actual_cost in costs_by_arm.items():
        if actual_cost > per_arm_limit:
            issues.append(_issue("outcome_report_arm_experiment_budget_exceeded", f"$.outcome_report.{arm_id}"))
    if sum(costs_by_arm.values()) > total_limit:
        issues.append(_issue("outcome_report_total_experiment_budget_exceeded", "$.outcome_report"))


def _per_arm_experiment_budget_limit(protocol: Mapping[str, Any]) -> float | None:
    shared_budget = protocol.get("shared_budget")
    if not isinstance(shared_budget, Mapping):
        return None
    return _positive_number(shared_budget.get("per_arm_experiment_budget_limit"))


def _validate_human_acceptance(
    report: Mapping[str, Any],
    report_time: datetime | None,
    issues: list[ProspectiveLoopEvaluationOutcomeIssue],
) -> None:
    value = report.get("human_acceptance")
    if not isinstance(value, Mapping):
        issues.append(_issue("outcome_report_human_acceptance_invalid", "$.outcome_report.human_acceptance"))
        return
    if value.get("decision") != "accept":
        issues.append(_issue("outcome_report_human_acceptance_not_accepted", "$.outcome_report.human_acceptance.decision"))
    for field in ("reviewer", "rationale"):
        if _string_or_none(value.get(field)) is None:
            issues.append(_issue(f"outcome_report_human_acceptance_{field}_missing", f"$.outcome_report.human_acceptance.{field}"))
    reviewed_at = _timestamp_or_none(value.get("reviewed_at"))
    if reviewed_at is None:
        issues.append(_issue("outcome_report_human_acceptance_reviewed_at_invalid", "$.outcome_report.human_acceptance.reviewed_at"))
    elif report_time is not None and reviewed_at > report_time:
        issues.append(_issue("outcome_report_human_acceptance_after_report", "$.outcome_report.human_acceptance.reviewed_at"))


def _validate_authority_fields(
    report: Mapping[str, Any],
    issues: list[ProspectiveLoopEvaluationOutcomeIssue],
) -> None:
    for field in _REPORT_AUTHORITY_FIELDS:
        if report.get(field) is not False:
            issues.append(_issue(f"outcome_report_{field}_must_be_false", f"$.outcome_report.{field}"))


def _assignments_by_id(assignment: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    assignments = assignment.get("assignments")
    if not isinstance(assignments, list | tuple):
        return {}
    return {
        assignment_id: item
        for item in assignments
        if isinstance(item, Mapping)
        for assignment_id in (_string_or_none(item.get("assignment_id")),)
        if assignment_id is not None
    }


def _ledger_by_consumption_id(ledger: Sequence[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    return {
        consumption_id: item
        for item in ledger
        if isinstance(item, Mapping)
        for consumption_id in (_string_or_none(item.get("consumption_id")),)
        if consumption_id is not None
    }


def _consumptions_for_assignment(
    ledger: Sequence[Mapping[str, Any]],
    assignment: Mapping[str, Any],
) -> tuple[Mapping[str, Any], ...]:
    fields = ("approval_id", "request_id", "subject_id")
    return tuple(
        item
        for item in ledger
        if isinstance(item, Mapping)
        and all(item.get(field) == assignment.get(field) for field in fields)
    )


def _arm_outcomes(
    protocol: Mapping[str, Any] | None,
    assignment: Mapping[str, Any] | None,
    report: Mapping[str, Any] | None,
    ledger: Sequence[Mapping[str, Any]] | None,
) -> tuple[ProspectiveLoopEvaluationArmOutcomeCount, ...]:
    assignments = _assignments_by_id(assignment or {})
    ledger_by_id = _ledger_by_consumption_id(ledger or ())
    outcome_items = {
        assignment_id: item
        for item in (report or {}).get("outcomes", ())
        if isinstance(item, Mapping)
        for assignment_id in (_string_or_none(item.get("assignment_id")),)
        if assignment_id is not None
    }
    arm_ids = [
        arm_id
        for item in (protocol or {}).get("arms", ())
        if isinstance(item, Mapping)
        for arm_id in (_string_or_none(item.get("arm_id")),)
        if arm_id is not None
    ]
    counts: list[ProspectiveLoopEvaluationArmOutcomeCount] = []
    per_arm_budget = _per_arm_experiment_budget_limit(protocol or {})
    for arm_id in sorted(arm_ids):
        items = [
            outcome_items.get(assignment_id)
            for assignment_id, assignment_item in assignments.items()
            if assignment_item.get("arm_id") == arm_id
        ]
        statuses = Counter(
            item.get("outcome_status") for item in items if isinstance(item, Mapping)
        )
        primary_values = [
            item.get("primary_outcome_value")
            for item in items
            if isinstance(item, Mapping)
            and item.get("outcome_status") == "admitted_assay_outcome"
            and isinstance(item.get("primary_outcome_value"), bool)
        ]
        consumption_ids = {
            _string_or_none(item.get("consumption_id"))
            for item in items
            if isinstance(item, Mapping)
            and _string_or_none(item.get("consumption_id")) is not None
        }
        actual_cost = sum(
            float(record.get("actual_cost_spent"))
            for consumption_id in consumption_ids
            for record in (ledger_by_id.get(consumption_id),)
            if record is not None
            and not isinstance(record.get("actual_cost_spent"), bool)
            and isinstance(record.get("actual_cost_spent"), int | float)
            and record.get("actual_cost_spent") >= 0
            and math.isfinite(float(record.get("actual_cost_spent")))
        )
        counts.append(
            ProspectiveLoopEvaluationArmOutcomeCount(
                arm_id=arm_id,
                randomized_episode_count=sum(
                    1
                    for item in assignments.values()
                    if item.get("arm_id") == arm_id
                ),
                admitted_assay_outcome_count=statuses["admitted_assay_outcome"],
                invalid_or_unresolved_outcome_count=statuses[
                    "invalid_or_unresolved_assay_outcome"
                ],
                missing_outcome_count=statuses["missing_outcome"],
                primary_positive_count=sum(value is True for value in primary_values),
                primary_negative_count=sum(value is False for value in primary_values),
                declared_experiment_budget_limit=per_arm_budget,
                actual_experiment_cost_spent=actual_cost,
            )
        )
    return tuple(counts)


def _outcome_report_status(
    issues: Sequence[ProspectiveLoopEvaluationOutcomeIssue],
    arm_outcomes: Sequence[ProspectiveLoopEvaluationArmOutcomeCount],
) -> str:
    if issues:
        return "FAIL"
    if any(item.missing_outcome_count for item in arm_outcomes):
        return "COMPLETE_WITH_MISSING_OUTCOMES"
    if any(item.invalid_or_unresolved_outcome_count for item in arm_outcomes):
        return "COMPLETE_WITH_UNRESOLVED_OUTCOMES"
    return "COMPLETE_ADMITTED_OUTCOMES"


def _campaign_hash(store: CampaignStore) -> str:
    return canonical_content_sha256([event_to_dict(event) for event in store.events])


def _read_object(
    path: Path,
    name: str,
) -> tuple[Mapping[str, Any] | None, ProspectiveLoopEvaluationOutcomeIssue | None]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError:
        return None, _issue(f"{name}_unreadable", f"$.{name}")
    except json.JSONDecodeError:
        return None, _issue(f"{name}_invalid_json", f"$.{name}")
    if not isinstance(payload, Mapping):
        return None, _issue(f"{name}_not_object", f"$.{name}")
    return payload, None


def _read_objects(
    paths: Sequence[str | Path],
    name: str,
) -> tuple[tuple[Mapping[str, Any], ...], tuple[ProspectiveLoopEvaluationOutcomeIssue, ...]]:
    values: list[Mapping[str, Any]] = []
    issues: list[ProspectiveLoopEvaluationOutcomeIssue] = []
    for index, path in enumerate(paths):
        value, issue = _read_object(Path(path), f"{name}_{index}")
        if value is not None:
            values.append(value)
        if issue is not None:
            issues.append(issue)
    return tuple(values), tuple(issues)


def _read_jsonl(
    path: Path,
) -> tuple[tuple[Mapping[str, Any], ...] | None, ProspectiveLoopEvaluationOutcomeIssue | None]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None, _issue("consumption_ledger_unreadable", "$.consumption_ledger")
    records: list[Mapping[str, Any]] = []
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            return None, _issue("consumption_ledger_invalid_json", f"$.consumption_ledger[{line_number}]")
        if not isinstance(value, Mapping):
            return None, _issue("consumption_ledger_item_not_object", f"$.consumption_ledger[{line_number}]")
        records.append(value)
    return tuple(records), None


def _read_campaign_store(
    path: Path,
) -> tuple[CampaignStore | None, ProspectiveLoopEvaluationOutcomeIssue | None]:
    try:
        return CampaignStore.load_json(path), None
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
        return None, _issue("campaign_store_unreadable", "$.campaign_store")


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


def _positive_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float) or value <= 0:
        return None
    if not math.isfinite(float(value)):
        return None
    return float(value)


def _nonnegative_finite_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float) or value < 0:
        return None
    if not math.isfinite(float(value)):
        return None
    return float(value)


def _issue(code: str, location: str) -> ProspectiveLoopEvaluationOutcomeIssue:
    return ProspectiveLoopEvaluationOutcomeIssue(
        code=code,
        location=location,
        message=code.replace("_", " ") + ".",
    )


def _summary_line(result: ProspectiveLoopEvaluationOutcomeVerification) -> str:
    return (
        "Prospective loop evaluation outcome verification: "
        f"status={result.verification_status}; outcome_status={result.outcome_report_status}; "
        "policy_ranking=false; execution_authority=false; "
        "scientific_claim_upgrade=false; analysis_computed=false."
    )
