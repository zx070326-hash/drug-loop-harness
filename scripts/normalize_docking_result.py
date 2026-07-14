from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from loop_harness import (  # noqa: E402
    DockingCalibrationAnchorVerifier,
    DockingResultNormalizer,
    TrustGate,
    calibration_anchor_mismatch_codes,
    calibrated_docking_oracle,
    contract_to_payload,
    unanchored_docking_oracle,
)


def main() -> None:
    args = parse_args()
    normalized = DockingResultNormalizer().normalize_paths(
        args.result,
        args.verification,
        oracle_name=args.oracle_name,
    )
    payload = normalized.to_dict()
    calibration_failed = False
    if normalized.observation is not None:
        oracle = unanchored_docking_oracle(
            name=normalized.observation.oracle_name,
            engine_version=normalized.engine_version or "unknown",
        )
        if args.calibration_anchor is not None:
            calibration = DockingCalibrationAnchorVerifier().verify_path(
                args.calibration_anchor
            )
            scoring_function = str(
                normalized.observation.metrics.get("scoring_function", "")
            )
            mismatch_codes = calibration_anchor_mismatch_codes(
                calibration,
                target_id=normalized.target_id or "",
                engine_name=normalized.engine_name or "",
                engine_version=normalized.engine_version or "",
                scoring_function=scoring_function,
            )
            payload["calibration_anchor"] = calibration.to_dict()
            payload["calibration_application"] = {
                "requested": True,
                "status": "applied" if not mismatch_codes else "rejected",
                "mismatch_codes": list(mismatch_codes),
            }
            if mismatch_codes:
                calibration_failed = True
            else:
                oracle = calibrated_docking_oracle(
                    calibration,
                    name=normalized.observation.oracle_name,
                )
        else:
            payload["calibration_application"] = {
                "requested": False,
                "status": "not_requested",
                "mismatch_codes": [],
            }
        ticket = TrustGate().evaluate(normalized.observation, oracle)
        payload["trust_gate"] = contract_to_payload(ticket)
        payload["trust_gate_note"] = (
            "The default imported docking oracle has no target calibration anchor; "
            "TrustGate must abstain even when result normalization succeeds."
        )
    payload["execution_authority_granted"] = False
    payload["autonomous_execution_permitted"] = False
    payload["scientific_claim_upgrade_permitted"] = False
    write_json(args.output, payload)
    print(normalized.summary_line)
    print(f"wrote {args.output}")
    if normalized.normalization_status != "NORMALIZED" or calibration_failed:
        raise SystemExit(1)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Normalize an existing docking result only when it binds to a still-valid "
            "input manifest and pose artifact. This command cannot launch docking."
        ),
    )
    parser.add_argument("result", type=Path)
    parser.add_argument("--verification", type=Path, required=True)
    parser.add_argument(
        "--calibration-anchor",
        type=Path,
        default=None,
        help="Optional artifact-verified calibration anchor applied only on exact target/engine match.",
    )
    parser.add_argument("--oracle-name", default=None)
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "state" / "docking-result-normalization.json",
    )
    return parser.parse_args()


def write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
