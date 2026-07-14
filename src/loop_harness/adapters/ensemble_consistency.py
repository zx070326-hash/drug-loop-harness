from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Iterable

from ..contracts import (
    EvidenceTier,
    Observation,
    OracleAnchorSpec,
    OracleKind,
    OracleSpec,
    TrustProfile,
)
from .base import ObservationAdapter


ENSEMBLE_CONSISTENCY_RULESET_VERSION = "two_template_vina_score_consistency_v1"


@dataclass(frozen=True)
class EnsembleConsistencyConfig:
    max_score_delta_kcal_per_mol: float = 2.0


class ReceptorEnsembleConsistencyAdapter(ObservationAdapter):
    """Turn two declared docking caches into an L4 robustness observation."""

    def __init__(
        self,
        primary: Iterable[Observation],
        secondary: Iterable[Observation],
        *,
        primary_template_id: str,
        secondary_template_id: str,
        oracle_name: str = "receptor_ensemble_consistency",
        config: EnsembleConsistencyConfig | None = None,
    ) -> None:
        if not primary_template_id or not secondary_template_id:
            raise ValueError("both template identifiers are required")
        self.primary = {item.subject_id: item for item in primary}
        self.secondary = {item.subject_id: item for item in secondary}
        self.primary_template_id = primary_template_id
        self.secondary_template_id = secondary_template_id
        self.oracle_name = oracle_name
        self.config = config or EnsembleConsistencyConfig()
        if self.config.max_score_delta_kcal_per_mol <= 0:
            raise ValueError("max_score_delta_kcal_per_mol must be positive")

    def load_observations(self) -> tuple[Observation, ...]:
        subject_ids = tuple(sorted(set(self.primary) | set(self.secondary)))
        return tuple(self._observation(subject_id) for subject_id in subject_ids)

    def _observation(self, subject_id: str) -> Observation:
        primary = self.primary.get(subject_id)
        secondary = self.secondary.get(subject_id)
        primary_score = _score(primary)
        secondary_score = _score(secondary)
        if primary_score is None or secondary_score is None:
            status = "template_result_missing"
            score_delta = None
            passed = False
        else:
            score_delta = round(abs(primary_score - secondary_score), 6)
            passed = score_delta <= self.config.max_score_delta_kcal_per_mol
            status = "consistent" if passed else "score_disagreement"
        metrics = {
            "passed": passed,
            "ensemble_status": status,
            "primary_template_id": self.primary_template_id,
            "secondary_template_id": self.secondary_template_id,
            "primary_score_kcal_per_mol": primary_score,
            "secondary_score_kcal_per_mol": secondary_score,
            "score_delta_kcal_per_mol": score_delta,
            "max_score_delta_kcal_per_mol": self.config.max_score_delta_kcal_per_mol,
        }
        input_hash = _hash_payload(
            {
                "subject_id": subject_id,
                "primary_output_hash": _output_hash(primary),
                "secondary_output_hash": _output_hash(secondary),
                "config": {"max_score_delta_kcal_per_mol": self.config.max_score_delta_kcal_per_mol},
                "ruleset": ENSEMBLE_CONSISTENCY_RULESET_VERSION,
            }
        )
        return Observation(
            id=f"obs_ensemble_consistency_{subject_id}_{input_hash[:12]}",
            subject_id=subject_id,
            oracle_name=self.oracle_name,
            metrics=metrics,
            evidence_tier=EvidenceTier.L4_HIGHER_FIDELITY,
            cost_spent=0.0,
            provenance={
                "source": "fixed_receptor_ensemble_cache",
                "adapter": self.__class__.__name__,
                "adapter_version": "0.1.0",
                "ruleset": ENSEMBLE_CONSISTENCY_RULESET_VERSION,
                "domain": ("protein_ligand", "non_covalent", "receptor_ensemble"),
                "input_hash": input_hash,
                "output_hash": _hash_payload(metrics),
            },
            notes=(
                "Two-template score consistency is structural robustness evidence only; "
                "it is not independent binding or experimental validation."
            ),
        )


def receptor_ensemble_consistency_oracle(
    *,
    primary_template_id: str,
    secondary_template_id: str,
    name: str = "receptor_ensemble_consistency",
) -> OracleSpec:
    return OracleSpec(
        name=name,
        kind=OracleKind.RESCORING,
        trust=TrustProfile(
            fidelity="medium",
            cost=5.0,
            latency="minutes",
            independence_from_generator="medium",
            gameability="medium",
            applicability_domain=("protein_ligand", "non_covalent", "receptor_ensemble"),
            failure_modes=(
                "shared_scoring_function_bias",
                "template_selection_bias",
                "score_consistency_without_binding",
            ),
        ),
        anchors=(
            OracleAnchorSpec(
                name=f"receptor_ensemble:{primary_template_id}:{secondary_template_id}",
                description="Agreement between two fixed receptor-template docking caches.",
                anchor_type="structural_robustness",
                validation_basis="Hash-bound scores from two pre-declared receptor templates.",
                known_gap="Shared Vina scoring is not an independent binding validation.",
            ),
        ),
        version=ENSEMBLE_CONSISTENCY_RULESET_VERSION,
        adapter="ReceptorEnsembleConsistencyAdapter",
    )


def _score(observation: Observation | None) -> float | None:
    if observation is None or observation.metrics.get("docking_status") != "docked":
        return None
    value = observation.metrics.get("score_kcal_per_mol")
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def _output_hash(observation: Observation | None) -> str | None:
    if observation is None:
        return None
    value = observation.provenance.get("output_hash")
    return value if isinstance(value, str) else None


def _hash_payload(payload: Any) -> str:
    text = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=list)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
