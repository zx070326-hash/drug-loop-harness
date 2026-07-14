"""Register the bounded, non-scientific PTP1B OpenFE pilot."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from loop_harness.v2.fep_repair_execution import (  # noqa: E402
    build_repair_pilot_manifest,
    write_json_artifact,
)


def main() -> None:
    args = _parse_args()
    payload = build_repair_pilot_manifest(
        root=ROOT,
        profile_manifest_path=args.profile_manifest,
        mechanism_audit_path=args.mechanism_audit,
    )
    sha256 = write_json_artifact(args.output, payload)
    print(
        json.dumps(
            {
                "status": "PTP1B_REPAIR_OPERATIONAL_PILOT_REGISTERED",
                "selected_edge_id": payload["selection"]["selected_edge_id"],
                "task_count": payload["task_count"],
                "pilot_manifest_sha256": sha256,
                "scientific_evaluation_permitted": False,
                "output": str(args.output),
            },
            indent=2,
        )
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--profile-manifest",
        type=Path,
        default=(
            ROOT
            / "external/runs/ptp1b-paired-repair-v1/inputs/profile-manifest.json"
        ),
    )
    parser.add_argument(
        "--mechanism-audit",
        type=Path,
        default=ROOT / "state/ptp1b-repair-mechanism-audit-v1.json",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "state/ptp1b-paired-repair-pilot-manifest-v1.json",
    )
    return parser.parse_args()


if __name__ == "__main__":
    main()
