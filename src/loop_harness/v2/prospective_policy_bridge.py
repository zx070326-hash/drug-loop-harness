from __future__ import annotations

import importlib.metadata
import math
import platform
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any
from uuid import uuid4

from ..artifact_hash import canonical_content_sha256, json_ready
from . import action_space, contracts, ledger as ledger_module, mfbo_policy, state
from .action_space import ActionSpace
from .contracts import ActionScore, AcquisitionAction
from .gates import DecisionGate
from .ledger import CampaignLedger
from .mfbo_policy import JensenStyleTVRPolicy
from .reliability_aware_policy import ReliabilityAwareTVRPolicy
from . import reliability_aware_policy
from .state import StateProjector


PROSPECTIVE_V2_POLICY_SPEC_SCHEMA = "prospective_v2_policy_spec.v1"
PROSPECTIVE_V2_POLICY_RUNTIME_MANIFEST_SCHEMA = (
    "prospective_v2_policy_runtime_manifest.v1"
)
PROSPECTIVE_V2_POLICY_DECISION_RECEIPT_SCHEMA = (
    "prospective_v2_policy_decision_receipt.v1"
)
PROSPECTIVE_V2_POLICY_FIDELITY_SCHEMA = "prospective_v2_policy_fidelity.v1"
PROSPECTIVE_V2_STATE_CONTRACT_ID = "campaign_ledger_global_action_space.v2"

_POLICY_KINDS = {"jensen_tvr", "reliability_tvr"}
_PARAMETER_DEFAULTS: dict[str, Any] = {
    "min_observations_per_oracle": 1,
    "fingerprint_bits": 256,
    "noise": 1e-4,
    "default_task_correlation": 0.5,
    "legacy_reference_indexing": False,
}
_AUTHORITY_FIELDS = (
    "execution_authority_granted",
    "autonomous_execution_permitted",
    "scientific_claim_upgrade_permitted",
)


@dataclass(frozen=True)
class ProspectiveV2PolicyDecisionReceipt:
    """Replayable round-level policy output; never an oracle execution token."""

    schema_version: str
    receipt_id: str
    decision_round_id: str
    decided_at: str
    campaign_id: str
    target_id: str
    round_index: int
    source_campaign_ledger_content_sha256: str
    source_policy_spec_content_sha256: str
    source_policy_runtime_content_sha256: str
    source_action_space_semantic_sha256: str
    source_policy_ranking_semantic_sha256: str
    policy_kind: str
    policy_version: str
    state_contract_id: str
    legal_action_count: int
    requested_selection_count: int
    selected_proposals: tuple[Mapping[str, Any], ...]
    selected_kernel_dispositions: tuple[Mapping[str, Any], ...]
    full_ranking_semantic_committed: bool
    future_outcomes_supplied_to_policy: bool
    execution_authority_granted: bool
    autonomous_execution_permitted: bool
    scientific_claim_upgrade_permitted: bool
    summary_line: str

    def to_dict(self) -> dict[str, Any]:
        return json_ready(asdict(self))


@dataclass(frozen=True)
class ProspectiveV2PolicyFidelityIssue:
    code: str
    location: str
    message: str


@dataclass(frozen=True)
class ProspectiveV2PolicyFidelity:
    schema_version: str
    verification_status: str
    receipt_id: str | None
    decision_round_id: str | None
    campaign_id: str | None
    policy_kind: str | None
    replayed_selected_proposals: tuple[Mapping[str, Any], ...]
    issue_codes: tuple[str, ...]
    issues: tuple[ProspectiveV2PolicyFidelityIssue, ...]
    execution_authority_granted: bool
    autonomous_execution_permitted: bool
    scientific_claim_upgrade_permitted: bool
    summary_line: str

    def to_dict(self) -> dict[str, Any]:
        return json_ready(asdict(self))


class ProspectiveV2PolicyDecisionRecorder:
    """Record a global acquisition ranking against one immutable v2 ledger state."""

    def record(
        self,
        policy_spec: Mapping[str, Any],
        campaign_ledger: CampaignLedger,
        *,
        decision_round_id: str,
        decided_at: str | None = None,
        receipt_id: str | None = None,
    ) -> ProspectiveV2PolicyDecisionReceipt:
        if not decision_round_id:
            raise ValueError("decision_round_id is required")
        timestamp = decided_at or datetime.now(timezone.utc).isoformat()
        _parse_timestamp(timestamp, "decided_at")
        material = _replay_material(policy_spec, campaign_ledger)
        receipt = ProspectiveV2PolicyDecisionReceipt(
            schema_version=PROSPECTIVE_V2_POLICY_DECISION_RECEIPT_SCHEMA,
            receipt_id=receipt_id or f"prospective_v2_policy_receipt_{uuid4().hex}",
            decision_round_id=decision_round_id,
            decided_at=timestamp,
            campaign_id=campaign_ledger.campaign_id,
            target_id=material["snapshot"].spec.target_id,
            round_index=material["snapshot"].round_index,
            source_campaign_ledger_content_sha256=campaign_ledger.content_sha256(),
            source_policy_spec_content_sha256=canonical_content_sha256(policy_spec),
            source_policy_runtime_content_sha256=canonical_content_sha256(
                material["runtime_manifest"]
            ),
            source_action_space_semantic_sha256=canonical_content_sha256(
                material["action_semantics"]
            ),
            source_policy_ranking_semantic_sha256=canonical_content_sha256(
                material["ranking_semantics"]
            ),
            policy_kind=str(policy_spec["policy_kind"]),
            policy_version=material["policy"].policy_version,
            state_contract_id=PROSPECTIVE_V2_STATE_CONTRACT_ID,
            legal_action_count=len(material["actions"]),
            requested_selection_count=material["selection_count"],
            selected_proposals=material["selected_proposals"],
            selected_kernel_dispositions=material["selected_dispositions"],
            full_ranking_semantic_committed=True,
            future_outcomes_supplied_to_policy=False,
            execution_authority_granted=False,
            autonomous_execution_permitted=False,
            scientific_claim_upgrade_permitted=False,
            summary_line="",
        )
        return replace(receipt, summary_line=_receipt_summary(receipt))


class ProspectiveV2PolicyDecisionFidelityVerifier:
    """Rebuild the ledger and independently reproduce one v2 policy receipt."""

    def verify(
        self,
        policy_spec: Mapping[str, Any],
        campaign_ledger: CampaignLedger,
        receipt: Mapping[str, Any],
    ) -> ProspectiveV2PolicyFidelity:
        issues: list[ProspectiveV2PolicyFidelityIssue] = []
        material: Mapping[str, Any] | None = None
        if receipt.get("schema_version") != PROSPECTIVE_V2_POLICY_DECISION_RECEIPT_SCHEMA:
            issues.append(_issue("receipt_schema_invalid", "$.receipt.schema_version"))
        if _string_or_none(receipt.get("receipt_id")) is None:
            issues.append(_issue("receipt_id_missing", "$.receipt.receipt_id"))
        if _string_or_none(receipt.get("decision_round_id")) is None:
            issues.append(_issue("decision_round_id_missing", "$.receipt.decision_round_id"))
        if _timestamp_or_none(receipt.get("decided_at")) is None:
            issues.append(_issue("decided_at_invalid", "$.receipt.decided_at"))
        for field in _AUTHORITY_FIELDS:
            if receipt.get(field) is not False:
                issues.append(_issue(f"{field}_must_be_false", f"$.receipt.{field}"))
        if receipt.get("future_outcomes_supplied_to_policy") is not False:
            issues.append(
                _issue(
                    "future_outcomes_supplied_to_policy_must_be_false",
                    "$.receipt.future_outcomes_supplied_to_policy",
                )
            )
        try:
            material = _replay_material(policy_spec, campaign_ledger)
        except (KeyError, TypeError, ValueError, RuntimeError) as exc:
            issues.append(_issue("policy_replay_failed", "$.replay", str(exc)))

        if material is not None:
            expected = {
                "campaign_id": campaign_ledger.campaign_id,
                "target_id": material["snapshot"].spec.target_id,
                "round_index": material["snapshot"].round_index,
                "source_campaign_ledger_content_sha256": campaign_ledger.content_sha256(),
                "source_policy_spec_content_sha256": canonical_content_sha256(policy_spec),
                "source_policy_runtime_content_sha256": canonical_content_sha256(
                    material["runtime_manifest"]
                ),
                "source_action_space_semantic_sha256": canonical_content_sha256(
                    material["action_semantics"]
                ),
                "source_policy_ranking_semantic_sha256": canonical_content_sha256(
                    material["ranking_semantics"]
                ),
                "policy_kind": policy_spec.get("policy_kind"),
                "policy_version": material["policy"].policy_version,
                "state_contract_id": PROSPECTIVE_V2_STATE_CONTRACT_ID,
                "legal_action_count": len(material["actions"]),
                "requested_selection_count": material["selection_count"],
                "full_ranking_semantic_committed": True,
            }
            for field, value in expected.items():
                if receipt.get(field) != value:
                    issues.append(_issue(f"{field}_mismatch", f"$.receipt.{field}"))
            _compare_semantic(
                receipt.get("selected_proposals"),
                material["selected_proposals"],
                "selected_proposals",
                issues,
            )
            _compare_semantic(
                receipt.get("selected_kernel_dispositions"),
                material["selected_dispositions"],
                "selected_kernel_dispositions",
                issues,
            )

        result = ProspectiveV2PolicyFidelity(
            schema_version=PROSPECTIVE_V2_POLICY_FIDELITY_SCHEMA,
            verification_status="PASS" if not issues else "FAIL",
            receipt_id=_string_or_none(receipt.get("receipt_id")),
            decision_round_id=_string_or_none(receipt.get("decision_round_id")),
            campaign_id=_string_or_none(receipt.get("campaign_id")),
            policy_kind=_string_or_none(receipt.get("policy_kind")),
            replayed_selected_proposals=(
                material["selected_proposals"] if material is not None else ()
            ),
            issue_codes=tuple(sorted({issue.code for issue in issues})),
            issues=tuple(issues),
            execution_authority_granted=False,
            autonomous_execution_permitted=False,
            scientific_claim_upgrade_permitted=False,
            summary_line="",
        )
        return replace(result, summary_line=_fidelity_summary(result))


def policy_from_spec(
    policy_spec: Mapping[str, Any],
) -> JensenStyleTVRPolicy | ReliabilityAwareTVRPolicy:
    if policy_spec.get("schema_version") != PROSPECTIVE_V2_POLICY_SPEC_SCHEMA:
        raise ValueError("prospective v2 policy spec schema is invalid")
    if policy_spec.get("state_contract_id") != PROSPECTIVE_V2_STATE_CONTRACT_ID:
        raise ValueError("prospective v2 state contract is invalid")
    kind = _string_or_none(policy_spec.get("policy_kind"))
    if kind not in _POLICY_KINDS:
        raise ValueError("prospective v2 policy kind is not implemented")
    parameters = _normalized_parameters(policy_spec.get("parameters"))
    base = JensenStyleTVRPolicy(**parameters)
    return base if kind == "jensen_tvr" else ReliabilityAwareTVRPolicy(base_policy=base)


def policy_runtime_manifest(policy_spec: Mapping[str, Any]) -> dict[str, Any]:
    policy = policy_from_spec(policy_spec)
    parameters = _normalized_parameters(policy_spec.get("parameters"))
    return {
        "schema_version": PROSPECTIVE_V2_POLICY_RUNTIME_MANIFEST_SCHEMA,
        "state_contract_id": PROSPECTIVE_V2_STATE_CONTRACT_ID,
        "policy_kind": policy_spec["policy_kind"],
        "policy_version": policy.policy_version,
        "implementation_class": type(policy).__name__,
        "parameters": parameters,
        "runtime_dependencies": {
            "python": platform.python_version(),
            "numpy": _distribution_version("numpy"),
            "rdkit": _distribution_version("rdkit"),
        },
        "source_modules": {
            "contracts": _module_sha256(contracts),
            "ledger": _module_sha256(ledger_module),
            "state": _module_sha256(state),
            "action_space": _module_sha256(action_space),
            "mfbo_policy": _module_sha256(mfbo_policy),
            "reliability_aware_policy": _module_sha256(reliability_aware_policy),
            "prospective_policy_bridge": _module_sha256(
                __import__(__name__, fromlist=["*"])
            ),
        },
    }


def action_semantic(action: AcquisitionAction) -> dict[str, Any]:
    return {
        "action_id": action.action_id,
        "candidate_id": action.candidate_id,
        "oracle_id": action.oracle_id,
        "endpoint_id": action.endpoint_id,
        "expected_cost": action.expected_cost,
        "execution_prerequisites": list(action.execution_prerequisites),
        "promotion_predecessors": list(action.promotion_predecessors),
        "policy_version": action.policy_version,
    }


def score_semantic(score: ActionScore, *, rank: int) -> dict[str, Any]:
    return {
        "rank": rank,
        "action": action_semantic(score.action),
        "information_gain": score.information_gain,
        "decision_value": score.decision_value,
        "cost": score.cost,
        "priority": score.priority,
        "rationale": score.rationale,
    }


def _replay_material(
    policy_spec: Mapping[str, Any],
    campaign_ledger: CampaignLedger,
) -> dict[str, Any]:
    policy = policy_from_spec(policy_spec)
    snapshot = StateProjector().project(campaign_ledger)
    if snapshot.stop_reason is not None:
        raise ValueError("campaign is stopped")
    if snapshot.pending_action_ids:
        raise ValueError("campaign has unresolved pending actions")
    if snapshot.round_index >= snapshot.spec.max_rounds:
        raise ValueError("campaign has reached max_rounds")
    if snapshot.budget_available <= 1e-9:
        raise ValueError("campaign budget is exhausted")
    selection_count = _selection_count(policy_spec, snapshot.spec.batch_size)
    actions = ActionSpace().enumerate(snapshot, policy_version=policy.policy_version)
    ranking = policy.rank(snapshot, actions)
    if {score.action.action_id for score in ranking} - {
        action.action_id for action in actions
    }:
        raise ValueError("policy ranking contains an action outside the legal action space")
    if len({score.action.action_id for score in ranking}) != len(ranking):
        raise ValueError("policy ranking contains duplicate action IDs")
    ranking_semantics = tuple(
        score_semantic(score, rank=index)
        for index, score in enumerate(ranking, start=1)
    )
    selected = tuple(ranking_semantics[:selection_count])
    gate = DecisionGate()
    dispositions = tuple(
        {
            "action_id": semantic["action"]["action_id"],
            "disposition": decision.disposition.value,
            "reason": decision.reason,
            "approval_applied": False,
        }
        for semantic in selected
        for decision in (gate.evaluate(_score_action(ranking, semantic), snapshot),)
    )
    return {
        "policy": policy,
        "snapshot": snapshot,
        "actions": actions,
        "action_semantics": tuple(action_semantic(action) for action in actions),
        "ranking_semantics": ranking_semantics,
        "selected_proposals": selected,
        "selected_dispositions": dispositions,
        "selection_count": selection_count,
        "runtime_manifest": policy_runtime_manifest(policy_spec),
    }


def _score_action(
    ranking: Sequence[ActionScore],
    semantic: Mapping[str, Any],
) -> AcquisitionAction:
    action_id = semantic["action"]["action_id"]
    return next(score.action for score in ranking if score.action.action_id == action_id)


def _normalized_parameters(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("prospective v2 policy parameters are required")
    if set(value) - set(_PARAMETER_DEFAULTS):
        raise ValueError("prospective v2 policy parameter is not implemented")
    result = {**_PARAMETER_DEFAULTS, **dict(value)}
    for field in ("min_observations_per_oracle", "fingerprint_bits"):
        item = result[field]
        if isinstance(item, bool) or not isinstance(item, int) or item <= 0:
            raise ValueError(f"{field} must be a positive integer")
    for field in ("noise", "default_task_correlation"):
        item = result[field]
        if isinstance(item, bool) or not isinstance(item, int | float) or not math.isfinite(item):
            raise ValueError(f"{field} must be finite")
    if result["noise"] <= 0:
        raise ValueError("noise must be positive")
    if not 0 <= result["default_task_correlation"] < 1:
        raise ValueError("default_task_correlation must be in [0, 1)")
    if not isinstance(result["legacy_reference_indexing"], bool):
        raise ValueError("legacy_reference_indexing must be boolean")
    return result


def _selection_count(policy_spec: Mapping[str, Any], campaign_batch_size: int) -> int:
    value = policy_spec.get("selection_count", campaign_batch_size)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError("selection_count must be a positive integer")
    if value != campaign_batch_size:
        raise ValueError("selection_count must equal the frozen campaign batch_size")
    return value


def _compare_semantic(
    actual: Any,
    expected: Sequence[Mapping[str, Any]],
    field: str,
    issues: list[ProspectiveV2PolicyFidelityIssue],
) -> None:
    if not isinstance(actual, list | tuple) or canonical_content_sha256(actual) != canonical_content_sha256(expected):
        issues.append(_issue(f"{field}_not_replayed", f"$.receipt.{field}"))


def _module_sha256(module: Any) -> str:
    source_path = getattr(module, "__file__", None)
    if not isinstance(source_path, str):
        raise ValueError("policy runtime source is unavailable")
    return sha256(Path(source_path).read_bytes()).hexdigest()


def _distribution_version(name: str) -> str:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError as exc:
        raise ValueError(f"required policy runtime dependency is unavailable: {name}") from exc


def _parse_timestamp(value: str, field: str) -> datetime:
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field} must be timezone-aware ISO-8601") from exc
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware ISO-8601")
    return result


def _timestamp_or_none(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return _parse_timestamp(value, "timestamp")
    except ValueError:
        return None


def _string_or_none(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _issue(
    code: str,
    location: str,
    message: str | None = None,
) -> ProspectiveV2PolicyFidelityIssue:
    return ProspectiveV2PolicyFidelityIssue(
        code=code,
        location=location,
        message=message or code.replace("_", " ") + ".",
    )


def _receipt_summary(receipt: ProspectiveV2PolicyDecisionReceipt) -> str:
    return (
        "Prospective v2 policy decision receipt: "
        f"round={receipt.decision_round_id}; policy={receipt.policy_kind}; "
        f"proposals={len(receipt.selected_proposals)}; execution_authority=false; "
        "scientific_claim_upgrade=false."
    )


def _fidelity_summary(result: ProspectiveV2PolicyFidelity) -> str:
    return (
        "Prospective v2 policy fidelity: "
        f"status={result.verification_status}; issues={len(result.issues)}; "
        "execution_authority=false; scientific_claim_upgrade=false."
    )
