from __future__ import annotations

import csv
import tempfile
from pathlib import Path

from loop_harness import (
    Budget,
    CampaignStore,
    ClaimLevel,
    ClaimProtocol,
    EventKind,
    EvidenceTier,
    EvidenceRequirement,
    LoopState,
    OracleAnchorSpec,
    OracleKind,
    OracleSpec,
    FixedCascadePolicy,
    ReplayRunner,
    ReportBuilder,
    Route,
    TrustAwarePolicy,
    TrustGate,
    TrustProfile,
    contract_to_payload,
    new_event,
)
from loop_harness.adapters import TableCandidateAdapter, TableObservationAdapter


CAMPAIGN_ID = "campaign_demo"


def main() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        candidate_csv = temp_path / "candidates.csv"
        observation_csv = temp_path / "observations.csv"
        write_demo_candidates(candidate_csv)
        write_demo_observations(observation_csv)

        store = build_store(candidate_csv)
        gate = TrustGate()

        for observation in TableObservationAdapter(observation_csv).load_observations():
            state = store.rebuild()
            existing = tuple(
                state.observations[obs_id]
                for obs_id in state.compounds[observation.subject_id].observations
            )
            append_contract(store, EventKind.OBSERVATION_RECORDED, observation)
            result = gate.evaluate(
                observation,
                state.oracles[observation.oracle_name],
                existing_observations=existing,
            )
            append_contract(store, EventKind.TRUST_GATE_RESULT_RECORDED, result)

        final_state = store.rebuild()
        replay_results = ReplayRunner().run(
            final_state,
            {
                "fixed_cascade": FixedCascadePolicy(),
                "trust_aware": TrustAwarePolicy(),
            },
        )
        report = ReportBuilder().build(final_state, replay_results)

        for compound in report.compounds:
            print(f"{compound.compound_id}: {compound.smiles}")
            for result in compound.evidence:
                tier = result.evidence_tier.value if result.evidence_tier else "none"
                claims = ",".join(level.value for level in result.can_support_claims)
                print(
                    f"  {result.oracle_name}: {result.decision.value}"
                    f" tier={tier} claims=[{claims}] notes={result.notes}"
                )
            strongest_label = (
                compound.strongest_claim.value if compound.strongest_claim else "none"
            )
            print(f"  strongest_claim={strongest_label}")
            for evaluation in compound.claims:
                missing_tiers = ",".join(tier.value for tier in evaluation.missing_tiers)
                missing_kinds = ",".join(
                    kind.value for kind in evaluation.missing_oracle_kinds
                )
                print(
                    f"    claim {evaluation.claim_level.value}:"
                    f" satisfied={evaluation.satisfied}"
                    f" missing_tiers=[{missing_tiers}]"
                    f" missing_oracles=[{missing_kinds}]"
                )

        print("policy_replay:")
        for replay in report.policy_replays:
            print(
                f"  {replay.policy_name}: actions={replay.action_count}"
                f" cost={replay.planned_action_cost:g}"
                f" credible_hit_rate={replay.credible_hit_rate:.2f}"
            )
            for action in replay.actions:
                print(
                    f"    {action.kind} {','.join(action.subject_ids)}"
                    f" cost={action.budget_reserved:g} reason={action.reason}"
                )


def build_store(candidate_csv: Path) -> CampaignStore:
    store = CampaignStore()

    campaign = LoopState(
        campaign_id=CAMPAIGN_ID,
        goal={
            "target": "demo_kinase",
            "truth_anchor": "human_review_or_assay",
            "objective": "credible hit under ADMET and binding proxy evidence",
        },
        budget_total=Budget(cheap_calls=100, medium_calls=20, human_reviews=3),
    )
    append_contract(store, EventKind.CAMPAIGN_CREATED, campaign)

    for oracle in demo_oracles():
        append_contract(store, EventKind.ORACLE_REGISTERED, oracle)

    append_contract(
        store,
        EventKind.CLAIM_PROTOCOL_REGISTERED,
        ClaimProtocol(
            name="credible_hit_minimal",
            level=ClaimLevel.CREDIBLE_HIT,
            requirements=(
                EvidenceRequirement(
                    claim_level=ClaimLevel.CREDIBLE_HIT,
                    required_tiers=(),
                    required_oracle_kinds=(OracleKind.ADMET, OracleKind.DOCKING),
                    notes="Proxy-level credible hit requires ADMET and binding evidence.",
                ),
            ),
        ),
    )
    append_contract(
        store,
        EventKind.CLAIM_PROTOCOL_REGISTERED,
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
                    notes="Experiment-ready requires proxy evidence and explicit human review.",
                ),
            ),
            requires_human_decision=True,
        ),
    )

    append_contract(
        store,
        EventKind.ROUTE_OPENED,
        Route(id="route_hinge_binder", hypothesis="preserve hinge binder motif"),
    )

    for compound in TableCandidateAdapter(candidate_csv).load_candidates():
        append_contract(store, EventKind.CANDIDATE_INGESTED, compound)

    return store


def append_contract(store: CampaignStore, kind: EventKind, contract) -> None:
    store.append(new_event(kind, CAMPAIGN_ID, contract_to_payload(contract)))


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
            anchors=(
                OracleAnchorSpec(
                    name="valence_and_rule_checks",
                    description="mechanical chemistry sanity checks",
                    anchor_type="mechanical_rule",
                    validation_basis="deterministic structural rules",
                ),
            ),
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
            anchors=(
                OracleAnchorSpec(
                    name="retrospective_admet_calibration",
                    description="model calibrated on historical ADMET labels",
                    anchor_type="retrospective_validation",
                    validation_basis="held-out endpoint performance",
                    known_gap="proxy ADMET can miss assay-specific liabilities",
                ),
            ),
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
            anchors=(
                OracleAnchorSpec(
                    name="pose_recovery",
                    description="pose validity against known complexes",
                    anchor_type="retrospective_validation",
                    validation_basis="known pose recovery",
                    known_gap="pose recovery is not affinity",
                ),
            ),
        ),
    )


def write_demo_candidates(path: Path) -> None:
    write_csv(
        path,
        ("id", "smiles", "route_id", "scaffold", "tags"),
        (
            ("cmpd_001", "CCOc1ncccc1", "route_hinge_binder", "pyridine", "seed"),
            ("cmpd_002", "CCN(CC)CC", "route_hinge_binder", "amine", "explore"),
        ),
    )


def write_demo_observations(path: Path) -> None:
    write_csv(
        path,
        (
            "id",
            "subject_id",
            "oracle_name",
            "passed",
            "score",
            "pose_valid",
            "steric_clash",
            "ligand_strain_flag",
            "hERG_risk",
            "solubility",
            "uncertainty",
            "domain",
            "source",
        ),
        (
            (
                "obs_chem_001",
                "cmpd_001",
                "basic_chem_filter",
                "true",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "small_molecule",
                "table_campaign",
            ),
            (
                "obs_admet_001",
                "cmpd_001",
                "table_admet",
                "true",
                "",
                "",
                "",
                "",
                "low",
                "ok",
                "0.22",
                "small_molecule",
                "table_campaign",
            ),
            (
                "obs_dock_001",
                "cmpd_001",
                "table_docking",
                "true",
                "-9.1",
                "true",
                "false",
                "false",
                "",
                "",
                "",
                "protein_ligand|non_covalent",
                "table_campaign",
            ),
            (
                "obs_chem_002",
                "cmpd_002",
                "basic_chem_filter",
                "true",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "small_molecule",
                "table_campaign",
            ),
            (
                "obs_admet_002",
                "cmpd_002",
                "table_admet",
                "false",
                "",
                "",
                "",
                "",
                "high",
                "poor",
                "0.18",
                "small_molecule",
                "table_campaign",
            ),
        ),
    )


def write_csv(path: Path, header: tuple[str, ...], rows: tuple[tuple[str, ...], ...]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        writer.writerows(rows)


if __name__ == "__main__":
    main()
