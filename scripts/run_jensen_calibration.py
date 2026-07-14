"""Run a retrospective posterior calibration curve over fixed Jensen cohorts."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from loop_harness.v2 import (  # noqa: E402
    DEFAULT_TARGET_IDS,
    JensenCalibrationRunner,
    JensenCalibrationSpec,
)


def main() -> None:
    args = _parse_args()
    workspace = Path(args.workspace).resolve()
    spec = JensenCalibrationSpec(
        target_ids=tuple(args.targets),
        candidate_limit=args.candidate_limit,
        candidate_offsets=tuple(args.candidate_offsets),
        warm_start_seeds=tuple(args.warm_start_seeds),
        seed_per_oracle=args.seed_per_oracle,
        continuation_steps=args.continuation_steps,
        total_budget=args.total_budget,
    )
    result = JensenCalibrationRunner().run(
        workspace=str(workspace),
        artifact_root=workspace / args.artifact_root,
        spec=spec,
    )
    output_path = workspace / args.output
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "output": str(output_path.relative_to(workspace)),
                "row_count": len(result.rows),
                "curve_points": len(result.curve),
                "analysis_boundary": result.analysis_boundary,
            },
            indent=2,
        )
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", default=".")
    parser.add_argument("--targets", nargs="+", choices=DEFAULT_TARGET_IDS, default=("FACTOR_D",))
    parser.add_argument("--candidate-limit", type=_positive_int, default=64)
    parser.add_argument("--candidate-offsets", nargs="+", type=_nonnegative_int, default=(0,))
    parser.add_argument("--warm-start-seeds", nargs="+", type=int, default=(17, 37, 59))
    parser.add_argument("--seed-per-oracle", type=_positive_int, default=1)
    parser.add_argument("--continuation-steps", type=_positive_int, default=2)
    parser.add_argument("--total-budget", type=_positive_float, default=12.0)
    parser.add_argument("--artifact-root", default="state/jensen-calibration-artifacts")
    parser.add_argument("--output", default="state/jensen-calibration.json")
    return parser.parse_args()


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("value must be positive")
    return parsed


def _nonnegative_int(value: str) -> int:
    parsed = int(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("value must be non-negative")
    return parsed


def _positive_float(value: str) -> float:
    parsed = float(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("value must be positive")
    return parsed


if __name__ == "__main__":
    main()
