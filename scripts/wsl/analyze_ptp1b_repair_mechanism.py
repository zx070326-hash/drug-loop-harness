"""Audit PTP1B repair-request failure mechanisms from published MBAR inputs."""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import zipfile
from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np
import pymbar
from pymbar import MBAR


def main() -> None:
    args = _parse_args()
    plan_bytes = args.risk_plan.read_bytes()
    plan = json.loads(plan_bytes.decode("utf-8"))
    selected = sorted(
        proposal["edge_id"]
        for proposal in plan["proposals"]
        if proposal["disposition"] == "request_repair"
    )
    aggregate_rows = _aggregate_rows(args.official_aggregate)
    run_audits: list[dict[str, Any]] = []
    source_features = {}
    with zipfile.ZipFile(args.results_archive) as archive:
        network = json.loads(archive.read("all_network_properties.json"))
        edges = {
            f"{value['ligand_A']}->{value['ligand_B']}": value
            for value in network["Edges"].values()
        }
        for edge_id in selected:
            if edge_id not in aggregate_rows or edge_id not in edges:
                raise ValueError(f"selected edge is missing from source inputs: {edge_id}")
            source_features[edge_id] = {
                key: edges[edge_id][key]
                for key in (
                    "num_heavy_core",
                    "num_heavy_dummy_A",
                    "num_heavy_dummy_B",
                    "difference_num_rings_AB",
                    "difference_num_rot_bonds_AB",
                    "morgan_tanimoto_similarity",
                    "difference_solvent_accessible_surface_area",
                )
            }
            left, right = edge_id.split("->")
            row = aggregate_rows[edge_id]
            for leg in ("complex", "solvent"):
                for repeat in range(3):
                    prefix = f"{leg}_{left}_{right}_repeat_{repeat}/"
                    counts = np.loadtxt(
                        io.BytesIO(archive.read(prefix + "N_l.txt"))
                    ).astype(int)
                    reduced = np.loadtxt(
                        io.BytesIO(archive.read(prefix + "u_ln.txt"))
                    )
                    if reduced.shape == (int(counts.sum()), len(counts)):
                        reduced = reduced.T
                    if reduced.shape != (len(counts), int(counts.sum())):
                        raise ValueError(f"unexpected u_ln shape for {prefix}")
                    overlap = MBAR(reduced, counts, verbose=False).compute_overlap()[
                        "matrix"
                    ]
                    adjacent = [
                        float(min(overlap[index, index + 1], overlap[index + 1, index]))
                        for index in range(len(counts) - 1)
                    ]
                    weak_index = int(np.argmin(adjacent))
                    official = float(
                        row[f"{leg}_repeat_{repeat}_smallest_overlap"]
                    )
                    run_audits.append(
                        {
                            "edge_id": edge_id,
                            "leg": leg,
                            "repeat_index": repeat,
                            "lambda_state_count": len(counts),
                            "samples_per_state": counts.tolist(),
                            "official_smallest_overlap": official,
                            "official_overlap_below_0_03": official < 0.03,
                            "weakest_adjacent_state_pair": [weak_index, weak_index + 1],
                            "weakest_symmetric_adjacent_overlap": adjacent[weak_index],
                        }
                    )
    pair_counts = Counter(
        tuple(item["weakest_adjacent_state_pair"]) for item in run_audits
    )
    payload = {
        "schema_version": "fep_repair_mechanism_audit.v1",
        "analysis_role": "post_outcome_development_only",
        "selected_edge_ids": selected,
        "risk_action_plan_sha256": hashlib.sha256(plan_bytes).hexdigest(),
        "source_sha256": {
            "results_archive": _sha256(args.results_archive),
            "official_aggregate": _sha256(args.official_aggregate),
            "analysis_implementation": _sha256(Path(__file__)),
        },
        "runtime": {"pymbar": pymbar.__version__, "numpy": np.__version__},
        "source_edge_features": source_features,
        "run_audits": run_audits,
        "summary": {
            "leg_repeat_count": len(run_audits),
            "all_selected_leg_repeats_below_official_overlap_0_03": all(
                item["official_overlap_below_0_03"] for item in run_audits
            ),
            "lambda_state_counts": sorted(
                {item["lambda_state_count"] for item in run_audits}
            ),
            "samples_per_state_counts": sorted(
                {
                    value
                    for item in run_audits
                    for value in item["samples_per_state"]
                }
            ),
            "weakest_adjacent_state_pair_counts": {
                f"{left}-{right}": count
                for (left, right), count in sorted(pair_counts.items())
            },
        },
        "mechanism_interpretation": {
            "single_stochastic_repeat_failure_supported": False,
            "systematic_path_overlap_failure_supported": True,
            "uniform_21_window_intervention_selected": True,
            "edge_specific_window_tuning_used": False,
            "causal_repair_effectiveness_established": False,
        },
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
                "status": "PTP1B_REPAIR_MECHANISM_AUDIT_COMPLETE",
                "selected_edge_count": len(selected),
                "leg_repeat_count": len(run_audits),
                "all_below_overlap_threshold": payload["summary"][
                    "all_selected_leg_repeats_below_official_overlap_0_03"
                ],
                "weakest_pair_counts": payload["summary"][
                    "weakest_adjacent_state_pair_counts"
                ],
                "output_sha256": hashlib.sha256(data).hexdigest(),
                "output": str(args.output),
            },
            indent=2,
        )
    )


def _aggregate_rows(path: Path) -> dict[str, dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return {
            f"{row['ligand_A']}->{row['ligand_B']}": row
            for row in csv.DictReader(handle)
            if row.get("partner_id") == "BI"
            and row.get("dataset_name") == "bi_public/jacs_ptp1b/result_data"
        }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--risk-plan", type=Path, required=True)
    parser.add_argument("--results-archive", type=Path, required=True)
    parser.add_argument("--official-aggregate", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


if __name__ == "__main__":
    main()
