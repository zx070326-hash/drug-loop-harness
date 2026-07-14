from __future__ import annotations

import json
import os
import re
from contextlib import contextmanager
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from .artifact_hash import canonical_content_sha256, json_ready
from .prospective_acquisition_consumption import ProspectiveAcquisitionConsumptionLedger
from .prospective_admission_bundle import (
    PROSPECTIVE_ADMISSION_BUNDLE_SCHEMA,
    STAGED_FOR_EXPLICIT_COMMIT,
    ProspectiveAdmissionBundleAuditor,
)
from .store import CampaignStore, event_to_dict


PROSPECTIVE_ADMISSION_COMMIT_INTENT_SCHEMA = "prospective_admission_commit_intent.v2"
PROSPECTIVE_ADMISSION_COMMIT_RECEIPT_SCHEMA = "prospective_admission_commit_receipt.v1"
PROSPECTIVE_ADMISSION_COMMIT_AUDIT_SCHEMA = "prospective_admission_commit_audit.v1"
PROSPECTIVE_ADMISSION_COMMIT_OUTCOME_SCHEMA = "prospective_admission_commit_outcome.v1"
PROSPECTIVE_ADMISSION_COMMIT_RESOLUTION_SCHEMA = "prospective_admission_commit_resolution.v1"

COMMIT_INTENT_FILENAME = "commit-intent.json"
ADMISSION_BUNDLE_SNAPSHOT_FILENAME = "admission-bundle.json"
ADMISSION_BUNDLE_AUDIT_FILENAME = "admission-bundle-audit.json"
DERIVED_CAMPAIGN_FILENAME = "campaign-events.json"
CONSUMPTION_DRAFT_FILENAME = "prospective-consumption.json"
COMMIT_RECEIPT_FILENAME = "commit-receipt.json"

PASS_COMMITTED = "PASS_COMMITTED"
RECOVERABLE_APPEND = "RECOVERABLE_APPEND"
RECOVERABLE_FINALIZE = "RECOVERABLE_FINALIZE"
REPAIR_REQUIRED = "REPAIR_REQUIRED"
FAIL = "FAIL"
COMMITTED = "COMMITTED"
CONFLICT = "CONFLICT"
RESOLVED_COMMITTED_CAMPAIGN = "RESOLVED_COMMITTED_CAMPAIGN"
RESOLUTION_REJECTED = "REJECTED"

_COMMIT_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,119}")


@dataclass(frozen=True)
class ProspectiveAdmissionCommitIntent:
    """Immutable human-confirmed intent for a separate derived campaign artifact."""

    schema_version: str
    commit_id: str
    admission_id: str
    source_bundle_content_sha256: str
    source_bundle_audit_content_sha256: str
    base_campaign_content_sha256: str
    derived_campaign_content_sha256: str
    prospective_consumption_content_sha256: str
    approval_id: str
    ledger_path: str
    ledger_path_identity_sha256: str
    commit_reviewer: str
    commit_rationale: str
    commit_confirmed_at: str
    execution_authority_granted: bool
    autonomous_execution_permitted: bool
    scientific_claim_upgrade_permitted: bool
    summary_line: str

    def to_dict(self) -> dict[str, Any]:
        return json_ready(asdict(self))


@dataclass(frozen=True)
class ProspectiveAdmissionCommitReceipt:
    """Final marker written only after the exact consumption record is durable."""

    schema_version: str
    commit_status: str
    commit_id: str
    source_intent_content_sha256: str
    source_bundle_content_sha256: str
    base_campaign_content_sha256: str
    derived_campaign_content_sha256: str
    prospective_consumption_content_sha256: str
    approval_id: str
    ledger_path_identity_sha256: str
    finalized_by: str
    finalization_rationale: str
    finalized_at: str
    ledger_append_performed: bool
    execution_authority_granted: bool
    autonomous_execution_permitted: bool
    scientific_claim_upgrade_permitted: bool
    summary_line: str

    def to_dict(self) -> dict[str, Any]:
        return json_ready(asdict(self))


@dataclass(frozen=True)
class ProspectiveAdmissionCommitAudit:
    """Read-only diagnosis of one local multi-file admission commit."""

    schema_version: str
    audit_status: str
    commit_id: str | None
    approval_id: str | None
    bundle_audit_status: str | None
    ledger_record_match_count: int
    receipt_present: bool
    issue_codes: tuple[str, ...]
    summary_line: str

    def to_dict(self) -> dict[str, Any]:
        return json_ready(asdict(self))


@dataclass(frozen=True)
class ProspectiveAdmissionCommitOutcome:
    """Outcome of a commit or human-confirmed recovery attempt."""

    schema_version: str
    commit_status: str
    commit_id: str
    commit_directory: str
    ledger_append_performed: bool
    receipt_written: bool
    recovery_used: bool
    issue_codes: tuple[str, ...]
    execution_authority_granted: bool
    autonomous_execution_permitted: bool
    scientific_claim_upgrade_permitted: bool
    summary_line: str

    def to_dict(self) -> dict[str, Any]:
        return json_ready(asdict(self))


@dataclass(frozen=True)
class ProspectiveAdmissionCommitResolution:
    """Read-only handle for a derived campaign that passed commit audit."""

    schema_version: str
    resolution_status: str
    commit_id: str | None
    source_commit_audit_content_sha256: str
    resolved_campaign_path: str | None
    resolved_campaign_content_sha256: str | None
    campaign_id: str | None
    observation_id: str | None
    trust_gate_result_id: str | None
    evidence_admitted: bool | None
    issue_codes: tuple[str, ...]
    execution_authority_granted: bool
    autonomous_execution_permitted: bool
    scientific_claim_upgrade_permitted: bool
    summary_line: str

    def to_dict(self) -> dict[str, Any]:
        return json_ready(asdict(self))


class ProspectiveAdmissionCommitter:
    """Commit a staged package through an explicit, recoverable local protocol."""

    def __init__(self, *, bundle_auditor: ProspectiveAdmissionBundleAuditor | None = None) -> None:
        self.bundle_auditor = bundle_auditor or ProspectiveAdmissionBundleAuditor()

    def prepare(
        self,
        bundle: Mapping[str, Any],
        plan: Mapping[str, Any],
        randomization_verification: Mapping[str, Any],
        approval: Mapping[str, Any],
        campaign_store: CampaignStore,
        ledger_path: str | Path,
        commit_root: str | Path,
        *,
        commit_reviewer: str,
        commit_rationale: str,
        commit_confirmed_at: str | None = None,
        commit_id: str | None = None,
    ) -> ProspectiveAdmissionCommitIntent:
        timestamp = commit_confirmed_at or datetime.now(timezone.utc).isoformat()
        _validate_human_confirmation(commit_reviewer, commit_rationale, timestamp)
        audit = self.bundle_auditor.audit(
            bundle,
            plan,
            randomization_verification,
            approval,
            campaign_store,
        )
        if audit.audit_status != "PASS_STAGED":
            raise ValueError("Staged admission bundle cannot be committed: " + audit.audit_status)
        if bundle.get("schema_version") != PROSPECTIVE_ADMISSION_BUNDLE_SCHEMA:
            raise ValueError("Unsupported prospective admission bundle schema")
        if bundle.get("bundle_status") != STAGED_FOR_EXPLICIT_COMMIT:
            raise ValueError("Admission bundle is not staged for explicit commit")
        campaign_hash = _campaign_store_hash(campaign_store)
        if bundle.get("base_campaign_content_sha256") != campaign_hash:
            raise ValueError("Admission bundle base campaign hash mismatch")
        consumption = _required_mapping(bundle, "prospective_consumption_draft")
        approval_id = _required_string(consumption, "approval_id")
        derived_events = bundle.get("staged_campaign_events")
        if not isinstance(derived_events, list | tuple):
            raise ValueError("Admission bundle staged_campaign_events is missing")
        derived_hash = canonical_content_sha256(derived_events)
        if bundle.get("staged_campaign_content_sha256") != derived_hash:
            raise ValueError("Admission bundle staged campaign hash mismatch")
        ledger = Path(ledger_path).resolve()
        existing = ProspectiveAcquisitionConsumptionLedger().records(ledger)
        if any(record.get("approval_id") == approval_id for record in existing):
            raise ValueError("Prospective approval is already consumed in ledger")
        selected_id = commit_id or f"prospective_commit_{uuid4().hex}"
        _validate_commit_id(selected_id)
        target = Path(commit_root).resolve() / selected_id
        if target.exists():
            raise ValueError(f"Commit directory already exists: {target}")
        intent = ProspectiveAdmissionCommitIntent(
            schema_version=PROSPECTIVE_ADMISSION_COMMIT_INTENT_SCHEMA,
            commit_id=selected_id,
            admission_id=_required_string(bundle, "admission_id"),
            source_bundle_content_sha256=canonical_content_sha256(bundle),
            source_bundle_audit_content_sha256=canonical_content_sha256(
                audit.to_dict()
            ),
            base_campaign_content_sha256=campaign_hash,
            derived_campaign_content_sha256=derived_hash,
            prospective_consumption_content_sha256=canonical_content_sha256(consumption),
            approval_id=approval_id,
            ledger_path=str(ledger),
            ledger_path_identity_sha256=_path_identity_sha256(ledger),
            commit_reviewer=commit_reviewer,
            commit_rationale=commit_rationale,
            commit_confirmed_at=timestamp,
            execution_authority_granted=False,
            autonomous_execution_permitted=False,
            scientific_claim_upgrade_permitted=False,
            summary_line="",
        )
        intent = replace(intent, summary_line=_intent_summary_line(intent))
        target.mkdir(parents=True, exist_ok=False)
        _atomic_write_json(target / COMMIT_INTENT_FILENAME, intent.to_dict())
        _atomic_write_json(target / ADMISSION_BUNDLE_AUDIT_FILENAME, audit.to_dict())
        return intent

    def materialize(
        self,
        intent: Mapping[str, Any],
        bundle: Mapping[str, Any],
        commit_directory: str | Path,
    ) -> None:
        """Materialize immutable candidate artifacts; this does not append the ledger."""

        target = Path(commit_directory).resolve()
        _validate_intent_bundle_binding(intent, bundle, target)
        events = bundle.get("staged_campaign_events")
        consumption = _required_mapping(bundle, "prospective_consumption_draft")
        if canonical_content_sha256(events) != intent.get("derived_campaign_content_sha256"):
            raise ValueError("Bundle derived campaign hash differs from intent")
        if canonical_content_sha256(consumption) != intent.get(
            "prospective_consumption_content_sha256"
        ):
            raise ValueError("Bundle prospective consumption hash differs from intent")
        campaign_path = target / DERIVED_CAMPAIGN_FILENAME
        consumption_path = target / CONSUMPTION_DRAFT_FILENAME
        bundle_path = target / ADMISSION_BUNDLE_SNAPSHOT_FILENAME
        if campaign_path.exists() or consumption_path.exists() or bundle_path.exists():
            raise ValueError("Commit candidate artifacts already exist")
        _atomic_write_json(bundle_path, bundle)
        _atomic_write_json(campaign_path, list(events))
        _atomic_write_json(consumption_path, consumption)

    def finalize(
        self,
        bundle: Mapping[str, Any],
        ledger_path: str | Path,
        commit_directory: str | Path,
        *,
        finalized_by: str,
        finalization_rationale: str,
        finalized_at: str | None = None,
        recovery_used: bool = False,
    ) -> ProspectiveAdmissionCommitOutcome:
        """Append once, then write the final receipt. Safe only after materialization."""

        timestamp = finalized_at or datetime.now(timezone.utc).isoformat()
        _validate_human_confirmation(finalized_by, finalization_rationale, timestamp)
        target = Path(commit_directory).resolve()
        intent = _read_json_object(target / COMMIT_INTENT_FILENAME)
        _validate_intent_bundle_binding(intent, bundle, target)
        _validate_materialized_artifacts(intent, bundle, target)
        ledger = Path(ledger_path).resolve()
        _validate_ledger_identity(intent, ledger)
        consumption = _required_mapping(bundle, "prospective_consumption_draft")
        receipt_path = target / COMMIT_RECEIPT_FILENAME
        with _exclusive_ledger_lock(ledger, _required_string(intent, "commit_id")) as locked:
            if not locked:
                return _outcome(
                    status=CONFLICT,
                    intent=intent,
                    directory=target,
                    ledger_append_performed=False,
                    receipt_written=receipt_path.exists(),
                    recovery_used=recovery_used,
                    issue_codes=("ledger_single_writer_lock_unavailable",),
                )
            existing = ProspectiveAcquisitionConsumptionLedger().records(ledger)
            matching = [
                record for record in existing if record.get("approval_id") == intent.get("approval_id")
            ]
            expected_hash = canonical_content_sha256(consumption)
            if len(matching) == 0:
                _append_jsonl_durably(ledger, consumption)
                ledger_append_performed = True
            elif len(matching) == 1 and canonical_content_sha256(matching[0]) == expected_hash:
                ledger_append_performed = False
            else:
                return _outcome(
                    status=CONFLICT,
                    intent=intent,
                    directory=target,
                    ledger_append_performed=False,
                    receipt_written=receipt_path.exists(),
                    recovery_used=recovery_used,
                    issue_codes=("ledger_approval_conflict",),
                )
            if receipt_path.exists():
                try:
                    _validate_receipt(intent, _read_json_object(receipt_path))
                except ValueError:
                    return _outcome(
                        status=CONFLICT,
                        intent=intent,
                        directory=target,
                        ledger_append_performed=ledger_append_performed,
                        receipt_written=True,
                        recovery_used=recovery_used,
                        issue_codes=("commit_receipt_content_conflict",),
                    )
            else:
                receipt = _receipt(
                    intent,
                    finalized_by=finalized_by,
                    finalization_rationale=finalization_rationale,
                    finalized_at=timestamp,
                )
                _atomic_write_json(receipt_path, receipt.to_dict())
        return _outcome(
            status=COMMITTED,
            intent=intent,
            directory=target,
            ledger_append_performed=ledger_append_performed,
            receipt_written=True,
            recovery_used=recovery_used,
        )

    def commit(
        self,
        bundle: Mapping[str, Any],
        plan: Mapping[str, Any],
        randomization_verification: Mapping[str, Any],
        approval: Mapping[str, Any],
        campaign_store: CampaignStore,
        ledger_path: str | Path,
        commit_root: str | Path,
        *,
        commit_reviewer: str,
        commit_rationale: str,
        commit_confirmed_at: str | None = None,
        commit_id: str | None = None,
    ) -> ProspectiveAdmissionCommitOutcome:
        intent = self.prepare(
            bundle,
            plan,
            randomization_verification,
            approval,
            campaign_store,
            ledger_path,
            commit_root,
            commit_reviewer=commit_reviewer,
            commit_rationale=commit_rationale,
            commit_confirmed_at=commit_confirmed_at,
            commit_id=commit_id,
        )
        directory = Path(commit_root).resolve() / intent.commit_id
        self.materialize(intent.to_dict(), bundle, directory)
        return self.finalize(
            bundle,
            ledger_path,
            directory,
            finalized_by=commit_reviewer,
            finalization_rationale=commit_rationale,
            finalized_at=commit_confirmed_at,
        )

    def recover(
        self,
        bundle: Mapping[str, Any],
        plan: Mapping[str, Any],
        randomization_verification: Mapping[str, Any],
        approval: Mapping[str, Any],
        campaign_store: CampaignStore,
        ledger_path: str | Path,
        commit_directory: str | Path,
        *,
        recovery_reviewer: str,
        recovery_rationale: str,
        recovered_at: str | None = None,
    ) -> ProspectiveAdmissionCommitOutcome:
        """Human-confirmed recovery only for the post-materialization states."""

        target = Path(commit_directory).resolve()
        audit = ProspectiveAdmissionCommitAuditor(
            bundle_auditor=self.bundle_auditor
        ).audit(
            bundle,
            plan,
            randomization_verification,
            approval,
            campaign_store,
            ledger_path,
            target,
        )
        if audit.audit_status not in {RECOVERABLE_APPEND, RECOVERABLE_FINALIZE}:
            raise ValueError("Commit is not in a recoverable post-materialization state")
        return self.finalize(
            bundle,
            ledger_path,
            target,
            finalized_by=recovery_reviewer,
            finalization_rationale=recovery_rationale,
            finalized_at=recovered_at,
            recovery_used=True,
        )


class ProspectiveAdmissionCommitAuditor:
    """Classify committed, recoverable, and invalid local admission states."""

    def __init__(self, *, bundle_auditor: ProspectiveAdmissionBundleAuditor | None = None) -> None:
        self.bundle_auditor = bundle_auditor or ProspectiveAdmissionBundleAuditor()

    def audit(
        self,
        bundle: Mapping[str, Any],
        plan: Mapping[str, Any],
        randomization_verification: Mapping[str, Any],
        approval: Mapping[str, Any],
        campaign_store: CampaignStore,
        ledger_path: str | Path,
        commit_directory: str | Path,
    ) -> ProspectiveAdmissionCommitAudit:
        target = Path(commit_directory).resolve()
        issues: list[str] = []
        intent_path = target / COMMIT_INTENT_FILENAME
        if not intent_path.is_file():
            return _audit(FAIL, None, None, None, 0, False, ("commit_intent_missing",))
        try:
            intent = _read_json_object(intent_path)
        except ValueError:
            return _audit(FAIL, None, None, None, 0, False, ("commit_intent_invalid",))
        commit_id = _string_or_none(intent.get("commit_id"))
        approval_id = _string_or_none(intent.get("approval_id"))
        if intent.get("schema_version") != PROSPECTIVE_ADMISSION_COMMIT_INTENT_SCHEMA:
            issues.append("commit_intent_schema_invalid")
        persisted_audit_status: str | None = None
        try:
            persisted_audit = _read_json_object(target / ADMISSION_BUNDLE_AUDIT_FILENAME)
            persisted_audit_status = _string_or_none(persisted_audit.get("audit_status"))
            if (
                intent.get("source_bundle_audit_content_sha256")
                != canonical_content_sha256(persisted_audit)
            ):
                issues.append("commit_bundle_audit_receipt_hash_mismatch")
            if persisted_audit_status != "PASS_STAGED":
                issues.append("commit_bundle_audit_receipt_not_pass")
        except ValueError:
            issues.append("commit_bundle_audit_receipt_missing_or_invalid")
        try:
            _validate_intent_bundle_binding(intent, bundle, target)
        except ValueError:
            issues.append("commit_intent_bundle_binding_invalid")
        bundle_audit = self.bundle_auditor.audit(
            bundle,
            plan,
            randomization_verification,
            approval,
            campaign_store,
        )
        if bundle_audit.audit_status != "PASS_STAGED":
            issues.append("source_admission_bundle_not_replayable")
        if _campaign_store_hash(campaign_store) != intent.get("base_campaign_content_sha256"):
            issues.append("commit_base_campaign_hash_mismatch")
        try:
            _validate_ledger_identity(intent, Path(ledger_path).resolve())
        except ValueError:
            issues.append("commit_ledger_identity_mismatch")
        candidate_paths_present = all(
            (target / filename).is_file()
            for filename in (
                ADMISSION_BUNDLE_SNAPSHOT_FILENAME,
                DERIVED_CAMPAIGN_FILENAME,
                CONSUMPTION_DRAFT_FILENAME,
            )
        )
        if not candidate_paths_present:
            materialized = False
        else:
            try:
                _validate_materialized_artifacts(intent, bundle, target)
                materialized = True
            except ValueError:
                materialized = False
                issues.append("commit_materialized_artifact_invalid")
        receipt_path = target / COMMIT_RECEIPT_FILENAME
        receipt_present = receipt_path.is_file()
        try:
            records = ProspectiveAcquisitionConsumptionLedger().records(ledger_path)
        except ValueError:
            return _audit(
                FAIL,
                commit_id,
                approval_id,
                persisted_audit_status or bundle_audit.audit_status,
                0,
                receipt_present,
                tuple(sorted(set(issues + ["commit_ledger_invalid"]))),
            )
        consumption = bundle.get("prospective_consumption_draft")
        expected_hash = canonical_content_sha256(consumption)
        matching = [record for record in records if record.get("approval_id") == approval_id]
        exact_matches = [
            record for record in matching if canonical_content_sha256(record) == expected_hash
        ]
        if len(matching) != len(exact_matches):
            issues.append("ledger_approval_conflict")
        if len(exact_matches) > 1:
            issues.append("ledger_duplicate_exact_record")
        if issues:
            return _audit(
                FAIL,
                commit_id,
                approval_id,
                persisted_audit_status or bundle_audit.audit_status,
                len(exact_matches),
                receipt_present,
                tuple(sorted(set(issues))),
            )
        if not materialized:
            return _audit(
                REPAIR_REQUIRED,
                commit_id,
                approval_id,
                persisted_audit_status or bundle_audit.audit_status,
                len(exact_matches),
                receipt_present,
                ("materialized_artifacts_missing_or_invalid",),
            )
        if len(exact_matches) == 0:
            if receipt_present:
                return _audit(
                    FAIL,
                    commit_id,
                    approval_id,
                    persisted_audit_status or bundle_audit.audit_status,
                    0,
                    True,
                    ("commit_receipt_without_ledger_record",),
                )
            return _audit(
                RECOVERABLE_APPEND,
                commit_id,
                approval_id,
                persisted_audit_status or bundle_audit.audit_status,
                0,
                False,
                (),
            )
        if not receipt_present:
            return _audit(
                RECOVERABLE_FINALIZE,
                commit_id,
                approval_id,
                persisted_audit_status or bundle_audit.audit_status,
                1,
                False,
                (),
            )
        try:
            receipt = _read_json_object(receipt_path)
            _validate_receipt(intent, receipt)
        except ValueError:
            return _audit(
                FAIL,
                commit_id,
                approval_id,
                persisted_audit_status or bundle_audit.audit_status,
                1,
                True,
                ("commit_receipt_invalid",),
            )
        return _audit(
            PASS_COMMITTED,
            commit_id,
            approval_id,
            persisted_audit_status or bundle_audit.audit_status,
            1,
            True,
            (),
        )


class ProspectiveAdmissionCommitResolver:
    """Expose a derived campaign only after the whole commit chain passes audit."""

    def __init__(self, *, auditor: ProspectiveAdmissionCommitAuditor | None = None) -> None:
        self.auditor = auditor or ProspectiveAdmissionCommitAuditor()

    def resolve(
        self,
        bundle: Mapping[str, Any],
        plan: Mapping[str, Any],
        randomization_verification: Mapping[str, Any],
        approval: Mapping[str, Any],
        campaign_store: CampaignStore,
        ledger_path: str | Path,
        commit_directory: str | Path,
    ) -> ProspectiveAdmissionCommitResolution:
        audit = self.auditor.audit(
            bundle,
            plan,
            randomization_verification,
            approval,
            campaign_store,
            ledger_path,
            commit_directory,
        )
        audit_hash = canonical_content_sha256(audit.to_dict())
        if audit.audit_status != PASS_COMMITTED:
            return _resolution(
                status=RESOLUTION_REJECTED,
                audit_hash=audit_hash,
                commit_id=audit.commit_id,
                issue_codes=(f"commit_audit_{audit.audit_status.lower()}", *audit.issue_codes),
            )
        directory = Path(commit_directory).resolve()
        try:
            store = CampaignStore.load_json(directory / DERIVED_CAMPAIGN_FILENAME)
            state = store.rebuild()
            observation = _required_mapping(bundle, "observation")
            ticket = _required_mapping(bundle, "trust_gate_result")
            observation_id = _required_string(observation, "id")
            ticket_id = _required_string(ticket, "id")
            if observation_id not in state.observations or ticket_id not in state.trust_gate_results:
                raise ValueError("Committed campaign omits staged observation or TrustGate result")
        except (TypeError, ValueError, KeyError):
            return _resolution(
                status=RESOLUTION_REJECTED,
                audit_hash=audit_hash,
                commit_id=audit.commit_id,
                issue_codes=("resolved_campaign_state_unavailable",),
            )
        return _resolution(
            status=RESOLVED_COMMITTED_CAMPAIGN,
            audit_hash=audit_hash,
            commit_id=audit.commit_id,
            campaign_path=directory / DERIVED_CAMPAIGN_FILENAME,
            campaign_hash=_campaign_store_hash(store),
            campaign_id=state.campaign_id,
            observation_id=observation_id,
            trust_gate_result_id=ticket_id,
            evidence_admitted=(
                bundle.get("evidence_admitted")
                if isinstance(bundle.get("evidence_admitted"), bool)
                else None
            ),
        )


def _validate_intent_bundle_binding(
    intent: Mapping[str, Any],
    bundle: Mapping[str, Any],
    directory: Path,
) -> None:
    if not directory.is_dir():
        raise ValueError("Commit directory is absent")
    if intent.get("source_bundle_content_sha256") != canonical_content_sha256(bundle):
        raise ValueError("Commit intent bundle content hash mismatch")
    if not _is_sha256(intent.get("source_bundle_audit_content_sha256")):
        raise ValueError("Commit intent bundle audit hash is invalid")
    if intent.get("commit_id") != directory.name:
        raise ValueError("Commit directory name does not match intent")
    if intent.get("admission_id") != bundle.get("admission_id"):
        raise ValueError("Commit intent admission id mismatch")


def _validate_materialized_artifacts(
    intent: Mapping[str, Any],
    bundle: Mapping[str, Any],
    directory: Path,
) -> None:
    campaign_path = directory / DERIVED_CAMPAIGN_FILENAME
    consumption_path = directory / CONSUMPTION_DRAFT_FILENAME
    bundle_path = directory / ADMISSION_BUNDLE_SNAPSHOT_FILENAME
    if not campaign_path.is_file() or not consumption_path.is_file() or not bundle_path.is_file():
        raise ValueError("Commit candidate artifacts are missing")
    snapshot = _read_json_object(bundle_path)
    campaign_events = _read_json_array(campaign_path)
    consumption = _read_json_object(consumption_path)
    expected_events = bundle.get("staged_campaign_events")
    expected_consumption = _required_mapping(bundle, "prospective_consumption_draft")
    if canonical_content_sha256(snapshot) != intent.get("source_bundle_content_sha256"):
        raise ValueError("Admission bundle snapshot hash mismatch")
    if canonical_content_sha256(snapshot) != canonical_content_sha256(bundle):
        raise ValueError("Admission bundle snapshot differs from supplied bundle")
    if canonical_content_sha256(campaign_events) != intent.get("derived_campaign_content_sha256"):
        raise ValueError("Derived campaign artifact hash mismatch")
    if canonical_content_sha256(campaign_events) != canonical_content_sha256(expected_events):
        raise ValueError("Derived campaign artifact differs from bundle")
    if canonical_content_sha256(consumption) != intent.get(
        "prospective_consumption_content_sha256"
    ):
        raise ValueError("Prospective consumption artifact hash mismatch")
    if canonical_content_sha256(consumption) != canonical_content_sha256(expected_consumption):
        raise ValueError("Prospective consumption artifact differs from bundle")
    # Rebuild catches malformed event payloads even when their JSON hash is self-consistent.
    CampaignStore.load_json(campaign_path).rebuild()


def _validate_ledger_identity(intent: Mapping[str, Any], ledger: Path) -> None:
    if intent.get("ledger_path") != str(ledger):
        raise ValueError("Commit ledger path differs from intent")
    if intent.get("ledger_path_identity_sha256") != _path_identity_sha256(ledger):
        raise ValueError("Commit ledger identity differs from intent")


def _validate_receipt(
    intent: Mapping[str, Any],
    receipt: Mapping[str, Any],
) -> None:
    if receipt.get("schema_version") != PROSPECTIVE_ADMISSION_COMMIT_RECEIPT_SCHEMA:
        raise ValueError("Commit receipt schema invalid")
    if receipt.get("commit_status") != COMMITTED:
        raise ValueError("Commit receipt status invalid")
    expected = {
        "commit_id": intent.get("commit_id"),
        "source_intent_content_sha256": canonical_content_sha256(intent),
        "source_bundle_content_sha256": intent.get("source_bundle_content_sha256"),
        "base_campaign_content_sha256": intent.get("base_campaign_content_sha256"),
        "derived_campaign_content_sha256": intent.get("derived_campaign_content_sha256"),
        "prospective_consumption_content_sha256": intent.get(
            "prospective_consumption_content_sha256"
        ),
        "approval_id": intent.get("approval_id"),
        "ledger_path_identity_sha256": intent.get("ledger_path_identity_sha256"),
    }
    for field, value in expected.items():
        if receipt.get(field) != value:
            raise ValueError(f"Commit receipt {field} mismatch")
    if receipt.get("ledger_append_performed") is not True:
        raise ValueError("Commit receipt does not attest ledger append")
    _validate_human_confirmation(
        _required_string(receipt, "finalized_by"),
        _required_string(receipt, "finalization_rationale"),
        _required_string(receipt, "finalized_at"),
    )
    if any(
        receipt.get(field) is not False
        for field in (
            "execution_authority_granted",
            "autonomous_execution_permitted",
            "scientific_claim_upgrade_permitted",
        )
    ):
        raise ValueError("Commit receipt authority flags invalid")


def _receipt(
    intent: Mapping[str, Any],
    *,
    finalized_by: str,
    finalization_rationale: str,
    finalized_at: str,
) -> ProspectiveAdmissionCommitReceipt:
    receipt = ProspectiveAdmissionCommitReceipt(
        schema_version=PROSPECTIVE_ADMISSION_COMMIT_RECEIPT_SCHEMA,
        commit_status=COMMITTED,
        commit_id=_required_string(intent, "commit_id"),
        source_intent_content_sha256=canonical_content_sha256(intent),
        source_bundle_content_sha256=_required_string(intent, "source_bundle_content_sha256"),
        base_campaign_content_sha256=_required_string(intent, "base_campaign_content_sha256"),
        derived_campaign_content_sha256=_required_string(
            intent, "derived_campaign_content_sha256"
        ),
        prospective_consumption_content_sha256=_required_string(
            intent, "prospective_consumption_content_sha256"
        ),
        approval_id=_required_string(intent, "approval_id"),
        ledger_path_identity_sha256=_required_string(intent, "ledger_path_identity_sha256"),
        finalized_by=finalized_by,
        finalization_rationale=finalization_rationale,
        finalized_at=finalized_at,
        ledger_append_performed=True,
        execution_authority_granted=False,
        autonomous_execution_permitted=False,
        scientific_claim_upgrade_permitted=False,
        summary_line="",
    )
    return replace(receipt, summary_line=_receipt_summary_line(receipt))


def _outcome(
    *,
    status: str,
    intent: Mapping[str, Any],
    directory: Path,
    ledger_append_performed: bool,
    receipt_written: bool,
    recovery_used: bool,
    issue_codes: Sequence[str] = (),
) -> ProspectiveAdmissionCommitOutcome:
    outcome = ProspectiveAdmissionCommitOutcome(
        schema_version=PROSPECTIVE_ADMISSION_COMMIT_OUTCOME_SCHEMA,
        commit_status=status,
        commit_id=_required_string(intent, "commit_id"),
        commit_directory=str(directory),
        ledger_append_performed=ledger_append_performed,
        receipt_written=receipt_written,
        recovery_used=recovery_used,
        issue_codes=tuple(issue_codes),
        execution_authority_granted=False,
        autonomous_execution_permitted=False,
        scientific_claim_upgrade_permitted=False,
        summary_line="",
    )
    return replace(outcome, summary_line=_outcome_summary_line(outcome))


def _audit(
    status: str,
    commit_id: str | None,
    approval_id: str | None,
    bundle_audit_status: str | None,
    match_count: int,
    receipt_present: bool,
    issue_codes: Sequence[str],
) -> ProspectiveAdmissionCommitAudit:
    audit = ProspectiveAdmissionCommitAudit(
        schema_version=PROSPECTIVE_ADMISSION_COMMIT_AUDIT_SCHEMA,
        audit_status=status,
        commit_id=commit_id,
        approval_id=approval_id,
        bundle_audit_status=bundle_audit_status,
        ledger_record_match_count=match_count,
        receipt_present=receipt_present,
        issue_codes=tuple(issue_codes),
        summary_line="",
    )
    return replace(audit, summary_line=_audit_summary_line(audit))


def _resolution(
    *,
    status: str,
    audit_hash: str,
    commit_id: str | None,
    campaign_path: Path | None = None,
    campaign_hash: str | None = None,
    campaign_id: str | None = None,
    observation_id: str | None = None,
    trust_gate_result_id: str | None = None,
    evidence_admitted: bool | None = None,
    issue_codes: Sequence[str] = (),
) -> ProspectiveAdmissionCommitResolution:
    resolution = ProspectiveAdmissionCommitResolution(
        schema_version=PROSPECTIVE_ADMISSION_COMMIT_RESOLUTION_SCHEMA,
        resolution_status=status,
        commit_id=commit_id,
        source_commit_audit_content_sha256=audit_hash,
        resolved_campaign_path=str(campaign_path) if campaign_path is not None else None,
        resolved_campaign_content_sha256=campaign_hash,
        campaign_id=campaign_id,
        observation_id=observation_id,
        trust_gate_result_id=trust_gate_result_id,
        evidence_admitted=evidence_admitted,
        issue_codes=tuple(issue_codes),
        execution_authority_granted=False,
        autonomous_execution_permitted=False,
        scientific_claim_upgrade_permitted=False,
        summary_line="",
    )
    return replace(resolution, summary_line=_resolution_summary_line(resolution))


def _campaign_store_hash(store: CampaignStore) -> str:
    return canonical_content_sha256([event_to_dict(event) for event in store.events])


def _path_identity_sha256(path: Path) -> str:
    return canonical_content_sha256({"absolute_path": str(path)})


def _atomic_write_json(path: Path, payload: Mapping[str, Any] | Sequence[Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    data = (json.dumps(payload, indent=2) + "\n").encode("utf-8")
    try:
        with temporary.open("wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _append_jsonl_durably(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


@contextmanager
def _exclusive_ledger_lock(ledger: Path, commit_id: str):
    """Fail closed on another or stale local committer; never steal a lock."""

    lock_path = ledger.with_name(f"{ledger.name}.commit.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with lock_path.open("x", encoding="utf-8") as handle:
            handle.write(commit_id + "\n")
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError:
        yield False
        return
    try:
        yield True
    finally:
        try:
            lock_path.unlink()
        except FileNotFoundError:
            pass


def _read_json_object(path: Path) -> Mapping[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"Unable to read {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON at {path}") from exc
    if not isinstance(payload, Mapping):
        raise ValueError(f"Expected JSON object at {path}")
    return payload


def _read_json_array(path: Path) -> list[Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"Unable to read {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON at {path}") from exc
    if not isinstance(payload, list):
        raise ValueError(f"Expected JSON array at {path}")
    return payload


def _required_mapping(payload: Mapping[str, Any], field: str) -> Mapping[str, Any]:
    value = payload.get(field)
    if not isinstance(value, Mapping):
        raise ValueError(f"{field} must be a JSON object")
    return value


def _required_string(payload: Mapping[str, Any], field: str) -> str:
    value = _string_or_none(payload.get(field))
    if value is None:
        raise ValueError(f"{field} must be non-empty")
    return value


def _string_or_none(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _is_sha256(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _validate_commit_id(value: str) -> None:
    if not _COMMIT_ID_PATTERN.fullmatch(value):
        raise ValueError("commit_id may contain only letters, digits, dot, underscore, and hyphen")


def _validate_human_confirmation(reviewer: str, rationale: str, timestamp: str) -> None:
    if not reviewer.strip():
        raise ValueError("commit reviewer must be non-empty")
    if not rationale.strip():
        raise ValueError("commit rationale must be non-empty")
    try:
        parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("commit timestamp must be ISO-8601") from exc
    if parsed.tzinfo is None:
        raise ValueError("commit timestamp must include a timezone")


def _intent_summary_line(intent: ProspectiveAdmissionCommitIntent) -> str:
    return (
        "Prospective admission commit intent: "
        f"commit_id={intent.commit_id}; ledger_append=false; "
        "execution_authority=false; scientific_claim_upgrade=false."
    )


def _receipt_summary_line(receipt: ProspectiveAdmissionCommitReceipt) -> str:
    return (
        "Prospective admission commit receipt: "
        f"commit_id={receipt.commit_id}; status={receipt.commit_status}; "
        "execution_authority=false; scientific_claim_upgrade=false."
    )


def _outcome_summary_line(outcome: ProspectiveAdmissionCommitOutcome) -> str:
    return (
        "Prospective admission commit outcome: "
        f"commit_id={outcome.commit_id}; status={outcome.commit_status}; "
        f"ledger_append={outcome.ledger_append_performed}; receipt={outcome.receipt_written}."
    )


def _audit_summary_line(audit: ProspectiveAdmissionCommitAudit) -> str:
    return (
        "Prospective admission commit audit: "
        f"status={audit.audit_status}; ledger_matches={audit.ledger_record_match_count}; "
        f"issues={len(audit.issue_codes)}."
    )


def _resolution_summary_line(resolution: ProspectiveAdmissionCommitResolution) -> str:
    return (
        "Prospective admission commit resolution: "
        f"status={resolution.resolution_status}; "
        f"issues={len(resolution.issue_codes)}; "
        "execution_authority=false; scientific_claim_upgrade=false."
    )
