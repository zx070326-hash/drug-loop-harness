from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Any, Iterable

from ..contracts import (
    Compound,
    EvidenceTier,
    Observation,
    OracleAnchorSpec,
    OracleKind,
    OracleSpec,
    TrustProfile,
)
from .base import ObservationAdapter


TARGET_LIKENESS_RULESET_VERSION = "reference_ligand_morgan_tanimoto_v1"


@dataclass(frozen=True)
class TargetLikenessConfig:
    radius: int = 2
    n_bits: int = 2048

    def to_dict(self) -> dict[str, int]:
        return asdict(self)


class ReferenceLigandTargetLikenessAdapter(ObservationAdapter):
    """Compute a label-free, target-conditioned cheap signal from one reference ligand."""

    def __init__(
        self,
        compounds: Iterable[Compound],
        *,
        reference_smiles: str,
        reference_id: str,
        oracle_name: str = "reference_ligand_target_likeness",
        source: str = "reference_ligand_target_likeness",
        config: TargetLikenessConfig | None = None,
    ) -> None:
        if not reference_id:
            raise ValueError("reference_id must not be empty")
        self.compounds = tuple(compounds)
        self.reference_smiles = reference_smiles
        self.reference_id = reference_id
        self.oracle_name = oracle_name
        self.source = source
        self.config = config or TargetLikenessConfig()
        self._reference, self.rdkit_version = _reference_molecule(reference_smiles, self.config)

    def load_observations(self) -> tuple[Observation, ...]:
        return tuple(self._observation(compound) for compound in self.compounds)

    def _observation(self, compound: Compound) -> Observation:
        similarity = _similarity(compound.smiles, self._reference, self.config)
        metrics = {
            "passed": similarity is not None,
            "tanimoto_similarity": similarity,
            "reference_id": self.reference_id,
            "reference_smiles_sha256": _sha256(self.reference_smiles),
            "config": self.config.to_dict(),
        }
        input_hash = _hash_payload(
            {
                "compound_id": compound.id,
                "smiles": compound.smiles,
                "reference_smiles": self.reference_smiles,
                "reference_id": self.reference_id,
                "config": self.config.to_dict(),
                "ruleset": TARGET_LIKENESS_RULESET_VERSION,
                "rdkit_version": self.rdkit_version,
            }
        )
        provenance = {
            "source": self.source,
            "adapter": self.__class__.__name__,
            "adapter_version": "0.1.0",
            "ruleset": TARGET_LIKENESS_RULESET_VERSION,
            "tool": "rdkit",
            "tool_version": self.rdkit_version,
            "domain": ("small_molecule", "reference_ligand_conditioned"),
            "input_hash": input_hash,
            "output_hash": _hash_payload(metrics),
        }
        return Observation(
            id=f"obs_target_likeness_{compound.id}_{input_hash[:12]}",
            subject_id=compound.id,
            oracle_name=self.oracle_name,
            metrics=metrics,
            evidence_tier=EvidenceTier.L1_PROPERTY_FILTER,
            cost_spent=0.2,
            provenance=provenance,
            notes=(
                "Reference-ligand similarity is a promotion feature, not evidence of "
                "binding, activity, selectivity, or novelty."
            ),
        )


def reference_ligand_target_likeness_oracle(
    *,
    reference_id: str,
    rdkit_version: str,
    name: str = "reference_ligand_target_likeness",
) -> OracleSpec:
    return OracleSpec(
        name=name,
        kind=OracleKind.OTHER,
        trust=TrustProfile(
            fidelity="low",
            cost=0.2,
            latency="seconds",
            independence_from_generator="medium",
            gameability="high",
            applicability_domain=("small_molecule", "reference_ligand_conditioned"),
            failure_modes=(
                "reference_ligand_analogue_bias",
                "fingerprint_representation_dependence",
                "target_family_transfer_gap",
            ),
        ),
        anchors=(
            OracleAnchorSpec(
                name=f"reference_ligand:{reference_id}",
                description="Declared reference ligand used only as a cheap promotion feature.",
                anchor_type="reference_structure_ligand",
                validation_basis="Versioned Morgan fingerprint and immutable reference SMILES.",
                known_gap="Similarity does not establish binding, novelty, or experimental activity.",
            ),
        ),
        version=rdkit_version,
        adapter="ReferenceLigandTargetLikenessAdapter",
    )


def _reference_molecule(smiles: str, config: TargetLikenessConfig):
    rdBase, Chem, _, _ = _rdkit_modules()
    molecule = Chem.MolFromSmiles(smiles)
    if molecule is None:
        raise ValueError("reference_smiles must be RDKit-parseable")
    if config.radius < 0 or config.n_bits < 16:
        raise ValueError("radius must be nonnegative and n_bits must be at least 16")
    return molecule, str(rdBase.rdkitVersion)


def _similarity(smiles: str, reference: Any, config: TargetLikenessConfig) -> float | None:
    _, Chem, _, DataStructs = _rdkit_modules()
    from rdkit.Chem import rdFingerprintGenerator

    molecule = Chem.MolFromSmiles(smiles)
    if molecule is None:
        return None
    generator = rdFingerprintGenerator.GetMorganGenerator(
        radius=config.radius,
        fpSize=config.n_bits,
    )
    reference_fp = generator.GetFingerprint(reference)
    candidate_fp = generator.GetFingerprint(molecule)
    return round(float(DataStructs.TanimotoSimilarity(reference_fp, candidate_fp)), 6)


def _rdkit_modules():
    try:
        from rdkit import Chem, DataStructs, rdBase
        from rdkit.Chem import AllChem
    except ImportError as exc:
        raise RuntimeError("RDKit is required for reference-ligand target likeness.") from exc
    return rdBase, Chem, AllChem, DataStructs


def _hash_payload(payload: Any) -> str:
    return _sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), default=list))


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
