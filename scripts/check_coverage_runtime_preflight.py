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
    ActionKind,
    ActionRecord,
    CoverageRuntimePreflightEvaluator,
)


def main() -> None:
    args = parse_args()
    plan = read_json(args.plan)
    approval = read_json(args.approval_record)
    hot_state = read_json(args.hot_state)
    request = request_by_id(plan, args.request_id)
    action = ActionRecord(
        id=f"coverage_preflight_{args.request_id}",
        kind=action_kind(request),
        subject_ids=(required_string(request, "subject_id"),),
        requested_by="coverage_runtime_preflight",
        budget_reserved=args.budget_reserved,
        reason="read_only_coverage_runtime_preflight",
        parameters={
            "coverage_request_id": args.request_id,
            "coverage_approval_id": required_string(approval, "approval_id"),
        },
    )
    result = CoverageRuntimePreflightEvaluator(
        plan,
        approval,
        consumption_ledger_path=args.ledger,
        reference_time=args.reference_time,
    ).evaluate(action, hot_state=hot_state)
    payload = result.to_dict()
    rendered = json.dumps(payload, indent=2)
    print(rendered)
    if args.output is not None:
        write_json(args.output, payload)
        print(f"wrote {args.output}")
    if result.preflight_status != "ready_for_human_confirmed_runtime":
        raise SystemExit(1)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only coverage runtime preflight. It cannot authorize execution, "
            "call an oracle, or upgrade scientific claims."
        ),
    )
    parser.add_argument(
        "--plan",
        type=Path,
        default=ROOT / "state" / "oracle-cache-coverage-plan.json",
    )
    parser.add_argument(
        "--approval-record",
        type=Path,
        default=ROOT / "state" / "coverage-approval-record.json",
    )
    parser.add_argument(
        "--ledger",
        type=Path,
        default=ROOT / "state" / "coverage-consumptions.jsonl",
    )
    parser.add_argument(
        "--hot-state",
        type=Path,
        default=ROOT / "state" / "coverage-runtime-hot-state.json",
    )
    parser.add_argument("--request-id", required=True)
    parser.add_argument("--budget-reserved", required=True, type=float)
    parser.add_argument("--reference-time", default=None)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()
    if args.budget_reserved <= 0:
        parser.error("--budget-reserved must be positive")
    return args


def read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise SystemExit(f"Unable to read {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Invalid JSON in {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise SystemExit(f"Expected JSON object at {path}.")
    return payload


def request_by_id(plan: Mapping[str, Any], request_id: str) -> Mapping[str, Any]:
    requests = plan.get("requests")
    if not isinstance(requests, list):
        raise SystemExit("Coverage plan has no request list.")
    matches = [
        request
        for request in requests
        if isinstance(request, Mapping) and request.get("request_id") == request_id
    ]
    if len(matches) != 1:
        raise SystemExit(f"Expected exactly one coverage request for {request_id}.")
    return matches[0]


def action_kind(request: Mapping[str, Any]) -> ActionKind:
    value = required_string(request, "action_kind")
    try:
        return ActionKind(value)
    except ValueError as exc:
        raise SystemExit(f"Unsupported action kind in coverage request: {value}") from exc


def required_string(payload: Mapping[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise SystemExit(f"Expected non-empty {key}.")
    return value


def write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
