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
    KNOWN_DOCKING_ENGINES,
    DockingEngineCapabilityProbe,
    DockingEngineCapabilitySpec,
)


def main() -> None:
    args = parse_args()
    path_overrides = engine_path_overrides(tuple(args.engine_path))
    specs = tuple(
        DockingEngineCapabilitySpec(
            name=engine,
            command=(path_overrides.get(engine, engine), "--version"),
            timeout_seconds=args.timeout,
        )
        for engine in args.engine
    )
    result = DockingEngineCapabilityProbe().probe(specs)
    write_json(args.output, result.to_dict())
    print(result.summary_line)
    print(f"wrote {args.output}")
    if args.require_available and result.available_engine_count == 0:
        raise SystemExit(1)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run fixed local <engine> --version capability probes only. It does not "
            "accept receptor, ligand, pocket, or docking task inputs."
        ),
    )
    parser.add_argument(
        "--engine",
        action="append",
        choices=KNOWN_DOCKING_ENGINES,
        default=[],
        help="Repeatable; defaults to all known local engines.",
    )
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument(
        "--engine-path",
        action="append",
        default=[],
        metavar="NAME=PATH",
        help="Use one explicit local executable path for a named known engine.",
    )
    parser.add_argument("--require-available", action="store_true")
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "state" / "docking-engine-capabilities.json",
    )
    args = parser.parse_args()
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    if not args.engine:
        args.engine = list(KNOWN_DOCKING_ENGINES)
    return args


def engine_path_overrides(values: tuple[str, ...]) -> dict[str, str]:
    overrides: dict[str, str] = {}
    for value in values:
        name, separator, path = value.partition("=")
        if not separator or name not in KNOWN_DOCKING_ENGINES or not path:
            raise ValueError("--engine-path must have the form NAME=PATH for a known engine")
        if name in overrides:
            raise ValueError(f"duplicate --engine-path override for {name}")
        overrides[name] = path
    return overrides


def write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
