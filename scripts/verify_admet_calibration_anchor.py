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

from loop_harness import AdmetCalibrationAnchorVerifier  # noqa: E402


def main() -> None:
    args = parse_args()
    verification = AdmetCalibrationAnchorVerifier().verify_path(
        args.anchor,
        read_json(args.profile),
        read_json(args.policy),
    )
    write_json(args.output, verification.to_dict())
    print(verification.summary_line)
    print(f"wrote {args.output}")
    if verification.verification_status != "PASS":
        raise SystemExit(2)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only verification of an external-holdout ADMET calibration anchor. "
            "A pass scopes one policy/profile proxy; it does not create a campaign claim."
        ),
    )
    parser.add_argument("anchor", type=Path)
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "state" / "admet-calibration-verification.json",
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
