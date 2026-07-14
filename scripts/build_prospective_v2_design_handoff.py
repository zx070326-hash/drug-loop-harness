"""Build the evidence-bound handoff into assay-specific prospective design."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from loop_harness.v2.prospective_design_handoff import (  # noqa: E402
    build_prospective_v2_design_handoff,
    verify_prospective_v2_design_handoff,
    write_immutable_json,
)


def main() -> None:
    args = _parse_args()
    handoff = (
        json.loads(args.output.read_text(encoding="utf-8"))
        if args.output.exists()
        else build_prospective_v2_design_handoff(root=ROOT)
    )
    verification = verify_prospective_v2_design_handoff(handoff, root=ROOT)
    if verification["verification_status"] != "PASS":
        raise SystemExit("prospective v2 design handoff failed verification")
    handoff_sha256 = write_immutable_json(args.output, handoff)
    verification_sha256 = write_immutable_json(args.verification, verification)
    print(
        json.dumps(
            {
                "status": handoff["stage_status"],
                "handoff_sha256": handoff_sha256,
                "verification_status": verification["verification_status"],
                "verification_sha256": verification_sha256,
                "external_input_blocker_count": handoff["readiness"][
                    "external_input_blocker_count"
                ],
                "assay_protocol_registration_ready": False,
                "output": str(args.output),
                "verification_output": str(args.verification),
                "scientific_claim_upgrade_permitted": False,
            },
            indent=2,
        )
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "state/prospective-v2-design-handoff-v1.json",
    )
    parser.add_argument(
        "--verification",
        type=Path,
        default=ROOT / "state/prospective-v2-design-handoff-verification-v1.json",
    )
    return parser.parse_args()


if __name__ == "__main__":
    main()
