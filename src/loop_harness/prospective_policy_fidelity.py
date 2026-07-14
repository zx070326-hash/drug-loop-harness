from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .artifact_hash import canonical_content_sha256, json_ready
from .contracts import LoopState, OracleKind
from .prospective_evaluation_assignment import (
    PROSPECTIVE_LOOP_EVALUATION_ARM_ASSIGNMENT_VERIFICATION_SCHEMA,
)
from .prospective_evaluation_protocol import PROSPECTIVE_LOOP_EVALUATION_PROTOCOL_SCHEMA
from .prospective_policy_receipt import PROSPECTIVE_POLICY_DECISION_RECEIPT_SCHEMA
from .prospective_policy_runtime import (
    policy_from_spec,
    policy_output_semantics,
    policy_runtime_manifest,
)
from .store import CampaignStore, event_to_dict


PROSPECTIVE_POLICY_FIDELITY_SCHEMA = "prospective_policy_fidelity.v1"
PROSPECTIVE_POLICY_FIDELITY_SET_SCHEMA = "prospective_policy_fidelity_set.v1"
_AUTHORITY_FIELDS = (
    "policy_ranking_permitted",
    "execution_authority_granted",
    "autonomous_execution_permitted",
    "scientific_claim_upgrade_permitted",
)


@dataclass(frozen=True)
class ProspectivePolicyFidelityIssue:
    code: str
    location: str
    message: str


@dataclass(frozen=True)
class ProspectivePolicyFidelity:
    """Read-only proof that a registered policy produced one pre-outcome decision."""

    schema_version: str
    verification_status: str
    receipt_id: str | None
    assignment_id: str | None
    arm_id: str | None
    subject_id: str | None
    policy_kind: str | None
    source_protocol_content_sha256: str | None
    source_assignment_content_sha256: str | None
    source_assignment_verification_content_sha256: str | None
    source_decision_campaign_content_sha256: str | None
    source_policy_output_semantic_sha256: str | None
    source_policy_runtime_content_sha256: str | None
    selected_decision: Mapping[str, Any] | None
    issue_codes: tuple[str, ...]
    issues: tuple[ProspectivePolicyFidelityIssue, ...]
    policy_ranking_permitted: bool
    execution_authority_granted: bool
    autonomous_execution_permitted: bool
    scientific_claim_upgrade_permitted: bool
    summary_line: str

    def to_dict(self) -> dict[str, Any]:
        return json_ready(asdict(self))


@dataclass(frozen=True)
class ProspectivePolicyFidelitySet:
    """Canonical receipts required to establish intervention fidelity for one outcome report."""

    schema_version: str
    verification_status: str
    fidelity: tuple[Mapping[str, Any], ...]
    source_fidelity_content_sha256: str
    issue_codes: tuple[str, ...]
    policy_ranking_permitted: bool
    execution_authority_granted: bool
    autonomous_execution_permitted: bool
    scientific_claim_upgrade_permitted: bool
    summary_line: str

    def to_dict(self) -> dict[str, Any]:
        return json_ready(asdict(self))


class ProspectivePolicyDecisionFidelityVerifier:
    """Replay only implemented frozen policies against a pre-outcome campaign state."""

    def verify_path(
        self,
        protocol_path: str | Path,
        assignment_path: str | Path,
        assignment_verification_path: str | Path,
        receipt_path: str | Path,
        decision_campaign_store_path: str | Path,
    ) -> ProspectivePolicyFidelity:
        protocol, protocol_issue = _read_object(Path(protocol_path), "protocol")
        assignment, assignment_issue = _read_object(Path(assignment_path), "assignment")
        assignment_verification, assignment_verification_issue = _read_object(
            Path(assignment_verification_path), "assignment_verification"
        )
        receipt, receipt_issue = _read_object(Path(receipt_path), "receipt")
        store, store_issue = _read_store(Path(decision_campaign_store_path))
        return self._verify(
            protocol,
            assignment,
            assignment_verification,
            receipt,
            store,
            initial_issues=tuple(
                issue
                for issue in (
                    protocol_issue,
                    assignment_issue,
                    assignment_verification_issue,
                    receipt_issue,
                    store_issue,
                )
                if issue is not None
            ),
        )

    def verify(
        self,
        protocol: Mapping[str, Any],
        assignment: Mapping[str, Any],
        assignment_verification: Mapping[str, Any],
        receipt: Mapping[str, Any],
        decision_campaign_store: CampaignStore,
    ) -> ProspectivePolicyFidelity:
        return self._verify(
            protocol,
            assignment,
            assignment_verification,
            receipt,
            decision_campaign_store,
            initial_issues=(),
        )

    def _verify(
        self,
        protocol: Mapping[str, Any] | None,
        assignment: Mapping[str, Any] | None,
        assignment_verification: Mapping[str, Any] | None,
        receipt: Mapping[str, Any] | None,
        decision_campaign_store: CampaignStore | None,
        *,
        initial_issues: tuple[ProspectivePolicyFidelityIssue, ...],
    ) -> ProspectivePolicyFidelity:
        issues = list(initial_issues)
        for name, value in (
            ("protocol", protocol),
            ("assignment", assignment),
            ("assignment_verification", assignment_verification),
            ("receipt", receipt),
            ("decision_campaign_store", decision_campaign_store),
        ):
            if value is None:
                issues.append(_issue(f"policy_fidelity_{name}_unavailable", f"$.{name}"))
        state: LoopState | None = None
        campaign_hash: str | None = None
        if decision_campaign_store is not None:
            try:
                state = decision_campaign_store.rebuild()
                campaign_hash = _campaign_hash(decision_campaign_store)
            except (TypeError, ValueError):
                issues.append(_issue("policy_fidelity_decision_campaign_unrebuildable", "$.decision_campaign_store"))
        if (
            protocol is not None
            and assignment is not None
            and assignment_verification is not None
            and receipt is not None
            and state is not None
            and campaign_hash is not None
        ):
            _validate_receipt(
                protocol,
                assignment,
                assignment_verification,
                receipt,
                decision_campaign_store,
                state,
                campaign_hash,
                issues,
            )
        result = ProspectivePolicyFidelity(
            schema_version=PROSPECTIVE_POLICY_FIDELITY_SCHEMA,
            verification_status="PASS" if not issues else "FAIL",
            receipt_id=_string_or_none((receipt or {}).get("receipt_id")),
            assignment_id=_string_or_none((receipt or {}).get("assignment_id")),
            arm_id=_string_or_none((receipt or {}).get("arm_id")),
            subject_id=_string_or_none((receipt or {}).get("subject_id")),
            policy_kind=_policy_kind(protocol, receipt),
            source_protocol_content_sha256=(
                canonical_content_sha256(protocol) if protocol is not None else None
            ),
            source_assignment_content_sha256=(
                canonical_content_sha256(assignment) if assignment is not None else None
            ),
            source_assignment_verification_content_sha256=(
                canonical_content_sha256(assignment_verification)
                if assignment_verification is not None
                else None
            ),
            source_decision_campaign_content_sha256=campaign_hash,
            source_policy_output_semantic_sha256=(
                _string_or_none((receipt or {}).get("source_policy_output_semantic_sha256"))
            ),
            source_policy_runtime_content_sha256=(
                _string_or_none((receipt or {}).get("source_policy_runtime_content_sha256"))
            ),
            selected_decision=(
                receipt.get("selected_decision") if isinstance(receipt, Mapping) else None
            ),
            issue_codes=tuple(sorted({issue.code for issue in issues})),
            issues=tuple(issues),
            policy_ranking_permitted=False,
            execution_authority_granted=False,
            autonomous_execution_permitted=False,
            scientific_claim_upgrade_permitted=False,
            summary_line="",
        )
        return replace(result, summary_line=_summary_line(result))


class ProspectivePolicyFidelitySetBuilder:
    """Build a canonical set of replayed policy-decision receipts for one study."""

    def __init__(
        self,
        *,
        verifier: ProspectivePolicyDecisionFidelityVerifier | None = None,
    ) -> None:
        self.verifier = verifier or ProspectivePolicyDecisionFidelityVerifier()

    def build_paths(
        self,
        protocol_path: str | Path,
        assignment_path: str | Path,
        assignment_verification_path: str | Path,
        receipt_paths: list[str | Path] | tuple[str | Path, ...],
        decision_campaign_store_paths: list[str | Path] | tuple[str | Path, ...],
    ) -> ProspectivePolicyFidelitySet:
        issues: list[str] = []
        if len(receipt_paths) != len(decision_campaign_store_paths):
            issues.append("receipt_and_decision_campaign_count_mismatch")
        fidelity = tuple(
            self.verifier.verify_path(
                protocol_path,
                assignment_path,
                assignment_verification_path,
                receipt_path,
                store_path,
            ).to_dict()
            for receipt_path, store_path in zip(receipt_paths, decision_campaign_store_paths)
        )
        assignment_ids: set[str] = set()
        for index, item in enumerate(fidelity):
            if item.get("verification_status") != "PASS":
                issues.append(f"fidelity_{index}_not_pass")
            assignment_id = _string_or_none(item.get("assignment_id"))
            if assignment_id is None or assignment_id in assignment_ids:
                issues.append("fidelity_assignment_id_missing_or_duplicate")
            else:
                assignment_ids.add(assignment_id)
        result = ProspectivePolicyFidelitySet(
            schema_version=PROSPECTIVE_POLICY_FIDELITY_SET_SCHEMA,
            verification_status="PASS" if not issues else "FAIL",
            fidelity=fidelity,
            source_fidelity_content_sha256=canonical_content_sha256(fidelity),
            issue_codes=tuple(sorted(set(issues))),
            policy_ranking_permitted=False,
            execution_authority_granted=False,
            autonomous_execution_permitted=False,
            scientific_claim_upgrade_permitted=False,
            summary_line="",
        )
        return replace(result, summary_line=_set_summary_line(result))


def _validate_receipt(
    protocol: Mapping[str, Any],
    assignment: Mapping[str, Any],
    assignment_verification: Mapping[str, Any],
    receipt: Mapping[str, Any],
    decision_campaign_store: CampaignStore,
    state: LoopState,
    campaign_hash: str,
    issues: list[ProspectivePolicyFidelityIssue],
) -> None:
    if protocol.get("schema_version") != PROSPECTIVE_LOOP_EVALUATION_PROTOCOL_SCHEMA:
        issues.append(_issue("policy_fidelity_protocol_schema_invalid", "$.protocol.schema_version"))
    if receipt.get("schema_version") != PROSPECTIVE_POLICY_DECISION_RECEIPT_SCHEMA:
        issues.append(_issue("policy_fidelity_receipt_schema_invalid", "$.receipt.schema_version"))
    if _string_or_none(receipt.get("receipt_id")) is None:
        issues.append(_issue("policy_fidelity_receipt_id_missing", "$.receipt.receipt_id"))
    expected = {
        "source_protocol_content_sha256": canonical_content_sha256(protocol),
        "source_assignment_content_sha256": canonical_content_sha256(assignment),
        "source_assignment_verification_content_sha256": canonical_content_sha256(
            assignment_verification
        ),
        "source_decision_campaign_content_sha256": campaign_hash,
    }
    for field, value in expected.items():
        if receipt.get(field) != value:
            issues.append(_issue(f"policy_fidelity_{field}_mismatch", f"$.receipt.{field}"))
    _validate_assignment_verification(protocol, assignment, assignment_verification, issues)
    for field in _AUTHORITY_FIELDS:
        if receipt.get(field) is not False:
            issues.append(_issue(f"policy_fidelity_{field}_must_be_false", f"$.receipt.{field}"))
    if receipt.get("outcomes_observed_at_decision") is not False:
        issues.append(_issue("policy_fidelity_outcomes_observed_at_decision", "$.receipt.outcomes_observed_at_decision"))
    decided_at = _timestamp_or_none(receipt.get("decided_at"))
    if decided_at is None:
        issues.append(_issue("policy_fidelity_decided_at_invalid", "$.receipt.decided_at"))
    else:
        _validate_decision_campaign_temporality(
            decision_campaign_store,
            decided_at,
            issues,
        )

    assignment_item = _assignment_by_id(assignment, receipt.get("assignment_id"))
    if assignment_item is None:
        issues.append(_issue("policy_fidelity_assignment_missing", "$.receipt.assignment_id"))
        return
    for field in ("arm_id", "subject_id"):
        if receipt.get(field) != assignment_item.get(field):
            issues.append(_issue(f"policy_fidelity_{field}_assignment_mismatch", f"$.receipt.{field}"))
    assigned_at = _timestamp_or_none(assignment.get("assigned_at"))
    if decided_at is not None and assigned_at is not None and decided_at < assigned_at:
        issues.append(_issue("policy_fidelity_decision_before_assignment", "$.receipt.decided_at"))
    verified_at = _timestamp_or_none(assignment_verification.get("verified_at"))
    if verified_at is None:
        issues.append(_issue("policy_fidelity_assignment_verification_time_invalid", "$.assignment_verification.verified_at"))
    elif assigned_at is not None and verified_at < assigned_at:
        issues.append(_issue("policy_fidelity_assignment_verification_before_assignment", "$.assignment_verification.verified_at"))
    elif decided_at is not None and verified_at > decided_at:
        issues.append(_issue("policy_fidelity_assignment_verification_after_decision", "$.assignment_verification.verified_at"))
    subject_id = _string_or_none(assignment_item.get("subject_id"))
    if _study_assay_subject_ids(state, assignment):
        issues.append(_issue("policy_fidelity_study_outcome_present_in_decision_state", "$.decision_campaign_store"))

    arm = _arm_by_id(protocol, assignment_item.get("arm_id"))
    if arm is None:
        issues.append(_issue("policy_fidelity_protocol_arm_missing", "$.receipt.arm_id"))
        return
    policy_spec = arm.get("policy_spec")
    if not isinstance(policy_spec, Mapping):
        issues.append(_issue("policy_fidelity_policy_spec_invalid", "$.protocol.arms"))
        return
    if receipt.get("source_policy_spec_content_sha256") != canonical_content_sha256(policy_spec):
        issues.append(_issue("policy_fidelity_policy_spec_hash_mismatch", "$.receipt.source_policy_spec_content_sha256"))
    try:
        policy = policy_from_spec(policy_spec)
    except ValueError as exc:
        issues.append(_issue("policy_fidelity_policy_not_replayable", "$.protocol.arms", str(exc)))
        return
    runtime_manifest = policy_runtime_manifest(policy_spec)
    runtime_hash = canonical_content_sha256(runtime_manifest)
    if arm.get("policy_runtime_manifest") != runtime_manifest:
        issues.append(_issue("policy_fidelity_policy_runtime_manifest_mismatch", "$.protocol.arms"))
    if arm.get("policy_runtime_content_sha256") != runtime_hash:
        issues.append(_issue("policy_fidelity_policy_runtime_hash_mismatch", "$.protocol.arms"))
    if receipt.get("source_policy_runtime_content_sha256") != runtime_hash:
        issues.append(_issue("policy_fidelity_policy_runtime_receipt_hash_mismatch", "$.receipt.source_policy_runtime_content_sha256"))
    semantics = policy_output_semantics(policy, state)
    output_hash = canonical_content_sha256(semantics)
    if receipt.get("source_policy_output_semantic_sha256") != output_hash:
        issues.append(_issue("policy_fidelity_policy_output_hash_mismatch", "$.receipt.source_policy_output_semantic_sha256"))
    selected = receipt.get("selected_decision")
    expected_selected = [
        semantic for semantic in semantics if semantic["subject_id"] == subject_id
    ]
    if not isinstance(selected, Mapping) or canonical_content_sha256(selected) not in {
        canonical_content_sha256(item) for item in expected_selected
    }:
        issues.append(_issue("policy_fidelity_selected_decision_not_replayed", "$.receipt.selected_decision"))


def _assignment_by_id(
    assignment: Mapping[str, Any],
    assignment_id: Any,
) -> Mapping[str, Any] | None:
    if not isinstance(assignment_id, str):
        return None
    items = assignment.get("assignments")
    if not isinstance(items, list | tuple):
        return None
    return next(
        (
            item
            for item in items
            if isinstance(item, Mapping) and item.get("assignment_id") == assignment_id
        ),
        None,
    )


def _arm_by_id(protocol: Mapping[str, Any], arm_id: Any) -> Mapping[str, Any] | None:
    if not isinstance(arm_id, str):
        return None
    arms = protocol.get("arms")
    if not isinstance(arms, list | tuple):
        return None
    return next(
        (arm for arm in arms if isinstance(arm, Mapping) and arm.get("arm_id") == arm_id),
        None,
    )


def _has_assay_observation(state: LoopState, subject_id: str) -> bool:
    return any(
        observation.subject_id == subject_id
        and (oracle := state.oracles.get(observation.oracle_name)) is not None
        and oracle.kind is OracleKind.ASSAY
        for observation in state.observations.values()
    )


def _study_assay_subject_ids(state: LoopState, assignment: Mapping[str, Any]) -> tuple[str, ...]:
    items = assignment.get("assignments")
    if not isinstance(items, list | tuple):
        return ()
    subject_ids = {
        item.get("subject_id")
        for item in items
        if isinstance(item, Mapping) and isinstance(item.get("subject_id"), str)
    }
    return tuple(
        sorted(
            subject_id
            for subject_id in subject_ids
            if _has_assay_observation(state, subject_id)
        )
    )


def _policy_kind(protocol: Mapping[str, Any] | None, receipt: Mapping[str, Any] | None) -> str | None:
    if protocol is None or receipt is None:
        return None
    assignment = _string_or_none(receipt.get("arm_id"))
    arm = _arm_by_id(protocol, assignment)
    policy = arm.get("policy_spec") if arm is not None else None
    return _string_or_none(policy.get("policy_kind")) if isinstance(policy, Mapping) else None


def _campaign_hash(store: CampaignStore) -> str:
    return canonical_content_sha256([event_to_dict(event) for event in store.events])


def _validate_assignment_verification(
    protocol: Mapping[str, Any],
    assignment: Mapping[str, Any],
    verification: Mapping[str, Any],
    issues: list[ProspectivePolicyFidelityIssue],
) -> None:
    if verification.get("schema_version") != PROSPECTIVE_LOOP_EVALUATION_ARM_ASSIGNMENT_VERIFICATION_SCHEMA:
        issues.append(_issue("policy_fidelity_assignment_verification_schema_invalid", "$.assignment_verification.schema_version"))
    if verification.get("verification_status") != "PASS":
        issues.append(_issue("policy_fidelity_assignment_verification_not_pass", "$.assignment_verification.verification_status"))
    if verification.get("source_protocol_content_sha256") != canonical_content_sha256(protocol):
        issues.append(_issue("policy_fidelity_assignment_verification_protocol_hash_mismatch", "$.assignment_verification.source_protocol_content_sha256"))
    if verification.get("source_assignment_content_sha256") != canonical_content_sha256(assignment):
        issues.append(_issue("policy_fidelity_assignment_verification_assignment_hash_mismatch", "$.assignment_verification.source_assignment_content_sha256"))
    if verification.get("comparison_analysis_permitted") is not False:
        issues.append(_issue("policy_fidelity_assignment_verification_analysis_authority_invalid", "$.assignment_verification.comparison_analysis_permitted"))
    for field in _AUTHORITY_FIELDS:
        if verification.get(field) is not False:
            issues.append(_issue(f"policy_fidelity_assignment_verification_{field}_must_be_false", f"$.assignment_verification.{field}"))


def _read_object(path: Path, name: str) -> tuple[Mapping[str, Any] | None, ProspectivePolicyFidelityIssue | None]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError:
        return None, _issue(f"policy_fidelity_{name}_unreadable", f"$.{name}")
    except json.JSONDecodeError:
        return None, _issue(f"policy_fidelity_{name}_invalid_json", f"$.{name}")
    if not isinstance(payload, Mapping):
        return None, _issue(f"policy_fidelity_{name}_not_object", f"$.{name}")
    return payload, None


def _read_store(path: Path) -> tuple[CampaignStore | None, ProspectivePolicyFidelityIssue | None]:
    try:
        return CampaignStore.load_json(path), None
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
        return None, _issue("policy_fidelity_decision_campaign_unreadable", "$.decision_campaign_store")


def _timestamp_or_none(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None and parsed.utcoffset() is not None else None


def _validate_decision_campaign_temporality(
    store: CampaignStore,
    decided_at: datetime,
    issues: list[ProspectivePolicyFidelityIssue],
) -> None:
    for event in store.events:
        event_time = _timestamp_or_none(event.created_at)
        if event_time is None:
            issues.append(_issue("policy_fidelity_decision_campaign_event_time_invalid", "$.decision_campaign_store"))
        elif event_time > decided_at:
            issues.append(_issue("policy_fidelity_decision_campaign_contains_future_event", "$.decision_campaign_store"))


def _string_or_none(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _issue(code: str, location: str, message: str | None = None) -> ProspectivePolicyFidelityIssue:
    return ProspectivePolicyFidelityIssue(
        code=code,
        location=location,
        message=message or code.replace("_", " ") + ".",
    )


def _summary_line(result: ProspectivePolicyFidelity) -> str:
    return (
        "Prospective policy fidelity: "
        f"status={result.verification_status}; assignment_id={result.assignment_id}; "
        "policy_ranking=false; execution_authority=false; scientific_claim_upgrade=false."
    )


def _set_summary_line(result: ProspectivePolicyFidelitySet) -> str:
    return (
        "Prospective policy fidelity set: "
        f"status={result.verification_status}; fidelity={len(result.fidelity)}; "
        "policy_ranking=false; execution_authority=false; scientific_claim_upgrade=false."
    )
