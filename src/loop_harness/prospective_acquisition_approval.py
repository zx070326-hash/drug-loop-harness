from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from .artifact_hash import canonical_content_sha256, json_ready


PROSPECTIVE_ACQUISITION_APPROVAL_SCHEMA = "prospective_acquisition_approval.v1"
PROSPECTIVE_ACQUISITION_PREFLIGHT_SCHEMA = "prospective_acquisition_preflight.v1"
PROSPECTIVE_ACQUISITION_PLAN_SCHEMA = "prospective_acquisition_plan.v1"
EXTERNAL_RANDOMIZATION_VERIFICATION_SCHEMA = "external_randomization_verification.v1"
SINGLE_SUBJECT_PREPARATION_SCOPE = "single_subject_preparation_only"
VALID_PROSPECTIVE_APPROVAL_DECISIONS = ("accept", "reject", "defer")


@dataclass(frozen=True)
class ProspectiveAcquisitionApproval:
    """Human decision for one receipt-selected subject, not executor permission."""

    schema_version: str
    approval_id: str
    source_plan_content_sha256: str
    source_randomization_verification_content_sha256: str
    request_id: str
    subject_id: str
    action_kind: str
    oracle_name: str
    decision: str
    rationale: str
    approved_budget: float | None
    preparation_scope: str | None
    expires_at: str | None
    reviewer: str
    reviewed_at: str
    summary_line: str

    def to_dict(self) -> dict[str, Any]:
        return json_ready(asdict(self))


@dataclass(frozen=True)
class ProspectiveAcquisitionPreflight:
    """Read-only join of plan, verified randomization, and exact human approval."""

    schema_version: str
    preflight_status: str
    source_plan_content_sha256: str
    source_randomization_verification_content_sha256: str
    approval_id: str | None
    request_id: str | None
    subject_id: str | None
    action_kind: str | None
    oracle_name: str | None
    approved_budget: float | None
    expires_at: str | None
    human_confirmed_acquisition_preparation_permitted: bool
    execution_authority_granted: bool
    autonomous_execution_permitted: bool
    scientific_claim_upgrade_permitted: bool
    issue_codes: tuple[str, ...]
    summary_line: str

    def to_dict(self) -> dict[str, Any]:
        return json_ready(asdict(self))


class ProspectiveAcquisitionApprovalRecorder:
    """Create exact human preparation approvals after verified external randomization."""

    def build(
        self,
        plan: Mapping[str, Any],
        randomization_verification: Mapping[str, Any],
        *,
        request_id: str,
        subject_id: str,
        decision: str = "defer",
        rationale: str = "",
        approved_budget: float | None = None,
        expires_at: str | None = None,
        reviewer: str = "pending_human_prospective_approval",
        reviewed_at: str | None = None,
        approval_id: str | None = None,
    ) -> ProspectiveAcquisitionApproval:
        request = _request_by_id(plan, request_id)
        if request is None:
            raise ValueError("request_id not found in prospective acquisition plan")
        if decision not in VALID_PROSPECTIVE_APPROVAL_DECISIONS:
            raise ValueError("decision must be accept, reject, or defer")
        plan_hash = canonical_content_sha256(plan)
        verification_hash = canonical_content_sha256(randomization_verification)
        selected = _string_items(randomization_verification.get("selected_subject_ids"))
        if subject_id not in selected:
            raise ValueError("subject_id is not selected by randomization verification")
        if randomization_verification.get("request_id") != request_id:
            raise ValueError("randomization verification request_id mismatch")
        if randomization_verification.get("source_plan_content_sha256") != plan_hash:
            raise ValueError("randomization verification plan hash mismatch")
        reviewed = reviewed_at or datetime.now(timezone.utc).isoformat()
        reviewed_timestamp = _parse_timestamp(reviewed, "reviewed_at")
        if decision == "accept":
            _validate_accepted_recording(
                request,
                randomization_verification,
                rationale=rationale,
                approved_budget=approved_budget,
                expires_at=expires_at,
                reviewer=reviewer,
                reviewed_timestamp=reviewed_timestamp,
            )
        elif approved_budget is not None or expires_at is not None:
            raise ValueError("only accepted approval may carry budget or expiry")
        approval = ProspectiveAcquisitionApproval(
            schema_version=PROSPECTIVE_ACQUISITION_APPROVAL_SCHEMA,
            approval_id=approval_id or f"prospective_approval_{uuid4().hex}",
            source_plan_content_sha256=plan_hash,
            source_randomization_verification_content_sha256=verification_hash,
            request_id=request_id,
            subject_id=subject_id,
            action_kind=str(request["action_kind"]),
            oracle_name=str(request["oracle_name"]),
            decision=decision,
            rationale=rationale,
            approved_budget=approved_budget,
            preparation_scope=(SINGLE_SUBJECT_PREPARATION_SCOPE if decision == "accept" else None),
            expires_at=expires_at,
            reviewer=reviewer,
            reviewed_at=reviewed,
            summary_line="",
        )
        return replace(approval, summary_line=_approval_summary_line(approval))


class ProspectiveAcquisitionPreflightEvaluator:
    """Fail-closed read-only preflight; valid status still cannot execute acquisition."""

    def evaluate(
        self,
        plan: Mapping[str, Any],
        randomization_verification: Mapping[str, Any],
        approval: Mapping[str, Any],
        *,
        reference_time: str | None = None,
    ) -> ProspectiveAcquisitionPreflight:
        plan_hash = canonical_content_sha256(plan)
        verification_hash = canonical_content_sha256(randomization_verification)
        reference = _parse_timestamp(
            reference_time or datetime.now(timezone.utc).isoformat(),
            "reference_time",
        )
        issue_codes: list[str] = []
        if plan.get("schema_version") != PROSPECTIVE_ACQUISITION_PLAN_SCHEMA:
            issue_codes.append("prospective_plan_schema_invalid")
        if (
            randomization_verification.get("schema_version")
            != EXTERNAL_RANDOMIZATION_VERIFICATION_SCHEMA
        ):
            issue_codes.append("randomization_verification_schema_invalid")
        if randomization_verification.get("verification_status") != "PASS":
            issue_codes.append("randomization_verification_not_pass")
        if randomization_verification.get("source_plan_content_sha256") != plan_hash:
            issue_codes.append("randomization_verification_plan_hash_mismatch")
        if (
            randomization_verification.get(
                "human_confirmed_acquisition_preparation_permitted"
            )
            is not True
        ):
            issue_codes.append("randomization_preparation_not_permitted")

        request_id = _optional_string(approval.get("request_id"))
        subject_id = _optional_string(approval.get("subject_id"))
        request = _request_by_id(plan, request_id)
        if request is None:
            issue_codes.append("approval_request_not_found")
        if approval.get("schema_version") != PROSPECTIVE_ACQUISITION_APPROVAL_SCHEMA:
            issue_codes.append("prospective_approval_schema_invalid")
        if approval.get("source_plan_content_sha256") != plan_hash:
            issue_codes.append("approval_plan_hash_mismatch")
        if (
            approval.get("source_randomization_verification_content_sha256")
            != verification_hash
        ):
            issue_codes.append("approval_randomization_verification_hash_mismatch")
        if approval.get("decision") != "accept":
            issue_codes.append("prospective_approval_not_accepted")
        if approval.get("preparation_scope") != SINGLE_SUBJECT_PREPARATION_SCOPE:
            issue_codes.append("prospective_approval_scope_invalid")
        if subject_id not in _string_items(
            randomization_verification.get("selected_subject_ids")
        ):
            issue_codes.append("approval_subject_not_in_verified_randomization")

        approved_budget = _positive_number(approval.get("approved_budget"))
        if approved_budget is None:
            issue_codes.append("prospective_approval_budget_invalid")
        if request is not None:
            if subject_id not in _string_items(request.get("candidate_pool_subject_ids")):
                issue_codes.append("approval_subject_not_in_request_pool")
            if approval.get("action_kind") != request.get("action_kind"):
                issue_codes.append("approval_action_kind_mismatch")
            if approval.get("oracle_name") != request.get("oracle_name"):
                issue_codes.append("approval_oracle_name_mismatch")
            request_cost = _positive_or_zero_number(
                request.get("expected_cost_per_observation")
            )
            if (
                approved_budget is not None
                and request_cost is not None
                and approved_budget < request_cost
            ):
                issue_codes.append("approval_budget_below_request_cost")

        expires_at = _optional_string(approval.get("expires_at"))
        if expires_at is None:
            issue_codes.append("prospective_approval_expiry_missing")
        else:
            try:
                if _parse_timestamp(expires_at, "expires_at") <= reference:
                    issue_codes.append("prospective_approval_expired")
            except ValueError:
                issue_codes.append("prospective_approval_expiry_invalid")
        if not _optional_string(approval.get("reviewer")):
            issue_codes.append("prospective_approval_reviewer_missing")
        if not _optional_string(approval.get("rationale")):
            issue_codes.append("prospective_approval_rationale_missing")
        try:
            _parse_timestamp(_optional_string(approval.get("reviewed_at")) or "", "reviewed_at")
        except ValueError:
            issue_codes.append("prospective_approval_reviewed_at_invalid")

        status = (
            "ready_for_human_confirmed_acquisition_preparation"
            if not issue_codes
            else _preflight_status(issue_codes)
        )
        result = ProspectiveAcquisitionPreflight(
            schema_version=PROSPECTIVE_ACQUISITION_PREFLIGHT_SCHEMA,
            preflight_status=status,
            source_plan_content_sha256=plan_hash,
            source_randomization_verification_content_sha256=verification_hash,
            approval_id=_optional_string(approval.get("approval_id")),
            request_id=request_id,
            subject_id=subject_id,
            action_kind=_optional_string(approval.get("action_kind")),
            oracle_name=_optional_string(approval.get("oracle_name")),
            approved_budget=approved_budget,
            expires_at=expires_at,
            human_confirmed_acquisition_preparation_permitted=not issue_codes,
            execution_authority_granted=False,
            autonomous_execution_permitted=False,
            scientific_claim_upgrade_permitted=False,
            issue_codes=tuple(sorted(set(issue_codes))),
            summary_line="",
        )
        return replace(result, summary_line=_preflight_summary_line(result))


def _validate_accepted_recording(
    request: Mapping[str, Any],
    verification: Mapping[str, Any],
    *,
    rationale: str,
    approved_budget: float | None,
    expires_at: str | None,
    reviewer: str,
    reviewed_timestamp: datetime,
) -> None:
    if verification.get("schema_version") != EXTERNAL_RANDOMIZATION_VERIFICATION_SCHEMA:
        raise ValueError("randomization verification schema invalid")
    if verification.get("verification_status") != "PASS":
        raise ValueError("randomization verification must pass before acceptance")
    if verification.get("human_confirmed_acquisition_preparation_permitted") is not True:
        raise ValueError("randomization verification does not permit preparation")
    if not rationale:
        raise ValueError("accepted approval requires rationale")
    if not reviewer:
        raise ValueError("accepted approval requires reviewer")
    budget = _positive_number(approved_budget)
    if budget is None:
        raise ValueError("accepted approval requires positive budget")
    expected_cost = _positive_or_zero_number(request.get("expected_cost_per_observation"))
    if expected_cost is None or budget < expected_cost:
        raise ValueError("accepted approval budget must cover request cost")
    if expires_at is None:
        raise ValueError("accepted approval requires expiry")
    if _parse_timestamp(expires_at, "expires_at") <= reviewed_timestamp:
        raise ValueError("accepted approval expiry must be after review")


def _request_by_id(
    plan: Mapping[str, Any], request_id: str | None
) -> Mapping[str, Any] | None:
    if request_id is None:
        return None
    requests = plan.get("requests")
    if not isinstance(requests, list | tuple):
        return None
    return next(
        (
            item
            for item in requests
            if isinstance(item, Mapping) and item.get("request_id") == request_id
        ),
        None,
    )


def _parse_timestamp(value: str, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{field} must be ISO-8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field} must include timezone")
    return parsed


def _positive_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float) or value <= 0:
        return None
    return float(value)


def _positive_or_zero_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float) or value < 0:
        return None
    return float(value)


def _optional_string(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _string_items(value: Any) -> tuple[str, ...]:
    if isinstance(value, str) or not isinstance(value, list | tuple):
        return ()
    return tuple(item for item in value if isinstance(item, str) and item)


def _preflight_status(issue_codes: list[str]) -> str:
    if any(code.startswith("randomization_") for code in issue_codes):
        return "blocked_by_randomization_verification"
    if any(code.startswith("prospective_approval") or code.startswith("approval_") for code in issue_codes):
        return "blocked_by_prospective_approval"
    return "repair_prospective_acquisition_control_state"


def _approval_summary_line(record: ProspectiveAcquisitionApproval) -> str:
    return (
        "Prospective acquisition approval: "
        f"decision={record.decision}; request_id={record.request_id}; "
        "execution_authority=false; scientific_claim_upgrade=false."
    )


def _preflight_summary_line(result: ProspectiveAcquisitionPreflight) -> str:
    return (
        "Prospective acquisition preflight: "
        f"status={result.preflight_status}; request_id={result.request_id or 'missing'}; "
        "execution_authority=false; scientific_claim_upgrade=false."
    )
