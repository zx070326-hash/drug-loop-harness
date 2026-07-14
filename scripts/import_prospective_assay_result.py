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

from loop_harness import CampaignStore, ProspectiveAssayResultImporter  # noqa: E402


def main() -> None:
    args = parse_args()
    imported = ProspectiveAssayResultImporter().import_path(
        args.result,
        read_json(args.endpoint_policy),
        read_json(args.protocol),
        read_json(args.plan),
        read_json(args.randomization_verification),
        read_json(args.approval),
        CampaignStore.load_json(args.campaign_store).rebuild(),
    )
    write_json(args.output, imported.to_dict())
    print(imported.summary_line)
    print(f"wrote {args.output}")
    if imported.import_status != "NORMALIZED":
        raise SystemExit(2)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Normalize one policy-bound prospective assay receipt into an "
            "observation draft. This command never mutates campaign state, runs "
            "TrustGate, or upgrades a claim."
        ),
    )
    parser.add_argument("result", type=Path)
    parser.add_argument("--endpoint-policy", type=Path, required=True)
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--randomization-verification", type=Path, required=True)
    parser.add_argument("--approval", type=Path, required=True)
    parser.add_argument("--campaign-store", type=Path, required=True)
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "state" / "prospective-assay-import.json",
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
