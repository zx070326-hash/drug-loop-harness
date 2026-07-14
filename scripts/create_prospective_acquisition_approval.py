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
    ProspectiveAcquisitionApprovalRecorder,
    VALID_PROSPECTIVE_APPROVAL_DECISIONS,
)


def main() -> None:
    args = parse_args()
    approval = ProspectiveAcquisitionApprovalRecorder().build(
        read_json(args.plan),
        read_json(args.randomization_verification),
        request_id=args.request_id,
        subject_id=args.subject_id,
        decision=args.decision,
        rationale=args.rationale,
        approved_budget=args.approved_budget,
        expires_at=args.expires_at,
        reviewer=args.reviewer,
        reviewed_at=args.reviewed_at,
    )
    write_json(args.output, approval.to_dict())
    print(approval.summary_line)
    print(f"wrote {args.output}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Record one human prospective-acquisition preparation decision. This "
            "command does not execute acquisition or grant executor authority."
        ),
    )
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--randomization-verification", type=Path, required=True)
    parser.add_argument("--request-id", required=True)
    parser.add_argument("--subject-id", required=True)
    parser.add_argument(
        "--decision",
        choices=VALID_PROSPECTIVE_APPROVAL_DECISIONS,
        default="defer",
    )
    parser.add_argument("--rationale", default="")
    parser.add_argument("--approved-budget", type=float, default=None)
    parser.add_argument("--expires-at", default=None)
    parser.add_argument("--reviewer", default="pending_human_prospective_approval")
    parser.add_argument("--reviewed-at", default=None)
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "state" / "prospective-acquisition-approval.json",
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
