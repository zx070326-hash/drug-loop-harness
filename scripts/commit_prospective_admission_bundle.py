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

from loop_harness import CampaignStore, ProspectiveAdmissionCommitter  # noqa: E402


def main() -> None:
    args = parse_args()
    outcome = ProspectiveAdmissionCommitter().commit(
        read_json(args.bundle),
        read_json(args.plan),
        read_json(args.randomization_verification),
        read_json(args.approval),
        CampaignStore.load_json(args.campaign_store),
        args.ledger,
        args.commit_root,
        commit_reviewer=args.reviewer,
        commit_rationale=args.rationale,
        commit_confirmed_at=args.confirmed_at,
        commit_id=args.commit_id,
    )
    write_json(args.output, outcome.to_dict())
    print(outcome.summary_line)
    print(f"wrote {args.output}")
    if outcome.commit_status != "COMMITTED":
        raise SystemExit(2)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Commit a PASS_STAGED prospective admission package through a recoverable "
            "local intent/materialization/ledger/receipt protocol. The source campaign "
            "store is never replaced."
        ),
    )
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--randomization-verification", type=Path, required=True)
    parser.add_argument("--approval", type=Path, required=True)
    parser.add_argument("--campaign-store", type=Path, required=True)
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--commit-root", type=Path, required=True)
    parser.add_argument("--reviewer", required=True)
    parser.add_argument("--rationale", required=True)
    parser.add_argument("--confirmed-at")
    parser.add_argument("--commit-id")
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "state" / "prospective-admission-commit-outcome.json",
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
