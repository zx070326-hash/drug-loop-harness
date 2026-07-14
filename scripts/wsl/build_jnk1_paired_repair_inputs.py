"""Build frozen JNK1 control/repair OpenFE transformations without outcomes."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import warnings
from pathlib import Path
from typing import Any, Mapping

import gufe
import kartograf
import numpy as np
import openfe
import openmm
import pymbar
from kartograf.filters import (
    filter_ringbreak_changes,
    filter_ringsize_changes,
    filter_whole_rings_only,
)
from openff.units import unit
from rdkit import Chem

from openfe.protocols.openmm_rfe import RelativeHybridTopologyProtocol


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from loop_harness.v2.fep_untouched_protocol import (  # noqa: E402
    verify_untouched_protocol_design_lock,
)


SCHEMA = "fep_repair_profile_manifest.v1"
CONTROL_PROFILE_ID = "openfe-1.12.0-jnk1-am1bcc-11w-5ns-v1"
REPAIR_PROFILE_ID = "openfe-1.12.0-jnk1-am1bcc-21w-5ns-v1"
PILOT_CONTROL_PROFILE_ID = "openfe-1.12.0-jnk1-am1bcc-11w-20ps-smoke-v1"
PILOT_REPAIR_PROFILE_ID = "openfe-1.12.0-jnk1-am1bcc-21w-20ps-smoke-v1"


def main() -> None:
    args = _parse_args()
    root = Path(args.project_root).resolve()
    output = Path(args.output_dir).resolve()
    if output.exists():
        raise ValueError(f"refusing to overwrite existing JNK1 profiles: {output}")
    temp = output.with_name(output.name + ".building")
    if temp.exists():
        raise ValueError(f"stale JNK1 profile build directory exists: {temp}")
    lock_bytes = args.design_lock.read_bytes()
    lock = json.loads(lock_bytes.decode("utf-8"))
    verify_untouched_protocol_design_lock(root, lock)
    qualification_bytes = args.qualification.read_bytes()
    qualification = json.loads(qualification_bytes.decode("utf-8"))
    charge_bytes = args.charge_audit.read_bytes()
    charge_audit = json.loads(charge_bytes.decode("utf-8"))
    if hashlib.sha256(qualification_bytes).hexdigest() != lock["source_bindings"][
        "target_qualification_sha256"
    ]:
        raise ValueError("profile qualification differs from protocol design lock")
    if charge_audit.get("status") != "ACCEPTED_MODAL_CHARGE_VECTORS":
        raise ValueError("profile build requires accepted modal charge vectors")
    if charge_audit.get("protocol_design_lock_sha256") != hashlib.sha256(
        lock_bytes
    ).hexdigest():
        raise ValueError("charge audit belongs to another protocol design")
    if _sha256(args.ligands) != lock["source_bindings"]["ligands_worktree_sha256"]:
        raise ValueError("profile ligands differ from protocol design lock")
    if _sha256(args.protein) != lock["source_bindings"]["protein_worktree_sha256"]:
        raise ValueError("profile protein differs from protocol design lock")

    temp.mkdir(parents=True)
    try:
        selected_edges = lock["selected_edge_ids"]
        molecules = _load_selected_molecules(args.ligands, selected_edges)
        charged, charge_binding = _inject_audited_charges(molecules, charge_audit)
        expected_mapping_hashes = {
            edge["edge_id"]: edge["atom_map_sha256"]
            for edge in qualification["selected_edges"]
        }
        mappings, mapping_audit = _build_and_validate_mappings(
            charged, selected_edges, expected_mapping_hashes
        )
        protein = openfe.ProteinComponent.from_pdb_file(str(args.protein))
        solvent = openfe.SolventComponent()
        control_settings = _full_settings(11)
        repair_settings = _full_settings(21)
        expected_diff = {
            "lambda_settings.lambda_windows": [11, 21],
            "simulation_settings.n_replicas": [11, 21],
        }
        full_diff = _deep_diff(
            _settings_dict(control_settings), _settings_dict(repair_settings)
        )
        if full_diff != expected_diff:
            raise ValueError(f"unexpected JNK1 full settings diff: {full_diff}")
        pilot_control = _pilot_settings(control_settings)
        pilot_repair = _pilot_settings(repair_settings)
        pilot_diff = _deep_diff(
            _settings_dict(pilot_control), _settings_dict(pilot_repair)
        )
        if pilot_diff != expected_diff:
            raise ValueError(f"unexpected JNK1 pilot settings diff: {pilot_diff}")
        full_profiles = {
            "control": _build_profile(
                temp, output, root, "control", CONTROL_PROFILE_ID, control_settings,
                1.0, selected_edges, charged, mappings, protein, solvent
            ),
            "repair": _build_profile(
                temp, output, root, "repair", REPAIR_PROFILE_ID, repair_settings,
                21.0 / 11.0, selected_edges, charged, mappings, protein, solvent
            ),
        }
        pilot_profiles = {
            "control": _build_profile(
                temp, output, root, "pilot_control", PILOT_CONTROL_PROFILE_ID,
                pilot_control, 1.0, selected_edges, charged, mappings, protein, solvent
            ),
            "repair": _build_profile(
                temp, output, root, "pilot_repair", PILOT_REPAIR_PROFILE_ID,
                pilot_repair, 21.0 / 11.0, selected_edges, charged, mappings, protein,
                solvent
            ),
        }
        manifest = {
            "schema_version": SCHEMA,
            "manifest_id": "jnk1-untouched-lambda-density-repair-profiles-v1",
            "builder_sha256": _sha256(Path(__file__)),
            "selected_edge_ids": selected_edges,
            "input_sha256": {
                "protocol_design_lock": hashlib.sha256(lock_bytes).hexdigest(),
                "target_qualification": hashlib.sha256(qualification_bytes).hexdigest(),
                "charge_repeatability_audit": hashlib.sha256(charge_bytes).hexdigest(),
                "ligands_sdf": _sha256(args.ligands),
                "protein_pdb": _sha256(args.protein),
            },
            "runtime": _runtime_manifest(),
            "charge_audit": charge_binding,
            "mapping_audit": mapping_audit,
            "full_profiles": full_profiles,
            "pilot_profiles": pilot_profiles,
            "full_profile_settings_diff": full_diff,
            "pilot_profile_settings_diff": pilot_diff,
            "intervention": {
                "kind": "uniform_nested_lambda_interval_bisection",
                "control_windows": 11,
                "repair_windows": 21,
                "same_charged_components": True,
                "same_atom_mappings": True,
                "same_forcefield": True,
                "same_sampler_method": True,
                "same_production_length": True,
            },
            "scientific_controls": {
                "policy_labels_used_for_profile_construction": False,
                "post_outcome_mechanism_diagnostics_motivated_intervention": False,
                "audited_modal_charge_vectors_injected": True,
                "paired_repair_outcomes_present_at_build": False,
                "pilot_profiles_permit_scientific_evaluation": False,
                "execution_authority_granted": False,
                "scientific_claim_upgrade_permitted": False,
            },
        }
        (temp / "profile-manifest.json").write_bytes(_pretty_bytes(manifest))
        temp.rename(output)
        final_manifest = output / "profile-manifest.json"
        print(
            json.dumps(
                {
                    "status": "JNK1_PAIRED_REPAIR_INPUTS_BUILT",
                    "selected_edge_count": len(selected_edges),
                    "transformation_count": 24,
                    "profile_manifest_sha256": _sha256(final_manifest),
                    "simulation_execution_authority_granted": False,
                },
                indent=2,
            )
        )
    except Exception:
        if temp.exists():
            shutil.rmtree(temp)
        raise


def _load_selected_molecules(path: Path, edges: list[str]):
    required = {name for edge in edges for name in edge.split("->")}
    molecules = {}
    for molecule in Chem.SDMolSupplier(str(path), removeHs=False):
        if molecule is None:
            raise ValueError("JNK1 ligand SDF contains an unreadable record")
        component = openfe.SmallMoleculeComponent.from_rdkit(molecule)
        if component.name in required:
            molecules[component.name] = component
    if set(molecules) != required:
        raise ValueError(f"selected JNK1 ligands missing: {sorted(required-set(molecules))}")
    return molecules


def _inject_audited_charges(molecules, charge_audit):
    charged = {}
    audit = {}
    warnings.filterwarnings(
        "ignore", message="Partial charges have been provided, these will preferentially be used"
    )
    for name in sorted(molecules):
        source = charge_audit["ligand_audits"].get(name)
        if not source or source.get("modal_majority_present") is not True:
            raise ValueError(f"JNK1 charge audit lacks a strict majority for {name}")
        values = [float(value) for value in source["modal_partial_charges_e"]]
        offmol = molecules[name].to_openff()
        if len(values) != offmol.n_atoms:
            raise ValueError(f"JNK1 charge vector length mismatch for {name}")
        digest = hashlib.sha256(
            json.dumps(values, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        if digest != source["modal_partial_charge_sha256"]:
            raise ValueError(f"JNK1 charge vector hash mismatch for {name}")
        offmol.partial_charges = np.asarray(values) * unit.elementary_charge
        charged[name] = openfe.SmallMoleculeComponent.from_openff(offmol)
        audit[name] = {
            "atom_count": len(values),
            "formal_charge": int(Chem.GetFormalCharge(molecules[name].to_rdkit())),
            "partial_charge_sum_e": sum(values),
            "partial_charge_sha256": digest,
            "source_modal_count": int(source["modal_count"]),
            "source_trial_count": int(source["trial_count"]),
        }
    return charged, audit


def _build_and_validate_mappings(molecules, edges, expected_hashes):
    mapper = kartograf.KartografAtomMapper(
        atom_map_hydrogens=True,
        additional_mapping_filter_functions=[
            filter_ringbreak_changes,
            filter_ringsize_changes,
            filter_whole_rings_only,
        ],
    )
    mappings = {}
    audit = {}
    for edge_id in edges:
        left, right = edge_id.split("->")
        suggestions = list(mapper.suggest_mappings(molecules[left], molecules[right]))
        if len(suggestions) != 1:
            raise ValueError(f"expected one deterministic JNK1 mapping for {edge_id}")
        mapping = suggestions[0]
        atom_pairs = sorted(
            [int(index_a), int(index_b)]
            for index_a, index_b in mapping.componentA_to_componentB.items()
        )
        digest = hashlib.sha256(
            json.dumps(atom_pairs, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        if digest != expected_hashes[edge_id]:
            raise ValueError(f"JNK1 mapping differs from qualification for {edge_id}")
        mappings[edge_id] = mapping
        audit[edge_id] = {
            "atom_map_sha256": digest,
            "qualification_feature_match": True,
        }
    return mappings, audit


def _full_settings(lambda_windows: int):
    settings = RelativeHybridTopologyProtocol.default_settings()
    settings.protocol_repeats = 1
    settings.engine_settings.compute_platform = "CUDA"
    settings.forcefield_settings.small_molecule_forcefield = "openff-2.2.0"
    settings.lambda_settings.lambda_windows = lambda_windows
    settings.simulation_settings.n_replicas = lambda_windows
    settings.simulation_settings.sampler_method = "repex"
    settings.simulation_settings.equilibration_length = 1.0 * unit.nanosecond
    settings.simulation_settings.production_length = 5.0 * unit.nanosecond
    return settings


def _pilot_settings(full_settings):
    settings = full_settings.model_copy(deep=True)
    settings.simulation_settings.equilibration_length = 10.0 * unit.picosecond
    settings.simulation_settings.production_length = 20.0 * unit.picosecond
    settings.simulation_settings.real_time_analysis_interval = 10.0 * unit.picosecond
    settings.simulation_settings.real_time_analysis_minimum_time = 10.0 * unit.picosecond
    settings.output_settings.checkpoint_interval = 10.0 * unit.picosecond
    settings.output_settings.positions_write_frequency = 10.0 * unit.picosecond
    return settings


def _build_profile(
    temp: Path,
    final_output: Path,
    project_root: Path,
    profile_key: str,
    profile_id: str,
    settings,
    relative_work: float,
    edges,
    charged,
    mappings,
    protein,
    solvent,
):
    directory = temp / "transformations" / profile_key
    directory.mkdir(parents=True)
    transformations = {}
    files = []
    for edge_id in edges:
        left, right = edge_id.split("->")
        transformations[edge_id] = {}
        for leg in ("complex", "solvent"):
            components_a = {"ligand": charged[left], "solvent": solvent}
            components_b = {"ligand": charged[right], "solvent": solvent}
            if leg == "complex":
                components_a["protein"] = protein
                components_b["protein"] = protein
            transformation = openfe.Transformation(
                stateA=openfe.ChemicalSystem(components_a),
                stateB=openfe.ChemicalSystem(components_b),
                mapping=mappings[edge_id],
                protocol=RelativeHybridTopologyProtocol(settings=settings.model_copy(deep=True)),
                name=f"{profile_key}_{leg}_{left}_{right}",
            )
            destination = directory / f"{leg}_{_safe(left)}_{_safe(right)}.json"
            transformation.to_json(destination)
            relative = destination.relative_to(temp)
            final_path = final_output / relative
            record = {
                "path": final_path.relative_to(project_root).as_posix(),
                "sha256": _sha256(destination),
                "bytes": destination.stat().st_size,
            }
            transformations[edge_id][leg] = record
            files.append(record)
    settings_dict = _settings_dict(settings)
    return {
        "oracle_profile_id": profile_id,
        "settings": settings_dict,
        "settings_sha256": hashlib.sha256(_canonical_bytes(settings_dict)).hexdigest(),
        "relative_work_units_per_leg_repeat": relative_work,
        "transformations": transformations,
        "transformation_files": files,
    }


def _safe(value: str) -> str:
    return value.replace(" ", "_").replace("/", "_")


def _settings_dict(settings):
    return json.loads(settings.model_dump_json())


def _deep_diff(left: Mapping[str, Any], right: Mapping[str, Any]):
    differences = {}

    def walk(a, b, prefix):
        if isinstance(a, Mapping) and isinstance(b, Mapping):
            if set(a) != set(b):
                raise ValueError(f"JNK1 settings key mismatch at {prefix}")
            for key in sorted(a):
                walk(a[key], b[key], f"{prefix}.{key}" if prefix else str(key))
        elif a != b:
            differences[prefix] = [a, b]

    walk(left, right, "")
    return differences


def _runtime_manifest():
    packages = json.loads(
        subprocess.run(["conda", "list", "--json"], capture_output=True, text=True, check=True).stdout
    )
    required = {"ambertools", "gufe", "kartograf", "openfe", "openff-toolkit", "openmm", "pymbar"}
    versions = {item["name"]: item["version"] for item in packages if item["name"] in required}
    if set(versions) != required:
        raise ValueError(f"JNK1 runtime package inventory incomplete: {sorted(required-set(versions))}")
    return {
        "python": sys.version.split()[0],
        "conda_packages": versions,
        "import_reported_versions": {
            "openfe": openfe.__version__,
            "gufe": gufe.__version__,
            "openmm": openmm.__version__,
            "pymbar": pymbar.__version__,
            "kartograf": kartograf.__version__,
        },
        "antechamber_path": shutil.which("antechamber"),
    }


def _canonical_bytes(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _pretty_bytes(value):
    return (json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n").encode("utf-8")


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
    parser.add_argument("--charge-audit", type=Path, required=True)
    parser.add_argument("--ligands", type=Path, required=True)
    parser.add_argument("--protein", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args()


if __name__ == "__main__":
    main()
