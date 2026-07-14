"""Register a label-free FEP edge cohort from a prebound aggregate artifact."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import zipfile
from pathlib import Path


def main() -> None:
    args = _parse_args()
    aggregate_sha256 = _sha256(args.official_aggregate)
    deviation_sha256 = _sha256(args.protocol_deviation)
    with args.official_aggregate.open(newline="", encoding="utf-8-sig") as handle:
        rows = [
            row
            for row in csv.DictReader(handle)
            if row.get("partner_id") == args.partner_id
            and row.get("dataset_name") == args.dataset_name
        ]
    if not rows:
        raise ValueError("aggregate contains no rows for admitted edge cohort")
    edge_ids = sorted(f"{row['ligand_A']}->{row['ligand_B']}" for row in rows)
    if len(edge_ids) != len(set(edge_ids)):
        raise ValueError("admitted edge cohort contains duplicate directed edges")
    with zipfile.ZipFile(args.results_archive) as archive:
        network = json.loads(archive.read("all_network_properties.json"))
    source_edges = sorted(
        f"{item['ligand_A']}->{item['ligand_B']}"
        for item in network["Edges"].values()
    )
    if not set(edge_ids) < set(source_edges):
        raise ValueError("admitted cohort must be a strict source-network subset")
    payload = {
        "schema_version": "fep_admitted_edge_set.v1",
        "edge_set_id": args.edge_set_id,
        "registration_timing": "post_checkpoint_archive_access_pre_policy_outcome",
        "source_aggregate_sha256": aggregate_sha256,
        "protocol_deviation_sha256": deviation_sha256,
        "partner_id": args.partner_id,
        "dataset_name": args.dataset_name,
        "source_network_edge_count": len(source_edges),
        "admitted_edge_count": len(edge_ids),
        "excluded_edge_ids": sorted(set(source_edges) - set(edge_ids)),
        "edge_ids": edge_ids,
        "selection_rule": "exact directed edge IDs in the prebound official aggregate cohort",
        "policy_label_fields_present": False,
        "policy_outcomes_present": False,
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
                "status": "ADMITTED_EDGE_SET_REGISTERED_PRE_POLICY_OUTCOME",
                "admitted_edge_count": len(edge_ids),
                "excluded_edge_ids": payload["excluded_edge_ids"],
                "edge_set_sha256": hashlib.sha256(data).hexdigest(),
                "output": str(args.output),
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
    parser.add_argument("--official-aggregate", type=Path, required=True)
    parser.add_argument("--partner-id", required=True)
    parser.add_argument("--dataset-name", required=True)
    parser.add_argument("--results-archive", type=Path, required=True)
    parser.add_argument("--protocol-deviation", type=Path, required=True)
    parser.add_argument("--edge-set-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


if __name__ == "__main__":
    main()
