"""Run a restart-safe computational prospective campaign with a real RDKit profile."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from loop_harness.artifact_hash import json_ready  # noqa: E402
from loop_harness.v2 import (  # noqa: E402
    ActionSpace,
    AsyncJobController,
    CampaignLedger,
    CampaignSpec,
    CandidateSpec,
    ContentAddressedArtifactStore,
    ContinuousPosteriorFitter,
    EndpointSpec,
    EventKind,
    ExperimentProtocolSpec,
    JobControlError,
    JensenStyleTVRPolicy,
    JsonCommandLiveAdapter,
    JsonCommandProfile,
    OracleSpec,
    PosteriorRepository,
    StateProjector,
    TraceEvaluator,
)


TARGET_ID = "PROSPECTIVE_RDKIT_QED_V1"
ENDPOINT_ID = "qed"
ORACLE_ID = "rdkit_qed"
ORACLE_COST = 0.1
CANDIDATES = (
    ("mol_01", "CCO"),
    ("mol_02", "c1ccccc1"),
    ("mol_03", "CC(=O)OC1=CC=CC=C1C(=O)O"),
    ("mol_04", "CC(C)Cc1ccc(cc1)[C@@H](C)C(=O)O"),
    ("mol_05", "Cn1cnc2c1c(=O)n(C(=O)N2C)C"),
    ("mol_06", "CCN(CC)CCOC1=CC=CC=C1"),
)


def main() -> None:
    args = _parse_args()
    workspace = Path(args.workspace).resolve()
    run_root = workspace / args.run_root
    artifact_root = run_root / "artifacts"
    store = ContentAddressedArtifactStore(artifact_root)
    ledger = _build_ledger(args.total_budget, args.max_rounds)
    profile_ref = store.put_json(
        {
            "profile_id": "rdkit_qed_v1",
            "target_id": TARGET_ID,
            "endpoint_id": ENDPOINT_ID,
            "tool": "RDKit",
            "tool_policy": "local deterministic descriptor computation",
        },
        producer_version="rdkit_qed_v1",
    )
    calibration_ref = store.put_json(
        {
            "calibration_id": "rdkit_qed_descriptor_validity_v1",
            "target_id": TARGET_ID,
            "endpoint_id": ENDPOINT_ID,
            "status": "qualified_for_computational_descriptor_only",
            "claim_ceiling": "no experimental efficacy or safety claim",
        },
        producer_version="rdkit_qed_v1",
    )
    ledger.append(EventKind.RAW_ARTIFACT_RECORDED, {"artifact": profile_ref})
    ledger.append(EventKind.RAW_ARTIFACT_RECORDED, {"artifact": calibration_ref})
    profile = JsonCommandProfile(
        adapter_id="rdkit_json_command",
        adapter_version="rdkit_qed_v1",
        command=(
            sys.executable,
            str(workspace / "scripts/run_v2_rdkit_qed_oracle.py"),
            "{input_path}",
        ),
        accounted_cost=ORACLE_COST,
        timeout_seconds=60.0,
        target_id=TARGET_ID,
        endpoint_id=ENDPOINT_ID,
        profile_artifact=profile_ref,
        calibration_artifact=calibration_ref,
        require_calibration=True,
    )
    fitter = ContinuousPosteriorFitter()
    policy = JensenStyleTVRPolicy(posterior_repository=PosteriorRepository(store))
    controller = AsyncJobController(
        JsonCommandLiveAdapter(profile, store),
        posterior_fitter=fitter,
        posterior_store=store,
        posterior_endpoint_ids=(ENDPOINT_ID,),
    )
    projector = StateProjector()
    selected: list[dict] = []
    restart_recovered_rounds = 0
    duplicate_dispatch_rejections = 0
    while True:
        state = projector.project(ledger)
        if state.budget_available < ORACLE_COST - 1e-9:
            ledger.append(EventKind.CAMPAIGN_STOPPED, {"reason": "budget_exhausted"})
            break
        if state.round_index >= state.spec.max_rounds:
            ledger.append(EventKind.CAMPAIGN_STOPPED, {"reason": "max_rounds_reached"})
            break
        actions = ActionSpace().enumerate(state, policy_version=policy.policy_version)
        scores = policy.rank(state, actions)
        if not scores:
            ledger.append(EventKind.CAMPAIGN_STOPPED, {"reason": "no_available_actions"})
            break
        action = scores[0].action
        job = controller.prepare(ledger, action)
        controller.dispatch(ledger, job.job_id)
        try:
            controller.dispatch(ledger, job.job_id)
        except JobControlError as exc:
            if str(exc) != "job_already_dispatched":
                raise
            duplicate_dispatch_rejections += 1
        # Recreate both adapter and controller before poll to exercise restart recovery.
        controller = AsyncJobController(
            JsonCommandLiveAdapter(profile, store),
            posterior_fitter=fitter,
            posterior_store=store,
            posterior_endpoint_ids=(ENDPOINT_ID,),
        )
        receipt = controller.poll(ledger, job.job_id)
        if receipt.status.value != "succeeded":
            raise RuntimeError(f"RDKit oracle failed: {receipt.status.value}:{receipt.failure_code}")
        restart_recovered_rounds += 1
        selected.append(
            {
                "round_index": state.round_index,
                "candidate_id": action.candidate_id,
                "action_id": action.action_id,
                "oracle_id": action.oracle_id,
                "profile_artifact_sha256": profile_ref.sha256,
                "calibration_artifact_sha256": calibration_ref.sha256,
            }
        )

    final_state = projector.project(ledger)
    trace = TraceEvaluator().evaluate(ledger)
    timeout_probe = _run_timeout_probe(workspace, run_root)
    ledger_path = run_root / "ledger.json"
    ledger.dump(ledger_path)
    report = {
        "campaign_id": ledger.campaign_id,
        "target_id": TARGET_ID,
        "endpoint_id": ENDPOINT_ID,
        "profile": asdict(profile),
        "profile_artifact_sha256": profile_ref.sha256,
        "calibration_artifact_sha256": calibration_ref.sha256,
        "selected_actions": selected,
        "restart_recovered_rounds": restart_recovered_rounds,
        "duplicate_dispatch_rejections": duplicate_dispatch_rejections,
        "timeout_probe": timeout_probe,
        "terminal_reason": final_state.stop_reason,
        "budget_spent": final_state.budget_spent,
        "posterior_snapshot_count": len(final_state.posterior_snapshots),
        "trace_evaluation": asdict(trace),
        "prospective_status": (
            "PASS"
            if (
                trace.status == "PASS"
                and duplicate_dispatch_rejections == len(selected)
                and timeout_probe["status"] == "PASS"
            )
            else "FAIL"
        ),
        "claim_ceiling": (
            "Computational prospective operation and artifact/restart integrity only; "
            "no experimental efficacy, safety, or clinical claim."
        ),
        "ledger_path": str(ledger_path.relative_to(workspace)),
    }
    report_path = run_root / "report.json"
    report_path.write_text(
        json.dumps(json_ready(report), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "report": str(report_path.relative_to(workspace)),
                "ledger": str(ledger_path.relative_to(workspace)),
                "prospective_status": report["prospective_status"],
                "restart_recovered_rounds": restart_recovered_rounds,
                "duplicate_dispatch_rejections": duplicate_dispatch_rejections,
                "timeout_probe_status": timeout_probe["status"],
                "selected_action_count": len(selected),
                "terminal_reason": final_state.stop_reason,
            },
            indent=2,
        )
    )


def _run_timeout_probe(workspace: Path, run_root: Path) -> dict:
    probe_root = run_root / "timeout-probe"
    store = ContentAddressedArtifactStore(probe_root / "artifacts")
    ledger = _build_ledger(total_budget=0.6, max_rounds=2)
    profile_ref = store.put_json(
        {"profile_id": "rdkit_timeout_probe_v1", "target_id": TARGET_ID, "endpoint_id": ENDPOINT_ID},
        producer_version="rdkit_timeout_probe_v1",
    )
    calibration_ref = store.put_json(
        {"calibration_id": "rdkit_timeout_probe_calibration_v1", "status": "probe_only"},
        producer_version="rdkit_timeout_probe_v1",
    )
    ledger.append(EventKind.RAW_ARTIFACT_RECORDED, {"artifact": profile_ref})
    ledger.append(EventKind.RAW_ARTIFACT_RECORDED, {"artifact": calibration_ref})
    profile = JsonCommandProfile(
        adapter_id="rdkit_timeout_probe",
        adapter_version="rdkit_timeout_probe_v1",
        command=(
            sys.executable,
            "-c",
            "import time; time.sleep(0.2)",
            "{input_path}",
        ),
        accounted_cost=ORACLE_COST,
        timeout_seconds=0.01,
        target_id=TARGET_ID,
        endpoint_id=ENDPOINT_ID,
        profile_artifact=profile_ref,
        calibration_artifact=calibration_ref,
        require_calibration=True,
    )
    action = ActionSpace().enumerate(
        StateProjector().project(ledger),
        policy_version="rdkit_timeout_probe_v1",
    )[0]
    controller = AsyncJobController(JsonCommandLiveAdapter(profile, store))
    job = controller.prepare(ledger, action)
    controller.dispatch(ledger, job.job_id)
    receipt = controller.poll(ledger, job.job_id)
    trace = TraceEvaluator().evaluate(ledger)
    return {
        "status": "PASS"
        if receipt.status.value == "timeout" and trace.status == "PASS"
        else "FAIL",
        "receipt_status": receipt.status.value,
        "trace_status": trace.status,
    }


def _build_ledger(total_budget: float, max_rounds: int) -> CampaignLedger:
    campaign_id = "prospective-rdkit-qed-v1"
    ledger = CampaignLedger(campaign_id)
    ledger.append(
        EventKind.CAMPAIGN_CREATED,
        CampaignSpec(
            campaign_id=campaign_id,
            target_id=TARGET_ID,
            scientific_goal="Prospective computational QED descriptor loop",
            primary_endpoint=ENDPOINT_ID,
            endpoints=(EndpointSpec(ENDPOINT_ID, direction="maximize", unit="qed_score"),),
            total_budget=total_budget,
            max_rounds=max_rounds,
            allowed_oracle_ids=(ORACLE_ID,),
        ),
    )
    ledger.append(
        EventKind.EXPERIMENT_PROTOCOL_REGISTERED,
        ExperimentProtocolSpec(
            protocol_id="prospective-rdkit-qed-protocol-v1",
            protocol_version="v1",
            target_ids=(TARGET_ID,),
            candidate_cohort_ids=("rdkit_demo_molecules_v1",),
            policy_ids=(JensenStyleTVRPolicy.policy_version, "rdkit_json_command"),
            randomization_seed=17,
            total_budget=total_budget,
            max_rounds=max_rounds,
            primary_metrics=("qed", "restart_recovery", "trace_status"),
            claim_ceiling="computational_prospective_operational_only",
            evaluator_version="v2_trace_evaluator",
        ),
    )
    for candidate_id, smiles in CANDIDATES:
        ledger.append(EventKind.CANDIDATE_REGISTERED, CandidateSpec(candidate_id, smiles))
    ledger.append(
        EventKind.ORACLE_REGISTERED,
        OracleSpec(
            oracle_id=ORACLE_ID,
            evidence_tier="l1_computational_descriptor",
            expected_cost=ORACLE_COST,
            endpoint_id=ENDPOINT_ID,
            anchor_id="rdkit:qed:2024.03.3:rdkit_qed_v1",
        ),
    )
    return ledger


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", default=".")
    parser.add_argument("--total-budget", type=_positive_float, default=0.6)
    parser.add_argument("--max-rounds", type=_positive_int, default=8)
    parser.add_argument("--run-root", default="state/prospective-rdkit-qed-v1")
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
