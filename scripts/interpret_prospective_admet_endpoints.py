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

from loop_harness import CampaignStore, ProspectiveAdmetEndpointInterpreter  # noqa: E402


def main() -> None:
    args = parse_args()
    interpretation = ProspectiveAdmetEndpointInterpreter().interpret(
        read_json(args.attempt_directory / "execution-intent.json"),
        read_json(args.attempt_directory / "execution-receipt.json"),
        read_json(args.profile),
        read_json(args.policy),
        read_json(args.plan),
        read_json(args.randomization_verification),
        read_json(args.approval),
        CampaignStore.load_json(args.campaign_store).rebuild(),
        read_json(args.calibration_verification) if args.calibration_verification else None,
    )
    write_json(args.output, interpretation.to_dict())
    print(interpretation.summary_line)
    print(f"wrote {args.output}")
    if interpretation.interpretation_status != "INTERPRETED":
        raise SystemExit(2)
    assert interpretation.prospective_admet_result is not None
    write_json(args.result_output, interpretation.prospective_admet_result)
    print(f"wrote {args.result_output}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Interpret one verified ADMET-AI raw CSV through a reviewed endpoint policy. "
            "It writes a prospective ADMET result draft only; it never writes campaign "
            "state, calls TrustGate, or upgrades a claim."
        ),
    )
    parser.add_argument("--attempt-directory", type=Path, required=True)
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--randomization-verification", type=Path, required=True)
    parser.add_argument("--approval", type=Path, required=True)
    parser.add_argument("--campaign-store", type=Path, required=True)
    parser.add_argument("--calibration-verification", type=Path)
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "state" / "prospective-admet-interpretation.json",
    )
    parser.add_argument(
        "--result-output",
        type=Path,
        default=ROOT / "state" / "prospective-admet-result.json",
    )
    return parser.parse_args()


def read_json(path: Path) -> Mapping[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise SystemExit(f"Unable to read {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Invalid JSON in {path}: {exc}") from exc
    if not isinstance(payload, Mapping):
        raise SystemExit(f"Expected JSON object at {path}.")
    return payload


def write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
