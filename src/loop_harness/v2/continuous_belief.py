from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

from ..artifact_hash import canonical_content_sha256
from .contracts import EvidenceDisposition, PosteriorSnapshot, OutcomeDirection
from .ledger import CampaignLedger, EventKind
from .model_store import ContentAddressedArtifactStore
from .mfbo_policy import (
    JensenStyleTVRPolicy,
    _ICMTanimotoPosterior,
    _task_covariance,
)
from .state import CampaignSnapshot


@dataclass(frozen=True)
class PosteriorValue:
    candidate_id: str
    oracle_id: str
    mean: float
    variance: float


@dataclass(frozen=True)
class ContinuousPosteriorConfig:
    model_spec_id: str = "icm_tanimoto_numpy_v1"
    fingerprint_bits: int = 256
    fingerprint_radius: int = 2
    noise: float = 1e-4
    default_task_correlation: float = 0.5

    def __post_init__(self) -> None:
        if self.fingerprint_bits <= 0 or self.fingerprint_radius < 0:
            raise ValueError("fingerprint dimensions must be positive")
        if self.fingerprint_radius != 2:
            raise ValueError("current Jensen encoder is fixed at Morgan radius 2")
        if self.noise <= 0 or not 0.0 <= self.default_task_correlation < 1.0:
            raise ValueError("invalid posterior configuration")


class ContinuousPosteriorFitter:
    """Fit a reproducible continuous multi-fidelity posterior from qualified data."""

    def __init__(self, config: ContinuousPosteriorConfig | None = None) -> None:
        self.config = config or ContinuousPosteriorConfig()
        self._encoder = JensenStyleTVRPolicy(
            fingerprint_bits=self.config.fingerprint_bits,
        )

    def fit(
        self,
        state: CampaignSnapshot,
        store: ContentAddressedArtifactStore,
        *,
        endpoint_id: str,
    ) -> PosteriorSnapshot:
        observations = _qualified_continuous_observations(state, endpoint_id)
        candidate_ids = tuple(sorted(state.candidates))
        oracle_ids = tuple(
            sorted(
                oracle_id
                for oracle_id, oracle in state.oracles.items()
                if (oracle.endpoint_id or state.spec.primary_endpoint) == endpoint_id
            )
        )
        if not oracle_ids:
            raise ValueError(f"no oracles registered for endpoint: {endpoint_id}")
        features = self._encoder._fingerprints(state)
        model_payload: dict[str, Any]
        prediction_payload: dict[str, Any]
        if observations:
            import numpy as np

            task_index = {oracle_id: index for index, oracle_id in enumerate(oracle_ids)}
            x_train = np.vstack([features[candidate_id] for _, candidate_id, _, _ in observations])
            task_train = np.asarray(
                [task_index[oracle_id] for _, _, oracle_id, _ in observations],
                dtype=int,
            )
            y = np.asarray([value for _, _, _, value in observations], dtype=float)
            task_covariance = _task_covariance(
                [(candidate_id, oracle_id, value) for _, candidate_id, oracle_id, value in observations],
                oracle_ids,
                default_correlation=self.config.default_task_correlation,
            )
            model = _ICMTanimotoPosterior(
                x_train=x_train,
                task_train=task_train,
                y=y,
                task_covariance=task_covariance,
                noise=self.config.noise,
            )
            action_keys = tuple(
                (candidate_id, oracle_id)
                for candidate_id in candidate_ids
                for oracle_id in oracle_ids
            )
            x_test = np.vstack([features[candidate_id] for candidate_id, _ in action_keys])
            task_test = np.asarray([task_index[oracle_id] for _, oracle_id in action_keys], dtype=int)
            means, covariance = model.predict(x_test, task_test, covariance=True)
            variances = np.maximum(np.diag(covariance), self.config.noise)
            posterior_values = tuple(
                PosteriorValue(
                    candidate_id=candidate_id,
                    oracle_id=oracle_id,
                    mean=float(means[index]),
                    variance=float(variances[index]),
                )
                for index, (candidate_id, oracle_id) in enumerate(action_keys)
            )
            model_payload = {
                "model_spec_id": self.config.model_spec_id,
                "endpoint_id": endpoint_id,
                "oracle_ids": oracle_ids,
                "training_observation_ids": [result_id for result_id, _, _, _ in observations],
                "training_candidate_ids": [candidate_id for _, candidate_id, _, _ in observations],
                "training_oracle_ids": [oracle_id for _, _, oracle_id, _ in observations],
                "training_values": [value for _, _, _, value in observations],
                "training_features": x_train.tolist(),
                "training_tasks": task_train.tolist(),
                "task_covariance": task_covariance.tolist(),
                "noise": self.config.noise,
                "y_mean": model.y_mean,
            }
            covariance_payload = covariance.tolist()
        else:
            import numpy as np

            posterior_values = tuple(
                PosteriorValue(
                    candidate_id=candidate_id,
                    oracle_id=oracle_id,
                    mean=0.0,
                    variance=1.0,
                )
                for candidate_id in candidate_ids
                for oracle_id in oracle_ids
            )
            covariance_payload = np.eye(len(posterior_values), dtype=float).tolist()
            model_payload = {
                "model_spec_id": self.config.model_spec_id,
                "endpoint_id": endpoint_id,
                "oracle_ids": oracle_ids,
                "prior_only": True,
                "noise": self.config.noise,
            }
        training_ids = tuple(sorted(result_id for result_id, _, _, _ in observations))
        training_set_sha256 = canonical_content_sha256(
            [
                {
                    "result_id": result_id,
                    "candidate_id": candidate_id,
                    "oracle_id": oracle_id,
                    "value": value,
                }
                for result_id, candidate_id, oracle_id, value in observations
            ]
        )
        candidate_set_sha256 = canonical_content_sha256(
            [(candidate_id, state.candidates[candidate_id].smiles) for candidate_id in candidate_ids]
        )
        prediction_payload = {
            "endpoint_id": endpoint_id,
            "candidate_set_sha256": candidate_set_sha256,
            "values": [
                {
                    "candidate_id": value.candidate_id,
                    "oracle_id": value.oracle_id,
                    "mean": value.mean,
                    "variance": value.variance,
                }
                for value in posterior_values
            ],
            "covariance": covariance_payload,
            "action_order": [
                {"candidate_id": candidate_id, "oracle_id": oracle_id}
                for candidate_id, oracle_id in (
                    (value.candidate_id, value.oracle_id) for value in posterior_values
                )
            ],
        }
        diagnostics_payload = {
            "model_spec_id": self.config.model_spec_id,
            "endpoint_id": endpoint_id,
            "fit_status": "success",
            "observation_count": len(observations),
            "candidate_count": len(candidate_ids),
            "oracle_count": len(oracle_ids),
            "training_set_sha256": training_set_sha256,
            "candidate_set_sha256": candidate_set_sha256,
        }
        model_artifact = store.put_json(model_payload, producer_version=self.config.model_spec_id)
        prediction_artifact = store.put_json(
            prediction_payload,
            producer_version=self.config.model_spec_id,
        )
        diagnostics_artifact = store.put_json(
            diagnostics_payload,
            producer_version=self.config.model_spec_id,
        )
        snapshot_id = "posterior_" + hashlib.sha256(
            f"{endpoint_id}:{training_set_sha256}:{candidate_set_sha256}:{self.config.model_spec_id}".encode(
                "utf-8"
            )
        ).hexdigest()[:20]
        return PosteriorSnapshot(
            snapshot_id=snapshot_id,
            endpoint_id=endpoint_id,
            model_spec_id=self.config.model_spec_id,
            training_observation_ids=training_ids,
            training_set_sha256=training_set_sha256,
            candidate_set_sha256=candidate_set_sha256,
            model_artifact=model_artifact,
            prediction_artifact=prediction_artifact,
            diagnostics_artifact=diagnostics_artifact,
            fit_status="success",
        )


class PosteriorRepository:
    """Read verified candidate posterior values without exposing arbitrary files."""

    def __init__(self, store: ContentAddressedArtifactStore) -> None:
        self.store = store

    def values(self, snapshot: PosteriorSnapshot) -> tuple[PosteriorValue, ...]:
        payload = self.store.read_json(snapshot.prediction_artifact)
        if payload.get("candidate_set_sha256") != snapshot.candidate_set_sha256:
            raise ValueError("posterior candidate set hash mismatch")
        return tuple(
            PosteriorValue(
                candidate_id=str(item["candidate_id"]),
                oracle_id=str(item["oracle_id"]),
                mean=float(item["mean"]),
                variance=float(item["variance"]),
            )
            for item in payload.get("values", ())
        )

    def get(
        self,
        snapshot: PosteriorSnapshot,
        *,
        candidate_id: str,
        oracle_id: str,
    ) -> PosteriorValue:
        for value in self.values(snapshot):
            if value.candidate_id == candidate_id and value.oracle_id == oracle_id:
                return value
        raise KeyError((candidate_id, oracle_id))

    def matrix(
        self,
        snapshot: PosteriorSnapshot,
    ) -> tuple[tuple[tuple[str, str], ...], object]:
        """Return the verified action order and cross-fidelity covariance matrix."""

        import numpy as np

        payload = self.store.read_json(snapshot.prediction_artifact)
        if payload.get("candidate_set_sha256") != snapshot.candidate_set_sha256:
            raise ValueError("posterior candidate set hash mismatch")
        action_order = tuple(
            (str(item["candidate_id"]), str(item["oracle_id"]))
            for item in payload.get("action_order", ())
        )
        covariance = np.asarray(payload.get("covariance", ()), dtype=float)
        if covariance.size == 0 and action_order:
            raise ValueError("posterior covariance is missing")
        if covariance.shape != (len(action_order), len(action_order)):
            raise ValueError("posterior covariance shape mismatch")
        return action_order, covariance


def record_posterior_fit(ledger: CampaignLedger, snapshot: PosteriorSnapshot) -> None:
    """Register artifacts and the posterior snapshot as append-only facts."""

    seen: set[str] = set()
    for artifact in (
        snapshot.model_artifact,
        snapshot.prediction_artifact,
        snapshot.diagnostics_artifact,
    ):
        if artifact.artifact_id in seen:
            continue
        ledger.append(EventKind.RAW_ARTIFACT_RECORDED, {"artifact": artifact})
        seen.add(artifact.artifact_id)
    ledger.append(EventKind.POSTERIOR_FIT_RECORDED, {"snapshot": snapshot})


def _qualified_continuous_observations(
    state: CampaignSnapshot,
    endpoint_id: str,
) -> list[tuple[str, str, str, float]]:
    rows: list[tuple[str, str, str, float]] = []
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
        rows.append((result.action_id, candidate_id, oracle_id, float(outcome.value)))
    return rows
