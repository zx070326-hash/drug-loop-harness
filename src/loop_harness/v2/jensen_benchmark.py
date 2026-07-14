"""Predeclared multi-cohort evaluation for the Jensen MF-BO fixture.

The benchmark reports seed-averaged, paired cohort summaries without treating
warm-start seeds as independent scientific replicates. It deliberately emits
no p-values, confidence intervals, or scientific efficacy conclusions.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from statistics import fmean, median

from .jensen_experiment import JensenPolicyComparisonRun, JensenPolicyComparisonRunner
from .stress import StressScenarioSpec


DEFAULT_TARGET_IDS = ("FACTOR_D", "CXCR4", "HIF_PH", "PARP1", "NR1A2")


@dataclass(frozen=True)
class JensenBenchmarkSpec:
    """Fixed experimental design for simulated-online policy evaluation."""

    target_ids: tuple[str, ...] = DEFAULT_TARGET_IDS
    candidate_limit: int = 64
    candidate_offsets: tuple[int, ...] = (0, 64, 128)
    warm_start_seeds: tuple[int, ...] = (17, 37, 59)
    total_budget: float = 12.0
    seed_per_oracle: int = 1
    top_fraction: float = 0.02
    policy_names: tuple[str, ...] = ("heuristic", "greedy", "jensen_tvr")
    baseline_policy: str = "heuristic"

    def validate(self) -> None:
        if not self.target_ids:
            raise ValueError("target_ids must not be empty")
        if self.candidate_limit <= 0 or self.total_budget <= 0:
            raise ValueError("candidate_limit and total_budget must be positive")
        if self.seed_per_oracle <= 0:
            raise ValueError("seed_per_oracle must be positive")
        if not 0.0 < self.top_fraction <= 1.0:
            raise ValueError("top_fraction must be in (0, 1]")
        if not self.candidate_offsets or any(offset < 0 for offset in self.candidate_offsets):
            raise ValueError("candidate_offsets must be non-empty and non-negative")
        if len(set(self.candidate_offsets)) != len(self.candidate_offsets):
            raise ValueError("candidate_offsets must be distinct")
        if not self.warm_start_seeds or len(set(self.warm_start_seeds)) != len(
            self.warm_start_seeds
        ):
            raise ValueError("warm_start_seeds must be non-empty and distinct")
        if len(set(self.policy_names)) != len(self.policy_names) or len(self.policy_names) < 2:
            raise ValueError("policy_names must contain at least two distinct policies")
        if self.baseline_policy not in self.policy_names:
            raise ValueError("baseline_policy must be included in policy_names")


@dataclass(frozen=True)
class JensenBenchmarkArm:
    cohort_id: str
    target_id: str
    candidate_offset: int
    warm_start_seed: int
    run: JensenPolicyComparisonRun
    stress_scenario_id: str | None = None


@dataclass(frozen=True)
class DescriptiveSummary:
    count: int
    mean: float | None
    median: float | None
    minimum: float | None
    maximum: float | None

    @classmethod
    def from_values(cls, values: list[float]) -> "DescriptiveSummary":
        if not values:
            return cls(0, None, None, None, None)
        return cls(
            count=len(values),
            mean=fmean(values),
            median=median(values),
            minimum=min(values),
            maximum=max(values),
        )


@dataclass(frozen=True)
class JensenPairedCohortSummary:
    cohort_id: str
    challenger_policy: str
    planned_seed_count: int
    valid_seed_count: int
    top_recovery_advantage: float | None
    simple_regret_reduction: float | None
    cost_delta: float | None


@dataclass(frozen=True)
class JensenPairwiseSummary:
    baseline_policy: str
    challenger_policy: str
    paired_cohort_count: int
    excluded_cohort_ids: tuple[str, ...]
    top_recovery_advantage: DescriptiveSummary
    simple_regret_reduction: DescriptiveSummary
    cost_delta: DescriptiveSummary


@dataclass(frozen=True)
class JensenBenchmarkResult:
    spec: JensenBenchmarkSpec
    arms: tuple[JensenBenchmarkArm, ...]
    paired_cohorts: tuple[JensenPairedCohortSummary, ...]
    pairwise_summaries: tuple[JensenPairwiseSummary, ...]
    inferential_conclusion_permitted: bool = False
    scientific_claim_upgrade_permitted: bool = False

    def to_dict(self) -> dict:
        payload = asdict(self)
        payload["analysis_boundary"] = (
            "Descriptive simulated-online benchmark only. Warm-start seeds are not "
            "independent biological replicates; no p-values, confidence intervals, "
            "or molecular efficacy claims are permitted."
        )
        return payload


class JensenBenchmarkRunner:
    """Run an immutable predeclared protocol over target-window cohorts."""

    def __init__(self, *, comparison_runner: JensenPolicyComparisonRunner | None = None) -> None:
        self.comparison_runner = comparison_runner or JensenPolicyComparisonRunner()

    def run(self, *, workspace: str, spec: JensenBenchmarkSpec) -> JensenBenchmarkResult:
        spec.validate()
        arms: list[JensenBenchmarkArm] = []
        for target_id in spec.target_ids:
            for candidate_offset in spec.candidate_offsets:
                cohort_id = f"{target_id}:offset:{candidate_offset}"
                for warm_start_seed in spec.warm_start_seeds:
                    runs = self.comparison_runner.run(
                        workspace=workspace,
                        target_id=target_id,
                        candidate_limit=spec.candidate_limit,
                        candidate_offset=candidate_offset,
                        total_budget=spec.total_budget,
                        continuation_steps=None,
                        seed_per_oracle=spec.seed_per_oracle,
                        seed=warm_start_seed,
                        policy_names=spec.policy_names,
                        top_fraction=spec.top_fraction,
                    )
                    arms.extend(
                        JensenBenchmarkArm(
                            cohort_id=cohort_id,
                            target_id=target_id,
                            candidate_offset=candidate_offset,
                            warm_start_seed=warm_start_seed,
                            run=run,
                        )
                        for run in runs
                    )
        paired_cohorts, summaries = _paired_summaries(arms, spec)
        return JensenBenchmarkResult(
            spec=spec,
            arms=tuple(arms),
            paired_cohorts=tuple(paired_cohorts),
            pairwise_summaries=tuple(summaries),
        )


@dataclass(frozen=True)
class JensenStressBenchmarkSpec:
    base: JensenBenchmarkSpec
    scenarios: tuple[StressScenarioSpec, ...]

    def validate(self) -> None:
        self.base.validate()
        if not self.scenarios:
            raise ValueError("at least one stress scenario is required")
        scenario_ids = [scenario.scenario_id for scenario in self.scenarios]
        if len(scenario_ids) != len(set(scenario_ids)):
            raise ValueError("stress scenario IDs must be distinct")


@dataclass(frozen=True)
class JensenStressPolicySummary:
    scenario_id: str
    policy_name: str
    total_arm_count: int
    valid_arm_count: int
    trace_pass_rate: float
    stress_pass_rate: float
    mean_top_recovery: float | None
    mean_simple_regret: float | None


@dataclass(frozen=True)
class JensenStressBenchmarkResult:
    spec: JensenStressBenchmarkSpec
    arms: tuple[JensenBenchmarkArm, ...]
    summaries: tuple[JensenStressPolicySummary, ...]
    inferential_conclusion_permitted: bool = False
    scientific_claim_upgrade_permitted: bool = False

    def to_dict(self) -> dict:
        payload = asdict(self)
        payload["spec"]["scenarios"] = [_public_scenario(scenario) for scenario in self.spec.scenarios]
        payload["analysis_boundary"] = (
            "Descriptive stress benchmark only. Scenario realization seeds are "
            "executor-owned; no p-values or molecular efficacy claims are permitted."
        )
        return payload


class JensenStressBenchmarkRunner:
    """Run paired policies across frozen stress scenarios and cohorts."""

    def __init__(self, *, comparison_runner: JensenPolicyComparisonRunner | None = None) -> None:
        self.comparison_runner = comparison_runner or JensenPolicyComparisonRunner()

    def run(self, *, workspace: str, spec: JensenStressBenchmarkSpec) -> JensenStressBenchmarkResult:
        spec.validate()
        arms: list[JensenBenchmarkArm] = []
        for scenario in spec.scenarios:
            for target_id in spec.base.target_ids:
                for candidate_offset in spec.base.candidate_offsets:
                    cohort_id = f"{target_id}:offset:{candidate_offset}"
                    for warm_start_seed in spec.base.warm_start_seeds:
                        runs = self.comparison_runner.run(
                            workspace=workspace,
                            target_id=target_id,
                            candidate_limit=spec.base.candidate_limit,
                            candidate_offset=candidate_offset,
                            total_budget=spec.base.total_budget,
                            continuation_steps=None,
                            seed_per_oracle=spec.base.seed_per_oracle,
                            seed=warm_start_seed,
                            policy_names=spec.base.policy_names,
                            top_fraction=spec.base.top_fraction,
                            stress_scenario=scenario,
                        )
                        arms.extend(
                            JensenBenchmarkArm(
                                cohort_id=cohort_id,
                                target_id=target_id,
                                candidate_offset=candidate_offset,
                                warm_start_seed=warm_start_seed,
                                run=run,
                                stress_scenario_id=scenario.scenario_id,
                            )
                            for run in runs
                        )
        return JensenStressBenchmarkResult(
            spec=spec,
            arms=tuple(arms),
            summaries=_stress_summaries(arms),
        )


def _paired_summaries(
    arms: list[JensenBenchmarkArm],
    spec: JensenBenchmarkSpec,
) -> tuple[list[JensenPairedCohortSummary], list[JensenPairwiseSummary]]:
    indexed: dict[str, dict[int, dict[str, JensenPolicyComparisonRun]]] = {}
    for arm in arms:
        indexed.setdefault(arm.cohort_id, {}).setdefault(arm.warm_start_seed, {})[
            arm.run.policy_name
        ] = arm.run

    cohort_rows: list[JensenPairedCohortSummary] = []
    summaries: list[JensenPairwiseSummary] = []
    for challenger in spec.policy_names:
        if challenger == spec.baseline_policy:
            continue
        challenger_rows: list[JensenPairedCohortSummary] = []
        excluded: list[str] = []
        for cohort_id, seed_runs in sorted(indexed.items()):
            pairs = []
            for seed in spec.warm_start_seeds:
                policy_runs = seed_runs.get(seed, {})
                baseline = policy_runs.get(spec.baseline_policy)
                contender = policy_runs.get(challenger)
                if not _valid_pair(baseline, contender):
                    continue
                pairs.append((baseline, contender))
            if len(pairs) != len(spec.warm_start_seeds):
                excluded.append(cohort_id)
                row = JensenPairedCohortSummary(
                    cohort_id=cohort_id,
                    challenger_policy=challenger,
                    planned_seed_count=len(spec.warm_start_seeds),
                    valid_seed_count=len(pairs),
                    top_recovery_advantage=None,
                    simple_regret_reduction=None,
                    cost_delta=None,
                )
            else:
                row = JensenPairedCohortSummary(
                    cohort_id=cohort_id,
                    challenger_policy=challenger,
                    planned_seed_count=len(spec.warm_start_seeds),
                    valid_seed_count=len(pairs),
                    top_recovery_advantage=fmean(
                        contender.metrics.top_recovery - baseline.metrics.top_recovery
                        for baseline, contender in pairs
                    ),
                    simple_regret_reduction=_average_simple_regret_reduction(pairs),
                    cost_delta=_zero_small(
                        fmean(
                            contender.metrics.cost_spent - baseline.metrics.cost_spent
                            for baseline, contender in pairs
                        )
                    ),
                )
            challenger_rows.append(row)
            cohort_rows.append(row)
        valid_rows = [row for row in challenger_rows if row.valid_seed_count == row.planned_seed_count]
        summaries.append(
            JensenPairwiseSummary(
                baseline_policy=spec.baseline_policy,
                challenger_policy=challenger,
                paired_cohort_count=len(valid_rows),
                excluded_cohort_ids=tuple(excluded),
                top_recovery_advantage=DescriptiveSummary.from_values(
                    [row.top_recovery_advantage for row in valid_rows if row.top_recovery_advantage is not None]
                ),
                simple_regret_reduction=DescriptiveSummary.from_values(
                    [row.simple_regret_reduction for row in valid_rows if row.simple_regret_reduction is not None]
                ),
                cost_delta=DescriptiveSummary.from_values(
                    [row.cost_delta for row in valid_rows if row.cost_delta is not None]
                ),
            )
        )
    return cohort_rows, summaries


def _valid_pair(
    baseline: JensenPolicyComparisonRun | None,
    challenger: JensenPolicyComparisonRun | None,
) -> bool:
    return bool(
        baseline
        and challenger
        and baseline.fixed_budget_comparison_permitted
        and challenger.fixed_budget_comparison_permitted
        and baseline.trace_status == "PASS"
        and challenger.trace_status == "PASS"
    )


def _stress_summaries(
    arms: list[JensenBenchmarkArm],
) -> tuple[JensenStressPolicySummary, ...]:
    grouped: dict[tuple[str, str], list[JensenBenchmarkArm]] = {}
    for arm in arms:
        if arm.stress_scenario_id is None:
            continue
        grouped.setdefault((arm.stress_scenario_id, arm.run.policy_name), []).append(arm)
    summaries = []
    for (scenario_id, policy_name), group in sorted(grouped.items()):
        valid = [
            arm
            for arm in group
            if arm.run.fixed_budget_comparison_permitted
            and arm.run.trace_status == "PASS"
            and arm.run.stress_status == "PASS"
        ]
        summaries.append(
            JensenStressPolicySummary(
                scenario_id=scenario_id,
                policy_name=policy_name,
                total_arm_count=len(group),
                valid_arm_count=len(valid),
                trace_pass_rate=sum(arm.run.trace_status == "PASS" for arm in group) / len(group),
                stress_pass_rate=sum(arm.run.stress_status == "PASS" for arm in group) / len(group),
                mean_top_recovery=(
                    fmean(arm.run.metrics.top_recovery for arm in valid) if valid else None
                ),
                mean_simple_regret=(
                    fmean(
                        arm.run.metrics.high_fidelity_simple_regret
                        for arm in valid
                        if arm.run.metrics.high_fidelity_simple_regret is not None
                    )
                    if any(
                        arm.run.metrics.high_fidelity_simple_regret is not None
                        for arm in valid
                    )
                    else None
                ),
            )
        )
    return tuple(summaries)


def _public_scenario(scenario: StressScenarioSpec) -> dict:
    return {
        "scenario_id": scenario.scenario_id,
        "scenario_version": scenario.scenario_version,
        "public_manifest_sha256": scenario.public_manifest_sha256,
        "failure_rate": scenario.failure_rate,
        "noise_scale": scenario.noise_scale,
        "scaffold_bias_scale": scenario.scaffold_bias_scale,
        "drift_per_attempt": scenario.drift_per_attempt,
        "group_count": scenario.group_count,
        "realization_seed_recorded": False,
    }


def _average_simple_regret_reduction(
    pairs: list[tuple[JensenPolicyComparisonRun, JensenPolicyComparisonRun]],
) -> float | None:
    values = [
        baseline.metrics.high_fidelity_simple_regret
        - contender.metrics.high_fidelity_simple_regret
        for baseline, contender in pairs
        if baseline.metrics.high_fidelity_simple_regret is not None
        and contender.metrics.high_fidelity_simple_regret is not None
    ]
    return fmean(values) if values else None


def _zero_small(value: float, *, tolerance: float = 1e-9) -> float:
    return 0.0 if abs(value) <= tolerance else value
