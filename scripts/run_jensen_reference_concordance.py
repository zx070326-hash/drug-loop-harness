"""Run an auditable reference-worker versus local-policy concordance probe."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import asdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from loop_harness.v2 import (  # noqa: E402
    ActionScore,
    ActionSpace,
    JensenMFBOFixtureLoader,
    JensenStyleTVRPolicy,
    LoopRuntime,
    ReferenceConcordanceEvaluator,
    ReferencePolicyRunner,
    StateProjector,
    SubprocessReferencePolicy,
    TraceEvaluator,
    apply_common_warm_start,
)


class OneActionPolicy:
    """Execute one already-ranked action without recomputing a policy decision."""

    def __init__(self, action_id: str, policy_version: str) -> None:
        self.action_id = action_id
        self.policy_version = policy_version

    def rank(self, state, actions):
        return tuple(
            sorted(
                (
                    ActionScore(
                        action=action,
                        information_gain=action.expected_information_gain,
                        decision_value=action.expected_decision_value,
                        cost=action.expected_cost,
                        priority=1.0 if action.action_id == self.action_id else 0.0,
                        rationale="predeclared_concordance_action",
                    )
                    for action in actions
                ),
                key=lambda score: (
                    -score.priority,
                    score.action.candidate_id,
                    score.action.oracle_id,
                    score.action.action_id,
                ),
            )
        )


def main() -> None:
    args = _parse_args()
    workspace = Path(args.workspace).resolve()
    worker_python = str(
        workspace / "external/jensen-mfbo-2025/reference_env/.venv/Scripts/python.exe"
    )
    worker_script = str(workspace / "scripts/run_jensen_baybe_reference_worker.py")
    if args.baybe_wheel:
        wheel = Path(args.baybe_wheel).resolve()
        command = (
            worker_python,
            str(workspace / "scripts/run_jensen_baybe_reference_variant.py"),
            str(wheel),
            worker_script,
        )
    else:
        wheel = None
        command = (worker_python, worker_script)
    adapter = SubprocessReferencePolicy(
        command,
        policy_id="jensen_baybe_reference",
        policy_version="jensen_baybe_reference_v1",
        timeout_seconds=args.timeout_seconds,
    )
    reference_runs = [
        _run_reference(
            workspace=workspace,
            target_id=args.target_id,
            candidate_limit=args.candidate_limit,
            total_budget=args.total_budget,
            seed_per_oracle=args.seed_per_oracle,
            seed=args.seed,
            steps=args.steps,
            runner=ReferencePolicyRunner(adapter),
        )
        for _ in range(2)
    ]
    corrected_local_trace = _run_local(
        workspace=workspace,
        target_id=args.target_id,
        candidate_limit=args.candidate_limit,
        total_budget=args.total_budget,
        seed_per_oracle=args.seed_per_oracle,
        seed=args.seed,
        steps=args.steps,
        legacy_reference_indexing=False,
    )
    legacy_local_trace = _run_local(
        workspace=workspace,
        target_id=args.target_id,
        candidate_limit=args.candidate_limit,
        total_budget=args.total_budget,
        seed_per_oracle=args.seed_per_oracle,
        seed=args.seed,
        steps=args.steps,
        legacy_reference_indexing=True,
    )
    reference_trace = reference_runs[0]["top_actions"]
    deterministic = reference_runs[0]["top_actions"] == reference_runs[1]["top_actions"]
    corrected_concordance = ReferenceConcordanceEvaluator().compare(
        tuple((_semantic_action_key(action),) for action in reference_trace),
        tuple((_semantic_action_key(action),) for action in corrected_local_trace["top_actions"]),
    )
    legacy_concordance = ReferenceConcordanceEvaluator().compare(
        tuple((_semantic_action_key(action),) for action in reference_trace),
        tuple((_semantic_action_key(action),) for action in legacy_local_trace["top_actions"]),
    )
    leakage = _leakage_probe(reference_runs[0]["input_payloads"])
    lock_path = workspace / "external/jensen-mfbo-2025/reference_env/lock-py312.txt"
    lock_sha256 = _sha256(lock_path) if lock_path.is_file() else None
    report = {
        "protocol": {
            "target_id": args.target_id,
            "candidate_limit": args.candidate_limit,
            "total_budget": args.total_budget,
            "seed_per_oracle": args.seed_per_oracle,
            "seed": args.seed,
            "continuation_steps": args.steps,
        },
        "environment": {
            "reference_command": [str(item) for item in command],
            "dependency_lock_sha256": lock_sha256,
            "dependency_lock_status": "candidate_lock_not_historical" if lock_sha256 else "missing",
            "baybe_variant_wheel": str(wheel) if wheel is not None else None,
            "baybe_variant_wheel_sha256": _sha256(wheel) if wheel is not None else None,
        },
        "determinism_probe": {
            "status": "PASS" if deterministic else "FAIL",
            "reference_top_actions_run_1": reference_runs[0]["top_actions"],
            "reference_top_actions_run_2": reference_runs[1]["top_actions"],
        },
        "leakage_probe": leakage,
        "concordance": asdict(legacy_concordance),
        "corrected_policy_concordance": asdict(corrected_concordance),
        "legacy_reference_concordance": asdict(legacy_concordance),
        "reference_trace": reference_runs[0],
        "local_trace": corrected_local_trace,
        "legacy_local_trace": legacy_local_trace,
        "code_reproduction_status": "PASS" if deterministic and leakage["status"] == "PASS" else "FAIL",
        "result_replication_status": legacy_concordance.status,
        "claim_ceiling": "exact reference decision concordance only; no molecular efficacy claim",
    }
    output_path = workspace / args.output
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "output": str(output_path.relative_to(workspace)),
                "code_reproduction_status": report["code_reproduction_status"],
                "result_replication_status": report["result_replication_status"],
                "determinism": report["determinism_probe"]["status"],
                "leakage": report["leakage_probe"]["status"],
            },
            indent=2,
        )
    )


def _run_reference(
    *, workspace: Path, target_id: str, candidate_limit: int, total_budget: float,
    seed_per_oracle: int, seed: int, steps: int, runner: ReferencePolicyRunner,
) -> dict:
    fixture = JensenMFBOFixtureLoader().load(
        workspace=workspace,
        target_id=target_id,
        candidate_limit=candidate_limit,
        total_budget=total_budget,
        max_rounds=seed_per_oracle * 3 + steps + 1,
    )
    apply_common_warm_start(fixture, seed_per_oracle=seed_per_oracle, seed=seed)
    projector = StateProjector()
    top_action_ids: list[str] = []
    top_actions: list[dict[str, str]] = []
    input_payloads: list[dict] = []
    proposal_statuses: list[str] = []
    for _ in range(steps):
        state = projector.project(fixture.ledger)
        run = runner.propose(
            fixture.ledger,
            deterministic_seed=seed + state.round_index,
            record_valid_proposal=True,
        )
        input_payloads.append(
            {
                "schema_version": "reference_policy_input.v1",
                "request": asdict(run.request),
                "candidates": [asdict(candidate) for candidate in state.candidates.values()],
                "observations": [
                    event.payload
                    for event in fixture.ledger.events
                    if event.kind.value == "oracle_result_recorded"
                ],
                "legal_action_ids": list(run.request.legal_action_ids),
            }
        )
        proposal_statuses.append("PASS" if run.validation.accepted else "FAIL")
        if not run.validation.accepted_action_ids:
            break
        action_id = run.validation.accepted_action_ids[0]
        legal_actions = ActionSpace().enumerate(
            state,
            policy_version=runner.adapter.policy_version,
        )
        action = next(action for action in legal_actions if action.action_id == action_id)
        top_action_ids.append(action_id)
        top_actions.append(_semantic_action(action))
        _execute_one(fixture, action_id, runner.adapter.policy_version)
    trace = asdict(TraceEvaluator().evaluate(fixture.ledger))
    return {
        "top_action_ids": top_action_ids,
        "top_actions": top_actions,
        "proposal_statuses": proposal_statuses,
        "trace_evaluation": trace,
        "input_payloads": input_payloads,
    }


def _run_local(
    *, workspace: Path, target_id: str, candidate_limit: int, total_budget: float,
    seed_per_oracle: int, seed: int, steps: int, legacy_reference_indexing: bool,
) -> dict:
    fixture = JensenMFBOFixtureLoader().load(
        workspace=workspace,
        target_id=target_id,
        candidate_limit=candidate_limit,
        total_budget=total_budget,
        max_rounds=seed_per_oracle * 3 + steps + 1,
    )
    apply_common_warm_start(fixture, seed_per_oracle=seed_per_oracle, seed=seed)
    projector = StateProjector()
    policy = JensenStyleTVRPolicy(
        legacy_reference_indexing=legacy_reference_indexing,
    )
    top_action_ids: list[str] = []
    top_actions: list[dict[str, str]] = []
    rank_traces: list[list[str]] = []
    for _ in range(steps):
        state = projector.project(fixture.ledger)
        actions = ActionSpace().enumerate(state, policy_version=policy.policy_version)
        scores = policy.rank(state, actions)
        ranked = [score.action.action_id for score in scores]
        if not ranked:
            break
        top_action_ids.append(ranked[0])
        top_actions.append(_semantic_action(scores[0].action))
        rank_traces.append(ranked)
        _execute_one(fixture, ranked[0], policy.policy_version)
    return {
        "top_action_ids": top_action_ids,
        "top_actions": top_actions,
        "rank_traces": rank_traces,
        "trace_evaluation": asdict(TraceEvaluator().evaluate(fixture.ledger)),
        "legacy_reference_indexing": legacy_reference_indexing,
    }


def _execute_one(fixture, action_id: str, policy_version: str) -> None:
    result = LoopRuntime(
        fixture.executor,
        policy=OneActionPolicy(action_id, policy_version),
    ).step(fixture.ledger)
    if result.status != "completed":
        raise RuntimeError(f"concordance execution stopped: {result.status}:{result.stop_reason}")


def _leakage_probe(input_payloads: list[dict]) -> dict:
    sentinel = "HIDDEN_TRUTH_SENTINEL_9f6c"
    for payload in input_payloads:
        if sentinel in json.dumps(payload, ensure_ascii=True, sort_keys=True):
            return {"status": "FAIL", "sentinel": sentinel}
    return {
        "status": "PASS",
        "sentinel": sentinel,
        "checked_payload_count": len(input_payloads),
        "method": "sentinel absent from canonical policy input payloads",
    }


def _semantic_action(action) -> dict[str, str]:
    return {
        "candidate_id": action.candidate_id,
        "oracle_id": action.oracle_id,
        "endpoint_id": action.endpoint_id,
    }


def _semantic_action_key(action: dict[str, str]) -> str:
    return "|".join((action["candidate_id"], action["oracle_id"], action["endpoint_id"]))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", default=".")
    parser.add_argument("--target-id", default="FACTOR_D")
    parser.add_argument("--candidate-limit", type=_positive_int, default=8)
    parser.add_argument("--total-budget", type=_positive_float, default=5.0)
    parser.add_argument("--seed-per-oracle", type=_positive_int, default=1)
    parser.add_argument("--seed", type=int, default=17)
    parser.add_argument("--steps", type=_positive_int, default=3)
    parser.add_argument("--timeout-seconds", type=_positive_float, default=120.0)
    parser.add_argument("--baybe-wheel")
    parser.add_argument("--output", default="state/jensen-reference-concordance-factor-d.json")
    return parser.parse_args()


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("value must be positive")
    return parsed


def _positive_float(value: str) -> float:
    parsed = float(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("value must be positive")
    return parsed


if __name__ == "__main__":
    main()
