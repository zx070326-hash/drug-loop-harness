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

from loop_harness import CampaignStore, ProspectiveAssayAdmissionBundleBuilder  # noqa: E402


def main() -> None:
    args = parse_args()
    builder = ProspectiveAssayAdmissionBundleBuilder(
        read_json(args.endpoint_policy),
        read_json(args.protocol),
    )
    common = {
        "admission_decision": args.decision,
        "admission_reviewer": args.reviewer,
        "admission_rationale": args.rationale,
        "admitted_at": args.admitted_at,
        "admission_id": args.admission_id,
    }
    store = CampaignStore.load_json(args.campaign_store)
    if args.ledger is None:
        bundle = builder.stage_path(
            args.result,
            read_json(args.plan),
            read_json(args.randomization_verification),
            read_json(args.approval),
            store,
            **common,
        )
    else:
        bundle = builder.stage_path_with_ledger(
            args.result,
            read_json(args.plan),
            read_json(args.randomization_verification),
            read_json(args.approval),
            store,
            args.ledger,
            **common,
        )
    write_json(args.output, bundle.to_dict())
    print(bundle.summary_line)
    print(f"wrote {args.output}")
    if bundle.bundle_status != "staged_for_explicit_commit":
        raise SystemExit(2)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Stage one human-confirmed prospective assay admission bundle. This "
            "command never commits campaign state or appends a consumption ledger."
        ),
    )
    parser.add_argument("result", type=Path)
    parser.add_argument("--endpoint-policy", type=Path, required=True)
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--randomization-verification", type=Path, required=True)
    parser.add_argument("--approval", type=Path, required=True)
    parser.add_argument("--campaign-store", type=Path, required=True)
    parser.add_argument("--ledger", type=Path)
    parser.add_argument("--reviewer", required=True)
    parser.add_argument("--rationale", required=True)
    parser.add_argument("--decision", choices=("accept", "reject", "defer"), default="accept")
    parser.add_argument("--admitted-at")
    parser.add_argument("--admission-id")
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "state" / "prospective-assay-admission-bundle.json",
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
