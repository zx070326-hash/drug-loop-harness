"""Audit one completed PTP1B full-repair batch without reading scientific metrics."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main() -> None:
    args = _parse_args()
    root = args.project_root.resolve()
    sys.path.insert(0, str(root / "src"))
    from loop_harness.v2.fep_repair_batch_commit import (
        audit_full_repair_batch_commit,
        write_full_repair_batch_commit_audit,
    )

    report = audit_full_repair_batch_commit(
        root=root,
        authorization_path=args.authorization,
        output_root=args.output_root,
        batch_index=args.batch_index,
    )
    output_sha256 = None
    if args.output is not None:
        output_sha256 = write_full_repair_batch_commit_audit(args.output, report)
    print(
        json.dumps(
            {
                **report,
                "output": str(args.output.resolve()) if args.output else None,
                "output_sha256": output_sha256,
            },
            indent=2,
            sort_keys=True,
        )
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--authorization", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--batch-index", type=int, required=True)
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


if __name__ == "__main__":
    main()
