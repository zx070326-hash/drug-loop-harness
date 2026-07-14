from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from .artifact_hash import canonical_content_sha256, json_ready
from .contracts import Observation, TrustGateResult
from .events import Event, EventKind
from .prospective_acquisition_consumption import (
    ProspectiveAcquisitionConsumptionLedger,
    ProspectiveAcquisitionConsumptionRecord,
    ProspectiveAcquisitionConsumptionRecorder,
)
from .prospective_admet_import import ProspectiveAdmetResultImporter
from .store import CampaignStore, contract_to_payload, event_to_dict
from .trust_gate import TrustGate


PROSPECTIVE_ADMISSION_BUNDLE_SCHEMA = "prospective_admission_bundle.v1"
PROSPECTIVE_ADMISSION_BUNDLE_AUDIT_SCHEMA = "prospective_admission_bundle_audit.v1"
STAGED_FOR_EXPLICIT_COMMIT = "staged_for_explicit_commit"
REJECTED = "rejected"
VALID_ADMISSION_DECISIONS = ("accept", "reject", "defer")


@dataclass(frozen=True)
class ProspectiveAdmissionBundleIssue:
    code: str
    location: str
    message: str


@dataclass(frozen=True)
class ProspectiveAdmissionBundle:
    """A human-confirmed, non-committed package for one external ADMET result.

    The package deliberately does not write a campaign event log or a consumption
    ledger. Those are separate durable resources and need an explicit commit and
    recovery protocol; describing this stage as atomic would be misleading.
    """

    schema_version: str
    admission_id: str
    bundle_status: str
    admission_decision: str
    admission_reviewer: str
    admission_rationale: str
    admitted_at: str
    base_campaign_content_sha256: str | None
    source_plan_content_sha256: str
    source_randomization_verification_content_sha256: str
    source_approval_content_sha256: str
    source_import_content_sha256: str | None
    source_import: Mapping[str, Any] | None
    source_interpretation_context_content_sha256: str | None
    source_interpretation_context: Mapping[str, Any] | None
    observation: Observation | None
    trust_gate_result: TrustGateResult | None
    prospective_consumption_draft: ProspectiveAcquisitionConsumptionRecord | None
    staged_campaign_events: tuple[Event, ...]
    staged_campaign_content_sha256: str | None
    evidence_admitted: bool
    ledger_append_performed: bool
    execution_authority_granted: bool
    autonomous_execution_permitted: bool
    scientific_claim_upgrade_permitted: bool
    issues: tuple[ProspectiveAdmissionBundleIssue, ...]
    summary_line: str

    def to_dict(self) -> dict[str, Any]:
        return json_ready(asdict(self))


@dataclass(frozen=True)
class ProspectiveAdmissionBundleAudit:
    """Read-only validation of a staged admission package against current inputs."""

    schema_version: str
    audit_status: str
    admission_id: str | None
    bundle_status: str | None
    evidence_admitted: bool | None
    issue_codes: tuple[str, ...]
    summary_line: str

    def to_dict(self) -> dict[str, Any]:
        return json_ready(asdict(self))


class ProspectiveAdmissionBundleBuilder:
    """Stage, but never commit, one normalized prospective ADMET observation."""

    def __init__(
        self,
        *,
        importer: ProspectiveAdmetResultImporter | None = None,
        trust_gate: TrustGate | None = None,
        consumption_recorder: ProspectiveAcquisitionConsumptionRecorder | None = None,
    ) -> None:
        self.importer = importer or ProspectiveAdmetResultImporter()
        self.trust_gate = trust_gate or TrustGate()
        self.consumption_recorder = (
            consumption_recorder or ProspectiveAcquisitionConsumptionRecorder()
        )

    def stage_path(
        self,
        result_path: str | Path,
        plan: Mapping[str, Any],
        randomization_verification: Mapping[str, Any],
        approval: Mapping[str, Any],
        campaign_store: CampaignStore,
        *,
        admission_decision: str = "accept",
        admission_reviewer: str,
        admission_rationale: str,
        admitted_at: str | None = None,
        admission_id: str | None = None,
        consumed_approval_ids: Sequence[str] = (),
        execution_intent: Mapping[str, Any] | None = None,
        execution_receipt: Mapping[str, Any] | None = None,
        execution_profile: Mapping[str, Any] | None = None,
        endpoint_policy: Mapping[str, Any] | None = None,
        calibration_verification: Mapping[str, Any] | None = None,
    ) -> ProspectiveAdmissionBundle:
        timestamp = admitted_at or datetime.now(timezone.utc).isoformat()
        bundle_id = admission_id or f"prospective_admission_{uuid4().hex}"
        _validate_human_confirmation(
            admission_decision,
            admission_reviewer,
            admission_rationale,
            timestamp,
        )
        source_hashes = _source_hashes(plan, randomization_verification, approval)
        interpretation_context = _interpretation_context(
            execution_intent,
            execution_receipt,
            execution_profile,
            endpoint_policy,
            calibration_verification,
        )
        try:
            state = campaign_store.rebuild()
        except (TypeError, ValueError, KeyError) as exc:
            return _rejected_bundle(
                admission_id=bundle_id,
                decision=admission_decision,
                reviewer=admission_reviewer,
                rationale=admission_rationale,
                admitted_at=timestamp,
                source_hashes=source_hashes,
                issue=_issue("campaign_store_unrebuildable", "$.campaign_store", exc.__class__.__name__),
            )

        base_hash = _campaign_store_hash(campaign_store)
        if admission_decision != "accept":
            return _rejected_bundle(
                admission_id=bundle_id,
                decision=admission_decision,
                reviewer=admission_reviewer,
                rationale=admission_rationale,
                admitted_at=timestamp,
                source_hashes=source_hashes,
                base_campaign_content_sha256=base_hash,
                issue=_issue("admission_decision_not_accepted", "$.admission_decision", admission_decision),
            )

        if interpretation_context is None:
            imported = self.importer.import_path(
                Path(result_path).resolve(),
                plan,
                randomization_verification,
                approval,
                state,
                normalized_at=timestamp,
            )
        else:
            imported = self.importer.import_interpreted_path(
                Path(result_path).resolve(),
                plan,
                randomization_verification,
                approval,
                state,
                execution_intent=interpretation_context["execution_intent"],
                execution_receipt=interpretation_context["execution_receipt"],
                execution_profile=interpretation_context["execution_profile"],
                endpoint_policy=interpretation_context["endpoint_policy"],
                calibration_verification=interpretation_context.get("calibration_verification"),
                normalized_at=timestamp,
            )
        source_import = imported.to_dict()
        source_import_hash = canonical_content_sha256(source_import)
        if imported.import_status != "NORMALIZED" or imported.observation is None:
            return _rejected_bundle(
                admission_id=bundle_id,
                decision=admission_decision,
                reviewer=admission_reviewer,
                rationale=admission_rationale,
                admitted_at=timestamp,
                source_hashes=source_hashes,
                base_campaign_content_sha256=base_hash,
                source_import=source_import,
                source_import_content_sha256=source_import_hash,
                issue=_issue("source_import_not_normalized", "$.source_import", imported.import_status),
            )

        observation = imported.observation
        if observation.id in state.observations:
            return _rejected_bundle(
                admission_id=bundle_id,
                decision=admission_decision,
                reviewer=admission_reviewer,
                rationale=admission_rationale,
                admitted_at=timestamp,
                source_hashes=source_hashes,
                base_campaign_content_sha256=base_hash,
                source_import=source_import,
                source_import_content_sha256=source_import_hash,
                issue=_issue("observation_already_recorded", "$.source_import.observation.id", observation.id),
            )

        oracle = state.oracles.get(observation.oracle_name)
        if oracle is None:
            return _rejected_bundle(
                admission_id=bundle_id,
                decision=admission_decision,
                reviewer=admission_reviewer,
                rationale=admission_rationale,
                admitted_at=timestamp,
                source_hashes=source_hashes,
                base_campaign_content_sha256=base_hash,
                source_import=source_import,
                source_import_content_sha256=source_import_hash,
                issue=_issue("import_oracle_missing_from_campaign", "$.source_import.observation.oracle_name", observation.oracle_name),
            )

        ticket = replace(
            self.trust_gate.evaluate(
            observation,
            oracle,
            tuple(state.observations.values()),
            ),
            id=f"tg_{bundle_id}",
        )
        if ticket.id in state.trust_gate_results:
            return _rejected_bundle(
                admission_id=bundle_id,
                decision=admission_decision,
                reviewer=admission_reviewer,
                rationale=admission_rationale,
                admitted_at=timestamp,
                source_hashes=source_hashes,
                base_campaign_content_sha256=base_hash,
                source_import=source_import,
                source_import_content_sha256=source_import_hash,
                issue=_issue("trust_gate_result_id_already_present", "$.admission_id", bundle_id),
            )
        staged_store = CampaignStore(campaign_store.events)
        observation_event = _admission_event(
            EventKind.OBSERVATION_RECORDED,
            state.campaign_id,
            observation,
            f"evt_{bundle_id}_observation",
            timestamp,
        )
        ticket_event = _admission_event(
            EventKind.TRUST_GATE_RESULT_RECORDED,
            state.campaign_id,
            ticket,
            f"evt_{bundle_id}_trust_gate",
            timestamp,
        )
        if {observation_event.id, ticket_event.id} & {event.id for event in campaign_store.events}:
            return _rejected_bundle(
                admission_id=bundle_id,
                decision=admission_decision,
                reviewer=admission_reviewer,
                rationale=admission_rationale,
                admitted_at=timestamp,
                source_hashes=source_hashes,
                base_campaign_content_sha256=base_hash,
                source_import=source_import,
                source_import_content_sha256=source_import_hash,
                issue=_issue("staged_event_id_already_present", "$.admission_id", bundle_id),
            )
        staged_store.extend((observation_event, ticket_event))
        try:
            staged_state = staged_store.rebuild()
            consumption = self.consumption_recorder.build_record(
                plan,
                randomization_verification,
                approval,
                staged_state,
                observation_id=observation.id,
                trust_gate_result_id=ticket.id,
                consumed_approval_ids=consumed_approval_ids,
                consumed_at=timestamp,
                consumption_id=f"prospective_consumption_{bundle_id}",
                notes=(
                    "Staged in prospective admission bundle; no consumption ledger "
                    "append has occurred."
                ),
            )
        except (TypeError, ValueError, KeyError) as exc:
            return _rejected_bundle(
                admission_id=bundle_id,
                decision=admission_decision,
                reviewer=admission_reviewer,
                rationale=admission_rationale,
                admitted_at=timestamp,
                source_hashes=source_hashes,
                base_campaign_content_sha256=base_hash,
                source_import=source_import,
                source_import_content_sha256=source_import_hash,
                observation=observation,
                trust_gate_result=ticket,
                issue=_issue("prospective_consumption_draft_unavailable", "$.prospective_consumption_draft", exc.__class__.__name__),
            )

        bundle = ProspectiveAdmissionBundle(
            schema_version=PROSPECTIVE_ADMISSION_BUNDLE_SCHEMA,
            admission_id=bundle_id,
            bundle_status=STAGED_FOR_EXPLICIT_COMMIT,
            admission_decision=admission_decision,
            admission_reviewer=admission_reviewer,
            admission_rationale=admission_rationale,
            admitted_at=timestamp,
            base_campaign_content_sha256=base_hash,
            source_plan_content_sha256=source_hashes["plan"],
            source_randomization_verification_content_sha256=source_hashes["verification"],
            source_approval_content_sha256=source_hashes["approval"],
            source_import_content_sha256=source_import_hash,
            source_import=source_import,
            source_interpretation_context_content_sha256=(
                canonical_content_sha256(interpretation_context)
                if interpretation_context is not None
                else None
            ),
            source_interpretation_context=interpretation_context,
            observation=observation,
            trust_gate_result=ticket,
            prospective_consumption_draft=consumption,
            staged_campaign_events=staged_store.events,
            staged_campaign_content_sha256=_campaign_store_hash(staged_store),
            evidence_admitted=ticket.admits_evidence(),
            ledger_append_performed=False,
            execution_authority_granted=False,
            autonomous_execution_permitted=False,
            scientific_claim_upgrade_permitted=False,
            issues=(),
            summary_line="",
        )
        return replace(bundle, summary_line=_bundle_summary_line(bundle))

    def stage_path_with_ledger(
        self,
        result_path: str | Path,
        plan: Mapping[str, Any],
        randomization_verification: Mapping[str, Any],
        approval: Mapping[str, Any],
        campaign_store: CampaignStore,
        ledger_path: str | Path,
        **kwargs: Any,
    ) -> ProspectiveAdmissionBundle:
        """Read a strict ledger before staging; this method never appends to it."""

        try:
            consumed = ProspectiveAcquisitionConsumptionLedger().consumed_approval_ids(ledger_path)
        except ValueError as exc:
            timestamp = kwargs.get("admitted_at") or datetime.now(timezone.utc).isoformat()
            decision = str(kwargs.get("admission_decision", "accept"))
            reviewer = str(kwargs.get("admission_reviewer", ""))
            rationale = str(kwargs.get("admission_rationale", ""))
            _validate_human_confirmation(decision, reviewer, rationale, timestamp)
            return _rejected_bundle(
                admission_id=str(kwargs.get("admission_id") or f"prospective_admission_{uuid4().hex}"),
                decision=decision,
                reviewer=reviewer,
                rationale=rationale,
                admitted_at=timestamp,
                source_hashes=_source_hashes(plan, randomization_verification, approval),
                issue=_issue("consumption_ledger_unreadable", "$.consumption_ledger", exc.__class__.__name__),
            )
        return self.stage_path(
            result_path,
            plan,
            randomization_verification,
            approval,
            campaign_store,
            consumed_approval_ids=consumed,
            **kwargs,
        )


class ProspectiveAdmissionBundleAuditor:
    """Audit a staged package without writing any campaign or consumption ledger."""

    def __init__(self, *, builder: ProspectiveAdmissionBundleBuilder | None = None) -> None:
        self.builder = builder or ProspectiveAdmissionBundleBuilder()

    def audit(
        self,
        bundle: Mapping[str, Any],
        plan: Mapping[str, Any],
        randomization_verification: Mapping[str, Any],
        approval: Mapping[str, Any],
        campaign_store: CampaignStore,
        *,
        consumed_approval_ids: Sequence[str] = (),
    ) -> ProspectiveAdmissionBundleAudit:
        issues: list[str] = []
        admission_id = _string_or_none(bundle.get("admission_id"))
        bundle_status = _string_or_none(bundle.get("bundle_status"))
        source_import = bundle.get("source_import")
        if bundle.get("schema_version") != PROSPECTIVE_ADMISSION_BUNDLE_SCHEMA:
            issues.append("unsupported_admission_bundle_schema")
        if bundle_status != STAGED_FOR_EXPLICIT_COMMIT:
            issues.append("admission_bundle_not_staged")
        if bundle.get("ledger_append_performed") is not False:
            issues.append("admission_bundle_claims_ledger_append")
        if any(
            bundle.get(field) is not False
            for field in (
                "execution_authority_granted",
                "autonomous_execution_permitted",
                "scientific_claim_upgrade_permitted",
            )
        ):
            issues.append("admission_bundle_authority_flag_invalid")
        if not isinstance(source_import, Mapping):
            issues.append("admission_bundle_source_import_missing")
        elif bundle.get("source_import_content_sha256") != canonical_content_sha256(source_import):
            issues.append("admission_bundle_source_import_hash_mismatch")
        source_interpretation_context = bundle.get("source_interpretation_context")
        replay_context: Mapping[str, Any] | None = None
        if source_interpretation_context is None:
            if bundle.get("source_interpretation_context_content_sha256") is not None:
                issues.append("admission_bundle_interpretation_context_hash_without_context")
        elif not isinstance(source_interpretation_context, Mapping):
            issues.append("admission_bundle_interpretation_context_invalid")
        elif bundle.get("source_interpretation_context_content_sha256") != canonical_content_sha256(
            source_interpretation_context
        ):
            issues.append("admission_bundle_interpretation_context_hash_mismatch")
        else:
            try:
                replay_context = _interpretation_context(
                    source_interpretation_context.get("execution_intent"),
                    source_interpretation_context.get("execution_receipt"),
                    source_interpretation_context.get("execution_profile"),
                    source_interpretation_context.get("endpoint_policy"),
                    source_interpretation_context.get("calibration_verification"),
                )
            except ValueError:
                issues.append("admission_bundle_interpretation_context_invalid")

        expected_hashes = _source_hashes(plan, randomization_verification, approval)
        for field, expected in (
            ("source_plan_content_sha256", expected_hashes["plan"]),
            ("source_randomization_verification_content_sha256", expected_hashes["verification"]),
            ("source_approval_content_sha256", expected_hashes["approval"]),
            ("base_campaign_content_sha256", _campaign_store_hash(campaign_store)),
        ):
            if bundle.get(field) != expected:
                issues.append(f"admission_bundle_{field}_mismatch")

        if isinstance(source_import, Mapping):
            result_path = _string_or_none(source_import.get("source_result_path"))
            if result_path is None:
                issues.append("admission_bundle_source_result_path_missing")
            else:
                replayed = self.builder.stage_path(
                    result_path,
                    plan,
                    randomization_verification,
                    approval,
                    campaign_store,
                    admission_decision=str(bundle.get("admission_decision", "")),
                    admission_reviewer=str(bundle.get("admission_reviewer", "")),
                    admission_rationale=str(bundle.get("admission_rationale", "")),
                    admitted_at=str(bundle.get("admitted_at", "")),
                    admission_id=admission_id,
                    consumed_approval_ids=consumed_approval_ids,
                    execution_intent=(
                        replay_context["execution_intent"] if replay_context is not None else None
                    ),
                    execution_receipt=(
                        replay_context["execution_receipt"] if replay_context is not None else None
                    ),
                    execution_profile=(
                        replay_context["execution_profile"] if replay_context is not None else None
                    ),
                    endpoint_policy=(
                        replay_context["endpoint_policy"] if replay_context is not None else None
                    ),
                    calibration_verification=(
                        replay_context.get("calibration_verification")
                        if replay_context is not None
                        else None
                    ),
                )
                if replayed.bundle_status != STAGED_FOR_EXPLICIT_COMMIT:
                    issues.append("admission_bundle_replay_not_staged")
                elif canonical_content_sha256(replayed.to_dict()) != canonical_content_sha256(bundle):
                    issues.append("admission_bundle_replay_content_mismatch")

        status = "PASS_STAGED" if not issues else "FAIL"
        audit = ProspectiveAdmissionBundleAudit(
            schema_version=PROSPECTIVE_ADMISSION_BUNDLE_AUDIT_SCHEMA,
            audit_status=status,
            admission_id=admission_id,
            bundle_status=bundle_status,
            evidence_admitted=(
                bundle.get("evidence_admitted")
                if isinstance(bundle.get("evidence_admitted"), bool)
                else None
            ),
            issue_codes=tuple(sorted(set(issues))),
            summary_line="",
        )
        return replace(audit, summary_line=_audit_summary_line(audit))


def _admission_event(
    kind: EventKind,
    campaign_id: str,
    contract: Observation | TrustGateResult,
    event_id: str,
    created_at: str,
) -> Event:
    return Event(
        id=event_id,
        kind=kind,
        campaign_id=campaign_id,
        payload=contract_to_payload(contract),
        created_at=created_at,
    )


def _campaign_store_hash(store: CampaignStore) -> str:
    return canonical_content_sha256([event_to_dict(event) for event in store.events])


def _source_hashes(
    plan: Mapping[str, Any],
    randomization_verification: Mapping[str, Any],
    approval: Mapping[str, Any],
) -> dict[str, str]:
    return {
        "plan": canonical_content_sha256(plan),
        "verification": canonical_content_sha256(randomization_verification),
        "approval": canonical_content_sha256(approval),
    }


def _interpretation_context(
    execution_intent: Mapping[str, Any] | None,
    execution_receipt: Mapping[str, Any] | None,
    execution_profile: Mapping[str, Any] | None,
    endpoint_policy: Mapping[str, Any] | None,
    calibration_verification: Mapping[str, Any] | None = None,
) -> Mapping[str, Any] | None:
    values = (
        execution_intent,
        execution_receipt,
        execution_profile,
        endpoint_policy,
    )
    if not any(value is not None for value in values):
        return None
    if not all(isinstance(value, Mapping) for value in values):
        raise ValueError("strict interpreted admission requires all execution context artifacts")
    context: dict[str, Any] = {
        "execution_intent": dict(execution_intent),
        "execution_receipt": dict(execution_receipt),
        "execution_profile": dict(execution_profile),
        "endpoint_policy": dict(endpoint_policy),
    }
    if calibration_verification is not None:
        if not isinstance(calibration_verification, Mapping):
            raise ValueError("calibration_verification must be a JSON object")
        context["calibration_verification"] = dict(calibration_verification)
    return context


def _rejected_bundle(
    *,
    admission_id: str,
    decision: str,
    reviewer: str,
    rationale: str,
    admitted_at: str,
    source_hashes: Mapping[str, str],
    issue: ProspectiveAdmissionBundleIssue,
    base_campaign_content_sha256: str | None = None,
    source_import: Mapping[str, Any] | None = None,
    source_import_content_sha256: str | None = None,
    source_interpretation_context: Mapping[str, Any] | None = None,
    observation: Observation | None = None,
    trust_gate_result: TrustGateResult | None = None,
) -> ProspectiveAdmissionBundle:
    bundle = ProspectiveAdmissionBundle(
        schema_version=PROSPECTIVE_ADMISSION_BUNDLE_SCHEMA,
        admission_id=admission_id,
        bundle_status=REJECTED,
        admission_decision=decision,
        admission_reviewer=reviewer,
        admission_rationale=rationale,
        admitted_at=admitted_at,
        base_campaign_content_sha256=base_campaign_content_sha256,
        source_plan_content_sha256=source_hashes["plan"],
        source_randomization_verification_content_sha256=source_hashes["verification"],
        source_approval_content_sha256=source_hashes["approval"],
        source_import_content_sha256=source_import_content_sha256,
        source_import=source_import,
        source_interpretation_context_content_sha256=(
            canonical_content_sha256(source_interpretation_context)
            if source_interpretation_context is not None
            else None
        ),
        source_interpretation_context=source_interpretation_context,
        observation=observation,
        trust_gate_result=trust_gate_result,
        prospective_consumption_draft=None,
        staged_campaign_events=(),
        staged_campaign_content_sha256=None,
        evidence_admitted=False,
        ledger_append_performed=False,
        execution_authority_granted=False,
        autonomous_execution_permitted=False,
        scientific_claim_upgrade_permitted=False,
        issues=(issue,),
        summary_line="",
    )
    return replace(bundle, summary_line=_bundle_summary_line(bundle))


def _validate_human_confirmation(
    decision: str,
    reviewer: str,
    rationale: str,
    admitted_at: str,
) -> None:
    if decision not in VALID_ADMISSION_DECISIONS:
        raise ValueError("admission_decision must be accept, reject, or defer")
    if not reviewer.strip():
        raise ValueError("admission_reviewer must be non-empty")
    if not rationale.strip():
        raise ValueError("admission_rationale must be non-empty")
    try:
        timestamp = datetime.fromisoformat(admitted_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("admitted_at must be an ISO-8601 timestamp") from exc
    if timestamp.tzinfo is None:
        raise ValueError("admitted_at must include a timezone")


def _issue(code: str, location: str, message: str) -> ProspectiveAdmissionBundleIssue:
    return ProspectiveAdmissionBundleIssue(code=code, location=location, message=message)


def _string_or_none(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _bundle_summary_line(bundle: ProspectiveAdmissionBundle) -> str:
    return (
        "Prospective admission bundle: "
        f"status={bundle.bundle_status}; evidence_admitted={bundle.evidence_admitted}; "
        "ledger_append=false; execution_authority=false; scientific_claim_upgrade=false."
    )


def _audit_summary_line(audit: ProspectiveAdmissionBundleAudit) -> str:
    return (
        "Prospective admission bundle audit: "
        f"status={audit.audit_status}; issues={len(audit.issue_codes)}; "
        "ledger_append=false."
    )
