from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from typing import Any

from .artifact_hash import canonical_content_sha256, json_ready


EXTERNAL_RANDOMIZATION_RECEIPT_SCHEMA = "external_randomization_receipt.v1"
EXTERNAL_RANDOMIZATION_VERIFICATION_SCHEMA = "external_randomization_verification.v1"
PROSPECTIVE_ACQUISITION_PLAN_SCHEMA = "prospective_acquisition_plan.v1"
_INDEPENDENT_SOURCE_KINDS = {
    "public_randomness_beacon",
    "third_party_random_service",
    "institutional_randomization_service",
    "manual_lottery_with_witness",
}


@dataclass(frozen=True)
class ExternalRandomizationIssue:
    code: str
    location: str
    message: str


@dataclass(frozen=True)
class ExternalRandomizationVerification:
    """Verifies a human-approved external randomization receipt without an action."""

    schema_version: str
    verification_status: str
    source_plan_content_sha256: str
    receipt_content_sha256: str
    request_id: str | None
    selected_subject_ids: tuple[str, ...]
    external_randomization_source_kind: str | None
    human_confirmed_acquisition_preparation_permitted: bool
    execution_authority_granted: bool
    autonomous_execution_permitted: bool
    scientific_claim_upgrade_permitted: bool
    issues: tuple[ExternalRandomizationIssue, ...]
    summary_line: str

    def to_dict(self) -> dict[str, Any]:
        return json_ready(asdict(self))


class ExternalRandomizationReceiptVerifier:
    """Fail closed unless receipt, pool, random source, and human review agree."""

    def verify(
        self,
        plan: Mapping[str, Any],
        receipt: Mapping[str, Any],
    ) -> ExternalRandomizationVerification:
        plan_hash = canonical_content_sha256(plan)
        receipt_hash = canonical_content_sha256(receipt)
        issues: list[ExternalRandomizationIssue] = []
        if plan.get("schema_version") != PROSPECTIVE_ACQUISITION_PLAN_SCHEMA:
            issues.append(
                _issue("unsupported_plan_schema", "$.schema_version", "Expected prospective_acquisition_plan.v1.")
            )
        if receipt.get("schema_version") != EXTERNAL_RANDOMIZATION_RECEIPT_SCHEMA:
            issues.append(
                _issue("unsupported_receipt_schema", "$.schema_version", "Expected external_randomization_receipt.v1.")
            )
        request_id = _optional_string(receipt.get("request_id"))
        selected_subject_ids = _string_items(receipt.get("selected_subject_ids"))
        if receipt.get("source_plan_content_sha256") != plan_hash:
            issues.append(
                _issue(
                    "source_plan_hash_mismatch",
                    "$.source_plan_content_sha256",
                    "Receipt is not bound to the supplied prospective acquisition plan.",
                )
            )
        request = _plan_request(plan, request_id)
        if request is None:
            issues.append(
                _issue("request_not_found", "$.request_id", "Receipt request_id is absent from plan.")
            )
        else:
            _verify_pool_binding(request, receipt, selected_subject_ids, issues)
        source_kind = _verify_random_source(receipt, issues)
        _verify_human_review(receipt, issues)

        status = "PASS" if not issues else "FAIL"
        result = ExternalRandomizationVerification(
            schema_version=EXTERNAL_RANDOMIZATION_VERIFICATION_SCHEMA,
            verification_status=status,
            source_plan_content_sha256=plan_hash,
            receipt_content_sha256=receipt_hash,
            request_id=request_id,
            selected_subject_ids=selected_subject_ids,
            external_randomization_source_kind=source_kind,
            human_confirmed_acquisition_preparation_permitted=status == "PASS",
            execution_authority_granted=False,
            autonomous_execution_permitted=False,
            scientific_claim_upgrade_permitted=False,
            issues=tuple(issues),
            summary_line="",
        )
        return replace(result, summary_line=_summary_line(result))


def _plan_request(
    plan: Mapping[str, Any],
    request_id: str | None,
) -> Mapping[str, Any] | None:
    if request_id is None:
        return None
    requests = plan.get("requests")
    if not isinstance(requests, list | tuple):
        return None
    return next(
        (
            request
            for request in requests
            if isinstance(request, Mapping) and request.get("request_id") == request_id
        ),
        None,
    )


def _verify_pool_binding(
    request: Mapping[str, Any],
    receipt: Mapping[str, Any],
    selected_subject_ids: tuple[str, ...],
    issues: list[ExternalRandomizationIssue],
) -> None:
    if request.get("external_randomization_required") is not True:
        issues.append(
            _issue(
                "request_does_not_require_external_randomization",
                "$.request_id",
                "Receipt may only bind a request marked external_randomization_required.",
            )
        )
    if request.get("human_approval_required") is not True:
        issues.append(
            _issue(
                "request_does_not_require_human_approval",
                "$.request_id",
                "Receipt may only bind a request marked human_approval_required.",
            )
        )
    if receipt.get("candidate_pool_content_sha256") != request.get(
        "candidate_pool_content_sha256"
    ):
        issues.append(
            _issue(
                "candidate_pool_hash_mismatch",
                "$.candidate_pool_content_sha256",
                "Receipt pool hash does not match the planned request.",
            )
        )
    pool_subject_ids = _string_items(request.get("candidate_pool_subject_ids"))
    requested_count = request.get("requested_observation_count")
    if isinstance(requested_count, bool) or not isinstance(requested_count, int):
        issues.append(
            _issue(
                "request_observation_count_invalid",
                "$.requests[].requested_observation_count",
                "Planned request observation count must be an integer.",
            )
        )
    elif len(selected_subject_ids) != requested_count:
        issues.append(
            _issue(
                "selected_subject_count_mismatch",
                "$.selected_subject_ids",
                "Receipt selection count does not equal planned quota.",
            )
        )
    if len(set(selected_subject_ids)) != len(selected_subject_ids):
        issues.append(
            _issue(
                "selected_subjects_not_unique",
                "$.selected_subject_ids",
                "Selected subjects must be unique.",
            )
        )
    unknown = sorted(set(selected_subject_ids) - set(pool_subject_ids))
    if unknown:
        issues.append(
            _issue(
                "selected_subject_outside_candidate_pool",
                "$.selected_subject_ids",
                f"Subjects outside planned pool: {','.join(unknown)}.",
            )
        )


def _verify_random_source(
    receipt: Mapping[str, Any],
    issues: list[ExternalRandomizationIssue],
) -> str | None:
    source = receipt.get("randomization_source")
    if not isinstance(source, Mapping):
        issues.append(
            _issue(
                "randomization_source_missing",
                "$.randomization_source",
                "External randomization source metadata is required.",
            )
        )
        return None
    source_kind = _optional_string(source.get("source_kind"))
    if source_kind not in _INDEPENDENT_SOURCE_KINDS:
        issues.append(
            _issue(
                "randomization_source_not_independent",
                "$.randomization_source.source_kind",
                "Source kind is not an allowed independent randomization source.",
            )
        )
    if not _optional_string(source.get("source_reference")):
        issues.append(
            _issue(
                "randomization_source_reference_missing",
                "$.randomization_source.source_reference",
                "Independent random source must have a durable reference.",
            )
        )
    if not _optional_string(receipt.get("selection_algorithm")):
        issues.append(
            _issue(
                "selection_algorithm_missing",
                "$.selection_algorithm",
                "Receipt must describe deterministic derivation from the source.",
            )
        )
    return source_kind


def _verify_human_review(
    receipt: Mapping[str, Any],
    issues: list[ExternalRandomizationIssue],
) -> None:
    review = receipt.get("human_review")
    if not isinstance(review, Mapping):
        issues.append(
            _issue("human_review_missing", "$.human_review", "Human review is required.")
        )
        return
    if review.get("decision") != "accept":
        issues.append(
            _issue(
                "human_review_not_accepted",
                "$.human_review.decision",
                "Human review decision must be accept.",
            )
        )
    if not _optional_string(review.get("reviewer")):
        issues.append(
            _issue(
                "human_reviewer_missing",
                "$.human_review.reviewer",
                "Human reviewer identity is required.",
            )
        )
    if not _timezone_aware_timestamp(review.get("reviewed_at")):
        issues.append(
            _issue(
                "human_review_timestamp_invalid",
                "$.human_review.reviewed_at",
                "Human review timestamp must be ISO-8601 with timezone.",
            )
        )
    if not _optional_string(review.get("rationale")):
        issues.append(
            _issue(
                "human_review_rationale_missing",
                "$.human_review.rationale",
                "Human acceptance requires a rationale.",
            )
        )


def _timezone_aware_timestamp(value: Any) -> bool:
    if not isinstance(value, str) or not value:
        return False
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.utcoffset() is not None


def _issue(code: str, location: str, message: str) -> ExternalRandomizationIssue:
    return ExternalRandomizationIssue(code=code, location=location, message=message)


def _optional_string(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _string_items(value: Any) -> tuple[str, ...]:
    if isinstance(value, str) or not isinstance(value, list | tuple):
        return ()
    return tuple(item for item in value if isinstance(item, str) and item)


def _summary_line(result: ExternalRandomizationVerification) -> str:
    return (
        "External randomization verification: "
        f"status={result.verification_status}; request_id={result.request_id or 'missing'}; "
        f"preparation_permitted={result.human_confirmed_acquisition_preparation_permitted}; "
        "execution_authority=false; scientific_claim_upgrade=false."
    )
