"""Normalize the staged TYK2 OpenFE cache into immutable checkpoint events."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from loop_harness.v2.fep_checkpoint_cache import (  # noqa: E402
    FepCheckpointCacheNormalizer,
    TYK2_RESULTS_ENTRY,
    TYK2_SOURCE_MD5,
)


def main() -> None:
    args = _parse_args()
    cache = FepCheckpointCacheNormalizer().normalize(
        source_archive_path=args.source_archive,
        results_archive_path=args.results_archive,
        expected_source_md5=args.expected_source_md5,
        results_entry=args.results_entry,
    )
    manifest = cache.write(args.output, args.manifest)
    print(
        json.dumps(
            {
                "status": "PASS_NORMALIZED_CACHE",
                "event_count": len(cache.observations),
                "edge_count": len(cache.edge_ids),
                "leg_repeat_run_count": manifest["inventory"]["leg_repeat_run_count"],
                "event_stream_sha256": manifest["event_stream"]["sha256"],
                "output": str(args.output),
                "manifest": str(args.manifest),
                "experimental_labels_present": False,
                "scientific_claim_upgrade_permitted": False,
            },
            indent=2,
        )
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source-archive",
        type=Path,
        default=(
            ROOT
            / "external"
            / "benchmarks"
            / "zenodo-14430985"
            / "jacs_set_tyk2.zip"
        ),
    )
    parser.add_argument(
        "--results-archive",
        type=Path,
        default=(
            ROOT
            / "external"
            / "benchmarks"
            / "zenodo-14430985"
            / "archive"
            / "jacs_set_tyk2"
            / "results_data.zip"
        ),
    )
    parser.add_argument("--expected-source-md5", default=TYK2_SOURCE_MD5)
    parser.add_argument("--results-entry", default=TYK2_RESULTS_ENTRY)
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "state" / "tyk2-fep-checkpoint-cache-v1.jsonl",
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=ROOT / "state" / "tyk2-fep-checkpoint-cache-manifest-v1.json",
    )
    return parser.parse_args()


if __name__ == "__main__":
    main()
