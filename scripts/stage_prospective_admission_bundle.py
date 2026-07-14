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
    ProspectiveAdmissionBundleBuilder,
)


def main() -> None:
    args = parse_args()
    builder = ProspectiveAdmissionBundleBuilder()
    common = {
        "admission_decision": args.decision,
        "admission_reviewer": args.reviewer,
        "admission_rationale": args.rationale,
        "admitted_at": args.admitted_at,
        "admission_id": args.admission_id,
    }
    common.update(strict_interpretation_context(args))
    if args.ledger is None:
        bundle = builder.stage_path(
            args.result,
            read_json(args.plan),
            read_json(args.randomization_verification),
            read_json(args.approval),
            CampaignStore.load_json(args.campaign_store),
            **common,
        )
    else:
        bundle = builder.stage_path_with_ledger(
            args.result,
            read_json(args.plan),
            read_json(args.randomization_verification),
            read_json(args.approval),
            CampaignStore.load_json(args.campaign_store),
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
            "Stage one human-confirmed prospective ADMET admission bundle. "
            "This command never writes campaign state or appends a consumption ledger."
        ),
    )
    parser.add_argument("result", type=Path)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--randomization-verification", type=Path, required=True)
    parser.add_argument("--approval", type=Path, required=True)
    parser.add_argument("--campaign-store", type=Path, required=True)
    parser.add_argument(
        "--ledger",
        type=Path,
        help="Optional existing strict ledger to check for consumed approvals; never modified.",
    )
    parser.add_argument("--reviewer", required=True)
    parser.add_argument("--rationale", required=True)
    parser.add_argument("--decision", choices=("accept", "reject", "defer"), default="accept")
    parser.add_argument("--admitted-at")
    parser.add_argument("--admission-id")
    parser.add_argument("--execution-intent", type=Path)
    parser.add_argument("--execution-receipt", type=Path)
    parser.add_argument("--execution-profile", type=Path)
    parser.add_argument("--endpoint-policy", type=Path)
    parser.add_argument("--calibration-verification", type=Path)
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "state" / "prospective-admission-bundle.json",
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


def strict_interpretation_context(args: argparse.Namespace) -> dict[str, Mapping[str, Any]]:
    paths = {
        "execution_intent": args.execution_intent,
        "execution_receipt": args.execution_receipt,
        "execution_profile": args.execution_profile,
        "endpoint_policy": args.endpoint_policy,
    }
    supplied = [path is not None for path in paths.values()]
    if any(supplied) and not all(supplied):
        raise SystemExit(
            "Strict interpreted admission requires --execution-intent, "
            "--execution-receipt, --execution-profile, and --endpoint-policy together."
        )
    if args.calibration_verification is not None and not all(supplied):
        raise SystemExit("--calibration-verification requires the complete strict execution context.")
    context = {key: read_json(path) for key, path in paths.items()} if all(supplied) else {}
    if args.calibration_verification is not None:
        context["calibration_verification"] = read_json(args.calibration_verification)
    return context


def write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
