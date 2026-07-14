from __future__ import annotations

import json
import math
from collections.abc import Mapping
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from statistics import NormalDist
from typing import Any

from .artifact_hash import canonical_content_sha256, json_ready


PROSPECTIVE_LOOP_EVALUATION_POWER_PLAN_SCHEMA = (
    "prospective_loop_evaluation_power_plan.v3"
)
PROSPECTIVE_LOOP_EVALUATION_POWER_PLAN_VERIFICATION_SCHEMA = (
    "prospective_loop_evaluation_power_plan_verification.v1"
)
_POWER_METHOD = "normal_approximation_two_independent_proportions"
_PRIMARY_OUTCOME_METRIC_ID = "prospective_assay_validated_candidate_rate"
_PRIMARY_DENOMINATOR_KIND = "randomized_candidate_promotion_episodes"
_AUTHORITY_FIELDS = (
    "policy_ranking_permitted",
    "execution_authority_granted",
    "autonomous_execution_permitted",
    "scientific_claim_upgrade_permitted",
)


@dataclass(frozen=True)
class ProspectiveLoopEvaluationPowerPlanIssue:
    code: str
    location: str
    message: str


@dataclass(frozen=True)
class ProspectiveLoopEvaluationPowerPlanVerification:
    """Read-only consistency check for a generated power-plan artifact."""

    schema_version: str
    verification_status: str
    verified_at: str
    power_plan_id: str | None
    source_power_plan_content_sha256: str | None
    issue_codes: tuple[str, ...]
    issues: tuple[ProspectiveLoopEvaluationPowerPlanIssue, ...]
    policy_ranking_permitted: bool
    execution_authority_granted: bool
    autonomous_execution_permitted: bool
    scientific_claim_upgrade_permitted: bool
    summary_line: str

    def to_dict(self) -> dict[str, Any]:
        return json_ready(asdict(self))


@dataclass(frozen=True)
class ProspectiveLoopEvaluationPowerPlan:
    """A priori sample-size plan; it is not an outcome analysis or claim."""

    schema_version: str
    power_plan_id: str
    planned_at: str
    source_declaration_content_sha256: str
    target_population_id: str
    primary_outcome_metric_id: str
    primary_outcome_validation_oracle_kind: str
    primary_outcome_metric_key: str
    primary_outcome_positive_value: bool
    primary_outcome_denominator_kind: str
    calculation_method: str
    alternative: str
    alpha: float
    target_power: float
    baseline_assay_validated_rate: float
    minimum_absolute_rate_difference: float
    assumed_credibility_aware_rate: float
    required_randomized_episodes_per_arm: int
    planned_randomized_episodes_per_arm: int
    expected_assay_cost_per_episode: float
    required_per_arm_experiment_budget: float
    planned_per_arm_experiment_budget: float
    declared_per_arm_experiment_budget_limit: float
    planned_assay_site_id: str
    independent_replication_cohort_required: bool
    replication_cohort_id: str
    independent_assay_site_required: bool
    external_arm_randomization_required: bool
    human_acceptance: dict[str, Any]
    readiness_status: str
    protocol_design_feasible: bool
    issues: tuple[ProspectiveLoopEvaluationPowerPlanIssue, ...]
    policy_ranking_permitted: bool
    execution_authority_granted: bool
    autonomous_execution_permitted: bool
    scientific_claim_upgrade_permitted: bool
    summary_line: str

    def to_dict(self) -> dict[str, Any]:
        return json_ready(asdict(self))


class ProspectiveLoopEvaluationPowerPlanner:
    """Compute a transparent a priori two-proportion sample-size requirement."""

    def plan_path(
        self,
        declaration_path: str | Path,
        *,
        planned_at: str | None = None,
    ) -> ProspectiveLoopEvaluationPowerPlan:
        declaration = _read_object(Path(declaration_path))
        return self.plan(declaration, planned_at=planned_at)

    def plan(
        self,
        declaration: Mapping[str, Any],
        *,
        planned_at: str | None = None,
    ) -> ProspectiveLoopEvaluationPowerPlan:
        timestamp = planned_at or datetime.now(timezone.utc).isoformat()
        _parse_timestamp(timestamp, "planned_at")
        issues = _validate_declaration(declaration)
        if issues:
            raise ValueError(
                "Invalid prospective power-plan declaration: "
                + ", ".join(issue.code for issue in issues)
            )

        alpha = float(declaration["alpha"])
        target_power = float(declaration["target_power"])
        baseline_rate = float(declaration["baseline_assay_validated_rate"])
        difference = float(declaration["minimum_absolute_rate_difference"])
        assumed_rate = baseline_rate + difference
        required_n = _required_episodes_per_arm(
            baseline_rate,
            assumed_rate,
            alpha,
            target_power,
        )
        planned_n = int(declaration["planned_randomized_episodes_per_arm"])
        cost_per_episode = float(declaration["expected_assay_cost_per_episode"])
        budget_limit = float(declaration["declared_per_arm_experiment_budget_limit"])
        required_budget = required_n * cost_per_episode
        planned_budget = planned_n * cost_per_episode
        result_issues: list[ProspectiveLoopEvaluationPowerPlanIssue] = []
        if planned_n < required_n:
            result_issues.append(
                _issue(
                    "planned_episodes_below_required_power",
                    "$.planned_randomized_episodes_per_arm",
                )
            )
        if planned_budget > budget_limit:
            result_issues.append(
                _issue(
                    "planned_per_arm_experiment_budget_exceeded",
                    "$.declared_per_arm_experiment_budget_limit",
                )
            )
        if required_budget > budget_limit:
            result_issues.append(
                _issue(
                    "required_power_not_feasible_with_declared_budget",
                    "$.declared_per_arm_experiment_budget_limit",
                )
            )
        status = _readiness_status(result_issues)
        result = ProspectiveLoopEvaluationPowerPlan(
            schema_version=PROSPECTIVE_LOOP_EVALUATION_POWER_PLAN_SCHEMA,
            power_plan_id=str(declaration["power_plan_id"]),
            planned_at=timestamp,
            source_declaration_content_sha256=canonical_content_sha256(declaration),
            target_population_id=str(declaration["target_population_id"]),
            primary_outcome_metric_id=str(declaration["primary_outcome_metric_id"]),
            primary_outcome_validation_oracle_kind=str(
                declaration["primary_outcome_validation_oracle_kind"]
            ),
            primary_outcome_metric_key=str(declaration["primary_outcome_metric_key"]),
            primary_outcome_positive_value=bool(
                declaration["primary_outcome_positive_value"]
            ),
            primary_outcome_denominator_kind=str(
                declaration["primary_outcome_denominator_kind"]
            ),
            calculation_method=_POWER_METHOD,
            alternative="two_sided",
            alpha=alpha,
            target_power=target_power,
            baseline_assay_validated_rate=baseline_rate,
            minimum_absolute_rate_difference=difference,
            assumed_credibility_aware_rate=assumed_rate,
            required_randomized_episodes_per_arm=required_n,
            planned_randomized_episodes_per_arm=planned_n,
            expected_assay_cost_per_episode=cost_per_episode,
            required_per_arm_experiment_budget=required_budget,
            planned_per_arm_experiment_budget=planned_budget,
            declared_per_arm_experiment_budget_limit=budget_limit,
            planned_assay_site_id=str(declaration["planned_assay_site_id"]),
            independent_replication_cohort_required=True,
            replication_cohort_id=str(declaration["replication_cohort_id"]),
            independent_assay_site_required=True,
            external_arm_randomization_required=True,
            human_acceptance=dict(declaration["human_acceptance"]),
            readiness_status=status,
            protocol_design_feasible=status == "READY_FOR_PROTOCOL_REGISTRATION",
            issues=tuple(result_issues),
            policy_ranking_permitted=False,
            execution_authority_granted=False,
            autonomous_execution_permitted=False,
            scientific_claim_upgrade_permitted=False,
            summary_line="",
        )
        return replace(result, summary_line=_summary_line(result))


class ProspectiveLoopEvaluationPowerPlanVerifier:
    """Recompute power-plan arithmetic before a protocol can consume the plan."""

    def verify_path(
        self,
        power_plan_path: str | Path,
        *,
        verified_at: str | None = None,
    ) -> ProspectiveLoopEvaluationPowerPlanVerification:
        path = Path(power_plan_path)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except OSError:
            return self._verification(None, verified_at=verified_at, initial_issues=(
                _issue("power_plan_unreadable", "$.power_plan"),
            ))
        except json.JSONDecodeError:
            return self._verification(None, verified_at=verified_at, initial_issues=(
                _issue("power_plan_invalid_json", "$.power_plan"),
            ))
        if not isinstance(payload, Mapping):
            return self._verification(None, verified_at=verified_at, initial_issues=(
                _issue("power_plan_not_object", "$.power_plan"),
            ))
        return self._verification(payload, verified_at=verified_at, initial_issues=())

    def verify(
        self,
        power_plan: Mapping[str, Any],
        *,
        verified_at: str | None = None,
    ) -> ProspectiveLoopEvaluationPowerPlanVerification:
        return self._verification(power_plan, verified_at=verified_at, initial_issues=())

    def _verification(
        self,
        power_plan: Mapping[str, Any] | None,
        *,
        verified_at: str | None,
        initial_issues: tuple[ProspectiveLoopEvaluationPowerPlanIssue, ...],
    ) -> ProspectiveLoopEvaluationPowerPlanVerification:
        timestamp = verified_at or datetime.now(timezone.utc).isoformat()
        _parse_timestamp(timestamp, "verified_at")
        issues = list(initial_issues)
        if power_plan is None:
            issues.append(_issue("power_plan_unavailable", "$.power_plan"))
        else:
            issues.extend(power_plan_artifact_issues(power_plan))
        result = ProspectiveLoopEvaluationPowerPlanVerification(
            schema_version=PROSPECTIVE_LOOP_EVALUATION_POWER_PLAN_VERIFICATION_SCHEMA,
            verification_status="PASS" if not issues else "FAIL",
            verified_at=timestamp,
            power_plan_id=_nonempty_string(power_plan.get("power_plan_id")) if power_plan else None,
            source_power_plan_content_sha256=(
                canonical_content_sha256(power_plan) if power_plan is not None else None
            ),
            issue_codes=tuple(sorted({issue.code for issue in issues})),
            issues=tuple(issues),
            policy_ranking_permitted=False,
            execution_authority_granted=False,
            autonomous_execution_permitted=False,
            scientific_claim_upgrade_permitted=False,
            summary_line="",
        )
        return replace(result, summary_line=_verification_summary_line(result))


def power_plan_artifact_issues(
    value: Mapping[str, Any],
) -> list[ProspectiveLoopEvaluationPowerPlanIssue]:
    """Validate the self-contained arithmetic and authority invariants of a plan."""

    issues: list[ProspectiveLoopEvaluationPowerPlanIssue] = []
    if value.get("schema_version") != PROSPECTIVE_LOOP_EVALUATION_POWER_PLAN_SCHEMA:
        issues.append(_issue("power_plan_artifact_schema_invalid", "$.power_plan.schema_version"))
    if _nonempty_string(value.get("power_plan_id")) is None:
        issues.append(_issue("power_plan_artifact_id_missing", "$.power_plan.power_plan_id"))
    if _timestamp_or_none(value.get("planned_at")) is None:
        issues.append(_issue("power_plan_artifact_planned_at_invalid", "$.power_plan.planned_at"))
    declaration_hash = value.get("source_declaration_content_sha256")
    if not _sha256_or_none(declaration_hash):
        issues.append(_issue("power_plan_artifact_declaration_hash_invalid", "$.power_plan.source_declaration_content_sha256"))
    _validate_artifact_identity(value, issues)

    alpha = _probability(value.get("alpha"), exclusive=True)
    target_power = _probability(value.get("target_power"), exclusive=True)
    baseline = _probability(value.get("baseline_assay_validated_rate"), exclusive=True)
    difference = _positive_finite(value.get("minimum_absolute_rate_difference"))
    planned_n = _positive_integer(value.get("planned_randomized_episodes_per_arm"))
    cost = _positive_finite(value.get("expected_assay_cost_per_episode"))
    limit = _positive_finite(value.get("declared_per_arm_experiment_budget_limit"))
    if alpha is None:
        issues.append(_issue("power_plan_artifact_alpha_invalid", "$.power_plan.alpha"))
    if target_power is None:
        issues.append(_issue("power_plan_artifact_target_power_invalid", "$.power_plan.target_power"))
    if baseline is None:
        issues.append(_issue("power_plan_artifact_baseline_rate_invalid", "$.power_plan.baseline_assay_validated_rate"))
    if difference is None or (baseline is not None and baseline + difference >= 1):
        issues.append(_issue("power_plan_artifact_minimum_difference_invalid", "$.power_plan.minimum_absolute_rate_difference"))
    if planned_n is None:
        issues.append(_issue("power_plan_artifact_planned_episodes_invalid", "$.power_plan.planned_randomized_episodes_per_arm"))
    if cost is None:
        issues.append(_issue("power_plan_artifact_assay_cost_invalid", "$.power_plan.expected_assay_cost_per_episode"))
    if limit is None:
        issues.append(_issue("power_plan_artifact_budget_limit_invalid", "$.power_plan.declared_per_arm_experiment_budget_limit"))

    if None not in (alpha, target_power, baseline, difference, planned_n, cost, limit):
        assert alpha is not None and target_power is not None
        assert baseline is not None and difference is not None and planned_n is not None
        assert cost is not None and limit is not None
        assumed_rate = baseline + difference
        required_n = _required_episodes_per_arm(baseline, assumed_rate, alpha, target_power)
        required_budget = required_n * cost
        planned_budget = planned_n * cost
        if not _close(value.get("assumed_credibility_aware_rate"), assumed_rate):
            issues.append(_issue("power_plan_artifact_assumed_rate_mismatch", "$.power_plan.assumed_credibility_aware_rate"))
        if value.get("required_randomized_episodes_per_arm") != required_n:
            issues.append(_issue("power_plan_artifact_required_episodes_mismatch", "$.power_plan.required_randomized_episodes_per_arm"))
        if not _close(value.get("required_per_arm_experiment_budget"), required_budget):
            issues.append(_issue("power_plan_artifact_required_budget_mismatch", "$.power_plan.required_per_arm_experiment_budget"))
        if not _close(value.get("planned_per_arm_experiment_budget"), planned_budget):
            issues.append(_issue("power_plan_artifact_planned_budget_mismatch", "$.power_plan.planned_per_arm_experiment_budget"))
        result_codes: set[str] = set()
        if planned_n < required_n:
            result_codes.add("planned_episodes_below_required_power")
        if planned_budget > limit:
            result_codes.add("planned_per_arm_experiment_budget_exceeded")
        if required_budget > limit:
            result_codes.add("required_power_not_feasible_with_declared_budget")
        expected_status = _readiness_status_from_codes(result_codes)
        if value.get("readiness_status") != expected_status:
            issues.append(_issue("power_plan_artifact_readiness_status_mismatch", "$.power_plan.readiness_status"))
        if value.get("protocol_design_feasible") is not (expected_status == "READY_FOR_PROTOCOL_REGISTRATION"):
            issues.append(_issue("power_plan_artifact_design_feasible_mismatch", "$.power_plan.protocol_design_feasible"))
    _validate_artifact_controls(value, issues)
    return issues


def _validate_declaration(
    value: Mapping[str, Any],
) -> list[ProspectiveLoopEvaluationPowerPlanIssue]:
    issues: list[ProspectiveLoopEvaluationPowerPlanIssue] = []
    if _nonempty_string(value.get("power_plan_id")) is None:
        issues.append(_issue("power_plan_id_missing", "$.power_plan_id"))
    if _nonempty_string(value.get("target_population_id")) is None:
        issues.append(_issue("power_plan_target_population_missing", "$.target_population_id"))
    if value.get("primary_outcome_metric_id") != _PRIMARY_OUTCOME_METRIC_ID:
        issues.append(_issue("power_plan_primary_outcome_metric_invalid", "$.primary_outcome_metric_id"))
    if value.get("primary_outcome_validation_oracle_kind") != "assay":
        issues.append(_issue("power_plan_primary_outcome_oracle_invalid", "$.primary_outcome_validation_oracle_kind"))
    if _nonempty_string(value.get("primary_outcome_metric_key")) is None:
        issues.append(_issue("power_plan_primary_outcome_metric_key_missing", "$.primary_outcome_metric_key"))
    if not isinstance(value.get("primary_outcome_positive_value"), bool):
        issues.append(_issue("power_plan_primary_outcome_positive_value_invalid", "$.primary_outcome_positive_value"))
    if value.get("primary_outcome_denominator_kind") != _PRIMARY_DENOMINATOR_KIND:
        issues.append(_issue("power_plan_primary_outcome_denominator_invalid", "$.primary_outcome_denominator_kind"))
    if value.get("calculation_method") != _POWER_METHOD:
        issues.append(_issue("power_plan_method_invalid", "$.calculation_method"))
    if value.get("alternative") != "two_sided":
        issues.append(_issue("power_plan_alternative_invalid", "$.alternative"))
    alpha = _probability(value.get("alpha"), exclusive=True)
    power = _probability(value.get("target_power"), exclusive=True)
    baseline = _probability(value.get("baseline_assay_validated_rate"), exclusive=True)
    difference = _positive_finite(value.get("minimum_absolute_rate_difference"))
    if alpha is None:
        issues.append(_issue("power_plan_alpha_invalid", "$.alpha"))
    if power is None:
        issues.append(_issue("power_plan_target_power_invalid", "$.target_power"))
    if baseline is None:
        issues.append(_issue("power_plan_baseline_rate_invalid", "$.baseline_assay_validated_rate"))
    if difference is None or (baseline is not None and baseline + difference >= 1):
        issues.append(_issue("power_plan_minimum_difference_invalid", "$.minimum_absolute_rate_difference"))
    if _positive_integer(value.get("planned_randomized_episodes_per_arm")) is None:
        issues.append(_issue("power_plan_planned_episodes_invalid", "$.planned_randomized_episodes_per_arm"))
    if _positive_finite(value.get("expected_assay_cost_per_episode")) is None:
        issues.append(_issue("power_plan_expected_assay_cost_invalid", "$.expected_assay_cost_per_episode"))
    if _positive_finite(value.get("declared_per_arm_experiment_budget_limit")) is None:
        issues.append(_issue("power_plan_budget_limit_invalid", "$.declared_per_arm_experiment_budget_limit"))
    if _nonempty_string(value.get("planned_assay_site_id")) is None:
        issues.append(_issue("power_plan_assay_site_missing", "$.planned_assay_site_id"))
    if value.get("independent_replication_cohort_required") is not True:
        issues.append(_issue("power_plan_independent_replication_required", "$.independent_replication_cohort_required"))
    if _nonempty_string(value.get("replication_cohort_id")) is None:
        issues.append(_issue("power_plan_replication_cohort_id_missing", "$.replication_cohort_id"))
    if value.get("independent_assay_site_required") is not True:
        issues.append(_issue("power_plan_independent_assay_site_required", "$.independent_assay_site_required"))
    if value.get("external_arm_randomization_required") is not True:
        issues.append(_issue("power_plan_external_arm_randomization_required", "$.external_arm_randomization_required"))
    acceptance = value.get("human_acceptance")
    if not isinstance(acceptance, Mapping):
        issues.append(_issue("power_plan_human_acceptance_invalid", "$.human_acceptance"))
    else:
        if acceptance.get("decision") != "accept":
            issues.append(_issue("power_plan_human_acceptance_not_accepted", "$.human_acceptance.decision"))
        for field in ("reviewer", "rationale"):
            if _nonempty_string(acceptance.get(field)) is None:
                issues.append(_issue(f"power_plan_human_acceptance_{field}_missing", f"$.human_acceptance.{field}"))
        if _timestamp_or_none(acceptance.get("reviewed_at")) is None:
            issues.append(_issue("power_plan_human_acceptance_reviewed_at_invalid", "$.human_acceptance.reviewed_at"))
    for field in _AUTHORITY_FIELDS:
        if value.get(field) is not False:
            issues.append(_issue(f"power_plan_{field}_must_be_false", f"$.{field}"))
    return issues


def _required_episodes_per_arm(
    baseline_rate: float,
    credibility_aware_rate: float,
    alpha: float,
    target_power: float,
) -> int:
    normal = NormalDist()
    z_alpha = normal.inv_cdf(1 - alpha / 2)
    z_power = normal.inv_cdf(target_power)
    average_rate = (baseline_rate + credibility_aware_rate) / 2
    numerator = (
        z_alpha * math.sqrt(2 * average_rate * (1 - average_rate))
        + z_power
        * math.sqrt(
            baseline_rate * (1 - baseline_rate)
            + credibility_aware_rate * (1 - credibility_aware_rate)
        )
    ) ** 2
    return math.ceil(numerator / (credibility_aware_rate - baseline_rate) ** 2)


def _validate_artifact_identity(
    value: Mapping[str, Any],
    issues: list[ProspectiveLoopEvaluationPowerPlanIssue],
) -> None:
    if _nonempty_string(value.get("target_population_id")) is None:
        issues.append(_issue("power_plan_artifact_target_population_missing", "$.power_plan.target_population_id"))
    if value.get("primary_outcome_metric_id") != _PRIMARY_OUTCOME_METRIC_ID:
        issues.append(_issue("power_plan_artifact_primary_metric_invalid", "$.power_plan.primary_outcome_metric_id"))
    if value.get("primary_outcome_validation_oracle_kind") != "assay":
        issues.append(_issue("power_plan_artifact_primary_oracle_invalid", "$.power_plan.primary_outcome_validation_oracle_kind"))
    if _nonempty_string(value.get("primary_outcome_metric_key")) is None:
        issues.append(_issue("power_plan_artifact_primary_metric_key_missing", "$.power_plan.primary_outcome_metric_key"))
    if not isinstance(value.get("primary_outcome_positive_value"), bool):
        issues.append(_issue("power_plan_artifact_primary_positive_value_invalid", "$.power_plan.primary_outcome_positive_value"))
    if value.get("primary_outcome_denominator_kind") != _PRIMARY_DENOMINATOR_KIND:
        issues.append(_issue("power_plan_artifact_primary_denominator_invalid", "$.power_plan.primary_outcome_denominator_kind"))
    if value.get("calculation_method") != _POWER_METHOD:
        issues.append(_issue("power_plan_artifact_method_invalid", "$.power_plan.calculation_method"))
    if value.get("alternative") != "two_sided":
        issues.append(_issue("power_plan_artifact_alternative_invalid", "$.power_plan.alternative"))
    if _nonempty_string(value.get("planned_assay_site_id")) is None:
        issues.append(_issue("power_plan_artifact_assay_site_missing", "$.power_plan.planned_assay_site_id"))
    if _nonempty_string(value.get("replication_cohort_id")) is None:
        issues.append(_issue("power_plan_artifact_replication_cohort_missing", "$.power_plan.replication_cohort_id"))


def _validate_artifact_controls(
    value: Mapping[str, Any],
    issues: list[ProspectiveLoopEvaluationPowerPlanIssue],
) -> None:
    for field in (
        "independent_replication_cohort_required",
        "independent_assay_site_required",
        "external_arm_randomization_required",
    ):
        if value.get(field) is not True:
            issues.append(_issue(f"power_plan_artifact_{field}_must_be_true", f"$.power_plan.{field}"))
    acceptance = value.get("human_acceptance")
    if not isinstance(acceptance, Mapping) or acceptance.get("decision") != "accept":
        issues.append(_issue("power_plan_artifact_human_acceptance_invalid", "$.power_plan.human_acceptance"))
    else:
        for field in ("reviewer", "rationale"):
            if _nonempty_string(acceptance.get(field)) is None:
                issues.append(_issue(f"power_plan_artifact_human_acceptance_{field}_missing", f"$.power_plan.human_acceptance.{field}"))
        if _timestamp_or_none(acceptance.get("reviewed_at")) is None:
            issues.append(_issue("power_plan_artifact_human_acceptance_time_invalid", "$.power_plan.human_acceptance.reviewed_at"))
    for field in _AUTHORITY_FIELDS:
        if value.get(field) is not False:
            issues.append(_issue(f"power_plan_artifact_{field}_must_be_false", f"$.power_plan.{field}"))


def _readiness_status_from_codes(codes: set[str]) -> str:
    if "planned_per_arm_experiment_budget_exceeded" in codes:
        return "PLANNED_BUDGET_EXCEEDED"
    if "required_power_not_feasible_with_declared_budget" in codes:
        return "POWER_NOT_FEASIBLE_WITH_DECLARED_BUDGET"
    if "planned_episodes_below_required_power" in codes:
        return "UNDERPOWERED"
    return "READY_FOR_PROTOCOL_REGISTRATION"


def _readiness_status(
    issues: list[ProspectiveLoopEvaluationPowerPlanIssue],
) -> str:
    return _readiness_status_from_codes({issue.code for issue in issues})


def _read_object(path: Path) -> Mapping[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"Unable to read power-plan declaration: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON power-plan declaration: {path}") from exc
    if not isinstance(payload, Mapping):
        raise ValueError("Power-plan declaration must be a JSON object")
    return payload


def _probability(value: Any, *, exclusive: bool) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    numeric = float(value)
    if not math.isfinite(numeric):
        return None
    if exclusive and not 0 < numeric < 1:
        return None
    return numeric


def _positive_finite(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    numeric = float(value)
    return numeric if math.isfinite(numeric) and numeric > 0 else None


def _positive_integer(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return None
    return value


def _close(value: Any, expected: float) -> bool:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return False
    return math.isclose(float(value), expected, rel_tol=0.0, abs_tol=1e-9)


def _sha256_or_none(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value.lower())
    )


def _nonempty_string(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


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


def _issue(code: str, location: str) -> ProspectiveLoopEvaluationPowerPlanIssue:
    return ProspectiveLoopEvaluationPowerPlanIssue(
        code=code,
        location=location,
        message=code.replace("_", " ") + ".",
    )


def _summary_line(result: ProspectiveLoopEvaluationPowerPlan) -> str:
    return (
        "Prospective loop evaluation power plan: "
        f"status={result.readiness_status}; required_per_arm="
        f"{result.required_randomized_episodes_per_arm}; "
        "protocol_registration_authority=false; policy_ranking=false; "
        "scientific_claim_upgrade=false."
    )


def _verification_summary_line(result: ProspectiveLoopEvaluationPowerPlanVerification) -> str:
    return (
        "Prospective loop evaluation power-plan verification: "
        f"status={result.verification_status}; protocol_registration_authority=false; "
        "policy_ranking=false; scientific_claim_upgrade=false."
    )
