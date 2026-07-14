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

from loop_harness import CampaignStore, ProspectiveAdmetExecutionRunner  # noqa: E402


def main() -> None:
    args = parse_args()
    intent, receipt = ProspectiveAdmetExecutionRunner().run(
        read_json(args.profile),
        read_json(args.plan),
        read_json(args.randomization_verification),
        read_json(args.approval),
        CampaignStore.load_json(args.campaign_store).rebuild(),
        args.attempt_root,
        execution_reviewer=args.reviewer,
        execution_rationale=args.rationale,
        execution_confirmed_at=args.confirmed_at,
        attempt_id=args.attempt_id,
    )
    attempt_directory = Path(intent.attempt_directory)
    print(intent.summary_line)
    print(receipt.summary_line)
    print(f"wrote {attempt_directory / 'execution-intent.json'}")
    print(f"wrote {attempt_directory / 'execution-receipt.json'}")
    if receipt.execution_status != "SUCCEEDED":
        raise SystemExit(2)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Human-trigger one local ADMET-AI CLI execution attempt. It writes raw "
            "input/output and a receipt only; it never normalizes evidence, runs "
            "TrustGate, writes campaign state, or appends a consumption ledger."
        ),
    )
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--randomization-verification", type=Path, required=True)
    parser.add_argument("--approval", type=Path, required=True)
    parser.add_argument("--campaign-store", type=Path, required=True)
    parser.add_argument("--attempt-root", type=Path, required=True)
    parser.add_argument("--reviewer", required=True)
    parser.add_argument("--rationale", required=True)
    parser.add_argument("--confirmed-at")
    parser.add_argument("--attempt-id")
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


if __name__ == "__main__":
    main()
