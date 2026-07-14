from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from loop_harness import (  # noqa: E402
    ActionKind,
    ActionRecord,
    BoundedLoopDriver,
    CoverageApprovedExecutor,
    CoverageConsumptionSink,
    LedgerBackedCoverageApprovalGate,
    Observation,
    ObservationCacheExecutor,
    PolicyOutput,
    ReportBuilder,
    TrustGate,
    oracle_cache_from_observations,
)
from loop_harness.adapters import TableObservationAdapter  # noqa: E402
from run_table_campaign import build_store  # noqa: E402


class CoverageRequestPolicy:
    """Submit selected coverage requests once; it does not promote candidates."""

    def __init__(self, actions: tuple[ActionRecord, ...]) -> None:
        self.actions = actions
        self.has_planned = False

    def plan(self, state) -> PolicyOutput:
        if self.has_planned:
            return PolicyOutput(decisions=(), actions=())
        self.has_planned = True
        return PolicyOutput(decisions=(), actions=self.actions)


def main() -> None:
    args = parse_args()
    plan = read_json(args.plan)
    approval = read_json(args.approval_record)
    selected_requests = select_requests(plan, tuple(args.request_id))
    observations = load_observations(tuple(args.observations))
    state = build_store(args.candidates).rebuild()
    seed_baseline_evidence(state, observations, selected_requests)
    run_id = f"coverage_runtime_{uuid4().hex}"
    actions = build_actions(
        selected_requests,
        run_id=run_id,
        approval_id=required_string(approval, "approval_id"),
        state=state,
        budget_reserved=args.budget_reserved,
    )
    executor = CoverageApprovedExecutor(
        ObservationCacheExecutor(oracle_cache_from_observations(observations)),
        approval_gate=LedgerBackedCoverageApprovalGate(
            plan,
            approval,
            ledger_path=args.ledger,
            reference_time=args.reference_time,
        ),
    )
    result = BoundedLoopDriver().run(
        state,
        policy_name="local_cache_coverage_runtime",
        policy=CoverageRequestPolicy(actions),
        executor=executor,
        max_rounds=2,
        coverage_admission_sink=CoverageConsumptionSink(
            plan,
            approval,
            ledger_path=args.ledger,
            reference_time=args.reference_time,
        ),
    )

    payload = ReportBuilder().build_loop_run(result).to_dict()
    targets = target_keys(selected_requests)
    target_cache_observation_count = sum(
        1
        for observation in observations
        if (observation.subject_id, observation.oracle_name) in targets
    )
    payload["coverage_runtime"] = {
        "schema_version": "local_cache_coverage_runtime.v1",
        "run_id": run_id,
        "plan_path": str(args.plan),
        "approval_record_path": str(args.approval_record),
        "consumption_ledger_path": str(args.ledger),
        "candidate_csv": str(args.candidates),
        "observation_cache_csvs": [str(path) for path in args.observations],
        "selected_request_ids": [request["request_id"] for request in selected_requests],
        "baseline_observation_count": len(observations) - target_cache_observation_count,
        "target_cache_observation_count": target_cache_observation_count,
        "executor_mode": "local_declared_observation_cache",
        "live_oracle_calls": 0,
        "autonomous_execution_permitted": False,
        "scientific_claim_upgrade_permitted": False,
        "reference_time": args.reference_time,
    }
    write_json(args.output, payload)
    print(
        "coverage runtime: "
        f"stop={result.stop_reason}; applied={payload['applied_observation_count']}; "
        f"blocked={payload['blocked_action_count']}; live_oracle_calls=0"
    )
    print(f"wrote {args.output}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run selected human-approved coverage requests against a declared "
            "local observation cache. This command never calls a live oracle."
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
        "--candidates",
        type=Path,
        default=ROOT / "examples" / "data" / "multi_fidelity_promotion_candidates.csv",
    )
    parser.add_argument(
        "--observations",
        type=Path,
        nargs="+",
        default=(
            ROOT / "examples" / "data" / "multi_fidelity_promotion_observations.csv",
        ),
    )
    parser.add_argument(
        "--request-id",
        action="append",
        default=[],
        help="Exact request id to run. Repeatable; defaults to every request in the plan.",
    )
    parser.add_argument(
        "--budget-reserved",
        type=float,
        default=None,
        help="Reserved budget for every selected action; defaults to registered oracle cost.",
    )
    parser.add_argument("--reference-time", default=None)
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "state" / "coverage-runtime-report.json",
    )
    return parser.parse_args()


def read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise SystemExit(f"Expected JSON object at {path}.")
    return payload


def load_observations(paths: tuple[Path, ...]) -> tuple[Observation, ...]:
    observations: list[Observation] = []
    for path in paths:
        observations.extend(TableObservationAdapter(path).load_observations())
    return tuple(observations)


def select_requests(
    plan: Mapping[str, Any],
    requested_ids: tuple[str, ...],
) -> tuple[dict[str, str], ...]:
    raw = plan.get("requests")
    if not isinstance(raw, list):
        raise SystemExit("Coverage plan has no request list.")
    requests = tuple(
        normalize_request(item)
        for item in raw
        if isinstance(item, Mapping)
    )
    if len(requests) != len(raw):
        raise SystemExit("Coverage plan contains a non-object request.")
    by_id = {request["request_id"]: request for request in requests}
    if len(by_id) != len(requests):
        raise SystemExit("Coverage plan contains duplicate request ids.")
    selected_ids = requested_ids or tuple(by_id)
    unknown = sorted(set(selected_ids) - set(by_id))
    if unknown:
        raise SystemExit("Unknown coverage request ids: " + ", ".join(unknown))
    return tuple(by_id[request_id] for request_id in selected_ids)


def normalize_request(request: Mapping[str, Any]) -> dict[str, str]:
    return {
        key: required_string(request, key)
        for key in ("request_id", "subject_id", "action_kind", "oracle_name")
    }


def build_actions(
    requests: tuple[dict[str, str], ...],
    *,
    run_id: str,
    approval_id: str,
    state,
    budget_reserved: float | None,
) -> tuple[ActionRecord, ...]:
    actions: list[ActionRecord] = []
    for request in requests:
        try:
            kind = ActionKind(request["action_kind"])
        except ValueError as exc:
            raise SystemExit(
                f"Unsupported action kind in coverage request: {request['action_kind']}"
            ) from exc
        oracle = state.oracles.get(request["oracle_name"])
        if oracle is None:
            raise SystemExit(
                f"Coverage request references an unregistered oracle: {request['oracle_name']}"
            )
        reserved = budget_reserved if budget_reserved is not None else oracle.trust.cost
        if reserved <= 0:
            raise SystemExit("--budget-reserved must be positive.")
        actions.append(
            ActionRecord(
                id=f"{run_id}_{request['request_id']}",
                kind=kind,
                subject_ids=(request["subject_id"],),
                requested_by="local_cache_coverage_runtime",
                budget_reserved=reserved,
                reason="human_approved_coverage_request",
                parameters={
                    "coverage_request_id": request["request_id"],
                    "coverage_approval_id": approval_id,
                },
            )
        )
    return tuple(actions)


def seed_baseline_evidence(
    state,
    observations: tuple[Observation, ...],
    requests: tuple[dict[str, str], ...],
) -> None:
    targets = target_keys(requests)
    gate = TrustGate()
    for observation in observations:
        if (observation.subject_id, observation.oracle_name) in targets:
            continue
        if observation.subject_id not in state.compounds:
            raise SystemExit(f"Observation has unknown subject: {observation.subject_id}")
        oracle = state.oracles.get(observation.oracle_name)
        if oracle is None:
            raise SystemExit(f"Observation has unregistered oracle: {observation.oracle_name}")
        existing = tuple(
            state.observations[observation_id]
            for observation_id in state.compounds[observation.subject_id].observations
        )
        state.add_observation(observation)
        state.add_trust_gate_result(
            gate.evaluate(observation, oracle, existing_observations=existing)
        )


def target_keys(requests: tuple[dict[str, str], ...]) -> set[tuple[str, str]]:
    return {(request["subject_id"], request["oracle_name"]) for request in requests}


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
