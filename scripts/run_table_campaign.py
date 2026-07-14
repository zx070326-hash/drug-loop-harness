from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from loop_harness import (  # noqa: E402
    Budget,
    BudgetCurveRunner,
    CampaignStore,
    ClaimLevel,
    ClaimProtocol,
    EventKind,
    ExperimentEvaluator,
    EvidenceRequirement,
    EvidenceTier,
    FixedCascadePolicy,
    GreedyProxyPolicy,
    LoopState,
    Observation,
    OracleAnchorSpec,
    OracleCacheReplayRunner,
    OracleKind,
    OracleSpec,
    ReplayRunner,
    ReportBuilder,
    Route,
    StagedPromotionPolicy,
    TrustAwarePolicy,
    TrustGate,
    TrustGapAwarePolicy,
    TrustGapCalculator,
    TrustProfile,
    contract_to_payload,
    new_event,
    oracle_cache_from_observations,
)
from loop_harness.adapters import TableCandidateAdapter, TableObservationAdapter  # noqa: E402


CAMPAIGN_ID = "fixture_campaign"


def main() -> None:
    args = parse_args()
    report_payload = build_report_payload_from_paths(
        args.candidates,
        tuple(args.observations),
        max_cache_rounds=args.max_cache_rounds,
        cache_budget_limit=args.cache_budget_limit,
        cache_budget_curve=(
            tuple(args.cache_budget_curve)
            if args.cache_budget_curve is not None
            else None
        ),
    )

    text = json.dumps(report_payload, indent=2)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
        print(f"wrote {args.output}")
    else:
        print(text)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the table-backed drug discovery loop fixture.",
    )
    parser.add_argument(
        "--candidates",
        type=Path,
        default=ROOT / "examples" / "data" / "candidates.csv",
    )
    parser.add_argument(
        "--observations",
        type=Path,
        nargs="+",
        default=(
            ROOT / "examples" / "data" / "observations_round_1.csv",
            ROOT / "examples" / "data" / "observations_round_2.csv",
        ),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "examples" / "data" / "campaign_report.json",
    )
    parser.add_argument("--max-cache-rounds", type=int, default=4)
    parser.add_argument("--cache-budget-limit", type=float, default=None)
    parser.add_argument(
        "--cache-budget-curve",
        type=float,
        nargs="+",
        default=None,
        help="Run oracle-cache replay at multiple budget limits.",
    )
    return parser.parse_args()


def build_store(candidate_csv: Path) -> CampaignStore:
    store = CampaignStore()
    append_contract(
        store,
        EventKind.CAMPAIGN_CREATED,
        LoopState(
            campaign_id=CAMPAIGN_ID,
            goal={
                "target": "demo_kinase",
                "truth_anchor": "human_review_or_assay",
                "objective": "credible hit under ADMET and binding evidence",
            },
            budget_total=Budget(
                cheap_calls=100,
                medium_calls=40,
                high_fidelity_calls=8,
                human_reviews=4,
            ),
        ),
    )
    append_contract(
        store,
        EventKind.ROUTE_OPENED,
        Route(id="route_hinge_binder", hypothesis="preserve hinge binder motif"),
    )
    for oracle in demo_oracles():
        append_contract(store, EventKind.ORACLE_REGISTERED, oracle)
    for protocol in claim_protocols():
        append_contract(store, EventKind.CLAIM_PROTOCOL_REGISTERED, protocol)
    for compound in TableCandidateAdapter(candidate_csv).load_candidates():
        append_contract(store, EventKind.CANDIDATE_INGESTED, compound)
    return store


def record_observations(
    store: CampaignStore,
    observations: tuple[Observation, ...],
) -> None:
    for observation in observations:
        state = store.rebuild()
        existing = tuple(
            state.observations[obs_id]
            for obs_id in state.compounds[observation.subject_id].observations
        )
        append_contract(store, EventKind.OBSERVATION_RECORDED, observation)
        result = TrustGate().evaluate(
            observation,
            state.oracles[observation.oracle_name],
            existing_observations=existing,
        )
        append_contract(store, EventKind.TRUST_GATE_RESULT_RECORDED, result)


def record_trust_gap(
    store: CampaignStore,
    weak_oracle: str,
    strong_oracle: str,
) -> None:
    metric = TrustGapCalculator().compute_pair(
        store.rebuild(),
        weak_oracle,
        strong_oracle,
    )
    append_contract(store, EventKind.TRUST_GAP_RECORDED, metric)


def load_observations(paths: tuple[Path, ...]) -> tuple[Observation, ...]:
    observations: list[Observation] = []
    for path in paths:
        observations.extend(TableObservationAdapter(path).load_observations())
    return tuple(observations)


def build_report_payload_from_paths(
    candidate_csv: Path,
    observation_paths: tuple[Path, ...],
    *,
    max_cache_rounds: int = 4,
    cache_budget_limit: float | None = None,
    cache_budget_curve: tuple[float, ...] | None = None,
) -> dict[str, object]:
    return build_report_payload(
        candidate_csv,
        load_observations(observation_paths),
        max_cache_rounds=max_cache_rounds,
        cache_budget_limit=cache_budget_limit,
        cache_budget_curve=cache_budget_curve,
    )


def build_report_payload(
    candidate_csv: Path,
    observations: tuple[Observation, ...],
    *,
    max_cache_rounds: int = 4,
    cache_budget_limit: float | None = None,
    cache_budget_curve: tuple[float, ...] | None = None,
) -> dict[str, object]:
    store = build_store(candidate_csv)
    initial_state = store.rebuild()
    record_observations(store, observations)
    record_trust_gap(store, "table_docking", "table_rescoring")

    final_state = store.rebuild()
    recorded_replays = ReplayRunner().run(
        final_state,
        {
            "fixed_cascade": FixedCascadePolicy(),
            "trust_aware": TrustAwarePolicy(),
            "trust_gap_aware": TrustGapAwarePolicy(),
        },
    )
    report_payload = ReportBuilder().build(final_state, recorded_replays).to_dict()
    report_payload["oracle_cache_replay"] = cache_replay_payload(
        initial_state,
        observations,
        max_cache_rounds,
        cache_budget_limit,
    )
    if cache_budget_curve is not None:
        report_payload["budget_curve"] = budget_curve_payload(
            initial_state,
            observations,
            max_cache_rounds,
            cache_budget_curve,
        )
    return report_payload


def cache_replay_payload(
    initial_state: LoopState,
    observations: tuple[Observation, ...],
    max_rounds: int,
    budget_limit: float | None,
) -> list[dict[str, object]]:
    replays = OracleCacheReplayRunner().run(
        initial_state,
        {"fixed_cascade": FixedCascadePolicy()},
        oracle_cache_from_observations(observations),
        max_rounds=max_rounds,
        budget_limit=budget_limit,
    )
    evaluator = ExperimentEvaluator()
    return [
        {
            "policy_name": replay.policy_name,
            "action_count": len(replay.actions),
            "planned_action_cost": replay.metrics.planned_action_cost,
            "credible_hit_rate": replay.metrics.credible_hit_rate,
            "experiment_ready_rate": replay.metrics.experiment_ready_rate,
            "experiment_summary": evaluator.summarize_cache_replay(replay).to_dict(),
            "missing_observations": [
                {
                    "subject_id": missing.subject_id,
                    "action_kind": missing.action_kind.value,
                    "oracle_name": missing.oracle_name,
                    "reason": missing.reason,
                }
                for missing in replay.missing_observations
            ],
            "rounds": [
                {
                    "round_index": round_trace.round_index,
                    "planned_action_count": round_trace.planned_action_count,
                    "applied_observation_count": round_trace.applied_observation_count,
                    "missing_observation_count": round_trace.missing_observation_count,
                    "stop_reason": round_trace.stop_reason,
                    "actions": [
                        {
                            "subject_id": trace.subject_id,
                            "action_kind": trace.action_kind.value,
                            "oracle_name": trace.oracle_name,
                            "status": trace.status,
                            "reason": trace.reason,
                            "observation_id": trace.observation_id,
                            "trust_gate_result_id": trace.trust_gate_result_id,
                            "trust_gate_decision": (
                                trace.trust_gate_decision.value
                                if trace.trust_gate_decision is not None
                                else None
                            ),
                            "evidence_tier": (
                                trace.evidence_tier.value
                                if trace.evidence_tier is not None
                                else None
                            ),
                        }
                        for trace in round_trace.action_traces
                    ],
                }
                for round_trace in replay.rounds
            ],
        }
        for replay in replays
    ]


def budget_curve_payload(
    initial_state: LoopState,
    observations: tuple[Observation, ...],
    max_rounds: int,
    budget_limits: tuple[float, ...],
) -> dict[str, object]:
    report = BudgetCurveRunner(
        replay_runner=OracleCacheReplayRunner(
            trust_gap_pairs=(("table_docking", "table_rescoring"),),
            include_scaffold_trust_gaps=True,
        )
    ).run(
        initial_state,
        budget_curve_policies(),
        oracle_cache_from_observations(observations),
        budget_limits=budget_limits,
        max_rounds=max_rounds,
    )
    return report.to_dict()


def budget_curve_policies() -> dict[str, object]:
    return {
        "fixed_cascade": FixedCascadePolicy(),
        "staged_greedy_proxy": StagedPromotionPolicy(
            GreedyProxyPolicy(),
            policy_name="staged_greedy_proxy",
            max_promotions_per_round=1,
        ),
        "staged_trust_aware": StagedPromotionPolicy(
            TrustAwarePolicy(),
            policy_name="staged_trust_aware",
            max_promotions_per_round=1,
        ),
        "staged_trust_gap_aware": StagedPromotionPolicy(
            TrustGapAwarePolicy(min_value_per_cost=0.1),
            policy_name="staged_trust_gap_aware",
            max_promotions_per_round=1,
        ),
    }


def claim_protocols() -> tuple[ClaimProtocol, ...]:
    return (
        ClaimProtocol(
            name="credible_hit_minimal",
            level=ClaimLevel.CREDIBLE_HIT,
            requirements=(
                EvidenceRequirement(
                    claim_level=ClaimLevel.CREDIBLE_HIT,
                    required_tiers=(
                        EvidenceTier.L2_ADMET_PROXY,
                        EvidenceTier.L3_BINDING_PROXY,
                    ),
                    required_oracle_kinds=(OracleKind.ADMET, OracleKind.DOCKING),
                    notes="Credible hit requires ADMET and binding proxy evidence.",
                ),
            ),
        ),
        ClaimProtocol(
            name="experiment_ready_minimal",
            level=ClaimLevel.EXPERIMENT_READY,
            requirements=(
                EvidenceRequirement(
                    claim_level=ClaimLevel.EXPERIMENT_READY,
                    required_tiers=(
                        EvidenceTier.L2_ADMET_PROXY,
                        EvidenceTier.L3_BINDING_PROXY,
                        EvidenceTier.L5_HUMAN_REVIEW,
                    ),
                    required_oracle_kinds=(OracleKind.ADMET, OracleKind.DOCKING),
                    notes="Experiment-ready requires proxy evidence and human approval.",
                ),
            ),
            requires_human_decision=True,
        ),
    )


def demo_oracles() -> tuple[OracleSpec, ...]:
    return (
        OracleSpec(
            name="basic_chem_filter",
            kind=OracleKind.CHEM_FILTER,
            trust=TrustProfile(
                fidelity="low",
                cost=1,
                latency="instant",
                independence_from_generator="high",
                gameability="low",
                applicability_domain=("small_molecule",),
                failure_modes=("rule_blind_spot",),
            ),
            anchors=(anchor("valence_and_rule_checks"),),
        ),
        OracleSpec(
            name="table_admet",
            kind=OracleKind.ADMET,
            trust=TrustProfile(
                fidelity="medium",
                cost=2,
                latency="seconds",
                independence_from_generator="medium",
                gameability="medium",
                applicability_domain=("small_molecule",),
                failure_modes=("domain_shift", "endpoint_noise"),
            ),
            anchors=(anchor("retrospective_admet_calibration"),),
        ),
        OracleSpec(
            name="table_docking",
            kind=OracleKind.DOCKING,
            trust=TrustProfile(
                fidelity="medium",
                cost=5,
                latency="minutes",
                independence_from_generator="medium",
                gameability="high",
                applicability_domain=("protein_ligand", "non_covalent"),
                failure_modes=("pose_artifact", "scoring_bias", "ligand_strain"),
            ),
            anchors=(anchor("pose_recovery"),),
        ),
        OracleSpec(
            name="table_rescoring",
            kind=OracleKind.RESCORING,
            trust=TrustProfile(
                fidelity="medium_high",
                cost=8,
                latency="minutes",
                independence_from_generator="medium",
                gameability="medium",
                applicability_domain=("protein_ligand", "non_covalent"),
                failure_modes=("scoring_transfer_gap",),
            ),
            anchors=(anchor("rescoring_retrospective_check"),),
        ),
        OracleSpec(
            name="human_review",
            kind=OracleKind.HUMAN_REVIEW,
            trust=TrustProfile(
                fidelity="human",
                cost=10,
                latency="hours",
                independence_from_generator="high",
                gameability="low",
                applicability_domain=("medchem_review",),
                failure_modes=("reviewer_variability",),
            ),
            anchors=(anchor("medchem_review_protocol"),),
        ),
    )


def anchor(name: str) -> OracleAnchorSpec:
    return OracleAnchorSpec(
        name=name,
        description=name,
        anchor_type="fixture",
        validation_basis="table campaign fixture",
    )


def append_contract(store: CampaignStore, kind: EventKind, contract) -> None:
    store.append(new_event(kind, CAMPAIGN_ID, contract_to_payload(contract)))


if __name__ == "__main__":
    main()
