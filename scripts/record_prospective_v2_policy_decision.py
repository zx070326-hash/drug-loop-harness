"""Record a replayable v2 TVR decision without executing an oracle."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from loop_harness.v2.ledger import CampaignLedger  # noqa: E402
from loop_harness.v2.prospective_design_handoff import (  # noqa: E402
    default_policy_specs,
    write_immutable_json,
)
from loop_harness.v2.prospective_policy_bridge import (  # noqa: E402
    ProspectiveV2PolicyDecisionRecorder,
)


def main() -> None:
    args = _parse_args()
    campaign_id = _campaign_id(args.ledger)
    ledger = CampaignLedger.load(campaign_id, args.ledger)
    policy_spec = (
        json.loads(args.policy_spec.read_text(encoding="utf-8"))
        if args.policy_spec is not None
        else default_policy_specs()[args.role]
    )
    receipt = ProspectiveV2PolicyDecisionRecorder().record(
        policy_spec,
        ledger,
        decision_round_id=args.decision_round_id,
    )
    receipt_sha256 = write_immutable_json(args.output, receipt.to_dict())
    print(
        json.dumps(
            {
                "status": "PROSPECTIVE_V2_POLICY_DECISION_RECORDED",
                "policy_kind": receipt.policy_kind,
                "proposal_count": len(receipt.selected_proposals),
                "receipt_sha256": receipt_sha256,
                "execution_authority_granted": False,
                "output": str(args.output),
            },
            indent=2,
        )
    )


def _campaign_id(path: Path) -> str:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list) or not payload or not isinstance(payload[0], dict):
        raise SystemExit("ledger must be a non-empty CampaignLedger JSON array")
    campaign_id = payload[0].get("campaign_id")
    if not isinstance(campaign_id, str) or not campaign_id:
        raise SystemExit("ledger campaign_id is missing")
    return campaign_id


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ledger", type=Path)
    parser.add_argument("decision_round_id")
    parser.add_argument("--role", choices=("baseline", "credibility_aware"), default="credibility_aware")
    parser.add_argument("--policy-spec", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


if __name__ == "__main__":
    main()
