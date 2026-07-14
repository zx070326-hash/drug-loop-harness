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

from loop_harness.tocodecoy_import import TocoDecoyEvaluationPoolImporter  # noqa: E402


def main() -> None:
    args = parse_args()
    pool = TocoDecoyEvaluationPoolImporter().export(
        args.source_archive,
        target_id=args.target,
        selected_split_value=args.split_value,
        candidate_csv_path=args.candidates_output,
        heldout_truth_jsonl_path=args.heldout_truth_output,
        max_candidates=args.max_candidates,
        selection_seed=args.selection_seed,
    )
    write_json(args.manifest_output, pool.to_dict())
    print(pool.summary_line)
    print(f"wrote {args.candidates_output}")
    print(f"wrote {args.heldout_truth_output}")
    print(f"wrote {args.manifest_output}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Export one TocoDecoy published split into a label-blinded policy "
            "candidate CSV and a separate held-out truth file."
        )
    )
    parser.add_argument("--source-archive", type=Path, required=True)
    parser.add_argument("--target", required=True)
    parser.add_argument("--split-value", type=int, choices=(0, 1), required=True)
    parser.add_argument("--max-candidates", type=int)
    parser.add_argument("--selection-seed", default="tocodecoy_evaluation_pool_v1")
    parser.add_argument("--candidates-output", type=Path, required=True)
    parser.add_argument("--heldout-truth-output", type=Path, required=True)
    parser.add_argument("--manifest-output", type=Path, required=True)
    return parser.parse_args()


def write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
