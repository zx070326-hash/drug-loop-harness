from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any

from ..artifact_hash import canonical_content_sha256
from .prospective_policy_bridge import (
    PROSPECTIVE_V2_POLICY_SPEC_SCHEMA,
    PROSPECTIVE_V2_STATE_CONTRACT_ID,
    policy_runtime_manifest,
)


PROSPECTIVE_V2_DESIGN_HANDOFF_SCHEMA = "prospective_v2_design_handoff.v1"
PROSPECTIVE_V2_DESIGN_HANDOFF_VERIFICATION_SCHEMA = (
    "prospective_v2_design_handoff_verification.v1"
)

_CURVE_PATH = Path("state/jensen-policy-curve-evaluation-v3.json")
_CURVE_SHA256 = "c936aa384f88594142265a351c2fe3c3e39c4aff1c910f0c2d70093ce1722902"
_STRESS_PATH = Path("state/jensen-reliability-stress-evaluation-v1.json")
_STRESS_SHA256 = "280450c4f020457d34a8b8ddc50ff4a5ee8f8db4c8c08a043b22eb5ab3b07781"

_AUTHORITY_FIELDS = (
    "execution_authority_granted",
    "autonomous_execution_permitted",
    "scientific_claim_upgrade_permitted",
)
_REQUIRED_EXTERNAL_INPUTS = (
    (
        "target_population_and_campaign_replicates",
        "Independent target/cohort campaign replicates and inclusion criteria.",
    ),
    (
        "candidate_pool_and_feature_freeze",
        "Pre-outcome candidate pools, chemistry features, exclusions, and provenance hashes.",
    ),
    (
        "oracle_execution_and_cost_contracts",
        "Exact docking, assay, and optional intermediate oracle adapters, costs, latency, and failure semantics.",
    ),
    (
        "assay_endpoint_threshold_and_site",
        "Primary assay endpoint, positive threshold, measurement protocol, and blinded assay site.",
    ),
    (
        "campaign_budget_and_stopping_rules",
        "Equal per-arm compute/assay budget, maximum rounds, batch size, and non-outcome stop rules.",
    ),
    (
        "effect_size_and_power_plan",
        "Defensible baseline rate, minimum relevant effect, attrition assumptions, and campaign-level power plan.",
    ),
    (
        "external_assignment_and_blinding",
        "Independent assignment of campaign replicates and blinded outcome capture before analysis release.",
    ),
    (
        "human_scientific_and_execution_acceptance",
        "Named acceptance of the frozen scientific design and separate authorization for consequential execution.",
    ),
)


def default_policy_specs() -> dict[str, dict[str, Any]]:
    common = {
        "schema_version": PROSPECTIVE_V2_POLICY_SPEC_SCHEMA,
        "state_contract_id": PROSPECTIVE_V2_STATE_CONTRACT_ID,
        "selection_count": 1,
        "parameters": {
            "min_observations_per_oracle": 1,
            "fingerprint_bits": 256,
            "noise": 0.0001,
            "default_task_correlation": 0.5,
            "legacy_reference_indexing": False,
        },
    }
    return {
        "baseline": {**common, "policy_kind": "jensen_tvr"},
        "credibility_aware": {**common, "policy_kind": "reliability_tvr"},
    }


def build_prospective_v2_design_handoff(
    *,
    root: Path,
    created_at: str | None = None,
) -> dict[str, Any]:
    root = root.resolve()
    timestamp = created_at or datetime.now(timezone.utc).isoformat()
    _parse_timestamp(timestamp)
    curve = _read_frozen_evidence(
        root / _CURVE_PATH,
        expected_sha256=_CURVE_SHA256,
        expected_schema="jensen_policy_curve_evaluation.v3",
        expected_status="PASS_COMPLETE_FROZEN_CURVE",
        expected_decision="SUPPORT_PROSPECTIVE_POLICY_STUDY_DESIGN",
    )
    stress = _read_frozen_evidence(
        root / _STRESS_PATH,
        expected_sha256=_STRESS_SHA256,
        expected_schema="jensen_reliability_stress_evaluation.v1",
        expected_status="PASS_COMPLETE_FROZEN_STRESS_ABLATION",
        expected_decision="SUPPORT_CREDIBILITY_AWARE_POLICY_FOR_PROSPECTIVE_DESIGN",
    )
    policy_specs = default_policy_specs()
    policy_arms = {
        role: {
            "policy_spec": spec,
            "policy_spec_content_sha256": canonical_content_sha256(spec),
            "policy_runtime_manifest": policy_runtime_manifest(spec),
            "policy_runtime_content_sha256": canonical_content_sha256(
                policy_runtime_manifest(spec)
            ),
        }
        for role, spec in policy_specs.items()
    }
    requirements = [
        {
            "input_id": input_id,
            "status": "MISSING_EXTERNAL_INPUT",
            "requirement": requirement,
            "agent_may_invent": False,
        }
        for input_id, requirement in _REQUIRED_EXTERNAL_INPUTS
    ]
    handoff = {
        "schema_version": PROSPECTIVE_V2_DESIGN_HANDOFF_SCHEMA,
        "handoff_id": "jensen-tvr-to-prospective-campaign-design-v1",
        "created_at": timestamp,
        "stage_status": "READY_FOR_ASSAY_SPECIFIC_DESIGN_INPUTS",
        "scientific_question": (
            "Under equal campaign budgets, does reliability-aware TVR reduce invalid-oracle "
            "resource loss without reducing the rate of independently assay-validated candidates?"
        ),
        "frozen_development_evidence": {
            "promotion_policy_curve": {
                "path": _CURVE_PATH.as_posix(),
                "sha256": _CURVE_SHA256,
                "decision": curve["decision"],
                "primary_gate_passed": curve["primary_gate_passed"],
                "scientific_unit_count": curve["aggregate"]["scientific_unit_count"],
            },
            "reliability_stress_ablation": {
                "path": _STRESS_PATH.as_posix(),
                "sha256": _STRESS_SHA256,
                "decision": stress["decision"],
                "primary_gate_passed": stress["primary_gate_passed"],
                "scientific_unit_count": stress["aggregate"]["scientific_unit_count"],
            },
        },
        "policy_arms": policy_arms,
        "state_and_decision_contract": {
            "state_contract_id": PROSPECTIVE_V2_STATE_CONTRACT_ID,
            "decision_scope": "global_candidate_oracle_ranking_per_campaign_round",
            "policy_input": (
                "complete immutable CampaignLedger projection plus the deterministic global ActionSpace"
            ),
            "policy_output": (
                "ranked candidate-oracle proposals with a hash-bound full ranking and kernel dispositions"
            ),
            "execution_separated_from_ranking": True,
            "receipt_replay_required": True,
        },
        "prospective_design_correction": {
            "existing_v5_candidate_episode_protocol_directly_compatible": False,
            "reason": (
                "TVR ranks a shared candidate-oracle action space and changes future state; individual "
                "candidate decisions are coupled and are not independent policy replicates."
            ),
            "recommended_design": "paired_isolated_campaign_arms",
            "randomization_unit": "independent_target_cohort_campaign_replicate",
            "scientific_unit": "independent_target_cohort_campaign_replicate",
            "arm_isolation": (
                "identical frozen starting pools and budgets, separate arm-local observation histories"
            ),
            "outcome_capture": (
                "blinded assay of the union of promoted candidates with overlap and shared measurements "
                "modeled as paired, correlated observations"
            ),
            "analysis_order": (
                "compare arms within campaign replicate, then aggregate campaign-level contrasts"
            ),
            "independent_replication_required": True,
        },
        "predeclared_primary_estimand": {
            "metric_id": "assay_validated_candidates_per_equal_total_campaign_cost",
            "comparison": "credibility_aware_minus_baseline",
            "failure_cost_supporting_metric": "invalid_or_failed_oracle_cost_fraction",
            "effectiveness_guard": "assay_validated_candidate_rate_noninferiority",
            "claim_ceiling": "prospective_policy_efficiency_evidence_only",
        },
        "required_external_inputs": requirements,
        "readiness": {
            "v2_policy_runtime_ready": True,
            "replayable_decision_receipt_ready": True,
            "assay_protocol_registration_ready": False,
            "external_input_blocker_count": len(requirements),
            "next_legal_transition": "supply_and_review_assay_specific_design_inputs",
        },
        "execution_authority_granted": False,
        "autonomous_execution_permitted": False,
        "scientific_claim_upgrade_permitted": False,
    }
    validate_prospective_v2_design_handoff(handoff, root=root)
    return handoff


def validate_prospective_v2_design_handoff(
    payload: Mapping[str, Any],
    *,
    root: Path,
) -> None:
    if payload.get("schema_version") != PROSPECTIVE_V2_DESIGN_HANDOFF_SCHEMA:
        raise ValueError("prospective v2 design handoff schema is invalid")
    if payload.get("stage_status") != "READY_FOR_ASSAY_SPECIFIC_DESIGN_INPUTS":
        raise ValueError("prospective v2 design handoff stage status is invalid")
    _parse_timestamp(_required_string(payload, "created_at"))
    for field in _AUTHORITY_FIELDS:
        if payload.get(field) is not False:
            raise ValueError(f"prospective v2 design handoff {field} must be false")
    evidence = _required_mapping(payload, "frozen_development_evidence")
    expected_evidence = {
        "promotion_policy_curve": (_CURVE_PATH, _CURVE_SHA256),
        "reliability_stress_ablation": (_STRESS_PATH, _STRESS_SHA256),
    }
    for name, (relative, expected_hash) in expected_evidence.items():
        item = _required_mapping(evidence, name)
        if item.get("path") != relative.as_posix() or item.get("sha256") != expected_hash:
            raise ValueError(f"prospective v2 design handoff {name} binding is invalid")
        if _sha256(root.resolve() / relative) != expected_hash:
            raise ValueError(f"prospective v2 design handoff {name} source drifted")
        if item.get("primary_gate_passed") is not True:
            raise ValueError(f"prospective v2 design handoff {name} gate did not pass")
    arms = _required_mapping(payload, "policy_arms")
    expected_specs = default_policy_specs()
    if set(arms) != set(expected_specs):
        raise ValueError("prospective v2 design handoff policy arm set is invalid")
    for role, spec in expected_specs.items():
        arm = _required_mapping(arms, role)
        manifest = policy_runtime_manifest(spec)
        expected = {
            "policy_spec": spec,
            "policy_spec_content_sha256": canonical_content_sha256(spec),
            "policy_runtime_manifest": manifest,
            "policy_runtime_content_sha256": canonical_content_sha256(manifest),
        }
        if canonical_content_sha256(arm) != canonical_content_sha256(expected):
            raise ValueError(f"prospective v2 design handoff {role} runtime binding is invalid")
    correction = _required_mapping(payload, "prospective_design_correction")
    if correction.get("existing_v5_candidate_episode_protocol_directly_compatible") is not False:
        raise ValueError("candidate-episode compatibility must be rejected")
    if correction.get("scientific_unit") != "independent_target_cohort_campaign_replicate":
        raise ValueError("prospective scientific unit is invalid")
    requirements = payload.get("required_external_inputs")
    if not isinstance(requirements, list | tuple) or len(requirements) != len(
        _REQUIRED_EXTERNAL_INPUTS
    ):
        raise ValueError("prospective external input requirements are incomplete")
    expected_ids = {item[0] for item in _REQUIRED_EXTERNAL_INPUTS}
    observed_ids = {
        item.get("input_id") for item in requirements if isinstance(item, Mapping)
    }
    if observed_ids != expected_ids or any(
        not isinstance(item, Mapping)
        or item.get("status") != "MISSING_EXTERNAL_INPUT"
        or item.get("agent_may_invent") is not False
        for item in requirements
    ):
        raise ValueError("prospective external input statuses are invalid")
    readiness = _required_mapping(payload, "readiness")
    if readiness.get("assay_protocol_registration_ready") is not False:
        raise ValueError("assay protocol registration cannot yet be ready")
    if readiness.get("external_input_blocker_count") != len(_REQUIRED_EXTERNAL_INPUTS):
        raise ValueError("prospective external input blocker count is invalid")


def verify_prospective_v2_design_handoff(
    payload: Mapping[str, Any],
    *,
    root: Path,
) -> dict[str, Any]:
    issues: list[str] = []
    try:
        validate_prospective_v2_design_handoff(payload, root=root)
    except (OSError, ValueError) as exc:
        issues.append(str(exc))
    return {
        "schema_version": PROSPECTIVE_V2_DESIGN_HANDOFF_VERIFICATION_SCHEMA,
        "verification_status": "PASS" if not issues else "FAIL",
        "source_handoff_content_sha256": canonical_content_sha256(payload),
        "issue_messages": issues,
        "assay_protocol_registration_ready": False,
        "execution_authority_granted": False,
        "autonomous_execution_permitted": False,
        "scientific_claim_upgrade_permitted": False,
    }


def write_immutable_json(path: Path, payload: Mapping[str, Any]) -> str:
    encoded = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")
    if path.exists():
        current = path.read_bytes()
        if current != encoded:
            raise FileExistsError(f"refusing to replace different immutable artifact: {path}")
        return sha256(current).hexdigest()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(encoded)
    return sha256(encoded).hexdigest()


def _read_frozen_evidence(
    path: Path,
    *,
    expected_sha256: str,
    expected_schema: str,
    expected_status: str,
    expected_decision: str,
) -> Mapping[str, Any]:
    if _sha256(path) != expected_sha256:
        raise ValueError(f"frozen development evidence drifted: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError(f"frozen development evidence is not an object: {path}")
    if (
        payload.get("schema_version") != expected_schema
        or payload.get("status") != expected_status
        or payload.get("decision") != expected_decision
        or payload.get("primary_gate_passed") is not True
    ):
        raise ValueError(f"frozen development evidence decision is invalid: {path}")
    return payload


def _sha256(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _parse_timestamp(value: str) -> datetime:
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("created_at must be timezone-aware ISO-8601") from exc
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("created_at must be timezone-aware ISO-8601")
    return result


def _required_mapping(payload: Mapping[str, Any], field: str) -> Mapping[str, Any]:
    value = payload.get(field)
    if not isinstance(value, Mapping):
        raise ValueError(f"{field} must be an object")
    return value


def _required_string(payload: Mapping[str, Any], field: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} must be a non-empty string")
    return value
