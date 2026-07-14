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
    ProspectiveAcquisitionConsumptionAuditor,
    ProspectiveAcquisitionConsumptionLedger,
)


def main() -> None:
    args = parse_args()
    ledger = ProspectiveAcquisitionConsumptionLedger()
    audit = ProspectiveAcquisitionConsumptionAuditor().audit(
        read_json(args.plan),
        read_json(args.randomization_verification),
        read_json(args.approval),
        read_json(args.consumption),
        CampaignStore.load_json(args.campaign_store).rebuild(),
        ledger.records(args.ledger),
    )
    write_json(args.output, audit.to_dict())
    print(audit.summary_line)
    print(f"wrote {args.output}")
    if audit.audit_status != "PASS":
        raise SystemExit(2)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only audit for one prospective result-consumption receipt. This "
            "command never executes an adapter or changes campaign evidence."
        ),
    )
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--randomization-verification", type=Path, required=True)
    parser.add_argument("--approval", type=Path, required=True)
    parser.add_argument("--consumption", type=Path, required=True)
    parser.add_argument("--campaign-store", type=Path, required=True)
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "state" / "prospective-acquisition-consumption-audit.json",
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
