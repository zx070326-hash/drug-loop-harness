"""Repeat AM1-BCC assignment to audit charge reproducibility before FEP runs."""

from __future__ import annotations

import argparse
import hashlib
import json
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


def main() -> None:
    args = _parse_args()
    plan_bytes = args.risk_plan.read_bytes()
    plan = json.loads(plan_bytes.decode("utf-8"))
    required = {
        ligand
        for proposal in plan["proposals"]
        if proposal["disposition"] == "request_repair"
        for ligand in proposal["edge_id"].split("->")
    }
    molecules = {
        molecule.GetProp("_Name"): molecule
        for molecule in Chem.SDMolSupplier(str(args.ligands), removeHs=False)
        if molecule is not None and molecule.GetProp("_Name") in required
    }
    if set(molecules) != required:
        raise ValueError("charge audit could not load all selected ligands")
    registry = ToolkitRegistry([RDKitToolkitWrapper(), AmberToolsToolkitWrapper()])
    audits = {}
    for name in sorted(molecules):
        trials = []
        vectors = []
        for trial_index in range(args.trials):
            component = openfe.SmallMoleculeComponent.from_rdkit(
                Chem.Mol(molecules[name])
            )
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
            "trial_count": args.trials,
            "unique_charge_hash_count": len(counts),
            "trial_results": trials,
            "modal_partial_charge_sha256": modal_hash,
            "modal_partial_charges_e": modal_vector,
            "modal_count": modal_count,
            "modal_majority_present": modal_count > args.trials / 2,
            "maximum_pairwise_absolute_charge_difference_e": max(
                float(np.max(np.abs(left - right)))
                for index, left in enumerate(vectors)
                for right in vectors[index:]
            ),
        }
    nondeterministic = sorted(
        name for name, audit in audits.items() if audit["unique_charge_hash_count"] > 1
    )
    payload = {
        "schema_version": "fep_charge_repeatability_audit.v1",
        "status": (
            "NONDETERMINISTIC_AM1BCC_ASSIGNMENT_OBSERVED"
            if nondeterministic
            else "PASS_IDENTICAL_AM1BCC_ASSIGNMENTS"
        ),
        "analysis_role": "pre_paired_outcome_profile_reproducibility_audit",
        "risk_action_plan_sha256": hashlib.sha256(plan_bytes).hexdigest(),
        "ligands_sdf_sha256": _sha256(args.ligands),
        "implementation_sha256": _sha256(Path(__file__)),
        "trial_count_per_ligand": args.trials,
        "ligand_audits": audits,
        "nondeterministic_ligand_ids": nondeterministic,
        "required_profile_rule": (
            "select a strict-majority modal charge vector from three assignments; "
            "fail closed when no strict majority exists"
        ),
        "paired_repair_outcomes_present": False,
        "scientific_claim_upgrade_permitted": False,
    }
    data = (json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n").encode(
        "utf-8"
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(data)
    print(
        json.dumps(
            {
                "status": payload["status"],
                "nondeterministic_ligand_ids": nondeterministic,
                "output_sha256": hashlib.sha256(data).hexdigest(),
                "output": str(args.output),
            },
            indent=2,
        )
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ligands", type=Path, required=True)
    parser.add_argument("--risk-plan", type=Path, required=True)
    parser.add_argument("--trials", type=int, default=3)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.trials < 3 or args.trials % 2 == 0:
        parser.error("--trials must be an odd integer of at least 3")
    return args


if __name__ == "__main__":
    main()
