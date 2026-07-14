from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Any, Iterable, Mapping

from ..contracts import (
    Compound,
    Observation,
    OracleAnchorSpec,
    OracleKind,
    OracleSpec,
    TrustProfile,
)
from .base import ObservationAdapter


RDKIT_CHEM_FILTER_ORACLE_NAME = "rdkit_chem_filter"
RDKIT_CHEM_FILTER_ADAPTER_VERSION = "0.1.0"
RDKIT_CHEM_FILTER_RULESET_VERSION = "rdkit_chem_filter_v1"


class RDKitDependencyUnavailable(RuntimeError):
    """Raised when the optional RDKit dependency is unavailable."""


class RDKitVersionMismatch(RuntimeError):
    """Raised when a run's declared RDKit version differs from the local tool."""


@dataclass(frozen=True)
class RDKitChemFilterConfig:
    """Declared L0 chemistry and basic property thresholds for RDKit.

    These are triage constraints, not a target-specific activity, ADMET, or
    developability model. QED is recorded as a descriptor only and is not a
    pass/fail gate.
    """

    max_molecular_weight: float = 600.0
    max_logp: float = 5.0
    max_hydrogen_bond_donors: int = 5
    max_hydrogen_bond_acceptors: int = 10
    max_rotatable_bonds: int = 10

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class RDKitChemFilterAdapter(ObservationAdapter):
    """Emit version-bound RDKit chemical-validity and property observations."""

    def __init__(
        self,
        compounds: Iterable[Compound],
        *,
        oracle_name: str = RDKIT_CHEM_FILTER_ORACLE_NAME,
        source: str = "rdkit_chem_filter",
        config: RDKitChemFilterConfig | None = None,
        expected_rdkit_version: str | None = None,
        input_context: Mapping[str, Any] | None = None,
        cost_spent: float = 0.1,
    ) -> None:
        if cost_spent < 0:
            raise ValueError("cost_spent must not be negative")
        self.compounds = tuple(compounds)
        self.oracle_name = oracle_name
        self.source = source
        self.config = config or RDKitChemFilterConfig()
        self.rdkit_version = require_rdkit_version(expected_rdkit_version)
        self.input_context = dict(input_context or {})
        self.cost_spent = cost_spent

    def load_observations(self) -> tuple[Observation, ...]:
        return tuple(self._observation(compound) for compound in self.compounds)

    def _observation(self, compound: Compound) -> Observation:
        metrics = evaluate_rdkit_smiles(compound.smiles, self.config)
        input_payload = {
            "compound_id": compound.id,
            "smiles": compound.smiles,
            "config": self.config.to_dict(),
            "ruleset": RDKIT_CHEM_FILTER_RULESET_VERSION,
            "rdkit_version": self.rdkit_version,
            "input_context": self.input_context,
        }
        input_hash = hash_payload(input_payload)
        metrics = {
            **metrics,
            "ruleset": RDKIT_CHEM_FILTER_RULESET_VERSION,
            "rdkit_version": self.rdkit_version,
        }
        provenance = {
            "source": self.source,
            "adapter": self.__class__.__name__,
            "adapter_version": RDKIT_CHEM_FILTER_ADAPTER_VERSION,
            "ruleset": RDKIT_CHEM_FILTER_RULESET_VERSION,
            "tool": "rdkit",
            "tool_version": self.rdkit_version,
            "domain": ("small_molecule",),
            "input_hash": input_hash,
            "output_hash": hash_payload(metrics),
            "input_context": dict(self.input_context),
        }
        return Observation(
            id=f"obs_rdkit_chem_filter_{compound.id}_{input_hash[:12]}",
            subject_id=compound.id,
            oracle_name=self.oracle_name,
            metrics=metrics,
            cost_spent=self.cost_spent,
            provenance=provenance,
            notes=(
                "RDKit chemical-validity and basic property triage only; "
                "not an ADMET, binding, synthesis, or experimental result."
            ),
        )


def rdkit_chem_filter_oracle(
    *,
    version: str | None = None,
    name: str = RDKIT_CHEM_FILTER_ORACLE_NAME,
) -> OracleSpec:
    """Return the honest trust profile for the local RDKit L0 oracle."""

    return OracleSpec(
        name=name,
        kind=OracleKind.CHEM_FILTER,
        trust=TrustProfile(
            fidelity="low",
            cost=0.1,
            latency="seconds",
            independence_from_generator="high",
            gameability="medium",
            applicability_domain=("small_molecule",),
            failure_modes=(
                "property_proxy_optimization",
                "tautomer_and_protonation_sensitivity",
                "unmodeled_target_and_admet_context",
            ),
        ),
        anchors=(
            OracleAnchorSpec(
                name="rdkit_sanitization_and_standard_descriptors",
                description=(
                    "Versioned RDKit sanitization and standard molecular descriptor "
                    "calculation on declared SMILES input."
                ),
                anchor_type="software_method",
                validation_basis=(
                    "Versioned local computation with immutable input and output "
                    "hashes."
                ),
                known_gap=(
                    "Does not establish target binding, ADMET behavior, synthetic "
                    "accessibility, or experimental activity."
                ),
            ),
        ),
        version=version or rdkit_version(),
        adapter="RDKitChemFilterAdapter",
    )


def evaluate_rdkit_smiles(
    smiles: str,
    config: RDKitChemFilterConfig | None = None,
) -> dict[str, Any]:
    """Return normalized descriptor metrics without creating a scientific claim."""

    _, Chem, Crippen, Descriptors, Lipinski, QED = _rdkit_modules()
    config = config or RDKitChemFilterConfig()
    molecule = Chem.MolFromSmiles(smiles)
    if molecule is None:
        return {
            "passed": False,
            "checks": {"rdkit_parse_and_sanitize": False},
            "failure_reasons": ("invalid_smiles",),
            "canonical_smiles": None,
            "molecular_weight": None,
            "logp": None,
            "hydrogen_bond_donors": None,
            "hydrogen_bond_acceptors": None,
            "rotatable_bonds": None,
            "ring_count": None,
            "qed": None,
            "config": config.to_dict(),
        }

    molecular_weight = round(float(Descriptors.MolWt(molecule)), 4)
    logp = round(float(Crippen.MolLogP(molecule)), 4)
    hbd = int(Lipinski.NumHDonors(molecule))
    hba = int(Lipinski.NumHAcceptors(molecule))
    rotatable_bonds = int(Lipinski.NumRotatableBonds(molecule))
    ring_count = int(Lipinski.RingCount(molecule))
    qed = round(float(QED.qed(molecule)), 6)
    checks = {
        "rdkit_parse_and_sanitize": True,
        "molecular_weight_limit": molecular_weight <= config.max_molecular_weight,
        "logp_limit": logp <= config.max_logp,
        "hydrogen_bond_donor_limit": hbd <= config.max_hydrogen_bond_donors,
        "hydrogen_bond_acceptor_limit": hba <= config.max_hydrogen_bond_acceptors,
        "rotatable_bond_limit": rotatable_bonds <= config.max_rotatable_bonds,
    }
    failure_reasons = tuple(name for name, passed in checks.items() if not passed)
    return {
        "passed": not failure_reasons,
        "checks": checks,
        "failure_reasons": failure_reasons,
        "canonical_smiles": Chem.MolToSmiles(molecule, canonical=True),
        "molecular_weight": molecular_weight,
        "logp": logp,
        "hydrogen_bond_donors": hbd,
        "hydrogen_bond_acceptors": hba,
        "rotatable_bonds": rotatable_bonds,
        "ring_count": ring_count,
        "qed": qed,
        "config": config.to_dict(),
    }


def rdkit_version() -> str:
    rdBase, *_ = _rdkit_modules()
    return str(rdBase.rdkitVersion)


def require_rdkit_version(expected_version: str | None = None) -> str:
    actual_version = rdkit_version()
    if expected_version is not None and actual_version != expected_version:
        raise RDKitVersionMismatch(
            f"Expected RDKit {expected_version}, found {actual_version}."
        )
    return actual_version


def hash_payload(payload: Any) -> str:
    normalized = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=list)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _rdkit_modules() -> tuple[Any, Any, Any, Any, Any, Any]:
    try:
        from rdkit import Chem, rdBase
        from rdkit.Chem import Crippen, Descriptors, Lipinski, QED
    except ImportError as exc:
        raise RDKitDependencyUnavailable(
            "RDKit is required for RDKitChemFilterAdapter."
        ) from exc
    return rdBase, Chem, Crippen, Descriptors, Lipinski, QED
