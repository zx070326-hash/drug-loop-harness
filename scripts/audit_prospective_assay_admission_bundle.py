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
    ProspectiveAssayAdmissionBundleAuditor,
)


def main() -> None:
    args = parse_args()
    consumed: tuple[str, ...] = ()
    if args.ledger is not None:
        consumed = ProspectiveAcquisitionConsumptionLedger().consumed_approval_ids(args.ledger)
    audit = ProspectiveAssayAdmissionBundleAuditor(
        read_json(args.endpoint_policy),
        read_json(args.protocol),
    ).audit(
        read_json(args.bundle),
        read_json(args.plan),
        read_json(args.randomization_verification),
        read_json(args.approval),
        CampaignStore.load_json(args.campaign_store),
        consumed_approval_ids=consumed,
    )
    write_json(args.output, audit.to_dict())
    print(audit.summary_line)
    print(f"wrote {args.output}")
    if audit.audit_status != "PASS_STAGED":
        raise SystemExit(2)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Read-only replay audit for one staged prospective assay admission bundle.",
    )
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--endpoint-policy", type=Path, required=True)
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--randomization-verification", type=Path, required=True)
    parser.add_argument("--approval", type=Path, required=True)
    parser.add_argument("--campaign-store", type=Path, required=True)
    parser.add_argument("--ledger", type=Path)
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "state" / "prospective-assay-admission-bundle-audit.json",
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
