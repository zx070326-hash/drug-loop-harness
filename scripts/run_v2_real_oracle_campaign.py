"""Run an ALDH1 loop with fixed local ADMET-AI and AutoDock Vina profiles."""

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
    EndpointSpec,
    EventKind,
    ExperimentProtocolSpec,
    JsonCommandLiveAdapter,
    JsonCommandProfile,
    OracleSpec,
    ScreenThenPromotePolicy,
    StateProjector,
    TraceEvaluator,
)


TARGET_ID = "ALDH1"
ADMET_ORACLE = "admet_ai_v2"
ADMET_ENDPOINT = "admet_ames_probability"
ADMET_COST = 0.25
VINA_ORACLE = "vina_4wp7"
VINA_ENDPOINT = "vina_affinity_kcal_mol"
VINA_COST = 2.0
CANDIDATE_SOURCE = Path(
    "state/multitarget-policy-replication-pools-v2/ALDH1/"
    "hou_tocodecoy_aldh1_replication_v1/candidates.csv"
)
CANDIDATES = (
    (
        "tocodecoy:ALDH1:241075",
        "CS(=O)(=O)N(CC#N)CCCN1C(=O)C(=Cc2c(O)ccc3ccccc23)SC1=S",
    ),
    ("tocodecoy:ALDH1:245034", "CC(=O)N(C)CCCn1c(=O)n(C)c(=O)c2c1c1c(n2C)CCC1"),
    ("tocodecoy:ALDH1:247372", "CN1CCC2(CC1)NCCCS2"),
    ("tocodecoy:ALDH1:263382", "CC(OCCOCc1n[nH]c(=S)o1)N1CCN(C)CC1"),
)


def main() -> None:
    args = _parse_args()
    workspace = Path(args.workspace).resolve()
    run_root = workspace / args.run_root
    store = ContentAddressedArtifactStore(run_root / "artifacts")
    ledger = _build_ledger(args.total_budget, args.max_rounds)
    profiles, profile_refs = _build_profiles(workspace, store, ledger)
    policy = ScreenThenPromotePolicy(
        screening_oracle_id=ADMET_ORACLE,
        promotion_oracle_id=VINA_ORACLE,
        screening_metric="AMES",
        min_screened_before_promotion=2,
    )
    projector = StateProjector()
    selected: list[dict] = []

    while True:
        state = projector.project(ledger)
        if state.round_index >= state.spec.max_rounds:
            ledger.append(EventKind.CAMPAIGN_STOPPED, {"reason": "max_rounds_reached"})
            break
        actions = ActionSpace().enumerate(state, policy_version=policy.policy_version)
        affordable = tuple(
            action
            for action in actions
            if action.expected_cost <= state.budget_available + 1e-9
        )
        scores = policy.rank(state, affordable)
        if not scores:
            reason = "budget_exhausted" if actions and not affordable else "no_policy_eligible_actions"
            ledger.append(EventKind.CAMPAIGN_STOPPED, {"reason": reason})
            break
        action = scores[0].action
        profile = profiles[action.oracle_id]
        controller = AsyncJobController(JsonCommandLiveAdapter(profile, store))
        job = controller.prepare(ledger, action)
        controller.dispatch(ledger, job.job_id)
        controller = AsyncJobController(JsonCommandLiveAdapter(profile, store))
        receipt = controller.poll(ledger, job.job_id)
        if receipt.status.value != "succeeded":
            raise RuntimeError(
                f"real oracle failed: {action.oracle_id}:{receipt.status.value}:"
                f"{receipt.failure_code}"
            )
        settled = projector.project(ledger).results[(action.candidate_id, action.oracle_id)]
        selected.append(
            {
                "round_index": state.round_index,
                "candidate_id": action.candidate_id,
                "oracle_id": action.oracle_id,
                "endpoint_id": action.endpoint_id,
                "priority": scores[0].priority,
                "rationale": scores[0].rationale,
                "metrics": dict(settled.metrics),
                "profile_artifact_sha256": profile_refs[action.oracle_id],
            }
        )

    final_state = projector.project(ledger)
    trace = TraceEvaluator().evaluate(ledger)
    ledger_path = run_root / "ledger.json"
    ledger.dump(ledger_path)
    admet_count = sum(item["oracle_id"] == ADMET_ORACLE for item in selected)
    vina_count = sum(item["oracle_id"] == VINA_ORACLE for item in selected)
    report = {
        "schema_version": "real_oracle_campaign_report.v1",
        "campaign_id": ledger.campaign_id,
        "target_id": TARGET_ID,
        "candidate_source": str(CANDIDATE_SOURCE),
        "candidate_count": len(CANDIDATES),
        "policy": {
            "policy_version": policy.policy_version,
            "screening_oracle": ADMET_ORACLE,
            "promotion_oracle": VINA_ORACLE,
            "screening_metric": "AMES",
            "min_screened_before_promotion": 2,
            "composite_score_created": False,
        },
        "profile_artifact_sha256": profile_refs,
        "selected_actions": selected,
        "admet_result_count": admet_count,
        "vina_result_count": vina_count,
        "terminal_reason": final_state.stop_reason,
        "budget_spent": final_state.budget_spent,
        "trace_evaluation": asdict(trace),
        "operational_status": (
            "PASS" if trace.status == "PASS" and admet_count >= 2 and vina_count >= 1 else "FAIL"
        ),
        "scientific_status": "COMPUTATIONAL_PROXY_ONLY",
        "claim_ceiling": (
            "The run demonstrates autonomous budgeted acquisition with fixed local profiles. "
            "It does not validate affinity ranking, activity, selectivity, ADMET safety, or a molecule."
        ),
        "ledger_path": str(ledger_path.relative_to(workspace)),
    }
    report_path = run_root / "report.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(json_ready(report), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "report": str(report_path.relative_to(workspace)),
                "operational_status": report["operational_status"],
                "scientific_status": report["scientific_status"],
                "admet_result_count": admet_count,
                "vina_result_count": vina_count,
                "budget_spent": final_state.budget_spent,
                "terminal_reason": final_state.stop_reason,
            },
            indent=2,
        )
    )


def _build_profiles(
    workspace: Path,
    store: ContentAddressedArtifactStore,
    ledger: CampaignLedger,
) -> tuple[dict[str, JsonCommandProfile], dict[str, str]]:
    vina_path = workspace / "external/docking/aldh1/4wp7_v2/vina_oracle_profile.v1.json"
    admet_path = workspace / "external/tools/admet-ai-2.0.1/admet_oracle_profile.v1.json"
    vina_payload = json.loads(vina_path.read_text(encoding="utf-8"))
    admet_payload = json.loads(admet_path.read_text(encoding="utf-8"))
    vina_ref = store.put_json(vina_payload, producer_version=vina_payload["adapter_version"])
    admet_ref = store.put_json(admet_payload, producer_version=admet_payload["adapter_version"])
    calibration_path = workspace / vina_payload["calibration"]["verification"]["path"]
    calibration_payload = json.loads(calibration_path.read_text(encoding="utf-8"))
    calibration_ref = store.put_json(calibration_payload, producer_version="vina_pose_recovery_v1")
    for reference in (vina_ref, admet_ref, calibration_ref):
        ledger.append(EventKind.RAW_ARTIFACT_RECORDED, {"artifact": reference})
    profiles = {
        ADMET_ORACLE: JsonCommandProfile(
            adapter_id="admet_ai_json_command",
            adapter_version=admet_payload["adapter_version"],
            command=(
                str(workspace / admet_payload["runtime"]["python"]["path"]),
                str(workspace / "scripts/run_v2_admet_ai_oracle.py"),
                "{input_path}",
                str(admet_path),
            ),
            accounted_cost=ADMET_COST,
            timeout_seconds=180,
            cwd=workspace,
            target_id=TARGET_ID,
            endpoint_id=ADMET_ENDPOINT,
            profile_artifact=admet_ref,
            require_calibration=False,
        ),
        VINA_ORACLE: JsonCommandProfile(
            adapter_id="vina_json_command",
            adapter_version=vina_payload["adapter_version"],
            command=(
                str(workspace / vina_payload["ligand_preparation"]["python"]["path"]),
                str(workspace / "scripts/run_v2_vina_oracle.py"),
                "{input_path}",
                str(vina_path),
            ),
            accounted_cost=VINA_COST,
            timeout_seconds=360,
            cwd=workspace,
            target_id=TARGET_ID,
            endpoint_id=VINA_ENDPOINT,
            profile_artifact=vina_ref,
            calibration_artifact=calibration_ref,
            require_calibration=True,
        ),
    }
    return profiles, {ADMET_ORACLE: admet_ref.sha256, VINA_ORACLE: vina_ref.sha256}


def _build_ledger(total_budget: float, max_rounds: int) -> CampaignLedger:
    campaign_id = "aldh1-real-oracle-loop-v1"
    ledger = CampaignLedger(campaign_id)
    ledger.append(
        EventKind.CAMPAIGN_CREATED,
        CampaignSpec(
            campaign_id=campaign_id,
            target_id=TARGET_ID,
            scientific_goal="Budgeted ALDH1 computational screen and docking promotion",
            primary_endpoint=VINA_ENDPOINT,
            endpoints=(
                EndpointSpec(VINA_ENDPOINT, direction="minimize", unit="kcal/mol"),
                EndpointSpec(ADMET_ENDPOINT, direction="minimize", unit="probability"),
            ),
            total_budget=total_budget,
            max_rounds=max_rounds,
            allowed_oracle_ids=(ADMET_ORACLE, VINA_ORACLE),
        ),
    )
    ledger.append(
        EventKind.EXPERIMENT_PROTOCOL_REGISTERED,
        ExperimentProtocolSpec(
            protocol_id="aldh1-real-oracle-loop-protocol-v1",
            protocol_version="v1",
            target_ids=(TARGET_ID,),
            candidate_cohort_ids=("hou_tocodecoy_aldh1_replication_v1_subset",),
            policy_ids=(
                ScreenThenPromotePolicy.policy_version,
                "fixed_profile_execution_controller_v1",
            ),
            randomization_seed=20260713,
            total_budget=total_budget,
            max_rounds=max_rounds,
            primary_metrics=("AMES", VINA_ENDPOINT, "trace_status"),
            claim_ceiling="computational_proxy_operational_only",
            evaluator_version="v2_trace_evaluator",
        ),
    )
    for candidate_id, smiles in CANDIDATES:
        ledger.append(
            EventKind.CANDIDATE_REGISTERED,
            CandidateSpec(candidate_id, smiles, route_id="target_ALDH1"),
        )
    ledger.append(
        EventKind.ORACLE_REGISTERED,
        OracleSpec(
            oracle_id=ADMET_ORACLE,
            evidence_tier="l1_uncalibrated_computational_admet",
            expected_cost=ADMET_COST,
            endpoint_id=ADMET_ENDPOINT,
            anchor_id="admet-ai:2.0.1:model-tree:57ced2e15a1c",
        ),
    )
    ledger.append(
        EventKind.ORACLE_REGISTERED,
        OracleSpec(
            oracle_id=VINA_ORACLE,
            evidence_tier="l2_pose_recovery_bounded_docking_proxy",
            expected_cost=VINA_COST,
            endpoint_id=VINA_ENDPOINT,
            promotion_predecessors=(ADMET_ORACLE,),
            anchor_id="aldh1_4wp7_vina_pose_recovery_v1",
        ),
    )
    return ledger


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", default=".")
    parser.add_argument("--run-root", default="state/aldh1-real-oracle-loop-v1")
    parser.add_argument("--total-budget", type=float, default=5.0)
    parser.add_argument("--max-rounds", type=int, default=8)
    return parser.parse_args()


if __name__ == "__main__":
    main()
