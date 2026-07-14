from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from pathlib import Path
from statistics import fmean
from typing import Mapping

from .continuous_belief import (
    ContinuousPosteriorFitter,
    PosteriorRepository,
    record_posterior_fit,
)
from .model_store import ContentAddressedArtifactStore
from .contracts import PosteriorSnapshot


@dataclass(frozen=True)
class PosteriorCalibrationMetrics:
    endpoint_id: str
    oracle_id: str
    candidate_count: int
    negative_log_predictive_density: float | None
    coverage_50: float | None
    coverage_80: float | None
    coverage_95: float | None
    spearman_rank_agreement: float | None
    claim_upgrade_permitted: bool = False


class PosteriorCalibrationEvaluator:
    """Read-only calibration evaluator against evaluator-owned continuous truth."""

    def __init__(self, repository: PosteriorRepository) -> None:
        self.repository = repository

    def evaluate(
        self,
        snapshot: PosteriorSnapshot,
        *,
        high_fidelity_oracle_id: str,
        truth: Mapping[str, float],
        exclude_candidate_ids: frozenset[str] = frozenset(),
    ) -> PosteriorCalibrationMetrics:
        predictions = {
            value.candidate_id: value
            for value in self.repository.values(snapshot)
            if (
                value.oracle_id == high_fidelity_oracle_id
                and value.candidate_id in truth
                and value.candidate_id not in exclude_candidate_ids
            )
        }
        rows = [
            (candidate_id, predictions[candidate_id].mean, predictions[candidate_id].variance, truth[candidate_id])
            for candidate_id in sorted(predictions)
        ]
        if not rows:
            return PosteriorCalibrationMetrics(
                endpoint_id=snapshot.endpoint_id,
                oracle_id=high_fidelity_oracle_id,
                candidate_count=0,
                negative_log_predictive_density=None,
                coverage_50=None,
                coverage_80=None,
                coverage_95=None,
                spearman_rank_agreement=None,
            )
        nll = sum(
            0.5
            * (
                math.log(2.0 * math.pi * max(variance, 1e-12))
                + ((actual - mean) ** 2) / max(variance, 1e-12)
            )
            for _, mean, variance, actual in rows
        ) / len(rows)
        return PosteriorCalibrationMetrics(
            endpoint_id=snapshot.endpoint_id,
            oracle_id=high_fidelity_oracle_id,
            candidate_count=len(rows),
            negative_log_predictive_density=nll,
            coverage_50=_coverage(rows, z=0.67448975),
            coverage_80=_coverage(rows, z=1.28155157),
            coverage_95=_coverage(rows, z=1.95996398),
            spearman_rank_agreement=_spearman_rank_agreement(rows),
        )


@dataclass(frozen=True)
class JensenCalibrationSpec:
    """Predeclared cohort design for posterior calibration snapshots."""

    target_ids: tuple[str, ...] = ("FACTOR_D",)
    candidate_limit: int = 64
    candidate_offsets: tuple[int, ...] = (0,)
    warm_start_seeds: tuple[int, ...] = (17, 37, 59)
    seed_per_oracle: int = 1
    continuation_steps: int = 2
    total_budget: float = 12.0

    def validate(self) -> None:
        if not self.target_ids or self.candidate_limit <= 0:
            raise ValueError("target_ids and candidate_limit are required")
        if not self.candidate_offsets or any(offset < 0 for offset in self.candidate_offsets):
            raise ValueError("candidate_offsets must be non-empty and non-negative")
        if len(set(self.candidate_offsets)) != len(self.candidate_offsets):
            raise ValueError("candidate_offsets must be distinct")
        if self.seed_per_oracle <= 0:
            raise ValueError("seed_per_oracle must be positive")
        if not self.warm_start_seeds or len(set(self.warm_start_seeds)) != len(self.warm_start_seeds):
            raise ValueError("warm_start_seeds must be distinct and non-empty")
        if self.continuation_steps <= 0 or self.total_budget <= 0:
            raise ValueError("continuation_steps and total_budget must be positive")


@dataclass(frozen=True)
class JensenCalibrationRow:
    target_id: str
    candidate_offset: int
    warm_start_seed: int
    snapshot_label: str
    observation_count: int
    round_index: int
    metrics: PosteriorCalibrationMetrics


@dataclass(frozen=True)
class JensenCalibrationCurvePoint:
    observation_count: int
    cohort_count: int
    negative_log_predictive_density_mean: float | None
    coverage_50_mean: float | None
    coverage_80_mean: float | None
    coverage_95_mean: float | None
    spearman_rank_agreement_mean: float | None


@dataclass(frozen=True)
class JensenCalibrationResult:
    spec: JensenCalibrationSpec
    rows: tuple[JensenCalibrationRow, ...]
    curve: tuple[JensenCalibrationCurvePoint, ...]
    analysis_boundary: str = (
        "Calibration diagnostics over retrospective Jensen cohorts only. "
        "No molecular efficacy, prospective success, or policy superiority claim is permitted."
    )

    def to_dict(self) -> dict:
        return asdict(self)


class JensenCalibrationRunner:
    """Replay fixed cohorts and emit artifact-backed posterior calibration traces."""

    def run(self, *, workspace: str, artifact_root: str | Path, spec: JensenCalibrationSpec) -> JensenCalibrationResult:
        spec.validate()
        from .jensen_mfbo_fixture import JensenMFBOFixtureLoader, load_jensen_high_fidelity_truth
        from .jensen_experiment import apply_common_warm_start
        from .mfbo_policy import JensenStyleTVRPolicy
        from .runtime import LoopRuntime
        from .state import StateProjector

        rows: list[JensenCalibrationRow] = []
        root = Path(artifact_root)
        for target_id in spec.target_ids:
            for candidate_offset in spec.candidate_offsets:
                for seed in spec.warm_start_seeds:
                    fixture = JensenMFBOFixtureLoader().load(
                        workspace=workspace,
                        target_id=target_id,
                        candidate_limit=spec.candidate_limit,
                        candidate_offset=candidate_offset,
                        total_budget=spec.total_budget,
                        max_rounds=spec.seed_per_oracle * 3 + spec.continuation_steps + 1,
                    )
                    apply_common_warm_start(
                        fixture,
                        seed_per_oracle=spec.seed_per_oracle,
                        seed=seed,
                    )
                    cohort_root = root / f"{fixture.target_id.lower()}-{candidate_offset}-{seed}"
                    store = ContentAddressedArtifactStore(cohort_root)
                    fitter = ContinuousPosteriorFitter()
                    projector = StateProjector()
                    state = projector.project(fixture.ledger)
                    posterior = fitter.fit(state, store, endpoint_id=state.spec.primary_endpoint)
                    record_posterior_fit(fixture.ledger, posterior)
                    normalized_target, _, truth = load_jensen_high_fidelity_truth(
                        workspace=workspace,
                        target_id=target_id,
                        candidate_limit=spec.candidate_limit,
                        candidate_offset=candidate_offset,
                    )
                    evaluator = PosteriorCalibrationEvaluator(PosteriorRepository(store))
                    _append_calibration_row(
                        rows,
                        evaluator=evaluator,
                        snapshot=posterior,
                        state=projector.project(fixture.ledger),
                        truth=truth,
                        excluded_candidate_ids=frozenset(
                            candidate_id
                            for candidate_id, oracle_id in state.results
                            if oracle_id == "dose_response"
                        ),
                        target_id=normalized_target,
                        candidate_offset=candidate_offset,
                        warm_start_seed=seed,
                        snapshot_label="warm_start",
                    )
                    policy = JensenStyleTVRPolicy(posterior_repository=PosteriorRepository(store))
                    runtime = LoopRuntime(
                        fixture.executor,
                        policy=policy,
                        posterior_fitter=fitter,
                        posterior_store=store,
                        posterior_endpoint_ids=(state.spec.primary_endpoint,),
                    )
                    for step_index in range(spec.continuation_steps):
                        step = runtime.step(fixture.ledger)
                        if step.status != "completed":
                            break
                        state = projector.project(fixture.ledger)
                        snapshot_id = state.active_posterior_snapshot_ids.get(state.spec.primary_endpoint)
                        if snapshot_id is None:
                            raise RuntimeError("calibration run lost its active posterior snapshot")
                        _append_calibration_row(
                            rows,
                            evaluator=evaluator,
                            snapshot=state.posterior_snapshots[snapshot_id],
                            state=state,
                            truth=truth,
                            excluded_candidate_ids=frozenset(
                                candidate_id
                                for candidate_id, oracle_id in state.results
                                if oracle_id == "dose_response"
                            ),
                            target_id=normalized_target,
                            candidate_offset=candidate_offset,
                            warm_start_seed=seed,
                            snapshot_label=f"continuation_{step_index + 1}",
                        )
        return JensenCalibrationResult(
            spec=spec,
            rows=tuple(rows),
            curve=_calibration_curve(rows),
        )


def _append_calibration_row(
    rows: list[JensenCalibrationRow],
    *,
    evaluator: PosteriorCalibrationEvaluator,
    snapshot: PosteriorSnapshot,
    state,
    truth: Mapping[str, float],
    excluded_candidate_ids: frozenset[str],
    target_id: str,
    candidate_offset: int,
    warm_start_seed: int,
    snapshot_label: str,
) -> None:
    metrics = evaluator.evaluate(
        snapshot,
        high_fidelity_oracle_id="dose_response",
        truth=truth,
        exclude_candidate_ids=excluded_candidate_ids,
    )
    rows.append(
        JensenCalibrationRow(
            target_id=target_id,
            candidate_offset=candidate_offset,
            warm_start_seed=warm_start_seed,
            snapshot_label=snapshot_label,
            observation_count=len(snapshot.training_observation_ids),
            round_index=state.round_index,
            metrics=metrics,
        )
    )


def _calibration_curve(rows: list[JensenCalibrationRow]) -> tuple[JensenCalibrationCurvePoint, ...]:
    points: list[JensenCalibrationCurvePoint] = []
    for observation_count in sorted({row.observation_count for row in rows}):
        cohort_rows = [row.metrics for row in rows if row.observation_count == observation_count]
        points.append(
            JensenCalibrationCurvePoint(
                observation_count=observation_count,
                cohort_count=len(cohort_rows),
                negative_log_predictive_density_mean=_mean_metric(
                    cohort_rows, "negative_log_predictive_density"
                ),
                coverage_50_mean=_mean_metric(cohort_rows, "coverage_50"),
                coverage_80_mean=_mean_metric(cohort_rows, "coverage_80"),
                coverage_95_mean=_mean_metric(cohort_rows, "coverage_95"),
                spearman_rank_agreement_mean=_mean_metric(
                    cohort_rows, "spearman_rank_agreement"
                ),
            )
        )
    return tuple(points)


def _mean_metric(metrics: list[PosteriorCalibrationMetrics], name: str) -> float | None:
    values = [getattr(metric, name) for metric in metrics]
    values = [float(value) for value in values if value is not None]
    return fmean(values) if values else None


def _coverage(rows, *, z: float) -> float:
    covered = sum(
        mean - z * math.sqrt(max(variance, 1e-12))
        <= actual
        <= mean + z * math.sqrt(max(variance, 1e-12))
        for _, mean, variance, actual in rows
    )
    return covered / len(rows)


def _spearman_rank_agreement(rows) -> float | None:
    if len(rows) < 2:
        return None
    predicted = _ranks([row[1] for row in rows])
    actual = _ranks([row[3] for row in rows])
    mean_pred = sum(predicted) / len(predicted)
    mean_actual = sum(actual) / len(actual)
    numerator = sum((left - mean_pred) * (right - mean_actual) for left, right in zip(predicted, actual))
    denominator_left = math.sqrt(sum((value - mean_pred) ** 2 for value in predicted))
    denominator_right = math.sqrt(sum((value - mean_actual) ** 2 for value in actual))
    if denominator_left == 0 or denominator_right == 0:
        return 0.0
    return numerator / (denominator_left * denominator_right)


def _ranks(values: list[float]) -> list[float]:
    ordered = sorted(enumerate(values), key=lambda item: (item[1], item[0]))
    ranks = [0.0] * len(values)
    index = 0
    while index < len(ordered):
        end = index + 1
        while end < len(ordered) and ordered[end][1] == ordered[index][1]:
            end += 1
        rank = (index + end - 1) / 2.0 + 1.0
        for position in range(index, end):
            ranks[ordered[position][0]] = rank
        index = end
    return ranks
