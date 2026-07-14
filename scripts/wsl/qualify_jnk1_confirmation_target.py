"""Qualify and select JNK1 confirmation edges using structure-only evidence."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from fractions import Fraction
from pathlib import Path

import kartograf
import openfe
from kartograf.filters import (
    filter_ringbreak_changes,
    filter_ringsize_changes,
    filter_whole_rings_only,
)
from rdkit import Chem


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from loop_harness.v2.fep_untouched_qualification import (  # noqa: E402
    FEP_UNTOUCHED_QUALIFICATION_SCHEMA,
    QUALIFICATION_SEED,
    verify_untouched_qualification_lock,
)


def main() -> None:
    args = _parse_args()
    root = Path(args.project_root).resolve()
    lock_bytes = Path(args.qualification_lock).read_bytes()
    lock = json.loads(lock_bytes.decode("utf-8"))
    verify_untouched_qualification_lock(root, lock)
    if _sha256(args.connectivity) != lock["source_bindings"][
        "redacted_connectivity_sha256"
    ]:
        raise ValueError("qualification connectivity differs from locked redacted input")
    if _sha256(args.ligands) != lock["source_bindings"]["ligands_worktree_sha256"]:
        raise ValueError("qualification ligands differ from locked input")
    if _sha256(args.protein) != lock["source_bindings"]["protein_worktree_sha256"]:
        raise ValueError("qualification protein differs from locked input")

    molecules = _load_molecules(args.ligands)
    pairs = _load_pairs(args.connectivity)
    if {name for pair in pairs for name in pair} - set(molecules):
        raise ValueError("qualification connectivity references a missing ligand")
    protein = openfe.ProteinComponent.from_pdb_file(str(args.protein))
    mapper = kartograf.KartografAtomMapper(
        atom_map_hydrogens=True,
        additional_mapping_filter_functions=[
            filter_ringbreak_changes,
            filter_ringsize_changes,
            filter_whole_rings_only,
        ],
    )
    eligible = []
    exclusion_counts: dict[str, int] = {}
    mapping_failure_count = 0
    for left, right in pairs:
        record, exclusion = _qualify_pair(mapper, molecules, left, right)
        if exclusion:
            exclusion_counts[exclusion] = exclusion_counts.get(exclusion, 0) + 1
            if exclusion == "MAPPING_NOT_UNIQUE":
                mapping_failure_count += 1
            continue
        eligible.append(record)
    eligible.sort(
        key=lambda item: (
            -Fraction(item["unmapped_heavy_atom_numerator"], item["heavy_atom_denominator"]),
            -item["max_side_unmapped_heavy_atom_count"],
            item["selection_tiebreaker_sha256"],
        )
    )
    selected = []
    used_ligands: set[str] = set()
    for record in eligible:
        if record["ligand_a"] in used_ligands or record["ligand_b"] in used_ligands:
            continue
        selected.append(record)
        used_ligands.update((record["ligand_a"], record["ligand_b"]))
        if len(selected) == lock["qualification_rule"]["selected_edge_count"]:
            break
    accepted = len(selected) == lock["qualification_rule"]["selected_edge_count"]
    payload = {
        "schema_version": FEP_UNTOUCHED_QUALIFICATION_SCHEMA,
        "qualification_id": "jnk1-untouched-target-qualification-v1",
        "status": "ACCEPTED" if accepted else "REJECTED",
        "target_id": "jnk1",
        "qualification_lock_sha256": _canonical_sha256(lock),
        "input_sha256": {
            "redacted_connectivity": _sha256(args.connectivity),
            "ligands_sdf": _sha256(args.ligands),
            "protein_pdb": _sha256(args.protein),
        },
        "environment": {
            "openfe": openfe.__version__,
            "kartograf": kartograf.__version__,
            "python": sys.version.split()[0],
        },
        "input_summary": {
            "edge_count": len(pairs),
            "ligand_count": len(molecules),
            "protein_component_name": protein.name,
        },
        "qualification_summary": {
            "eligible_edge_count": len(eligible),
            "mapping_failure_count": mapping_failure_count,
            "exclusion_counts": dict(sorted(exclusion_counts.items())),
        },
        "selected_edge_count": len(selected),
        "selected_edges": selected,
        "selection_rule": lock["qualification_rule"],
        "scientific_controls": {
            "experimental_labels_used": False,
            "public_fep_results_used": False,
            "official_overlap_values_used": False,
            "raw_labeled_edge_file_read": False,
            "scientific_claim_upgrade_permitted": False,
        },
        "authority": {
            "protocol_preregistration_authority_granted": accepted,
            "simulation_execution_authority_granted": False,
            "outcome_access_authority_granted": False,
        },
    }
    data = (json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n").encode(
        "utf-8"
    )
    if args.output.exists() and args.output.read_bytes() != data:
        raise ValueError("refusing to overwrite a different target qualification")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(data)
    print(
        json.dumps(
            {
                "status": payload["status"],
                "eligible_edge_count": len(eligible),
                "selected_edge_count": len(selected),
                "selected_edge_ids": [edge["edge_id"] for edge in selected],
                "mapping_failure_count": mapping_failure_count,
                "output_sha256": hashlib.sha256(data).hexdigest(),
                "simulation_execution_authority_granted": False,
            },
            indent=2,
        )
    )


def _qualify_pair(mapper, molecules, left: str, right: str):
    molecule_a = molecules[left]
    molecule_b = molecules[right]
    rdkit_a = molecule_a.to_rdkit()
    rdkit_b = molecule_b.to_rdkit()
    smiles_a = Chem.MolToSmiles(Chem.RemoveHs(rdkit_a), isomericSmiles=True)
    smiles_b = Chem.MolToSmiles(Chem.RemoveHs(rdkit_b), isomericSmiles=True)
    if smiles_a == smiles_b:
        return None, "IDENTICAL_CANONICAL_STRUCTURE"
    charge_a = int(Chem.GetFormalCharge(rdkit_a))
    charge_b = int(Chem.GetFormalCharge(rdkit_b))
    if charge_a != charge_b:
        return None, "FORMAL_CHARGE_CHANGE"
    suggestions = list(mapper.suggest_mappings(molecule_a, molecule_b))
    if len(suggestions) != 1:
        return None, "MAPPING_NOT_UNIQUE"
    mapping = suggestions[0]
    heavy_a = sum(atom.GetAtomicNum() > 1 for atom in rdkit_a.GetAtoms())
    heavy_b = sum(atom.GetAtomicNum() > 1 for atom in rdkit_b.GetAtoms())
    heavy_core = sum(
        rdkit_a.GetAtomWithIdx(index_a).GetAtomicNum() > 1
        and rdkit_b.GetAtomWithIdx(index_b).GetAtomicNum() > 1
        for index_a, index_b in mapping.componentA_to_componentB.items()
    )
    denominator = heavy_a + heavy_b
    numerator = denominator - 2 * heavy_core
    mapped_fraction = (2 * heavy_core) / denominator
    if mapped_fraction < 0.5:
        return None, "INSUFFICIENT_MAPPED_HEAVY_FRACTION"
    atom_pairs = sorted(
        [int(index_a), int(index_b)]
        for index_a, index_b in mapping.componentA_to_componentB.items()
    )
    edge_id = f"{left}->{right}"
    return {
        "edge_id": edge_id,
        "ligand_a": left,
        "ligand_b": right,
        "formal_charge_delta": charge_b - charge_a,
        "heavy_atom_count_a": heavy_a,
        "heavy_atom_count_b": heavy_b,
        "mapped_heavy_atom_count": heavy_core,
        "unmapped_heavy_atom_numerator": numerator,
        "heavy_atom_denominator": denominator,
        "symmetric_mapped_heavy_atom_fraction": mapped_fraction,
        "unmapped_heavy_atom_fraction": numerator / denominator,
        "max_side_unmapped_heavy_atom_count": max(heavy_a - heavy_core, heavy_b - heavy_core),
        "atom_map_sha256": hashlib.sha256(
            json.dumps(atom_pairs, separators=(",", ":")).encode("utf-8")
        ).hexdigest(),
        "selection_tiebreaker_sha256": hashlib.sha256(
            f"{QUALIFICATION_SEED}|{edge_id}".encode("utf-8")
        ).hexdigest(),
    }, None


def _load_molecules(path: Path) -> dict[str, openfe.SmallMoleculeComponent]:
    molecules = {}
    for molecule in Chem.SDMolSupplier(str(path), removeHs=False):
        if molecule is None:
            raise ValueError("qualification ligand SDF contains an unreadable record")
        component = openfe.SmallMoleculeComponent.from_rdkit(molecule)
        if component.name in molecules:
            raise ValueError(f"duplicate qualification ligand: {component.name}")
        molecules[component.name] = component
    return molecules


def _load_pairs(path: Path) -> list[tuple[str, str]]:
    with Path(path).open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != ["ligand_1", "ligand_2"]:
            raise ValueError("qualification requires exactly two redacted endpoint columns")
        return [(row["ligand_1"], row["ligand_2"]) for row in reader]


def _canonical_sha256(payload) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode(
            "utf-8"
        )
    ).hexdigest()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--qualification-lock", type=Path, required=True)
    parser.add_argument("--connectivity", type=Path, required=True)
    parser.add_argument("--ligands", type=Path, required=True)
    parser.add_argument("--protein", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


if __name__ == "__main__":
    main()
