"""Normalize one hash-bound OpenFE real-time archive into a hidden replay cache."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from loop_harness.v2.fep_checkpoint_cache import (  # noqa: E402
    FepCheckpointCacheNormalizer,
)


def main() -> None:
    args = _parse_args()
    normalizer = FepCheckpointCacheNormalizer(
        benchmark_id=args.benchmark_id,
        oracle_profile_id=args.oracle_profile_id,
        source_record=args.source_record,
        normalizer_version=args.normalizer_version,
        expected_repeats=tuple(args.expected_repeats),
        checkpoint_interval_percent=args.checkpoint_interval_percent,
    )
    admitted_edges = None
    admission_provenance = None
    if args.admitted_edge_set is not None:
        edge_set_bytes = args.admitted_edge_set.read_bytes()
        edge_set = json.loads(edge_set_bytes.decode("utf-8"))
        if edge_set.get("schema_version") != "fep_admitted_edge_set.v1":
            raise ValueError("unsupported admitted edge-set artifact")
        if edge_set.get("policy_label_fields_present") is not False:
            raise ValueError("admitted edge-set artifact contains policy labels")
        admitted_edges = tuple(
            tuple(str(edge_id).split("->", 1)) for edge_id in edge_set["edge_ids"]
        )
        if any(len(edge) != 2 or not all(edge) for edge in admitted_edges):
            raise ValueError("admitted edge-set artifact contains an invalid edge ID")
        admission_provenance = {
            "edge_set_artifact_sha256": hashlib.sha256(edge_set_bytes).hexdigest(),
            "source_aggregate_sha256": edge_set["source_aggregate_sha256"],
            "protocol_deviation_sha256": edge_set["protocol_deviation_sha256"],
            "admission_timing": edge_set["registration_timing"],
        }
    cache = normalizer.normalize(
        source_archive_path=args.source_archive,
        results_archive_path=args.results_archive,
        expected_source_md5=args.expected_source_md5,
        results_entry=args.results_entry,
        admitted_edges=admitted_edges,
        edge_admission_provenance=admission_provenance,
    )
    manifest = cache.write(args.output, args.manifest)
    print(
        json.dumps(
            {
                "status": "PASS_NORMALIZED_CACHE",
                "benchmark_id": cache.benchmark_id,
                "oracle_profile_id": cache.oracle_profile_id,
                "normalizer_version": cache.normalizer_version,
                "event_count": len(cache.observations),
                "edge_count": len(cache.edge_ids),
                "leg_repeat_run_count": manifest["inventory"][
                    "leg_repeat_run_count"
                ],
                "event_stream_sha256": manifest["event_stream"]["sha256"],
                "output": str(args.output),
                "manifest": str(args.manifest),
                "experimental_labels_present": False,
                "scientific_claim_upgrade_permitted": False,
                "edge_admission": manifest["source_provenance"].get(
                    "edge_admission"
                ),
            },
            indent=2,
        )
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-archive", type=Path, required=True)
    parser.add_argument("--results-archive", type=Path, required=True)
    parser.add_argument("--expected-source-md5", required=True)
    parser.add_argument("--results-entry", required=True)
    parser.add_argument("--benchmark-id", required=True)
    parser.add_argument("--oracle-profile-id", required=True)
    parser.add_argument("--source-record", required=True)
    parser.add_argument("--normalizer-version", required=True)
    parser.add_argument("--expected-repeats", type=int, nargs="+", default=[0, 1, 2])
    parser.add_argument("--checkpoint-interval-percent", type=float, default=5.0)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--admitted-edge-set", type=Path)
    return parser.parse_args()


if __name__ == "__main__":
    main()
