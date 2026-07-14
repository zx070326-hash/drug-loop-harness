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
    ProspectiveAdmissionBundleAuditor,
)


def main() -> None:
    args = parse_args()
    consumed_approval_ids: tuple[str, ...] = ()
    if args.ledger is not None:
        try:
            consumed_approval_ids = ProspectiveAcquisitionConsumptionLedger().consumed_approval_ids(
                args.ledger
            )
        except ValueError as exc:
            raise SystemExit(f"Invalid prospective consumption ledger: {exc}") from exc
    audit = ProspectiveAdmissionBundleAuditor().audit(
        read_json(args.bundle),
        read_json(args.plan),
        read_json(args.randomization_verification),
        read_json(args.approval),
        CampaignStore.load_json(args.campaign_store),
        consumed_approval_ids=consumed_approval_ids,
    )
    write_json(args.output, audit.to_dict())
    print(audit.summary_line)
    print(f"wrote {args.output}")
    if audit.audit_status != "PASS_STAGED":
        raise SystemExit(2)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only audit of a staged prospective admission bundle. This command "
            "does not commit campaign state or append a consumption ledger."
        ),
    )
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--randomization-verification", type=Path, required=True)
    parser.add_argument("--approval", type=Path, required=True)
    parser.add_argument("--campaign-store", type=Path, required=True)
    parser.add_argument("--ledger", type=Path)
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "state" / "prospective-admission-bundle-audit.json",
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
