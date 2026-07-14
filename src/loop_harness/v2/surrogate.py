from __future__ import annotations

import math

from .belief import BeliefUpdater
from .contracts import (
    BeliefState,
    BeliefUpdate,
    EvidenceAssessment,
    EvidenceDisposition,
    OracleResult,
    OracleResultStatus,
)
from .state import CampaignSnapshot


class MorganLogisticEnsembleUpdater(BeliefUpdater):
    """Target-local QSAR baseline with explicit uncalibrated uncertainty.

    Only oracle IDs named at construction may provide training labels. This
    prevents a docking or ADMET proxy from silently becoming assay truth.
    """

    model_version = "morgan_logistic_bootstrap_v1_uncalibrated"

    def __init__(
        self,
        *,
        training_oracle_ids: tuple[str, ...],
        min_training_size: int = 4,
        ensemble_size: int = 12,
        random_seed: int = 20260712,
    ) -> None:
        if not training_oracle_ids:
            raise ValueError("training_oracle_ids must be explicit")
        if min_training_size < 4 or ensemble_size <= 1:
            raise ValueError("min_training_size >= 4 and ensemble_size > 1 required")
        self.training_oracle_ids = frozenset(training_oracle_ids)
        self.min_training_size = min_training_size
        self.ensemble_size = ensemble_size
        self.random_seed = random_seed

    def update(
        self,
        state: CampaignSnapshot,
        result: OracleResult,
        assessment: EvidenceAssessment,
    ) -> tuple[BeliefUpdate, ...]:
        if result.oracle_id not in self.training_oracle_ids:
            return ()
        endpoint_id = result.outcome.endpoint_id if result.outcome else _endpoint_id(result, state)
        training = _training_rows(state, self.training_oracle_ids, endpoint_id)
        if len(training) < self.min_training_size or len({label for _, label in training}) < 2:
            return _prior_updates(
                state,
                result.action_id,
                training,
                self.model_version,
                endpoint_id,
            )

        candidate_ids = sorted(state.candidates)
        molecules = [state.candidates[candidate_id].smiles for candidate_id in candidate_ids]
        matrix = _fingerprint_matrix(molecules)
        row_by_id = {candidate_id: index for index, candidate_id in enumerate(candidate_ids)}
        train_indices = [row_by_id[candidate_id] for candidate_id, _ in training]
        labels = [label for _, label in training]
        probabilities = _bootstrap_probabilities(
            matrix,
            train_indices,
            labels,
            ensemble_size=self.ensemble_size,
            random_seed=self.random_seed + state.round_index,
        )
        if probabilities is None:
            return _prior_updates(
                state,
                result.action_id,
                training,
                self.model_version,
                endpoint_id,
            )

        updates: list[BeliefUpdate] = []
        for index, candidate_id in enumerate(candidate_ids):
            values = probabilities[:, index]
            mean = float(values.mean())
            disagreement = min(1.0, float(values.std()) / 0.5)
            entropy = _binary_entropy(mean)
            uncertainty = min(1.0, 0.7 * entropy + 0.3 * disagreement)
            updates.append(
                BeliefUpdate(
                    action_id=result.action_id,
                    belief=BeliefState(
                        candidate_id=candidate_id,
                        mean=mean,
                        uncertainty=uncertainty,
                        model_version=self.model_version,
                        evidence_count=len(training),
                        endpoint_id=endpoint_id,
                    ),
                    update_reason=(
                        f"refit_on_{len(training)}_acquired_labels; "
                        f"training_oracles={','.join(sorted(self.training_oracle_ids))}; "
                        f"assessment={assessment.disposition.value}"
                    ),
                )
            )
        return tuple(updates)


def _training_rows(
    state: CampaignSnapshot,
    training_oracle_ids: frozenset[str],
    endpoint_id: str,
) -> list[tuple[str, int]]:
    rows: list[tuple[str, int]] = []
    for (candidate_id, oracle_id), result in sorted(state.results.items()):
        if oracle_id not in training_oracle_ids or result.status is not OracleResultStatus.SUCCESS:
            continue
        action = state.actions[result.action_id]
        result_endpoint = result.outcome.endpoint_id if result.outcome else (
            action.endpoint_id or state.spec.primary_endpoint
        )
        if result_endpoint != endpoint_id:
            continue
        assessment = state.assessments.get((candidate_id, oracle_id))
        if assessment is None or assessment.disposition is not EvidenceDisposition.QUALIFIED:
            continue
        label = result.metrics.get("label")
        if isinstance(label, bool):
            rows.append((candidate_id, int(label)))
        elif isinstance(label, int | float) and label in (0, 1):
            rows.append((candidate_id, int(label)))
    return rows


def _prior_updates(
    state: CampaignSnapshot,
    action_id: str,
    training: list[tuple[str, int]],
    model_version: str,
    endpoint_id: str,
) -> tuple[BeliefUpdate, ...]:
    if training:
        prevalence = (sum(label for _, label in training) + 1.0) / (len(training) + 2.0)
    else:
        prevalence = 0.5
    uncertainty = min(1.0, 1.0 / math.sqrt(max(1, len(training))))
    return tuple(
        BeliefUpdate(
            action_id=action_id,
            belief=BeliefState(
                candidate_id=candidate_id,
                mean=prevalence,
                uncertainty=uncertainty,
                model_version=f"{model_version}:prior_fallback",
                evidence_count=len(training),
                endpoint_id=endpoint_id,
            ),
            update_reason=f"insufficient_training_labels:{len(training)}",
        )
        for candidate_id in sorted(state.candidates)
    )


def _endpoint_id(result: OracleResult, state: CampaignSnapshot) -> str:
    action = state.actions[result.action_id]
    return action.endpoint_id or state.spec.primary_endpoint


def _fingerprint_matrix(smiles_values: list[str]):
    try:
        import numpy as np
        from rdkit import Chem, DataStructs
        from rdkit.Chem import rdFingerprintGenerator
    except ImportError as exc:  # pragma: no cover - environment boundary
        raise RuntimeError("RDKit and NumPy are required for the QSAR surrogate") from exc

    generator = rdFingerprintGenerator.GetMorganGenerator(radius=2, fpSize=2048)
    matrix = np.zeros((len(smiles_values), 2048), dtype=float)
    for index, smiles in enumerate(smiles_values):
        molecule = Chem.MolFromSmiles(smiles)
        if molecule is None:
            raise ValueError(f"surrogate received invalid SMILES at row {index}")
        fingerprint = generator.GetFingerprint(molecule)
        DataStructs.ConvertToNumpyArray(fingerprint, matrix[index])
    return matrix


def _bootstrap_probabilities(
    matrix,
    train_indices: list[int],
    labels: list[int],
    *,
    ensemble_size: int,
    random_seed: int,
):
    import numpy as np
    from sklearn.linear_model import LogisticRegression

    rng = np.random.default_rng(random_seed)
    x_train = matrix[train_indices]
    y_train = np.asarray(labels, dtype=int)
    predictions = []
    for member in range(ensemble_size):
        sample = rng.integers(0, len(train_indices), size=len(train_indices))
        sampled_y = y_train[sample]
        if len(np.unique(sampled_y)) < 2:
            continue
        model = LogisticRegression(
            class_weight="balanced",
            max_iter=1000,
            random_state=random_seed + member,
            solver="liblinear",
        )
        model.fit(x_train[sample], sampled_y)
        predictions.append(model.predict_proba(matrix)[:, 1])
    if not predictions:
        return None
    return np.vstack(predictions)


def _binary_entropy(probability: float) -> float:
    clipped = min(max(probability, 1e-12), 1.0 - 1e-12)
    return -(
        clipped * math.log2(clipped)
        + (1.0 - clipped) * math.log2(1.0 - clipped)
    )
