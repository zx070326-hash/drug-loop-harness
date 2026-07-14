from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any

from .artifact_hash import canonical_content_sha256, json_ready
from .prospective_admission_bundle import (
    PROSPECTIVE_ADMISSION_BUNDLE_AUDIT_SCHEMA,
    PROSPECTIVE_ADMISSION_BUNDLE_SCHEMA,
    STAGED_FOR_EXPLICIT_COMMIT,
)
from .prospective_admission_commit import (
    ADMISSION_BUNDLE_AUDIT_FILENAME,
    ADMISSION_BUNDLE_SNAPSHOT_FILENAME,
    COMMIT_INTENT_FILENAME,
    COMMIT_RECEIPT_FILENAME,
    CONSUMPTION_DRAFT_FILENAME,
    DERIVED_CAMPAIGN_FILENAME,
    COMMITTED,
    PROSPECTIVE_ADMISSION_COMMIT_INTENT_SCHEMA,
    PROSPECTIVE_ADMISSION_COMMIT_RECEIPT_SCHEMA,
)
from .store import CampaignStore


PROSPECTIVE_ADMISSION_COMMIT_EVIDENCE_SCHEMA = (
    "prospective_admission_commit_evidence.v1"
)
PROSPECTIVE_ADMISSION_COMMIT_EVIDENCE_SET_SCHEMA = (
    "prospective_admission_commit_evidence_set.v1"
)
_AUTHORITY_FIELDS = (
    "execution_authority_granted",
    "autonomous_execution_permitted",
    "scientific_claim_upgrade_permitted",
)


@dataclass(frozen=True)
class ProspectiveAdmissionCommitEvidenceIssue:
    code: str
    location: str
    message: str


@dataclass(frozen=True)
class ProspectiveAdmissionCommitEvidence:
    """Read-only proof summary for one locally committed admission package."""

    schema_version: str
    verification_status: str
    commit_directory: str
    commit_id: str | None
    admission_id: str | None
    consumption_id: str | None
    observation_id: str | None
    trust_gate_result_id: str | None
    source_commit_intent_content_sha256: str | None
    source_bundle_content_sha256: str | None
    source_bundle_audit_content_sha256: str | None
    source_import_content_sha256: str | None
    source_result_sha256: str | None
    source_output_sha256: str | None
    source_endpoint_policy_content_sha256: str | None
    source_consumption_content_sha256: str | None
    source_derived_campaign_content_sha256: str | None
    issue_codes: tuple[str, ...]
    issues: tuple[ProspectiveAdmissionCommitEvidenceIssue, ...]
    execution_authority_granted: bool
    autonomous_execution_permitted: bool
    scientific_claim_upgrade_permitted: bool
    summary_line: str

    def to_dict(self) -> dict[str, Any]:
        return json_ready(asdict(self))


@dataclass(frozen=True)
class ProspectiveAdmissionCommitEvidenceSet:
    """Frozen read-only set of committed packages used by one outcome report."""

    schema_version: str
    verification_status: str
    evidence: tuple[Mapping[str, Any], ...]
    source_evidence_content_sha256: str
    issue_codes: tuple[str, ...]
    execution_authority_granted: bool
    autonomous_execution_permitted: bool
    scientific_claim_upgrade_permitted: bool
    summary_line: str

    def to_dict(self) -> dict[str, Any]:
        return json_ready(asdict(self))


class ProspectiveAdmissionCommitEvidenceVerifier:
    """Verify persisted commit evidence without executing or re-importing an oracle."""

    def verify_path(
        self,
        commit_directory: str | Path,
    ) -> ProspectiveAdmissionCommitEvidence:
        directory = Path(commit_directory).resolve()
        issues: list[ProspectiveAdmissionCommitEvidenceIssue] = []
        intent = _read_object(directory / COMMIT_INTENT_FILENAME, "commit_intent", issues)
        bundle = _read_object(
            directory / ADMISSION_BUNDLE_SNAPSHOT_FILENAME,
            "admission_bundle_snapshot",
            issues,
        )
        bundle_audit = _read_object(
            directory / ADMISSION_BUNDLE_AUDIT_FILENAME,
            "admission_bundle_audit",
            issues,
        )
        consumption = _read_object(
            directory / CONSUMPTION_DRAFT_FILENAME,
            "prospective_consumption",
            issues,
        )
        receipt = _read_object(directory / COMMIT_RECEIPT_FILENAME, "commit_receipt", issues)
        events = _read_array(directory / DERIVED_CAMPAIGN_FILENAME, "derived_campaign", issues)

        state = None
        if events is not None:
            try:
                state = CampaignStore.load_json(directory / DERIVED_CAMPAIGN_FILENAME).rebuild()
            except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
                issues.append(_issue("commit_evidence_derived_campaign_unrebuildable", "$.derived_campaign"))

        if all(
            value is not None
            for value in (intent, bundle, bundle_audit, consumption, receipt, events, state)
        ):
            _validate_chain(intent, bundle, bundle_audit, consumption, receipt, events, state, issues)

        source_import = bundle.get("source_import") if bundle is not None else None
        observation = bundle.get("observation") if bundle is not None else None
        ticket = bundle.get("trust_gate_result") if bundle is not None else None
        provenance = observation.get("provenance") if isinstance(observation, Mapping) else None
        result = ProspectiveAdmissionCommitEvidence(
            schema_version=PROSPECTIVE_ADMISSION_COMMIT_EVIDENCE_SCHEMA,
            verification_status="PASS" if not issues else "FAIL",
            commit_directory=str(directory),
            commit_id=_string_or_none((intent or {}).get("commit_id")),
            admission_id=_string_or_none((bundle or {}).get("admission_id")),
            consumption_id=_string_or_none((consumption or {}).get("consumption_id")),
            observation_id=_string_or_none((observation or {}).get("id")),
            trust_gate_result_id=_string_or_none((ticket or {}).get("id")),
            source_commit_intent_content_sha256=(
                canonical_content_sha256(intent) if intent is not None else None
            ),
            source_bundle_content_sha256=(
                canonical_content_sha256(bundle) if bundle is not None else None
            ),
            source_bundle_audit_content_sha256=(
                canonical_content_sha256(bundle_audit) if bundle_audit is not None else None
            ),
            source_import_content_sha256=(
                canonical_content_sha256(source_import)
                if isinstance(source_import, Mapping)
                else None
            ),
            source_result_sha256=(
                _string_or_none(source_import.get("source_result_sha256"))
                if isinstance(source_import, Mapping)
                else None
            ),
            source_output_sha256=(
                _string_or_none(provenance.get("source_output_sha256"))
                if isinstance(provenance, Mapping)
                else None
            ),
            source_endpoint_policy_content_sha256=(
                _string_or_none(provenance.get("source_endpoint_policy_content_sha256"))
                if isinstance(provenance, Mapping)
                else None
            ),
            source_consumption_content_sha256=(
                canonical_content_sha256(consumption) if consumption is not None else None
            ),
            source_derived_campaign_content_sha256=(
                canonical_content_sha256(events) if events is not None else None
            ),
            issue_codes=tuple(sorted({issue.code for issue in issues})),
            issues=tuple(issues),
            execution_authority_granted=False,
            autonomous_execution_permitted=False,
            scientific_claim_upgrade_permitted=False,
            summary_line="",
        )
        return replace(result, summary_line=_summary_line(result))


class ProspectiveAdmissionCommitEvidenceSetBuilder:
    """Build a canonical, non-executing evidence set from commit directories."""

    def __init__(
        self,
        *,
        verifier: ProspectiveAdmissionCommitEvidenceVerifier | None = None,
    ) -> None:
        self.verifier = verifier or ProspectiveAdmissionCommitEvidenceVerifier()

    def build_paths(
        self,
        commit_directories: tuple[str | Path, ...] | list[str | Path],
    ) -> ProspectiveAdmissionCommitEvidenceSet:
        evidence = tuple(
            self.verifier.verify_path(directory).to_dict()
            for directory in commit_directories
        )
        issues: list[str] = []
        seen_commit_ids: set[str] = set()
        seen_consumption_ids: set[str] = set()
        for index, item in enumerate(evidence):
            if item.get("verification_status") != "PASS":
                issues.append(f"evidence_{index}_not_pass")
            commit_id = _string_or_none(item.get("commit_id"))
            consumption_id = _string_or_none(item.get("consumption_id"))
            if commit_id is None or commit_id in seen_commit_ids:
                issues.append("evidence_commit_id_missing_or_duplicate")
            else:
                seen_commit_ids.add(commit_id)
            if consumption_id is None or consumption_id in seen_consumption_ids:
                issues.append("evidence_consumption_id_missing_or_duplicate")
            else:
                seen_consumption_ids.add(consumption_id)
        result = ProspectiveAdmissionCommitEvidenceSet(
            schema_version=PROSPECTIVE_ADMISSION_COMMIT_EVIDENCE_SET_SCHEMA,
            verification_status="PASS" if not issues else "FAIL",
            evidence=evidence,
            source_evidence_content_sha256=canonical_content_sha256(evidence),
            issue_codes=tuple(sorted(set(issues))),
            execution_authority_granted=False,
            autonomous_execution_permitted=False,
            scientific_claim_upgrade_permitted=False,
            summary_line="",
        )
        return replace(result, summary_line=_set_summary_line(result))


def _validate_chain(
    intent: Mapping[str, Any],
    bundle: Mapping[str, Any],
    bundle_audit: Mapping[str, Any],
    consumption: Mapping[str, Any],
    receipt: Mapping[str, Any],
    events: list[Any],
    state: Any,
    issues: list[ProspectiveAdmissionCommitEvidenceIssue],
) -> None:
    if intent.get("schema_version") != PROSPECTIVE_ADMISSION_COMMIT_INTENT_SCHEMA:
        issues.append(_issue("commit_evidence_intent_schema_invalid", "$.commit_intent.schema_version"))
    if bundle.get("schema_version") != PROSPECTIVE_ADMISSION_BUNDLE_SCHEMA:
        issues.append(_issue("commit_evidence_bundle_schema_invalid", "$.admission_bundle_snapshot.schema_version"))
    if bundle.get("bundle_status") != STAGED_FOR_EXPLICIT_COMMIT:
        issues.append(_issue("commit_evidence_bundle_not_staged", "$.admission_bundle_snapshot.bundle_status"))
    if bundle.get("admission_decision") != "accept":
        issues.append(_issue("commit_evidence_bundle_not_accepted", "$.admission_bundle_snapshot.admission_decision"))
    if bundle_audit.get("schema_version") != PROSPECTIVE_ADMISSION_BUNDLE_AUDIT_SCHEMA:
        issues.append(_issue("commit_evidence_bundle_audit_schema_invalid", "$.admission_bundle_audit.schema_version"))
    if bundle_audit.get("audit_status") != "PASS_STAGED":
        issues.append(_issue("commit_evidence_bundle_audit_not_pass", "$.admission_bundle_audit.audit_status"))
    expected = {
        "source_bundle_content_sha256": canonical_content_sha256(bundle),
        "source_bundle_audit_content_sha256": canonical_content_sha256(bundle_audit),
        "prospective_consumption_content_sha256": canonical_content_sha256(consumption),
        "derived_campaign_content_sha256": canonical_content_sha256(events),
        "admission_id": bundle.get("admission_id"),
        "approval_id": consumption.get("approval_id"),
    }
    for field, expected_value in expected.items():
        if intent.get(field) != expected_value:
            issues.append(_issue(f"commit_evidence_intent_{field}_mismatch", f"$.commit_intent.{field}"))
    source_import = bundle.get("source_import")
    observation = bundle.get("observation")
    ticket = bundle.get("trust_gate_result")
    if not isinstance(source_import, Mapping):
        issues.append(_issue("commit_evidence_source_import_missing", "$.admission_bundle_snapshot.source_import"))
    elif bundle.get("source_import_content_sha256") != canonical_content_sha256(source_import):
        issues.append(_issue("commit_evidence_source_import_hash_mismatch", "$.admission_bundle_snapshot.source_import_content_sha256"))
    elif source_import.get("import_status") != "NORMALIZED":
        issues.append(_issue("commit_evidence_source_import_not_normalized", "$.admission_bundle_snapshot.source_import.import_status"))
    if not isinstance(observation, Mapping) or not isinstance(ticket, Mapping):
        issues.append(_issue("commit_evidence_bundle_observation_or_ticket_missing", "$.admission_bundle_snapshot"))
    elif isinstance(source_import, Mapping):
        imported_observation = source_import.get("observation")
        if not isinstance(imported_observation, Mapping) or canonical_content_sha256(imported_observation) != canonical_content_sha256(observation):
            issues.append(_issue("commit_evidence_import_observation_mismatch", "$.admission_bundle_snapshot.observation"))
    if canonical_content_sha256(bundle.get("prospective_consumption_draft")) != canonical_content_sha256(consumption):
        issues.append(_issue("commit_evidence_bundle_consumption_mismatch", "$.prospective_consumption"))
    _validate_receipt(intent, receipt, issues)
    if isinstance(observation, Mapping) and isinstance(ticket, Mapping):
        observation_id = _string_or_none(observation.get("id"))
        ticket_id = _string_or_none(ticket.get("id"))
        if observation_id is None or observation_id not in state.observations:
            issues.append(_issue("commit_evidence_observation_missing_from_derived_campaign", "$.derived_campaign"))
        if ticket_id is None or ticket_id not in state.trust_gate_results:
            issues.append(_issue("commit_evidence_ticket_missing_from_derived_campaign", "$.derived_campaign"))
        if consumption.get("observation_id") != observation_id:
            issues.append(_issue("commit_evidence_consumption_observation_mismatch", "$.prospective_consumption.observation_id"))
        if consumption.get("trust_gate_result_id") != ticket_id:
            issues.append(_issue("commit_evidence_consumption_ticket_mismatch", "$.prospective_consumption.trust_gate_result_id"))
def _validate_receipt(
    intent: Mapping[str, Any],
    receipt: Mapping[str, Any],
    issues: list[ProspectiveAdmissionCommitEvidenceIssue],
) -> None:
    if receipt.get("schema_version") != PROSPECTIVE_ADMISSION_COMMIT_RECEIPT_SCHEMA:
        issues.append(_issue("commit_evidence_receipt_schema_invalid", "$.commit_receipt.schema_version"))
    if receipt.get("commit_status") != COMMITTED:
        issues.append(_issue("commit_evidence_receipt_not_committed", "$.commit_receipt.commit_status"))
    expected = {
        "commit_id": intent.get("commit_id"),
        "source_intent_content_sha256": canonical_content_sha256(intent),
        "source_bundle_content_sha256": intent.get("source_bundle_content_sha256"),
        "derived_campaign_content_sha256": intent.get("derived_campaign_content_sha256"),
        "prospective_consumption_content_sha256": intent.get("prospective_consumption_content_sha256"),
        "approval_id": intent.get("approval_id"),
    }
    for field, expected_value in expected.items():
        if receipt.get(field) != expected_value:
            issues.append(_issue(f"commit_evidence_receipt_{field}_mismatch", f"$.commit_receipt.{field}"))
    if receipt.get("ledger_append_performed") is not True:
        issues.append(_issue("commit_evidence_receipt_ledger_append_invalid", "$.commit_receipt.ledger_append_performed"))
    for field in _AUTHORITY_FIELDS:
        if receipt.get(field) is not False:
            issues.append(_issue(f"commit_evidence_receipt_{field}_must_be_false", f"$.commit_receipt.{field}"))


def _read_object(
    path: Path,
    name: str,
    issues: list[ProspectiveAdmissionCommitEvidenceIssue],
) -> Mapping[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError:
        issues.append(_issue(f"commit_evidence_{name}_unreadable", f"$.{name}"))
        return None
    except json.JSONDecodeError:
        issues.append(_issue(f"commit_evidence_{name}_invalid_json", f"$.{name}"))
        return None
    if not isinstance(payload, Mapping):
        issues.append(_issue(f"commit_evidence_{name}_not_object", f"$.{name}"))
        return None
    return payload


def _read_array(
    path: Path,
    name: str,
    issues: list[ProspectiveAdmissionCommitEvidenceIssue],
) -> list[Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError:
        issues.append(_issue(f"commit_evidence_{name}_unreadable", f"$.{name}"))
        return None
    except json.JSONDecodeError:
        issues.append(_issue(f"commit_evidence_{name}_invalid_json", f"$.{name}"))
        return None
    if not isinstance(payload, list):
        issues.append(_issue(f"commit_evidence_{name}_not_array", f"$.{name}"))
        return None
    return payload


def _string_or_none(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _issue(code: str, location: str) -> ProspectiveAdmissionCommitEvidenceIssue:
    return ProspectiveAdmissionCommitEvidenceIssue(
        code=code,
        location=location,
        message=code.replace("_", " ") + ".",
    )


def _summary_line(result: ProspectiveAdmissionCommitEvidence) -> str:
    return (
        "Prospective admission commit evidence: "
        f"status={result.verification_status}; commit_id={result.commit_id}; "
        "execution_authority=false; scientific_claim_upgrade=false."
    )


def _set_summary_line(result: ProspectiveAdmissionCommitEvidenceSet) -> str:
    return (
        "Prospective admission commit evidence set: "
        f"status={result.verification_status}; evidence={len(result.evidence)}; "
        "execution_authority=false; scientific_claim_upgrade=false."
    )
