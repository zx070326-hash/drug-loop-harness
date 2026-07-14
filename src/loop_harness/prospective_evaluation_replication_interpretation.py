from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .artifact_hash import canonical_content_sha256, json_ready
from .prospective_evaluation_replication_evidence import (
    PROSPECTIVE_LOOP_EVALUATION_REPLICATION_EVIDENCE_SCHEMA,
)


PROSPECTIVE_LOOP_EVALUATION_REPLICATION_INTERPRETATION_DECLARATION_SCHEMA = (
    "prospective_loop_evaluation_replication_interpretation_declaration.v1"
)
PROSPECTIVE_LOOP_EVALUATION_REPLICATION_INTERPRETATION_SCHEMA = (
    "prospective_loop_evaluation_replication_interpretation.v1"
)
PROSPECTIVE_LOOP_EVALUATION_REPLICATION_INTERPRETATION_VERIFICATION_SCHEMA = (
    "prospective_loop_evaluation_replication_interpretation_verification.v1"
)
_AUTHORITY_FIELDS = (
    "policy_interpretation_permitted",
    "policy_ranking_permitted",
    "execution_authority_granted",
    "autonomous_execution_permitted",
    "scientific_claim_upgrade_permitted",
)
_CONCLUSION_BY_RELATION = {
    "same_nonzero_direction": {
        "concordant_evidence_requires_additional_research",
        "inconclusive_requires_additional_research",
    },
    "opposite_direction": {
        "discordant_evidence_requires_investigation",
        "inconclusive_requires_additional_research",
    },
    "zero_or_undetermined": {"inconclusive_requires_additional_research"},
}


@dataclass(frozen=True)
class ProspectiveLoopEvaluationReplicationInterpretationIssue:
    code: str
    location: str
    message: str


@dataclass(frozen=True)
class ProspectiveLoopEvaluationReplicationInterpretation:
    """Human review record scoped to research follow-up, never a policy claim."""

    schema_version: str
    interpretation_id: str
    reviewed_at: str
    reviewer: str
    source_replication_evidence_content_sha256: str
    source_declaration_content_sha256: str
    evidence_direction_relation: str
    conclusion: str
    rationale: str
    limitations: tuple[str, ...]
    human_review_recorded: bool
    research_followup_recommended: bool
    policy_interpretation_permitted: bool
    policy_ranking_permitted: bool
    execution_authority_granted: bool
    autonomous_execution_permitted: bool
    scientific_claim_upgrade_permitted: bool
    summary_line: str

    def to_dict(self) -> dict[str, Any]:
        return json_ready(asdict(self))


@dataclass(frozen=True)
class ProspectiveLoopEvaluationReplicationInterpretationVerification:
    schema_version: str
    verification_status: str
    verified_at: str
    source_interpretation_content_sha256: str | None
    source_replication_evidence_content_sha256: str | None
    interpretation_id: str | None
    issue_codes: tuple[str, ...]
    issues: tuple[ProspectiveLoopEvaluationReplicationInterpretationIssue, ...]
    policy_interpretation_permitted: bool
    policy_ranking_permitted: bool
    execution_authority_granted: bool
    autonomous_execution_permitted: bool
    scientific_claim_upgrade_permitted: bool
    summary_line: str

    def to_dict(self) -> dict[str, Any]:
        return json_ready(asdict(self))


class ProspectiveLoopEvaluationReplicationInterpretationRecorder:
    """Record an accountable human conclusion without converting it into authority."""

    def record_path(
        self,
        replication_evidence_path: str | Path,
        declaration_path: str | Path,
    ) -> ProspectiveLoopEvaluationReplicationInterpretation:
        evidence = _read_object_or_raise(Path(replication_evidence_path), "replication evidence")
        declaration = _read_object_or_raise(Path(declaration_path), "interpretation declaration")
        return self.record(evidence, declaration)

    def record(
        self,
        replication_evidence: Mapping[str, Any],
        declaration: Mapping[str, Any],
    ) -> ProspectiveLoopEvaluationReplicationInterpretation:
        issues = _validation_issues(replication_evidence, declaration)
        if issues:
            raise ValueError(
                "Invalid replication interpretation declaration: "
                + ", ".join(issue.code for issue in issues)
            )
        reviewed_at = str(declaration["reviewed_at"])
        relation = str(replication_evidence["primary_effect_direction_relation"])
        result = ProspectiveLoopEvaluationReplicationInterpretation(
            schema_version=PROSPECTIVE_LOOP_EVALUATION_REPLICATION_INTERPRETATION_SCHEMA,
            interpretation_id=str(declaration["interpretation_id"]),
            reviewed_at=reviewed_at,
            reviewer=str(declaration["reviewer"]),
            source_replication_evidence_content_sha256=canonical_content_sha256(
                replication_evidence
            ),
            source_declaration_content_sha256=canonical_content_sha256(declaration),
            evidence_direction_relation=relation,
            conclusion=str(declaration["conclusion"]),
            rationale=str(declaration["rationale"]),
            limitations=tuple(str(item) for item in declaration["limitations"]),
            human_review_recorded=True,
            research_followup_recommended=True,
            policy_interpretation_permitted=False,
            policy_ranking_permitted=False,
            execution_authority_granted=False,
            autonomous_execution_permitted=False,
            scientific_claim_upgrade_permitted=False,
            summary_line="",
        )
        return replace(result, summary_line=_summary_line(result))


class ProspectiveLoopEvaluationReplicationInterpretationVerifier:
    """Rebuild a persisted human review from its exact evidence and declaration."""

    def __init__(
        self,
        *,
        recorder: ProspectiveLoopEvaluationReplicationInterpretationRecorder | None = None,
    ) -> None:
        self.recorder = recorder or ProspectiveLoopEvaluationReplicationInterpretationRecorder()

    def verify_path(
        self,
        interpretation_path: str | Path,
        replication_evidence_path: str | Path,
        declaration_path: str | Path,
        *,
        verified_at: str | None = None,
    ) -> ProspectiveLoopEvaluationReplicationInterpretationVerification:
        interpretation, interpretation_issue = _read_object(Path(interpretation_path), "interpretation")
        evidence, evidence_issue = _read_object(Path(replication_evidence_path), "replication_evidence")
        declaration, declaration_issue = _read_object(Path(declaration_path), "declaration")
        return self._verify(
            interpretation,
            evidence,
            declaration,
            verified_at=verified_at,
            initial_issues=tuple(
                issue
                for issue in (interpretation_issue, evidence_issue, declaration_issue)
                if issue is not None
            ),
        )

    def verify(
        self,
        interpretation: Mapping[str, Any],
        replication_evidence: Mapping[str, Any],
        declaration: Mapping[str, Any],
        *,
        verified_at: str | None = None,
    ) -> ProspectiveLoopEvaluationReplicationInterpretationVerification:
        return self._verify(
            interpretation,
            replication_evidence,
            declaration,
            verified_at=verified_at,
            initial_issues=(),
        )

    def _verify(
        self,
        interpretation: Mapping[str, Any] | None,
        replication_evidence: Mapping[str, Any] | None,
        declaration: Mapping[str, Any] | None,
        *,
        verified_at: str | None,
        initial_issues: tuple[ProspectiveLoopEvaluationReplicationInterpretationIssue, ...],
    ) -> ProspectiveLoopEvaluationReplicationInterpretationVerification:
        timestamp = verified_at or datetime.now(timezone.utc).isoformat()
        _parse_timestamp(timestamp, "verified_at")
        issues = list(initial_issues)
        for name, value in (
            ("interpretation", interpretation),
            ("replication_evidence", replication_evidence),
            ("declaration", declaration),
        ):
            if value is None:
                issues.append(_issue(f"replication_interpretation_{name}_unavailable", f"$.{name}"))
        if interpretation is not None and replication_evidence is not None and declaration is not None:
            try:
                rebuilt = self.recorder.record(replication_evidence, declaration).to_dict()
            except ValueError:
                issues.append(_issue("replication_interpretation_rebuild_failed", "$.interpretation"))
            else:
                if canonical_content_sha256(rebuilt) != canonical_content_sha256(interpretation):
                    issues.append(_issue("replication_interpretation_rebuild_mismatch", "$.interpretation"))
                reviewed_at = _timestamp_or_none(interpretation.get("reviewed_at"))
                if reviewed_at is not None and _parse_timestamp(timestamp, "verified_at") < reviewed_at:
                    issues.append(_issue("replication_interpretation_verified_before_review", "$.verified_at"))
        result = ProspectiveLoopEvaluationReplicationInterpretationVerification(
            schema_version=PROSPECTIVE_LOOP_EVALUATION_REPLICATION_INTERPRETATION_VERIFICATION_SCHEMA,
            verification_status="PASS" if not issues else "FAIL",
            verified_at=timestamp,
            source_interpretation_content_sha256=(
                canonical_content_sha256(interpretation) if interpretation else None
            ),
            source_replication_evidence_content_sha256=(
                canonical_content_sha256(replication_evidence)
                if replication_evidence
                else None
            ),
            interpretation_id=(
                _string_or_none(interpretation.get("interpretation_id"))
                if interpretation
                else None
            ),
            issue_codes=tuple(sorted({issue.code for issue in issues})),
            issues=tuple(issues),
            policy_interpretation_permitted=False,
            policy_ranking_permitted=False,
            execution_authority_granted=False,
            autonomous_execution_permitted=False,
            scientific_claim_upgrade_permitted=False,
            summary_line="",
        )
        return replace(result, summary_line=_verification_summary_line(result))


def _validation_issues(
    evidence: Mapping[str, Any],
    declaration: Mapping[str, Any],
) -> list[ProspectiveLoopEvaluationReplicationInterpretationIssue]:
    issues: list[ProspectiveLoopEvaluationReplicationInterpretationIssue] = []
    if evidence.get("schema_version") != PROSPECTIVE_LOOP_EVALUATION_REPLICATION_EVIDENCE_SCHEMA:
        issues.append(_issue("replication_interpretation_evidence_schema_invalid", "$.replication_evidence.schema_version"))
    if evidence.get("verification_status") != "PASS":
        issues.append(_issue("replication_interpretation_evidence_not_pass", "$.replication_evidence.verification_status"))
    if evidence.get("human_interpretation_required") is not True:
        issues.append(_issue("replication_interpretation_evidence_human_review_not_required", "$.replication_evidence.human_interpretation_required"))
    evidence_time = _timestamp_or_none(evidence.get("evaluated_at"))
    if evidence_time is None:
        issues.append(_issue("replication_interpretation_evidence_time_invalid", "$.replication_evidence.evaluated_at"))
    for field in _AUTHORITY_FIELDS:
        if evidence.get(field) is not False:
            issues.append(_issue(f"replication_interpretation_evidence_{field}_must_be_false", f"$.replication_evidence.{field}"))

    if declaration.get("schema_version") != PROSPECTIVE_LOOP_EVALUATION_REPLICATION_INTERPRETATION_DECLARATION_SCHEMA:
        issues.append(_issue("replication_interpretation_declaration_schema_invalid", "$.declaration.schema_version"))
    for field in ("interpretation_id", "reviewer", "rationale"):
        if _string_or_none(declaration.get(field)) is None:
            issues.append(_issue(f"replication_interpretation_{field}_missing", f"$.declaration.{field}"))
    review_time = _timestamp_or_none(declaration.get("reviewed_at"))
    if review_time is None:
        issues.append(_issue("replication_interpretation_reviewed_at_invalid", "$.declaration.reviewed_at"))
    elif evidence_time is not None and review_time < evidence_time:
        issues.append(_issue("replication_interpretation_before_evidence", "$.declaration.reviewed_at"))
    limitations = declaration.get("limitations")
    if not isinstance(limitations, list | tuple) or not limitations or any(
        _string_or_none(item) is None for item in limitations
    ):
        issues.append(_issue("replication_interpretation_limitations_invalid", "$.declaration.limitations"))
    relation = evidence.get("primary_effect_direction_relation")
    conclusion = declaration.get("conclusion")
    allowed = _CONCLUSION_BY_RELATION.get(relation)
    if allowed is None:
        issues.append(_issue("replication_interpretation_evidence_relation_invalid", "$.replication_evidence.primary_effect_direction_relation"))
    elif conclusion not in allowed:
        issues.append(_issue("replication_interpretation_conclusion_not_supported_by_evidence", "$.declaration.conclusion"))
    for field in _AUTHORITY_FIELDS:
        if declaration.get(field) is not False:
            issues.append(_issue(f"replication_interpretation_{field}_must_be_false", f"$.declaration.{field}"))
    return issues


def _read_object(
    path: Path,
    name: str,
) -> tuple[Mapping[str, Any] | None, ProspectiveLoopEvaluationReplicationInterpretationIssue | None]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError:
        return None, _issue(f"replication_interpretation_{name}_unreadable", f"$.{name}")
    except json.JSONDecodeError:
        return None, _issue(f"replication_interpretation_{name}_invalid_json", f"$.{name}")
    if not isinstance(payload, Mapping):
        return None, _issue(f"replication_interpretation_{name}_not_object", f"$.{name}")
    return payload, None


def _read_object_or_raise(path: Path, name: str) -> Mapping[str, Any]:
    payload, issue = _read_object(path, name)
    if payload is None:
        raise ValueError(issue.code if issue else f"{name} unavailable")
    return payload


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


def _issue(code: str, location: str) -> ProspectiveLoopEvaluationReplicationInterpretationIssue:
    return ProspectiveLoopEvaluationReplicationInterpretationIssue(
        code=code,
        location=location,
        message=code.replace("_", " ") + ".",
    )


def _summary_line(result: ProspectiveLoopEvaluationReplicationInterpretation) -> str:
    return (
        "Prospective loop evaluation replication interpretation: "
        f"conclusion={result.conclusion}; policy_interpretation=false; "
        "policy_ranking=false; scientific_claim_upgrade=false."
    )


def _verification_summary_line(
    result: ProspectiveLoopEvaluationReplicationInterpretationVerification,
) -> str:
    return (
        "Prospective loop evaluation replication interpretation verification: "
        f"status={result.verification_status}; policy_interpretation=false; "
        "policy_ranking=false; scientific_claim_upgrade=false."
    )
