from __future__ import annotations

import math
from collections.abc import Mapping
from hashlib import sha256
from pathlib import Path
from typing import Any

from .artifact_hash import canonical_content_sha256
from .contracts import ActionKind, PromotionDecision
from . import contracts, policies
from .policies import FixedCascadePolicy, TrustGapAwarePolicy


PROSPECTIVE_POLICY_RUNTIME_MANIFEST_SCHEMA = "prospective_policy_runtime_manifest.v1"
PROSPECTIVE_POLICY_RUNTIME_CONTRACT_ID = "prospective_policy_runtime.v1"


def policy_from_spec(policy_spec: Mapping[str, Any]) -> FixedCascadePolicy | TrustGapAwarePolicy:
    """Instantiate only policy semantics that the prospective harness can replay."""
    kind = _string_or_none(policy_spec.get("policy_kind"))
    parameters = policy_spec.get("parameters")
    if not isinstance(parameters, Mapping):
        raise ValueError("parameters_missing")
    if kind == "fixed_cascade":
        cascade = parameters.get("cascade")
        if not isinstance(cascade, list | tuple) or not cascade:
            raise ValueError("fixed_cascade_missing")
        try:
            return FixedCascadePolicy(tuple(ActionKind(item) for item in cascade))
        except ValueError as exc:
            raise ValueError("fixed_cascade_action_invalid") from exc
    if kind == "trust_gap_aware":
        allowed = {"max_actions", "min_multiplier", "min_value_per_cost"}
        if set(parameters) - allowed:
            raise ValueError("trust_gap_aware_parameter_not_implemented")
        max_actions = parameters.get("max_actions")
        if max_actions is not None and (
            isinstance(max_actions, bool)
            or not isinstance(max_actions, int)
            or max_actions <= 0
        ):
            raise ValueError("trust_gap_aware_max_actions_invalid")
        min_multiplier = parameters.get("min_multiplier", 0.05)
        min_value_per_cost = parameters.get("min_value_per_cost", 0.0)
        if not _finite_number(min_multiplier) or not _finite_number(min_value_per_cost):
            raise ValueError("trust_gap_aware_parameter_invalid")
        return TrustGapAwarePolicy(
            max_actions=max_actions,
            min_multiplier=float(min_multiplier),
            min_value_per_cost=float(min_value_per_cost),
        )
    raise ValueError("policy_kind_not_implemented")


def policy_runtime_manifest(policy_spec: Mapping[str, Any]) -> dict[str, Any]:
    """Identify the exact local source semantics used to replay one policy spec."""
    policy = policy_from_spec(policy_spec)
    kind = _string_or_none(policy_spec.get("policy_kind"))
    return {
        "schema_version": PROSPECTIVE_POLICY_RUNTIME_MANIFEST_SCHEMA,
        "runtime_contract_id": PROSPECTIVE_POLICY_RUNTIME_CONTRACT_ID,
        "policy_kind": kind,
        "implementation_class": type(policy).__name__,
        "source_modules": {
            "contracts": _module_content_sha256(contracts),
            "policies": _module_content_sha256(policies),
            "prospective_policy_runtime": _module_content_sha256(__import__(__name__, fromlist=["*"])),
        },
    }


def policy_runtime_content_sha256(policy_spec: Mapping[str, Any]) -> str:
    return canonical_content_sha256(policy_runtime_manifest(policy_spec))


def decision_semantic(decision: PromotionDecision) -> dict[str, Any]:
    """Stable decision representation for recorded policy-output commitments."""
    return {
        "subject_id": decision.subject_id,
        "from_oracle": decision.from_oracle,
        "to_action": decision.to_action.value,
        "expected_value": decision.expected_value,
        "expected_cost": decision.expected_cost,
        "credibility_reason": decision.credibility_reason,
        "required_human_approval": decision.required_human_approval,
    }


def policy_output_semantics(
    policy: FixedCascadePolicy | TrustGapAwarePolicy,
    state: Any,
) -> tuple[dict[str, Any], ...]:
    return tuple(decision_semantic(decision) for decision in policy.plan(state).decisions)


def _finite_number(value: Any) -> bool:
    return not isinstance(value, bool) and isinstance(value, int | float) and math.isfinite(float(value))


def _string_or_none(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _module_content_sha256(module: Any) -> str:
    source_path = getattr(module, "__file__", None)
    if not isinstance(source_path, str):
        raise ValueError("policy_runtime_source_unavailable")
    try:
        return sha256(Path(source_path).read_bytes()).hexdigest()
    except OSError as exc:
        raise ValueError("policy_runtime_source_unavailable") from exc
