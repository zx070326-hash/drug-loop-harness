from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from .artifact_hash import canonical_content_sha256, json_ready
from .contracts import LoopState, OracleKind
from .prospective_evaluation_assignment import (
    PROSPECTIVE_LOOP_EVALUATION_ARM_ASSIGNMENT_SCHEMA,
    PROSPECTIVE_LOOP_EVALUATION_ARM_ASSIGNMENT_VERIFICATION_SCHEMA,
)
from .prospective_evaluation_protocol import PROSPECTIVE_LOOP_EVALUATION_PROTOCOL_SCHEMA
from .prospective_policy_runtime import (
    policy_from_spec,
    policy_output_semantics,
    policy_runtime_manifest,
)
from .store import CampaignStore, event_to_dict


PROSPECTIVE_POLICY_DECISION_RECEIPT_SCHEMA = "prospective_policy_decision_receipt.v1"


@dataclass(frozen=True)
class ProspectivePolicyDecisionReceipt:
    """Recorded policy output for one externally assigned subject, never an execution token."""

    schema_version: str
    receipt_id: str
    source_protocol_content_sha256: str
    source_assignment_content_sha256: str
    source_assignment_verification_content_sha256: str
    source_decision_campaign_content_sha256: str
    assignment_id: str
    arm_id: str
    subject_id: str
    source_policy_spec_content_sha256: str
    source_policy_runtime_content_sha256: str
    decided_at: str
    outcomes_observed_at_decision: bool
    selected_decision: dict[str, Any]
    source_policy_output_semantic_sha256: str
    policy_ranking_permitted: bool
    execution_authority_granted: bool
    autonomous_execution_permitted: bool
    scientific_claim_upgrade_permitted: bool
    summary_line: str

    def to_dict(self) -> dict[str, Any]:
        return json_ready(asdict(self))


class ProspectivePolicyDecisionReceiptRecorder:
    """Record one deterministic, pre-outcome policy decision without taking an action."""

    def record(
        self,
        protocol: Mapping[str, Any],
        assignment: Mapping[str, Any],
        assignment_verification: Mapping[str, Any],
        decision_campaign_store: CampaignStore,
        *,
        assignment_id: str,
        decided_at: str | None = None,
        receipt_id: str | None = None,
    ) -> ProspectivePolicyDecisionReceipt:
        if protocol.get("schema_version") != PROSPECTIVE_LOOP_EVALUATION_PROTOCOL_SCHEMA:
            raise ValueError("protocol schema is not a supported prospective evaluation protocol")
        _validate_assignment_verification(protocol, assignment, assignment_verification)
        item = _assignment_by_id(assignment, assignment_id)
        if item is None:
            raise ValueError("assignment_id not found in prospective arm assignment")
        arm_id = _required_string(item, "arm_id")
        subject_id = _required_string(item, "subject_id")
        arm = _arm_by_id(protocol, arm_id)
        if arm is None:
            raise ValueError("assigned arm_id not found in protocol")
        policy_spec = arm.get("policy_spec")
        if not isinstance(policy_spec, Mapping):
            raise ValueError("assigned arm policy_spec is invalid")
        runtime_manifest = policy_runtime_manifest(policy_spec)
        runtime_hash = canonical_content_sha256(runtime_manifest)
        if arm.get("policy_runtime_manifest") != runtime_manifest:
            raise ValueError("assigned arm policy runtime manifest does not match")
        if arm.get("policy_runtime_content_sha256") != runtime_hash:
            raise ValueError("assigned arm policy runtime hash does not match")
        timestamp = decided_at or datetime.now(timezone.utc).isoformat()
        decision_time = _parse_timestamp(timestamp, "decided_at")
        assigned_time = _parse_timestamp(assignment.get("assigned_at"), "assigned_at")
        if decision_time < assigned_time:
            raise ValueError("decided_at cannot precede assigned_at")
        _validate_assignment_verification_timing(
            assignment_verification,
            assigned_time,
            decision_time,
        )
        _validate_decision_campaign_temporality(decision_campaign_store, decision_time)
        try:
            state = decision_campaign_store.rebuild()
        except (TypeError, ValueError) as exc:
            raise ValueError("decision campaign store is not rebuildable") from exc
        if _study_assay_subject_ids(state, assignment):
            raise ValueError("prospective assignment outcome is present in decision state")
        try:
            policy = policy_from_spec(policy_spec)
        except ValueError as exc:
            raise ValueError(f"assigned arm policy is not replayable: {exc}") from exc
        semantics = policy_output_semantics(policy, state)
        selected = tuple(item for item in semantics if item["subject_id"] == subject_id)
        if len(selected) != 1:
            raise ValueError("assigned subject must have exactly one replayed policy decision")
        receipt = ProspectivePolicyDecisionReceipt(
            schema_version=PROSPECTIVE_POLICY_DECISION_RECEIPT_SCHEMA,
            receipt_id=receipt_id or f"prospective_policy_receipt_{uuid4().hex}",
            source_protocol_content_sha256=canonical_content_sha256(protocol),
            source_assignment_content_sha256=canonical_content_sha256(assignment),
            source_assignment_verification_content_sha256=canonical_content_sha256(
                assignment_verification
            ),
            source_decision_campaign_content_sha256=_campaign_hash(decision_campaign_store),
            assignment_id=assignment_id,
            arm_id=arm_id,
            subject_id=subject_id,
            source_policy_spec_content_sha256=canonical_content_sha256(policy_spec),
            source_policy_runtime_content_sha256=runtime_hash,
            decided_at=timestamp,
            outcomes_observed_at_decision=False,
            selected_decision=selected[0],
            source_policy_output_semantic_sha256=canonical_content_sha256(semantics),
            policy_ranking_permitted=False,
            execution_authority_granted=False,
            autonomous_execution_permitted=False,
            scientific_claim_upgrade_permitted=False,
            summary_line="",
        )
        return replace(receipt, summary_line=_summary_line(receipt))


def _assignment_by_id(assignment: Mapping[str, Any], assignment_id: str) -> Mapping[str, Any] | None:
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


def _validate_assignment_verification(
    protocol: Mapping[str, Any],
    assignment: Mapping[str, Any],
    verification: Mapping[str, Any],
) -> None:
    if assignment.get("schema_version") != PROSPECTIVE_LOOP_EVALUATION_ARM_ASSIGNMENT_SCHEMA:
        raise ValueError("arm assignment schema is invalid")
    if assignment.get("source_protocol_content_sha256") != canonical_content_sha256(protocol):
        raise ValueError("arm assignment protocol hash does not match")
    if assignment.get("outcomes_observed_at_assignment") is not False:
        raise ValueError("arm assignment must declare outcomes unobserved")
    if verification.get("schema_version") != PROSPECTIVE_LOOP_EVALUATION_ARM_ASSIGNMENT_VERIFICATION_SCHEMA:
        raise ValueError("arm assignment verification schema is invalid")
    if verification.get("verification_status") != "PASS":
        raise ValueError("arm assignment verification is not PASS")
    if verification.get("source_protocol_content_sha256") != canonical_content_sha256(protocol):
        raise ValueError("arm assignment verification protocol hash does not match")
    if verification.get("source_assignment_content_sha256") != canonical_content_sha256(assignment):
        raise ValueError("arm assignment verification hash does not match")
    if verification.get("comparison_analysis_permitted") is not False:
        raise ValueError("arm assignment verification cannot grant analysis authority")
    for field in (
        "policy_ranking_permitted",
        "execution_authority_granted",
        "autonomous_execution_permitted",
        "scientific_claim_upgrade_permitted",
    ):
        if verification.get(field) is not False:
            raise ValueError(f"arm assignment verification {field} must be false")


def _arm_by_id(protocol: Mapping[str, Any], arm_id: str) -> Mapping[str, Any] | None:
    arms = protocol.get("arms")
    if not isinstance(arms, list | tuple):
        return None
    return next(
        (
            arm
            for arm in arms
            if isinstance(arm, Mapping) and arm.get("arm_id") == arm_id
        ),
        None,
    )


def _campaign_hash(store: CampaignStore) -> str:
    return canonical_content_sha256([event_to_dict(event) for event in store.events])


def _has_assay_observation(state: LoopState, subject_id: str) -> bool:
    return any(
        observation.subject_id == subject_id
        and (oracle := state.oracles.get(observation.oracle_name)) is not None
        and oracle.kind is OracleKind.ASSAY
        for observation in state.observations.values()
    )


def _study_assay_subject_ids(state: LoopState, assignment: Mapping[str, Any]) -> tuple[str, ...]:
    assignments = assignment.get("assignments")
    if not isinstance(assignments, list | tuple):
        return ()
    subject_ids = {
        item.get("subject_id")
        for item in assignments
        if isinstance(item, Mapping) and isinstance(item.get("subject_id"), str)
    }
    return tuple(
        sorted(
            subject_id
            for subject_id in subject_ids
            if _has_assay_observation(state, subject_id)
        )
    )


def _required_string(value: Mapping[str, Any], field: str) -> str:
    result = value.get(field)
    if not isinstance(result, str) or not result:
        raise ValueError(f"assignment {field} is required")
    return result


def _parse_timestamp(value: Any, field: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} must be a timezone-aware ISO-8601 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field} must be a timezone-aware ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field} must be a timezone-aware ISO-8601 timestamp")
    return parsed


def _validate_decision_campaign_temporality(
    store: CampaignStore,
    decided_at: datetime,
) -> None:
    for event in store.events:
        event_time = _parse_timestamp(event.created_at, f"campaign event {event.id} created_at")
        if event_time > decided_at:
            raise ValueError("decision campaign contains an event after decided_at")


def _validate_assignment_verification_timing(
    verification: Mapping[str, Any],
    assigned_at: datetime,
    decided_at: datetime,
) -> None:
    verified_at = _parse_timestamp(
        verification.get("verified_at"),
        "assignment verification verified_at",
    )
    if verified_at < assigned_at:
        raise ValueError("arm assignment verification cannot precede assigned_at")
    if verified_at > decided_at:
        raise ValueError("arm assignment verification cannot follow policy decision")


def _summary_line(receipt: ProspectivePolicyDecisionReceipt) -> str:
    return (
        "Prospective policy decision receipt: "
        f"assignment_id={receipt.assignment_id}; action={receipt.selected_decision['to_action']}; "
        "execution_authority=false; scientific_claim_upgrade=false."
    )
