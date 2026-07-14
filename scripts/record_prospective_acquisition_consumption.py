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
    CampaignStore,
    ProspectiveAcquisitionConsumptionLedger,
    ProspectiveAcquisitionConsumptionRecorder,
)


def main() -> None:
    args = parse_args()
    plan = read_json(args.plan)
    verification = read_json(args.randomization_verification)
    approval = read_json(args.approval)
    state = CampaignStore.load_json(args.campaign_store).rebuild()
    ledger = ProspectiveAcquisitionConsumptionLedger()
    consumed = ledger.consumed_approval_ids(args.ledger)
    recorder = ProspectiveAcquisitionConsumptionRecorder()
    record = recorder.build_record(
        plan,
        verification,
        approval,
        state,
        observation_id=args.observation_id,
        trust_gate_result_id=args.trust_gate_result_id,
        consumed_approval_ids=consumed,
    )
    recorder.write_record(record, args.output)
    recorder.append_jsonl(record, args.ledger)
    print(record.summary_line)
    print(f"wrote {args.output}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Record one already-returned prospective result after TrustGate. This "
            "command never calls an external oracle or upgrades a scientific claim."
        ),
    )
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--randomization-verification", type=Path, required=True)
    parser.add_argument("--approval", type=Path, required=True)
    parser.add_argument("--campaign-store", type=Path, required=True)
    parser.add_argument("--observation-id", required=True)
    parser.add_argument("--trust-gate-result-id", required=True)
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "state" / "prospective-acquisition-consumption.json",
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


if __name__ == "__main__":
    main()
