from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from loop_harness.adapters import (  # noqa: E402
    RDKIT_CHEM_FILTER_ORACLE_NAME,
    RDKitChemFilterAdapter,
    RDKitChemFilterConfig,
    TableCandidateAdapter,
)


def main() -> None:
    args = parse_args()
    candidates = TableCandidateAdapter(args.candidates).load_candidates()
    compound = next(
        (candidate for candidate in candidates if candidate.id == args.subject_id),
        None,
    )
    if compound is None:
        print(f"unknown subject: {args.subject_id}", file=sys.stderr)
        raise SystemExit(2)

    observation = RDKitChemFilterAdapter(
        (compound,),
        oracle_name=args.oracle_name,
        source=args.source,
        config=RDKitChemFilterConfig(),
        expected_rdkit_version=args.expected_rdkit_version,
        input_context={
            "candidate_csv_sha256": sha256_file(args.candidates),
            "candidate_csv_name": args.candidates.name,
        },
    ).load_observations()[0]
    print(
        json.dumps(
            {
                "id": observation.id,
                "subject_id": observation.subject_id,
                "oracle_name": observation.oracle_name,
                "metrics": _jsonable(dict(observation.metrics)),
                "cost_spent": observation.cost_spent,
                "provenance": _jsonable(dict(observation.provenance)),
                "notes": observation.notes,
            },
            sort_keys=True,
        )
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Emit one version-bound RDKit chemical-validity/property observation "
            "as JSON."
        ),
    )
    parser.add_argument("candidates", type=Path)
    parser.add_argument("subject_id")
    parser.add_argument("oracle_name", nargs="?", default=RDKIT_CHEM_FILTER_ORACLE_NAME)
    parser.add_argument("--expected-rdkit-version", default=None)
    parser.add_argument("--source", default="rdkit_local_command")
    return parser.parse_args()


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _jsonable(value: Any) -> Any:
    if isinstance(value, tuple):
        return [_jsonable(item) for item in value]
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    return value


if __name__ == "__main__":
    main()
