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

from loop_harness import OpenBenchmarkAdmissionAuditor  # noqa: E402


def main() -> None:
    args = parse_args()
    audit = OpenBenchmarkAdmissionAuditor().audit_path(
        args.manifest,
        asset_root=args.asset_root,
        audited_at=args.audited_at,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(audit.to_dict(), indent=2) + "\n", encoding="utf-8")
    print(audit.summary_line)
    print(f"wrote {args.output}")
    if audit.audit_status not in {"READY_FOR_ORACLE_CACHE_CONSTRUCTION", "READY_FOR_POLICY_REPLAY"}:
        raise SystemExit(2)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only audit for an open benchmark admission. It never downloads "
            "data, invokes an oracle, ranks policies, or upgrades scientific claims."
        )
    )
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--asset-root", type=Path, default=ROOT)
    parser.add_argument("--audited-at")
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "state" / "open-benchmark-admission-audit.json",
    )
    return parser.parse_args()


if __name__ == "__main__":
    main()
