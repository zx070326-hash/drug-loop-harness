"""Release a complete JNK1 cohort to a separate evaluator after nine commits."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from loop_harness.v2.fep_untouched_full_batch import (  # noqa: E402
    build_untouched_full_completion,
    write_untouched_full_batch_artifact,
)


def main() -> None:
    args = _parse_args()
    commits = [ROOT / f"state/jnk1-full-batch-{index}-commit-v1.json" for index in range(9)]
    payload = build_untouched_full_completion(
        root=ROOT,
        authorization_path=args.authorization,
        output_root=args.output_root,
        commit_paths=commits,
    )
    sha256 = write_untouched_full_batch_artifact(args.output, payload)
    print(json.dumps({**payload, "completion_sha256": sha256}, indent=2))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--authorization",
        type=Path,
        default=ROOT / "state/jnk1-full-execution-authorization-v2.json",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=ROOT / "external/runs/jnk1-paired-repair-v1/full",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "state/jnk1-full-execution-complete-v1.json",
    )
    return parser.parse_args()


if __name__ == "__main__":
    main()
