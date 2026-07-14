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

from loop_harness import CampaignStore, ProspectivePolicyDecisionReceiptRecorder  # noqa: E402


def main() -> None:
    args = parse_args()
    receipt = ProspectivePolicyDecisionReceiptRecorder().record(
        read_json(args.protocol),
        read_json(args.assignment),
        read_json(args.assignment_verification),
        CampaignStore.load_json(args.decision_campaign_store),
        assignment_id=args.assignment_id,
        decided_at=args.decided_at,
        receipt_id=args.receipt_id,
    )
    write_json(args.output, receipt.to_dict())
    print(receipt.summary_line)
    print(f"wrote {args.output}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Record one replayed prospective policy decision from an immutable pre-outcome "
            "campaign state. This command does not execute an oracle, approve work, or "
            "upgrade a scientific claim."
        ),
    )
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--assignment", type=Path, required=True)
    parser.add_argument("--assignment-verification", type=Path, required=True)
    parser.add_argument("--decision-campaign-store", type=Path, required=True)
    parser.add_argument("--assignment-id", required=True)
    parser.add_argument("--decided-at", default=None)
    parser.add_argument("--receipt-id", default=None)
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "state" / "prospective-policy-decision-receipt.json",
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
