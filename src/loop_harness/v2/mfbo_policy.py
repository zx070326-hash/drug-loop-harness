from __future__ import annotations

import math
from collections import defaultdict

from .contracts import ActionScore, AcquisitionAction, EvidenceDisposition, OutcomeDirection
from .state import CampaignSnapshot


class JensenStyleTVRPolicy:
    """ICM-Tanimoto GP plus targeted variance reduction per oracle cost.

    This is a dependency-light reference implementation of the decision rule in
    McDonald et al. It deliberately does not claim to be the authors' BayBE /
    BoTorch implementation: covariance fitting, warm starts, and batching are
    kept explicit and deterministic for harness-level comparisons.
    """

    policy_version = "jensen_style_tvr_numpy_icm_v1_not_original_baybe"

    def __init__(
        self,
        *,
        min_observations_per_oracle: int = 1,
        fingerprint_bits: int = 256,
        noise: float = 1e-4,
        default_task_correlation: float = 0.5,
        posterior_repository=None,
        legacy_reference_indexing: bool = False,
    ) -> None:
        if min_observations_per_oracle <= 0 or fingerprint_bits <= 0:
            raise ValueError("positive warm-start count and fingerprint_bits required")
        if noise <= 0 or not 0.0 <= default_task_correlation < 1.0:
            raise ValueError("invalid GP regularization parameters")
        self.min_observations_per_oracle = min_observations_per_oracle
        self.fingerprint_bits = fingerprint_bits
        self.noise = noise
        self.default_task_correlation = default_task_correlation
        self.posterior_repository = posterior_repository
        self.legacy_reference_indexing = legacy_reference_indexing
        if legacy_reference_indexing:
            self.policy_version = "jensen_style_tvr_numpy_legacy_reference_indexing_v1"
        self._fingerprint_cache: dict[tuple[str, str], object] = {}

    def rank(
        self,
        state: CampaignSnapshot,
        actions: tuple[AcquisitionAction, ...],
    ) -> tuple[ActionScore, ...]:
        if not actions:
            return ()
        if self.legacy_reference_indexing:
            actions = tuple(
                sorted(
                    actions,
                    key=lambda action: (
                        action.candidate_id,
                        -state.oracles[action.oracle_id].expected_cost,
                        action.oracle_id,
                    ),
                )
            )
        endpoint_id = _single_endpoint(actions, state)
        oracle_ids = tuple(sorted({action.oracle_id for action in actions}))
        observations = _continuous_observations(state, endpoint_id)
        snapshot_id = state.active_posterior_snapshot_ids.get(endpoint_id)
        if self.posterior_repository is not None and snapshot_id is not None:
            snapshot = state.posterior_snapshots[snapshot_id]
            return self._snapshot_tvr_scores(state, actions, snapshot, endpoint_id)
        counts = {oracle_id: 0 for oracle_id in oracle_ids}
        for _, oracle_id, _ in observations:
            if oracle_id in counts:
                counts[oracle_id] += 1
        missing = tuple(
            oracle_id
            for oracle_id in oracle_ids
            if counts[oracle_id] < self.min_observations_per_oracle
        )
        if missing:
            return self._warm_start_scores(actions, missing)
        return self._tvr_scores(state, actions, observations, endpoint_id)

    def _snapshot_tvr_scores(
        self,
        state: CampaignSnapshot,
        actions: tuple[AcquisitionAction, ...],
        snapshot,
        endpoint_id: str,
    ) -> tuple[ActionScore, ...]:
        import numpy as np

        values = {
            (value.candidate_id, value.oracle_id): value
            for value in self.posterior_repository.values(snapshot)
        }
        action_order, covariance = self.posterior_repository.matrix(snapshot)
        matrix_index = {key: index for index, key in enumerate(action_order)}
        try:
            indices = [matrix_index[(action.candidate_id, action.oracle_id)] for action in actions]
            means = np.asarray(
                [values[(action.candidate_id, action.oracle_id)].mean for action in actions],
                dtype=float,
            )
            variances = np.asarray(
                [values[(action.candidate_id, action.oracle_id)].variance for action in actions],
                dtype=float,
            )
        except KeyError as exc:
            raise ValueError("posterior snapshot does not cover legal actions") from exc
        covariance = covariance[np.ix_(indices, indices)]
        high_oracle = max(
            {action.oracle_id for action in actions},
            key=lambda oracle_id: state.oracles[oracle_id].expected_cost,
        )
        high_indices = [
            index for index, action in enumerate(actions) if action.oracle_id == high_oracle
        ]
        best_observed_high = max(
            (
                result.outcome.value
                for (candidate_id, oracle_id), result in state.results.items()
                if oracle_id == high_oracle
                and result.outcome is not None
                and result.outcome.value is not None
            ),
            default=float(np.max(means[high_indices])),
        )
        high_ei = _expected_improvement(
            means[high_indices],
            variances[high_indices],
            best_observed_high,
        )
        target_local_index = int(np.argmax(high_ei))
        target_index = (
            target_local_index
            if self.legacy_reference_indexing
            else high_indices[target_local_index]
        )
        target_ei = float(high_ei[target_local_index])
        target_variance = max(float(variances[target_index]), self.noise)
        covariance_to_target = covariance[:, target_index]
        tvr = np.square(covariance_to_target) / (
            np.maximum(variances, self.noise) * target_variance
        )
        return _sorted(
            [
                ActionScore(
                    action=action,
                    information_gain=float(tvr[index]),
                    decision_value=target_ei,
                    cost=action.expected_cost,
                    priority=float(tvr[index]) / max(action.expected_cost, 1e-12),
                    rationale=(
                        "verified_posterior_tvr_per_cost; "
                        f"legacy_reference_indexing={self.legacy_reference_indexing}; "
                        f"target_high_fidelity_action={actions[target_index].action_id}; "
                        f"endpoint={endpoint_id}"
                    ),
                )
                for index, action in enumerate(actions)
            ]
        )

    def _warm_start_scores(
        self,
        actions: tuple[AcquisitionAction, ...],
        missing_oracles: tuple[str, ...],
    ) -> tuple[ActionScore, ...]:
        scores = []
        for action in actions:
            if action.oracle_id not in missing_oracles:
                continue
            # Equal priority gives a deterministic candidate/oracle stratified seed.
            scores.append(
                ActionScore(
                    action=action,
                    information_gain=1.0,
                    decision_value=0.0,
                    cost=action.expected_cost,
                    priority=1.0,
                    rationale="stratified_multifidelity_warm_start",
                )
            )
        return _sorted(scores)

    def _tvr_scores(
        self,
        state: CampaignSnapshot,
        actions: tuple[AcquisitionAction, ...],
        observations: list[tuple[str, str, float]],
        endpoint_id: str,
    ) -> tuple[ActionScore, ...]:
        try:
            import numpy as np
        except ImportError as exc:  # pragma: no cover - environment boundary
            raise RuntimeError("NumPy is required for JensenStyleTVRPolicy") from exc

        task_ids = tuple(sorted(state.oracles))
        task_index = {oracle_id: index for index, oracle_id in enumerate(task_ids)}
        train_candidates = [candidate_id for candidate_id, _, _ in observations]
        train_tasks = [task_index[oracle_id] for _, oracle_id, _ in observations]
        y = np.asarray([value for _, _, value in observations], dtype=float)
        candidate_features = self._fingerprints(state)
        x_train = np.vstack([candidate_features[candidate_id] for candidate_id in train_candidates])
        task_covariance = _task_covariance(
            observations,
            task_ids,
            default_correlation=self.default_task_correlation,
        )
        model = _ICMTanimotoPosterior(
            x_train=x_train,
            task_train=np.asarray(train_tasks, dtype=int),
            y=y,
            task_covariance=task_covariance,
            noise=self.noise,
        )

        x_actions = np.vstack([candidate_features[action.candidate_id] for action in actions])
        action_tasks = np.asarray([task_index[action.oracle_id] for action in actions], dtype=int)
        _, covariance = model.predict(x_actions, action_tasks, covariance=True)
        variance = np.maximum(np.diag(covariance), self.noise)

        high_oracle = max(
            {action.oracle_id for action in actions},
            key=lambda oracle_id: state.oracles[oracle_id].expected_cost,
        )
        high_indices = [
            index for index, action in enumerate(actions) if action.oracle_id == high_oracle
        ]
        if not high_indices:
            return self._warm_start_scores(actions, (high_oracle,))
        high_mean, high_variance = model.predict(
            x_actions[high_indices],
            action_tasks[high_indices],
            covariance=False,
        )
        best_observed_high = max(
            (value for _, oracle_id, value in observations if oracle_id == high_oracle),
            default=float(y.max()),
        )
        high_ei = _expected_improvement(high_mean, high_variance, best_observed_high)
        target_local_index = int(np.argmax(high_ei))
        target_index = (
            target_local_index
            if self.legacy_reference_indexing
            else high_indices[target_local_index]
        )
        target_ei = float(high_ei[target_local_index])
        target_variance = variance[target_index]
        covariance_to_target = covariance[:, target_index]
        tvr = np.square(covariance_to_target) / (variance * target_variance)
        scores = [
            ActionScore(
                action=action,
                information_gain=float(tvr[index]),
                decision_value=target_ei,
                cost=action.expected_cost,
                priority=float(tvr[index]) / max(action.expected_cost, 1e-12),
                rationale=(
                    "tanimoto_icm_targeted_variance_reduction_per_cost; "
                    f"legacy_reference_indexing={self.legacy_reference_indexing}; "
                    f"target_high_fidelity_action={actions[target_index].action_id}; "
                    f"endpoint={endpoint_id}"
                ),
            )
            for index, action in enumerate(actions)
        ]
        return _sorted(scores)

    def _fingerprints(self, state: CampaignSnapshot) -> dict[str, object]:
        try:
            import numpy as np
            from rdkit import Chem, DataStructs
            from rdkit.Chem import rdFingerprintGenerator
        except ImportError as exc:  # pragma: no cover - environment boundary
            raise RuntimeError("RDKit and NumPy are required for JensenStyleTVRPolicy") from exc
        generator = rdFingerprintGenerator.GetMorganGenerator(radius=2, fpSize=self.fingerprint_bits)
        features: dict[str, object] = {}
        for candidate_id, candidate in state.candidates.items():
            key = (candidate_id, candidate.smiles)
            fingerprint = self._fingerprint_cache.get(key)
            if fingerprint is None:
                molecule = Chem.MolFromSmiles(candidate.smiles)
                if molecule is None:
                    raise ValueError(f"invalid SMILES for candidate {candidate_id}")
                fingerprint = np.zeros(self.fingerprint_bits, dtype=float)
                DataStructs.ConvertToNumpyArray(generator.GetFingerprint(molecule), fingerprint)
                self._fingerprint_cache[key] = fingerprint
            features[candidate_id] = fingerprint
        return features


class _ICMTanimotoPosterior:
    def __init__(self, *, x_train, task_train, y, task_covariance, noise: float) -> None:
        import numpy as np

        self.x_train = x_train
        self.task_train = task_train
        self.task_covariance = task_covariance
        self.y_mean = float(y.mean())
        self.y = y - self.y_mean
        kernel = _icm_kernel(x_train, task_train, x_train, task_train, task_covariance)
        kernel += np.eye(len(y)) * noise
        self.cholesky = np.linalg.cholesky(kernel)
        self.alpha = np.linalg.solve(self.cholesky.T, np.linalg.solve(self.cholesky, self.y))

    def predict(self, x_test, task_test, *, covariance: bool):
        import numpy as np

        cross = _icm_kernel(x_test, task_test, self.x_train, self.task_train, self.task_covariance)
        mean = self.y_mean + cross @ self.alpha
        if not covariance:
            diagonal = _icm_kernel_diagonal(task_test, self.task_covariance)
            projected = np.sum(np.linalg.solve(self.cholesky, cross.T) ** 2, axis=0)
            return mean, np.maximum(diagonal - projected, 1e-12)
        prior = _icm_kernel(x_test, task_test, x_test, task_test, self.task_covariance)
        projected = np.linalg.solve(self.cholesky, cross.T)
        posterior = prior - projected.T @ projected
        posterior = (posterior + posterior.T) / 2.0
        posterior += np.eye(len(x_test)) * 1e-12
        return mean, posterior


def _continuous_observations(
    state: CampaignSnapshot,
    endpoint_id: str,
) -> list[tuple[str, str, float]]:
    rows = []
    for (candidate_id, oracle_id), result in sorted(state.results.items()):
        assessment = state.assessments.get((candidate_id, oracle_id))
        outcome = result.outcome
        if (
            assessment is None
            or assessment.disposition is not EvidenceDisposition.QUALIFIED
            or outcome is None
            or outcome.endpoint_id != endpoint_id
            or outcome.direction is not OutcomeDirection.CONTINUOUS
            or outcome.value is None
        ):
            continue
        rows.append((candidate_id, oracle_id, outcome.value))
    return rows


def _single_endpoint(actions: tuple[AcquisitionAction, ...], state: CampaignSnapshot) -> str:
    endpoints = {action.endpoint_id or state.spec.primary_endpoint for action in actions}
    if len(endpoints) != 1:
        raise ValueError("JensenStyleTVRPolicy only supports one endpoint per campaign step")
    return next(iter(endpoints))


def _task_covariance(observations, task_ids, *, default_correlation: float):
    import numpy as np

    by_task: dict[str, dict[str, float]] = defaultdict(dict)
    for candidate_id, oracle_id, value in observations:
        by_task[oracle_id][candidate_id] = value
    global_values = np.asarray([value for _, _, value in observations], dtype=float)
    global_variance = max(float(global_values.var()), 0.05)
    variances = []
    for task_id in task_ids:
        values = np.asarray(list(by_task[task_id].values()), dtype=float)
        variances.append(max(float(values.var()) if len(values) > 1 else global_variance, 0.05))
    covariance = np.diag(variances)
    for left, left_task in enumerate(task_ids):
        for right, right_task in enumerate(task_ids[:left]):
            shared = sorted(set(by_task[left_task]).intersection(by_task[right_task]))
            if len(shared) >= 3:
                x = np.asarray([by_task[left_task][candidate] for candidate in shared])
                y = np.asarray([by_task[right_task][candidate] for candidate in shared])
                correlation = float(np.corrcoef(x, y)[0, 1])
                if not math.isfinite(correlation):
                    correlation = default_correlation
            else:
                correlation = default_correlation
            covariance[left, right] = covariance[right, left] = (
                max(-0.95, min(0.95, correlation))
                * math.sqrt(variances[left] * variances[right])
            )
    eigenvalues, eigenvectors = np.linalg.eigh(covariance)
    return eigenvectors @ np.diag(np.maximum(eigenvalues, 1e-8)) @ eigenvectors.T


def _icm_kernel(x_left, task_left, x_right, task_right, task_covariance):
    return _tanimoto(x_left, x_right) * task_covariance[task_left[:, None], task_right[None, :]]


def _icm_kernel_diagonal(tasks, task_covariance):
    return task_covariance[tasks, tasks]


def _tanimoto(x_left, x_right):
    import numpy as np

    dot = x_left @ x_right.T
    denominator = x_left.sum(axis=1)[:, None] + x_right.sum(axis=1)[None, :] - dot
    return np.divide(dot, denominator, out=np.zeros_like(dot), where=denominator > 0)


def _expected_improvement(mean, variance, best: float):
    import numpy as np

    std = np.sqrt(np.maximum(variance, 1e-12))
    z = (mean - best) / std
    normal_pdf = np.exp(-0.5 * np.square(z)) / math.sqrt(2.0 * math.pi)
    normal_cdf = 0.5 * (1.0 + np.vectorize(math.erf)(z / math.sqrt(2.0)))
    return (mean - best) * normal_cdf + std * normal_pdf


def _sorted(scores: list[ActionScore]) -> tuple[ActionScore, ...]:
    return tuple(
        sorted(
            scores,
            key=lambda item: (
                -item.priority,
                item.action.candidate_id,
                item.action.oracle_id,
            ),
        )
    )
