from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Iterable

from ..contracts import Compound, Observation
from .base import ObservationAdapter


RULESET_VERSION = "deterministic_chem_filter_v1"
_ALLOWED_SMILES = re.compile(r"^[A-Za-z0-9@+\-\[\]\(\)=#$\\/%.:]+$")
_ATOM_PATTERN = re.compile(
    r"Cl|Br|Si|Na|Li|Mg|Ca|Fe|Zn|Cu|Mn|B|C|N|O|P|S|F|I|c|n|o|p|s"
)
_ATOMIC_WEIGHTS = {
    "B": 10.81,
    "Br": 79.90,
    "C": 12.01,
    "Ca": 40.08,
    "Cl": 35.45,
    "Cu": 63.55,
    "F": 19.00,
    "Fe": 55.85,
    "I": 126.90,
    "Li": 6.94,
    "Mg": 24.31,
    "Mn": 54.94,
    "N": 14.01,
    "Na": 22.99,
    "O": 16.00,
    "P": 30.97,
    "S": 32.06,
    "Si": 28.09,
    "Zn": 65.38,
}


@dataclass(frozen=True)
class ChemFilterConfig:
    """Simple deterministic chemistry-screening thresholds."""

    max_heavy_atoms: int = 80
    max_approx_molecular_weight: float = 900.0
    max_ring_digit_occurrences: int = 12


class DeterministicChemFilterAdapter(ObservationAdapter):
    """Apply lightweight deterministic SMILES sanity checks.

    This is intentionally not a cheminformatics engine. It is a local,
    dependency-free oracle adapter that produces auditable L0 observations.
    """

    def __init__(
        self,
        compounds: Iterable[Compound],
        *,
        oracle_name: str = "basic_chem_filter",
        source: str = "deterministic_chem_filter",
        config: ChemFilterConfig | None = None,
    ) -> None:
        self.compounds = tuple(compounds)
        self.oracle_name = oracle_name
        self.source = source
        self.config = config or ChemFilterConfig()

    def load_observations(self) -> tuple[Observation, ...]:
        return tuple(self._observation(compound) for compound in self.compounds)

    def _observation(self, compound: Compound) -> Observation:
        metrics = evaluate_smiles(compound.smiles, self.config)
        provenance = {
            "source": self.source,
            "adapter": self.__class__.__name__,
            "adapter_version": "0.1.0",
            "ruleset": RULESET_VERSION,
            "domain": ("small_molecule",),
            "input_hash": _hash_payload(
                {
                    "compound_id": compound.id,
                    "smiles": compound.smiles,
                    "ruleset": RULESET_VERSION,
                }
            ),
            "output_hash": _hash_payload(metrics),
        }
        return Observation(
            id=f"obs_chem_filter_{compound.id}",
            subject_id=compound.id,
            oracle_name=self.oracle_name,
            metrics=metrics,
            cost_spent=1.0,
            provenance=provenance,
        )


def evaluate_smiles(
    smiles: str,
    config: ChemFilterConfig | None = None,
) -> dict[str, Any]:
    config = config or ChemFilterConfig()
    normalized = smiles.strip()
    atom_tokens = _atom_tokens(normalized)
    ring_digits = [char for char in normalized if char.isdigit()]
    checks = {
        "nonempty": bool(normalized),
        "allowed_characters": bool(_ALLOWED_SMILES.match(normalized)),
        "balanced_parentheses": _balanced(normalized, "(", ")"),
        "balanced_brackets": _balanced(normalized, "[", "]"),
        "ring_closures_paired": all(
            ring_digits.count(digit) % 2 == 0 for digit in set(ring_digits)
        ),
        "ring_digit_limit": len(ring_digits) <= config.max_ring_digit_occurrences,
        "contains_atom_token": bool(atom_tokens),
        "heavy_atom_limit": len(atom_tokens) <= config.max_heavy_atoms,
    }
    approx_weight = round(sum(_atom_weight(token) for token in atom_tokens), 2)
    checks["approx_molecular_weight_limit"] = (
        approx_weight <= config.max_approx_molecular_weight
    )
    failure_reasons = tuple(
        name for name, passed in checks.items() if not passed
    )
    return {
        "passed": not failure_reasons,
        "checks": checks,
        "failure_reasons": failure_reasons,
        "heavy_atom_count": len(atom_tokens),
        "approx_molecular_weight": approx_weight,
        "ruleset": RULESET_VERSION,
    }


def _atom_tokens(smiles: str) -> tuple[str, ...]:
    return tuple(match.group(0) for match in _ATOM_PATTERN.finditer(smiles))


def _atom_weight(token: str) -> float:
    normalized = token.capitalize() if len(token) == 1 else token
    return _ATOMIC_WEIGHTS.get(normalized, 12.0)


def _balanced(value: str, opening: str, closing: str) -> bool:
    depth = 0
    for char in value:
        if char == opening:
            depth += 1
        elif char == closing:
            depth -= 1
        if depth < 0:
            return False
    return depth == 0


def _hash_payload(payload: Any) -> str:
    text = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=list)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
