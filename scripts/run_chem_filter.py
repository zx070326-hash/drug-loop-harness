from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from loop_harness import Observation  # noqa: E402
from loop_harness.adapters import (  # noqa: E402
    DeterministicChemFilterAdapter,
    TableCandidateAdapter,
)


FIELDNAMES = (
    "id",
    "subject_id",
    "oracle_name",
    "passed",
    "checks",
    "failure_reasons",
    "heavy_atom_count",
    "approx_molecular_weight",
    "cost_spent",
    "domain",
    "source",
    "adapter",
    "adapter_version",
    "ruleset",
    "input_hash",
    "output_hash",
)


def main() -> None:
    args = parse_args()
    candidates = TableCandidateAdapter(args.candidates).load_candidates()
    observations = DeterministicChemFilterAdapter(
        candidates,
        oracle_name=args.oracle_name,
        source=args.source,
    ).load_observations()
    write_observations_csv(args.output, observations)
    print(f"wrote {args.output}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Apply deterministic chemistry filtering to a candidate CSV.",
    )
    parser.add_argument(
        "--candidates",
        type=Path,
        default=ROOT / "examples" / "data" / "chem_filter_candidates.csv",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "examples" / "data" / "chem_filter_observations.csv",
    )
    parser.add_argument("--oracle-name", default="basic_chem_filter")
    parser.add_argument("--source", default="deterministic_chem_filter")
    return parser.parse_args()


def write_observations_csv(path: Path, observations: tuple[Observation, ...]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDNAMES)
        writer.writeheader()
        for observation in observations:
            writer.writerow(observation_row(observation))


def observation_row(observation: Observation) -> dict[str, Any]:
    provenance = dict(observation.provenance)
    metrics = dict(observation.metrics)
    return {
        "id": observation.id,
        "subject_id": observation.subject_id,
        "oracle_name": observation.oracle_name,
        "passed": metrics.get("passed"),
        "checks": _json(metrics.get("checks", {})),
        "failure_reasons": _json(metrics.get("failure_reasons", ())),
        "heavy_atom_count": metrics.get("heavy_atom_count"),
        "approx_molecular_weight": metrics.get("approx_molecular_weight"),
        "cost_spent": observation.cost_spent,
        "domain": "|".join(provenance.get("domain", ())),
        "source": provenance.get("source", ""),
        "adapter": provenance.get("adapter", ""),
        "adapter_version": provenance.get("adapter_version", ""),
        "ruleset": provenance.get("ruleset", ""),
        "input_hash": provenance.get("input_hash", ""),
        "output_hash": provenance.get("output_hash", ""),
    }


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=list)


if __name__ == "__main__":
    main()
