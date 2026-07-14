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

from loop_harness import CampaignStore, ProspectiveAdmetExecutionAuditor  # noqa: E402


def main() -> None:
    args = parse_args()
    intent = read_json(args.attempt_directory / "execution-intent.json")
    receipt_path = args.attempt_directory / "execution-receipt.json"
    receipt = read_json(receipt_path) if receipt_path.is_file() else None
    audit = ProspectiveAdmetExecutionAuditor().audit(
        intent,
        receipt,
        read_json(args.profile),
        read_json(args.plan),
        read_json(args.randomization_verification),
        read_json(args.approval),
        CampaignStore.load_json(args.campaign_store).rebuild(),
    )
    write_json(args.output, audit.to_dict())
    print(audit.summary_line)
    print(f"wrote {args.output}")
    if audit.audit_status != "PASS_CONTROL":
        raise SystemExit(2)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only audit of a prospective ADMET-AI execution attempt. A pass "
            "proves control-chain integrity only, never an ADMET conclusion."
        ),
    )
    parser.add_argument("--attempt-directory", type=Path, required=True)
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--randomization-verification", type=Path, required=True)
    parser.add_argument("--approval", type=Path, required=True)
    parser.add_argument("--campaign-store", type=Path, required=True)
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "state" / "prospective-admet-execution-audit.json",
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
