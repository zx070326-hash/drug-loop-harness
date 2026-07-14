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

from loop_harness import DockingInputManifestVerifier  # noqa: E402


def main() -> None:
    args = parse_args()
    result = DockingInputManifestVerifier().verify_path(args.manifest)
    payload = result.to_dict()
    write_json(args.output, payload)
    print(result.summary_line)
    print(f"wrote {args.output}")
    if result.verification_status != "PASS":
        raise SystemExit(1)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only verification of receptor, pocket, ligand, and engine "
            "inputs before a future docking command. This command cannot run docking."
        ),
    )
    parser.add_argument("manifest", type=Path)
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "state" / "docking-input-manifest-verification.json",
    )
    return parser.parse_args()


def write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
