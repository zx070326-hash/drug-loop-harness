"""Audit AM1-BCC reproducibility for the frozen JNK1 confirmation ligands."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

import numpy as np
import openfe
from openff.toolkit import AmberToolsToolkitWrapper, RDKitToolkitWrapper
from openff.toolkit.utils.toolkit_registry import (
    ToolkitRegistry,
    toolkit_registry_manager,
)
from rdkit import Chem


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from loop_harness.v2.fep_untouched_protocol import (  # noqa: E402
    verify_untouched_protocol_design_lock,
)


def main() -> None:
    args = _parse_args()
    root = Path(args.project_root).resolve()
    lock_bytes = args.design_lock.read_bytes()
    lock = json.loads(lock_bytes.decode("utf-8"))
    verify_untouched_protocol_design_lock(root, lock)
    qualification_bytes = args.qualification.read_bytes()
    qualification = json.loads(qualification_bytes.decode("utf-8"))
    if hashlib.sha256(qualification_bytes).hexdigest() != lock["source_bindings"][
        "target_qualification_sha256"
    ]:
        raise ValueError("charge audit qualification differs from protocol design lock")
    if _sha256(args.ligands) != lock["source_bindings"]["ligands_worktree_sha256"]:
        raise ValueError("charge audit ligands differ from protocol design lock")
    required = {
        ligand
        for edge in qualification["selected_edges"]
        for ligand in (edge["ligand_a"], edge["ligand_b"])
    }
    molecules = {
        molecule.GetProp("_Name"): molecule
        for molecule in Chem.SDMolSupplier(str(args.ligands), removeHs=False)
        if molecule is not None and molecule.GetProp("_Name") in required
    }
    if set(molecules) != required:
        raise ValueError("charge audit could not load all selected JNK1 ligands")
    trials_per_ligand = lock["profile_design"]["charge_repeatability_trials"]
    registry = ToolkitRegistry([RDKitToolkitWrapper(), AmberToolsToolkitWrapper()])
    audits = {}
    for name in sorted(molecules):
        trials = []
        vectors = []
        for trial_index in range(trials_per_ligand):
            component = openfe.SmallMoleculeComponent.from_rdkit(Chem.Mol(molecules[name]))
            offmol = component.to_openff()
            with toolkit_registry_manager(registry):
                offmol.assign_partial_charges(
                    partial_charge_method="am1bcc",
                    use_conformers=offmol.conformers,
                )
            vector = np.array([float(value.m) for value in offmol.partial_charges])
            digest = hashlib.sha256(
                json.dumps(vector.tolist(), separators=(",", ":")).encode("utf-8")
            ).hexdigest()
            vectors.append(vector)
            trials.append(
                {
                    "trial_index": trial_index,
                    "partial_charge_sha256": digest,
                    "partial_charge_sum_e": float(vector.sum()),
                }
            )
        counts = Counter(trial["partial_charge_sha256"] for trial in trials)
        modal_hash, modal_count = counts.most_common(1)[0]
        modal_vector = next(
            vector.tolist()
            for trial, vector in zip(trials, vectors)
            if trial["partial_charge_sha256"] == modal_hash
        )
        audits[name] = {
            "trial_count": trials_per_ligand,
            "unique_charge_hash_count": len(counts),
            "trial_results": trials,
            "modal_partial_charge_sha256": modal_hash,
            "modal_partial_charges_e": modal_vector,
            "modal_count": modal_count,
            "modal_majority_present": modal_count > trials_per_ligand / 2,
            "maximum_pairwise_absolute_charge_difference_e": max(
                float(np.max(np.abs(left - right)))
                for index, left in enumerate(vectors)
                for right in vectors[index:]
            ),
        }
    no_majority = sorted(
        name for name, audit in audits.items() if not audit["modal_majority_present"]
    )
    payload = {
        "schema_version": "fep_charge_repeatability_audit.v1",
        "audit_id": "jnk1-untouched-confirmation-am1bcc-audit-v1",
        "status": "ACCEPTED_MODAL_CHARGE_VECTORS" if not no_majority else "REJECTED_NO_MODAL_MAJORITY",
        "protocol_design_lock_sha256": hashlib.sha256(lock_bytes).hexdigest(),
        "target_qualification_sha256": hashlib.sha256(qualification_bytes).hexdigest(),
        "ligands_sdf_sha256": _sha256(args.ligands),
        "implementation_sha256": _sha256(Path(__file__)),
        "trial_count_per_ligand": trials_per_ligand,
        "ligand_audits": audits,
        "no_modal_majority_ligand_ids": no_majority,
        "required_profile_rule": lock["profile_design"]["charge_selection_rule"],
        "experimental_labels_used": False,
        "public_fep_results_used": False,
        "paired_repair_outcomes_present": False,
        "simulation_execution_authority_granted": False,
        "scientific_claim_upgrade_permitted": False,
    }
    data = (json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n").encode(
        "utf-8"
    )
    if args.output.exists() and args.output.read_bytes() != data:
        raise ValueError("refusing to overwrite a different JNK1 charge audit")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(data)
    print(
        json.dumps(
            {
                "status": payload["status"],
                "ligand_count": len(audits),
                "no_modal_majority_ligand_ids": no_majority,
                "output_sha256": hashlib.sha256(data).hexdigest(),
                "simulation_execution_authority_granted": False,
            },
            indent=2,
        )
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--design-lock", type=Path, required=True)
    parser.add_argument("--qualification", type=Path, required=True)
    parser.add_argument("--ligands", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


if __name__ == "__main__":
    main()
