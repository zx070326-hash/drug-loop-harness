"""Build hash-bound OpenFE control/repair transformations for PTP1B.

Run this script inside the qualified WSL OpenFE prefix. It generates AM1-BCC
charges once, reuses the same charged components and atom mappings in both
arms, and permits only an 11-to-21 lambda-window intervention.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import warnings
import zipfile
from datetime import datetime, timezone
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


SCHEMA = "fep_repair_profile_manifest.v1"
CONTROL_PROFILE_ID = "openfe-1.12.0-ptp1b-am1bcc-11w-5ns-v1"
REPAIR_PROFILE_ID = "openfe-1.12.0-ptp1b-am1bcc-21w-5ns-v1"
PILOT_CONTROL_PROFILE_ID = "openfe-1.12.0-ptp1b-am1bcc-11w-20ps-smoke-v1"
PILOT_REPAIR_PROFILE_ID = "openfe-1.12.0-ptp1b-am1bcc-21w-20ps-smoke-v1"


def main() -> None:
    args = _parse_args()
    root = Path(args.project_root).resolve()
    output = Path(args.output_dir).resolve()
    if output.exists():
        raise ValueError(f"refusing to overwrite existing repair inputs: {output}")
    temp = output.with_name(output.name + ".building")
    if temp.exists():
        raise ValueError(f"stale repair input build directory exists: {temp}")
    temp.mkdir(parents=True)

    try:
        plan_bytes = Path(args.risk_plan).read_bytes()
        plan = json.loads(plan_bytes.decode("utf-8"))
        charge_audit_bytes = Path(args.charge_audit).read_bytes()
        charge_audit = json.loads(charge_audit_bytes.decode("utf-8"))
        supersession_bytes = Path(args.supersession).read_bytes()
        supersession = json.loads(supersession_bytes.decode("utf-8"))
        if charge_audit.get("schema_version") != "fep_charge_repeatability_audit.v1":
            raise ValueError("unsupported charge repeatability audit")
        if supersession.get("schema_version") != "fep_repair_preoutcome_supersession.v1":
            raise ValueError("unsupported repair profile supersession")
        if supersession.get("trigger_charge_repeatability_audit_sha256") != hashlib.sha256(
            charge_audit_bytes
        ).hexdigest():
            raise ValueError("repair supersession is not bound to charge audit")
        selected_edges = sorted(
            proposal["edge_id"]
            for proposal in plan["proposals"]
            if proposal["disposition"] == "request_repair"
        )
        if not selected_edges:
            raise ValueError("risk plan contains no repair requests")
        if any(
            proposal["execution_authority_granted"] is not False
            for proposal in plan["proposals"]
        ):
            raise ValueError("risk plan grants execution authority")

        molecules = _load_selected_molecules(Path(args.ligands), selected_edges)
        if charge_audit.get("risk_action_plan_sha256") != hashlib.sha256(
            plan_bytes
        ).hexdigest():
            raise ValueError("charge audit belongs to another risk plan")
        charged, charge_binding = _inject_audited_charges(molecules, charge_audit)
        mappings, mapping_audit = _build_and_validate_mappings(
            charged,
            selected_edges,
            Path(args.results_archive),
        )
        protein = openfe.ProteinComponent.from_pdb_file(str(args.protein))
        solvent = openfe.SolventComponent()

        control_settings = _full_settings(lambda_windows=11)
        repair_settings = _full_settings(lambda_windows=21)
        full_diff = _deep_diff(
            _settings_dict(control_settings), _settings_dict(repair_settings)
        )
        expected_diff = {
            "lambda_settings.lambda_windows": [11, 21],
            "simulation_settings.n_replicas": [11, 21],
        }
        if full_diff != expected_diff:
            raise ValueError(f"unexpected full-profile settings diff: {full_diff}")

        pilot_control_settings = _pilot_settings(control_settings)
        pilot_repair_settings = _pilot_settings(repair_settings)
        pilot_diff = _deep_diff(
            _settings_dict(pilot_control_settings),
            _settings_dict(pilot_repair_settings),
        )
        if pilot_diff != expected_diff:
            raise ValueError(f"unexpected pilot-profile settings diff: {pilot_diff}")

        profiles = {
            "control": _build_profile(
                temp=temp,
                final_output=output,
                project_root=root,
                profile_key="control",
                profile_id=CONTROL_PROFILE_ID,
                settings=control_settings,
                relative_work=1.0,
                selected_edges=selected_edges,
                charged=charged,
                mappings=mappings,
                protein=protein,
                solvent=solvent,
            ),
            "repair": _build_profile(
                temp=temp,
                final_output=output,
                project_root=root,
                profile_key="repair",
                profile_id=REPAIR_PROFILE_ID,
                settings=repair_settings,
                relative_work=21.0 / 11.0,
                selected_edges=selected_edges,
                charged=charged,
                mappings=mappings,
                protein=protein,
                solvent=solvent,
            ),
        }
        pilot_profiles = {
            "control": _build_profile(
                temp=temp,
                final_output=output,
                project_root=root,
                profile_key="pilot_control",
                profile_id=PILOT_CONTROL_PROFILE_ID,
                settings=pilot_control_settings,
                relative_work=1.0,
                selected_edges=selected_edges,
                charged=charged,
                mappings=mappings,
                protein=protein,
                solvent=solvent,
            ),
            "repair": _build_profile(
                temp=temp,
                final_output=output,
                project_root=root,
                profile_key="pilot_repair",
                profile_id=PILOT_REPAIR_PROFILE_ID,
                settings=pilot_repair_settings,
                relative_work=21.0 / 11.0,
                selected_edges=selected_edges,
                charged=charged,
                mappings=mappings,
                protein=protein,
                solvent=solvent,
            ),
        }

        manifest = {
            "schema_version": SCHEMA,
            "manifest_id": "ptp1b-bi-lambda-density-repair-profiles-v1",
            "built_at": datetime.now(timezone.utc).isoformat(),
            "builder_sha256": _sha256(Path(__file__)),
            "selected_edge_ids": selected_edges,
            "risk_action_plan_sha256": hashlib.sha256(plan_bytes).hexdigest(),
            "input_sha256": {
                "ligands_sdf": _sha256(Path(args.ligands)),
                "protein_pdb": _sha256(Path(args.protein)),
                "source_results_archive": _sha256(Path(args.results_archive)),
                "environment_receipt": _sha256(Path(args.environment_receipt)),
                "charge_repeatability_audit": hashlib.sha256(
                    charge_audit_bytes
                ).hexdigest(),
                "preoutcome_supersession": hashlib.sha256(
                    supersession_bytes
                ).hexdigest(),
            },
            "runtime": _runtime_manifest(),
            "charge_audit": charge_binding,
            "mapping_audit": mapping_audit,
            "full_profiles": profiles,
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
                "post_outcome_mechanism_diagnostics_motivated_intervention": True,
                "audited_modal_charge_vectors_injected": True,
                "paired_repair_outcomes_present_at_build": False,
                "pilot_profiles_permit_scientific_evaluation": False,
                "execution_authority_granted": False,
                "scientific_claim_upgrade_permitted": False,
            },
        }
        manifest_path = temp / "profile-manifest.json"
        manifest_path.write_bytes(_pretty_bytes(manifest))
        temp.rename(output)
        final_manifest = output / "profile-manifest.json"
        print(
            json.dumps(
                {
                    "status": "PTP1B_PAIRED_REPAIR_INPUTS_BUILT",
                    "selected_edge_count": len(selected_edges),
                    "transformation_count": sum(
                        len(profile["transformation_files"])
                        for profile in (*profiles.values(), *pilot_profiles.values())
                    ),
                    "profile_manifest_sha256": _sha256(final_manifest),
                    "output": str(output),
                },
                indent=2,
            )
        )
    except Exception:
        if temp.exists():
            shutil.rmtree(temp)
        raise


def _load_selected_molecules(
    ligand_path: Path, selected_edges: list[str]
) -> dict[str, openfe.SmallMoleculeComponent]:
    required = {ligand for edge in selected_edges for ligand in edge.split("->")}
    molecules = {}
    for molecule in Chem.SDMolSupplier(str(ligand_path), removeHs=False):
        if molecule is None:
            raise ValueError("ligand SDF contains an unreadable record")
        component = openfe.SmallMoleculeComponent.from_rdkit(molecule)
        if component.name in required:
            molecules[component.name] = component
    if set(molecules) != required:
        raise ValueError(f"selected ligands missing from SDF: {sorted(required-set(molecules))}")
    return molecules


def _inject_audited_charges(
    molecules: Mapping[str, openfe.SmallMoleculeComponent],
    charge_audit: Mapping[str, Any],
) -> tuple[dict[str, openfe.SmallMoleculeComponent], dict[str, Any]]:
    charged = {}
    audit = {}
    warnings.filterwarnings(
        "ignore",
        message="Partial charges have been provided, these will preferentially be used",
    )
    for name in sorted(molecules):
        source = charge_audit.get("ligand_audits", {}).get(name)
        if not isinstance(source, Mapping) or source.get("modal_majority_present") is not True:
            raise ValueError(f"charge audit lacks a strict majority for {name}")
        values = [float(value) for value in source["modal_partial_charges_e"]]
        offmol = molecules[name].to_openff()
        if len(values) != offmol.n_atoms:
            raise ValueError(f"audited charge vector length mismatch for {name}")
        digest = hashlib.sha256(
            json.dumps(values, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        if digest != source["modal_partial_charge_sha256"]:
            raise ValueError(f"audited charge vector hash mismatch for {name}")
        offmol.partial_charges = np.asarray(values) * unit.elementary_charge
        charged[name] = openfe.SmallMoleculeComponent.from_openff(offmol)
        audit[name] = {
            "atom_count": len(values),
            "formal_charge": int(Chem.rdmolops.GetFormalCharge(molecules[name].to_rdkit())),
            "partial_charge_sum_e": sum(values),
            "partial_charge_sha256": digest,
            "charge_selection_rule": "strict_majority_modal_vector_from_three_am1bcc_assignments",
            "source_modal_count": int(source["modal_count"]),
            "source_trial_count": int(source["trial_count"]),
        }
    return charged, audit


def _build_and_validate_mappings(
    molecules: Mapping[str, openfe.SmallMoleculeComponent],
    selected_edges: list[str],
    results_archive: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    mapper = kartograf.KartografAtomMapper(
        atom_map_hydrogens=True,
        additional_mapping_filter_functions=[
            filter_ringbreak_changes,
            filter_ringsize_changes,
            filter_whole_rings_only,
        ],
    )
    with zipfile.ZipFile(results_archive) as archive:
        network = json.loads(archive.read("all_network_properties.json"))
    source = {
        f"{value['ligand_A']}->{value['ligand_B']}": value
        for value in network["Edges"].values()
    }
    mappings = {}
    audit = {}
    for edge_id in selected_edges:
        left, right = edge_id.split("->")
        suggestions = list(mapper.suggest_mappings(molecules[left], molecules[right]))
        if len(suggestions) != 1:
            raise ValueError(f"expected one deterministic mapping for {edge_id}")
        mapping = suggestions[0]
        rd_left = molecules[left].to_rdkit()
        rd_right = molecules[right].to_rdkit()
        heavy_left = sum(atom.GetAtomicNum() > 1 for atom in rd_left.GetAtoms())
        heavy_right = sum(atom.GetAtomicNum() > 1 for atom in rd_right.GetAtoms())
        heavy_core = sum(
            rd_left.GetAtomWithIdx(index_a).GetAtomicNum() > 1
            and rd_right.GetAtomWithIdx(index_b).GetAtomicNum() > 1
            for index_a, index_b in mapping.componentA_to_componentB.items()
        )
        expected = source[edge_id]
        observed = {
            "num_heavy_core": heavy_core,
            "num_heavy_dummy_A": heavy_left - heavy_core,
            "num_heavy_dummy_B": heavy_right - heavy_core,
        }
        for field, value in observed.items():
            if int(expected[field]) != value:
                raise ValueError(
                    f"mapping/source mismatch for {edge_id} {field}: {value} != {expected[field]}"
                )
        atom_pairs = sorted(
            [int(index_a), int(index_b)]
            for index_a, index_b in mapping.componentA_to_componentB.items()
        )
        audit[edge_id] = {
            **observed,
            "mapped_atom_count_including_hydrogen": len(atom_pairs),
            "atom_map_sha256": hashlib.sha256(
                json.dumps(atom_pairs, separators=(",", ":")).encode("utf-8")
            ).hexdigest(),
            "source_feature_match": True,
        }
        mappings[edge_id] = mapping
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
    *,
    temp: Path,
    final_output: Path,
    project_root: Path,
    profile_key: str,
    profile_id: str,
    settings,
    relative_work: float,
    selected_edges: list[str],
    charged: Mapping[str, openfe.SmallMoleculeComponent],
    mappings: Mapping[str, Any],
    protein: openfe.ProteinComponent,
    solvent: openfe.SolventComponent,
) -> dict[str, Any]:
    directory = temp / "transformations" / profile_key
    directory.mkdir(parents=True)
    transformations = {}
    files = []
    for edge_id in selected_edges:
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
                protocol=RelativeHybridTopologyProtocol(
                    settings=settings.model_copy(deep=True)
                ),
                name=f"{profile_key}_{leg}_{left}_{right}",
            )
            destination = directory / f"{leg}_{left}_{right}.json"
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


def _settings_dict(settings) -> dict[str, Any]:
    return json.loads(settings.model_dump_json())


def _deep_diff(left: Mapping[str, Any], right: Mapping[str, Any]) -> dict[str, list[Any]]:
    differences = {}

    def walk(a: Any, b: Any, prefix: str) -> None:
        if isinstance(a, Mapping) and isinstance(b, Mapping):
            if set(a) != set(b):
                raise ValueError(f"settings key mismatch at {prefix}")
            for key in sorted(a):
                walk(a[key], b[key], f"{prefix}.{key}" if prefix else str(key))
        elif a != b:
            differences[prefix] = [a, b]

    walk(left, right, "")
    return differences


def _runtime_manifest() -> dict[str, Any]:
    packages = json.loads(
        subprocess.run(
            ["conda", "list", "--json"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    )
    required = {
        "ambertools",
        "gufe",
        "kartograf",
        "openfe",
        "openff-toolkit",
        "openmm",
        "pymbar",
    }
    package_versions = {
        package["name"]: package["version"]
        for package in packages
        if package["name"] in required
    }
    if set(package_versions) != required:
        raise ValueError(
            f"repair runtime package inventory is incomplete: {sorted(required-set(package_versions))}"
        )
    return {
        "python": sys.version.split()[0],
        "conda_packages": package_versions,
        "import_reported_versions": {
            "openfe": openfe.__version__,
            "gufe": gufe.__version__,
            "openmm": openmm.__version__,
            "pymbar": pymbar.__version__,
            "kartograf": kartograf.__version__,
        },
        "antechamber_path": shutil.which("antechamber"),
        "nvidia_smi": subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=name,driver_version",
                "--format=csv,noheader",
            ],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip(),
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode(
        "utf-8"
    )


def _pretty_bytes(value: Any) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n").encode(
        "utf-8"
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--ligands", type=Path, required=True)
    parser.add_argument("--protein", type=Path, required=True)
    parser.add_argument("--risk-plan", type=Path, required=True)
    parser.add_argument("--results-archive", type=Path, required=True)
    parser.add_argument("--environment-receipt", type=Path, required=True)
    parser.add_argument("--charge-audit", type=Path, required=True)
    parser.add_argument("--supersession", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args()


if __name__ == "__main__":
    main()
