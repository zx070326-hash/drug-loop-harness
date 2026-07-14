import json
import subprocess
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from loop_harness import (
    Budget,
    CampaignStore,
    ClaimPermission,
    Compound,
    ConflictAssessment,
    EvidenceTier,
    EventKind,
    LoopState,
    Observation,
    OracleAnchorSpec,
    OracleKind,
    OracleSpec,
    ProspectiveAcquisitionConsumptionRecorder,
    ProspectiveAdmissionCommitEvidenceSetBuilder,
    ProspectiveLoopEvaluationArmAssignmentVerifier,
    ProspectiveLoopEvaluationOutcomeReportVerifier,
    ProspectivePolicyDecisionReceiptRecorder,
    ProspectivePolicyFidelitySetBuilder,
    PromotionAdvice,
    QualityAssessment,
    Route,
    TrustGateDecision,
    TrustGateResult,
    TrustGate,
    TrustProfile,
    ApplicabilityAssessment,
    ActionKind,
    contract_to_payload,
    new_event,
)
from loop_harness.artifact_hash import canonical_content_sha256
from loop_harness.store import event_to_dict
from tests.test_prospective_evaluation_assignment import (
    VERIFIED_AT,
    control_artifacts,
)
from tests.test_prospective_evaluation_protocol import prospective_power_plan


OUTCOME_VERIFIED_AT = "2026-07-10T06:00:00+00:00"


class ProspectiveLoopEvaluationOutcomeTest(unittest.TestCase):
    def test_actual_registered_policy_receipts_reach_outcome_gate(self) -> None:
        artifacts = outcome_artifacts()
        decision_store = fixed_cascade_experiment_decision_store()
        baseline_receipt = ProspectivePolicyDecisionReceiptRecorder().record(
            artifacts.protocol,
            artifacts.assignment,
            artifacts.assignment_verification,
            decision_store,
            assignment_id="assignment_baseline",
            decided_at="2026-07-10T02:30:00+00:00",
            receipt_id="receipt_baseline_send_to_experiment",
        )
        credibility_receipt = ProspectivePolicyDecisionReceiptRecorder().record(
            artifacts.protocol,
            artifacts.assignment,
            artifacts.assignment_verification,
            decision_store,
            assignment_id="assignment_credibility",
            decided_at="2026-07-10T02:30:00+00:00",
            receipt_id="receipt_credibility_send_to_experiment",
        )

        self.assertEqual(baseline_receipt.selected_decision["to_action"], "send_to_experiment")
        self.assertEqual(credibility_receipt.selected_decision["to_action"], "send_to_experiment")
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            protocol_path = write_json(base / "protocol.json", artifacts.protocol)
            assignment_path = write_json(base / "assignment.json", artifacts.assignment)
            assignment_verification_path = write_json(
                base / "assignment-verification.json", artifacts.assignment_verification
            )
            baseline_receipt_path = write_json(
                base / "baseline-receipt.json", baseline_receipt.to_dict()
            )
            credibility_receipt_path = write_json(
                base / "credibility-receipt.json", credibility_receipt.to_dict()
            )
            decision_store_path = base / "decision-store.json"
            decision_store.dump_json(decision_store_path)
            artifacts.policy_fidelity_set = ProspectivePolicyFidelitySetBuilder().build_paths(
                protocol_path,
                assignment_path,
                assignment_verification_path,
                [baseline_receipt_path, credibility_receipt_path],
                [decision_store_path, decision_store_path],
            ).to_dict()
        refresh_report_hashes(artifacts)

        verification = verify_artifacts(artifacts)

        self.assertEqual(artifacts.policy_fidelity_set["verification_status"], "PASS")
        self.assertEqual(verification.verification_status, "PASS")
        self.assertEqual(
            verification.outcome_report_status,
            "COMPLETE_ADMITTED_OUTCOMES",
        )

    def test_admitted_assay_outcomes_bind_to_all_randomized_assignments(self) -> None:
        artifacts = outcome_artifacts()
        verification = ProspectiveLoopEvaluationOutcomeReportVerifier().verify(
            artifacts.protocol,
            artifacts.plan,
            artifacts.power_plan,
            artifacts.assignment,
            artifacts.assignment_verification,
            artifacts.randomizations,
            artifacts.approvals,
            artifacts.endpoint_policy,
            artifacts.report,
            artifacts.ledger,
            artifacts.store,
            admission_commit_evidence_set=artifacts.commit_evidence_set,
            policy_fidelity_set=artifacts.policy_fidelity_set,
            verified_at=OUTCOME_VERIFIED_AT,
        )

        self.assertEqual(verification.verification_status, "PASS")
        self.assertEqual(
            verification.outcome_report_status,
            "COMPLETE_ADMITTED_OUTCOMES",
        )
        counts = {item.arm_id: item for item in verification.arm_outcomes}
        self.assertEqual(counts["baseline_fixed"].primary_positive_count, 1)
        self.assertEqual(counts["credibility_aware"].primary_negative_count, 1)
        self.assertEqual(counts["baseline_fixed"].declared_experiment_budget_limit, 800.0)
        self.assertEqual(counts["baseline_fixed"].actual_experiment_cost_spent, 5.0)
        self.assertFalse(verification.policy_ranking_permitted)
        self.assertFalse(verification.execution_authority_granted)
        self.assertFalse(verification.autonomous_execution_permitted)
        self.assertFalse(verification.scientific_claim_upgrade_permitted)
        self.assertFalse(verification.predeclared_analysis_computed)

    def test_missing_outcome_is_visible_and_not_reclassified_as_negative(self) -> None:
        artifacts = outcome_artifacts(include_beta_consumption=False)
        artifacts.report["outcomes"][1] = {
            "assignment_id": "assignment_credibility",
            "outcome_status": "missing_outcome",
            "rationale": "No consumable external assay receipt was available.",
        }
        refresh_report_hashes(artifacts)

        verification = ProspectiveLoopEvaluationOutcomeReportVerifier().verify(
            artifacts.protocol,
            artifacts.plan,
            artifacts.power_plan,
            artifacts.assignment,
            artifacts.assignment_verification,
            artifacts.randomizations,
            artifacts.approvals,
            artifacts.endpoint_policy,
            artifacts.report,
            artifacts.ledger,
            artifacts.store,
            admission_commit_evidence_set=artifacts.commit_evidence_set,
            policy_fidelity_set=artifacts.policy_fidelity_set,
            verified_at=OUTCOME_VERIFIED_AT,
        )

        self.assertEqual(verification.verification_status, "PASS")
        self.assertEqual(
            verification.outcome_report_status,
            "COMPLETE_WITH_MISSING_OUTCOMES",
        )
        counts = {item.arm_id: item for item in verification.arm_outcomes}
        self.assertEqual(counts["credibility_aware"].missing_outcome_count, 1)
        self.assertEqual(counts["credibility_aware"].primary_negative_count, 0)

    def test_tampered_primary_metric_fails_closed(self) -> None:
        artifacts = outcome_artifacts()
        artifacts.report["outcomes"][0]["primary_outcome_value"] = False

        verification = verify_artifacts(artifacts)

        self.assertEqual(verification.verification_status, "FAIL")
        self.assertIn("outcome_report_primary_value_mismatch", verification.issue_codes)

    def test_assay_outcome_must_bind_the_frozen_endpoint_policy(self) -> None:
        artifacts = outcome_artifacts()
        observation_event = next(
            event
            for event in artifacts.store.events
            if event.kind is EventKind.OBSERVATION_RECORDED
            and event.payload["data"]["id"] == "obs_cmpd_alpha_assay"
        )
        observation_event.payload["data"]["provenance"].pop(
            "source_endpoint_policy_content_sha256"
        )

        verification = verify_artifacts(artifacts)

        self.assertEqual(verification.verification_status, "FAIL")
        self.assertIn(
            "outcome_report_assay_policy_provenance_mismatch",
            verification.issue_codes,
        )

    def test_assay_outcome_must_use_the_predeclared_assay_site(self) -> None:
        artifacts = outcome_artifacts()
        observation_event = next(
            event
            for event in artifacts.store.events
            if event.kind is EventKind.OBSERVATION_RECORDED
            and event.payload["data"]["id"] == "obs_cmpd_alpha_assay"
        )
        observation_event.payload["data"]["provenance"]["assay_site_id"] = (
            "unplanned_external_lab"
        )

        verification = verify_artifacts(artifacts)

        self.assertEqual(verification.verification_status, "FAIL")
        self.assertIn(
            "outcome_report_assay_site_not_predeclared",
            verification.issue_codes,
        )

    def test_consumed_outcome_requires_one_passing_commit_evidence_package(self) -> None:
        artifacts = outcome_artifacts()
        artifacts.commit_evidence.pop()
        refresh_report_hashes(artifacts)

        verification = verify_artifacts(artifacts)

        self.assertEqual(verification.verification_status, "FAIL")
        self.assertIn("outcome_report_commit_evidence_missing", verification.issue_codes)

    def test_consumed_outcome_rejects_duplicate_commit_evidence_packages(self) -> None:
        artifacts = outcome_artifacts()
        artifacts.commit_evidence.append(dict(artifacts.commit_evidence[0]))
        refresh_report_hashes(artifacts)

        verification = verify_artifacts(artifacts)

        self.assertEqual(verification.verification_status, "FAIL")
        self.assertIn("outcome_report_commit_evidence_not_unique", verification.issue_codes)

    def test_commit_evidence_must_bind_the_frozen_endpoint_policy(self) -> None:
        artifacts = outcome_artifacts()
        artifacts.commit_evidence[0]["source_endpoint_policy_content_sha256"] = "0" * 64
        refresh_report_hashes(artifacts)

        verification = verify_artifacts(artifacts)

        self.assertEqual(verification.verification_status, "FAIL")
        self.assertIn(
            "outcome_report_commit_evidence_source_endpoint_policy_content_sha256_mismatch",
            verification.issue_codes,
        )

    def test_consumed_outcome_requires_one_passing_policy_fidelity_receipt(self) -> None:
        artifacts = outcome_artifacts()
        artifacts.policy_fidelity_set["fidelity"].pop()
        refresh_report_hashes(artifacts)

        verification = verify_artifacts(artifacts)

        self.assertEqual(verification.verification_status, "FAIL")
        self.assertIn("outcome_report_policy_fidelity_missing", verification.issue_codes)

    def test_policy_fidelity_must_bind_the_verified_external_assignment(self) -> None:
        artifacts = outcome_artifacts()
        artifacts.policy_fidelity_set["fidelity"][0][
            "source_assignment_verification_content_sha256"
        ] = "0" * 64
        refresh_report_hashes(artifacts)

        verification = verify_artifacts(artifacts)

        self.assertEqual(verification.verification_status, "FAIL")
        self.assertIn(
            "outcome_report_policy_fidelity_assignment_verification_hash_mismatch",
            verification.issue_codes,
        )

    def test_assignment_verification_must_recompute_from_raw_control_inputs(self) -> None:
        artifacts = outcome_artifacts()
        artifacts.randomizations[0]["selected_subject_ids"] = ["cmpd_unselected"]

        verification = verify_artifacts(artifacts)

        self.assertEqual(verification.verification_status, "FAIL")
        self.assertIn(
            "assignment_verification_recomputation_mismatch",
            verification.issue_codes,
        )

    def test_policy_fidelity_must_bind_the_registered_policy_runtime(self) -> None:
        artifacts = outcome_artifacts()
        artifacts.policy_fidelity_set["fidelity"][0][
            "source_policy_runtime_content_sha256"
        ] = "0" * 64
        refresh_report_hashes(artifacts)

        verification = verify_artifacts(artifacts)

        self.assertEqual(verification.verification_status, "FAIL")
        self.assertIn(
            "outcome_report_policy_fidelity_runtime_hash_mismatch",
            verification.issue_codes,
        )

    def test_every_randomized_assignment_must_be_reported(self) -> None:
        artifacts = outcome_artifacts()
        artifacts.report["outcomes"].pop()

        verification = verify_artifacts(artifacts)

        self.assertEqual(verification.verification_status, "FAIL")
        self.assertIn(
            "outcome_report_randomized_assignments_incomplete",
            verification.issue_codes,
        )

    def test_consumption_cannot_be_attached_to_the_wrong_assigned_subject(self) -> None:
        artifacts = outcome_artifacts()
        artifacts.report["outcomes"][0]["consumption_id"] = "consumption_beta"
        artifacts.report["outcomes"][0]["primary_outcome_value"] = False

        verification = verify_artifacts(artifacts)

        self.assertEqual(verification.verification_status, "FAIL")
        self.assertIn(
            "outcome_report_consumption_approval_id_mismatch",
            verification.issue_codes,
        )

    def test_outcome_timestamp_must_follow_pre_outcome_arm_assignment(self) -> None:
        artifacts = outcome_artifacts()
        artifacts.ledger[0]["consumed_at"] = "2026-07-10T02:00:00+00:00"
        refresh_report_hashes(artifacts)

        verification = verify_artifacts(artifacts)

        self.assertEqual(verification.verification_status, "FAIL")
        self.assertIn(
            "outcome_report_outcome_not_after_assignment",
            verification.issue_codes,
        )

    def test_final_outcome_verification_must_follow_report(self) -> None:
        artifacts = outcome_artifacts()

        verification = verify_artifacts(
            artifacts,
            verified_at="2026-07-10T04:45:00+00:00",
        )

        self.assertEqual(verification.verification_status, "FAIL")
        self.assertIn("outcome_verification_before_report", verification.issue_codes)

    def test_actual_experiment_cost_cannot_exceed_frozen_per_arm_budget(self) -> None:
        artifacts = outcome_artifacts()
        artifacts.ledger[0]["actual_cost_spent"] = 801.0
        refresh_report_hashes(artifacts)

        verification = verify_artifacts(artifacts)

        self.assertEqual(verification.verification_status, "FAIL")
        self.assertIn(
            "outcome_report_arm_experiment_budget_exceeded",
            verification.issue_codes,
        )

    def test_valid_admitted_result_cannot_be_hidden_as_unresolved(self) -> None:
        artifacts = outcome_artifacts()
        artifacts.report["outcomes"][1] = {
            "assignment_id": "assignment_credibility",
            "outcome_status": "invalid_or_unresolved_assay_outcome",
            "consumption_id": artifacts.ledger[1]["consumption_id"],
            "rationale": "Attempted to suppress a valid assay result.",
        }

        verification = verify_artifacts(artifacts)

        self.assertEqual(verification.verification_status, "FAIL")
        self.assertIn(
            "outcome_report_valid_admitted_outcome_marked_unresolved",
            verification.issue_codes,
        )

    def test_cli_writes_read_only_outcome_verification(self) -> None:
        artifacts = outcome_artifacts(
            include_alpha_consumption=False,
            include_beta_consumption=False,
        )
        artifacts.report["outcomes"] = [
            {
                "assignment_id": "assignment_baseline",
                "outcome_status": "missing_outcome",
                "rationale": "No prospective consumption receipt was available.",
            },
            {
                "assignment_id": "assignment_credibility",
                "outcome_status": "missing_outcome",
                "rationale": "No prospective consumption receipt was available.",
            },
        ]
        refresh_report_hashes(artifacts)
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            protocol_path = write_json(base / "protocol.json", artifacts.protocol)
            plan_path = write_json(base / "plan.json", artifacts.plan)
            power_plan_path = write_json(base / "power-plan.json", artifacts.power_plan)
            assignment_path = write_json(base / "assignment.json", artifacts.assignment)
            assignment_verification_path = write_json(
                base / "assignment-verification.json",
                artifacts.assignment_verification,
            )
            randomization_paths = [
                write_json(base / f"randomization-{index}.json", item)
                for index, item in enumerate(artifacts.randomizations)
            ]
            approval_paths = [
                write_json(base / f"approval-{index}.json", item)
                for index, item in enumerate(artifacts.approvals)
            ]
            endpoint_policy_path = write_json(
                base / "assay-endpoint-policy.json",
                artifacts.endpoint_policy,
            )
            report_path = write_json(base / "outcome-report.json", artifacts.report)
            ledger_path = base / "consumption-ledger.jsonl"
            ledger_path.write_text(
                "".join(json.dumps(item, sort_keys=True) + "\n" for item in artifacts.ledger),
                encoding="utf-8",
            )
            campaign_path = base / "campaign.json"
            artifacts.store.dump_json(campaign_path)
            output_path = base / "verification.json"
            command = [
                sys.executable,
                str(SCRIPT),
                str(protocol_path),
                "--prospective-acquisition-plan",
                str(plan_path),
                "--power-plan",
                str(power_plan_path),
                "--assignment",
                str(assignment_path),
                "--assignment-verification",
                str(assignment_verification_path),
                "--assay-endpoint-policy",
                str(endpoint_policy_path),
                "--outcome-report",
                str(report_path),
                "--consumption-ledger",
                str(ledger_path),
                "--campaign-store",
                str(campaign_path),
                    "--verified-at",
                    OUTCOME_VERIFIED_AT,
                "--output",
                str(output_path),
            ]
            for path in randomization_paths:
                command.extend(["--randomization-verification", str(path)])
            for path in approval_paths:
                command.extend(["--approval", str(path)])
            completed = subprocess.run(
                command,
                check=True,
                capture_output=True,
                text=True,
            )
            payload = json.loads(output_path.read_text(encoding="utf-8"))

        self.assertIn("outcome verification: status=PASS", completed.stdout)
        self.assertEqual(payload["verification_status"], "PASS")
        self.assertFalse(payload["predeclared_analysis_computed"])


class OutcomeArtifacts:
    def __init__(
        self,
        protocol: dict,
        plan: dict,
        power_plan: dict,
        assignment: dict,
        assignment_verification: dict,
        randomizations: list[dict],
        approvals: list[dict],
        endpoint_policy: dict,
        report: dict,
        ledger: list[dict],
        store: CampaignStore,
        commit_evidence_set: dict,
        policy_fidelity_set: dict,
    ) -> None:
        self.protocol = protocol
        self.plan = plan
        self.power_plan = power_plan
        self.assignment = assignment
        self.assignment_verification = assignment_verification
        self.randomizations = randomizations
        self.approvals = approvals
        self.endpoint_policy = endpoint_policy
        self.report = report
        self.ledger = ledger
        self.store = store
        self.commit_evidence_set = commit_evidence_set
        self.policy_fidelity_set = policy_fidelity_set

    @property
    def commit_evidence(self) -> list[dict]:
        return self.commit_evidence_set["evidence"]


def outcome_artifacts(
    *,
    include_alpha_consumption: bool = True,
    include_beta_consumption: bool = True,
) -> OutcomeArtifacts:
    protocol, plan, randomizations, approvals, assignment = control_artifacts()
    power_plan = prospective_power_plan()
    assignment_verification = ProspectiveLoopEvaluationArmAssignmentVerifier().verify(
        protocol,
        plan,
        power_plan,
        assignment,
        randomizations,
        approvals,
        verified_at=VERIFIED_AT,
    ).to_dict()
    endpoint_policy = assay_endpoint_policy(protocol)
    store, state, observations, tickets = prospective_assay_campaign(endpoint_policy)
    recorder = ProspectiveAcquisitionConsumptionRecorder()
    records = []
    if include_alpha_consumption:
        records.append(
            recorder.build_record(
                plan,
                randomizations[0],
                approvals[0],
                state,
                observation_id=observations["cmpd_alpha"].id,
                trust_gate_result_id=tickets["cmpd_alpha"].id,
                consumed_at="2026-07-10T03:00:00+00:00",
                consumption_id="consumption_alpha",
            ).to_dict()
        )
    if include_beta_consumption:
        records.append(
            recorder.build_record(
                plan,
                randomizations[1],
                approvals[1],
                state,
                observation_id=observations["cmpd_beta"].id,
                trust_gate_result_id=tickets["cmpd_beta"].id,
                consumed_at="2026-07-10T04:00:00+00:00",
                consumption_id="consumption_beta",
            ).to_dict()
        )
    commit_evidence = committed_evidence(records, observations, tickets, endpoint_policy)
    commit_evidence_set = evidence_set(commit_evidence)
    policy_fidelity_set = fidelity_set(
        protocol,
        assignment,
        assignment_verification,
        records,
    )
    report = make_report(
        protocol,
        plan,
        power_plan,
        assignment,
        assignment_verification,
        endpoint_policy,
        store,
        records,
        commit_evidence_set,
        policy_fidelity_set,
    )
    return OutcomeArtifacts(
        protocol,
        plan,
        power_plan,
        assignment,
        assignment_verification,
        randomizations,
        approvals,
        endpoint_policy,
        report,
        records,
        store,
        commit_evidence_set,
        policy_fidelity_set,
    )


def assay_endpoint_policy(protocol: dict) -> dict:
    return {
        "schema_version": "prospective_assay_endpoint_policy.v1",
        "policy_id": "kinase_assay_endpoint_policy_v1",
        "source_protocol_content_sha256": canonical_content_sha256(protocol),
        "oracle_name": "prospective_assay_oracle",
        "assay_protocol_id": "kinase_ic50_assay_v1",
        "raw_metric_key": "ic50_nm",
        "output_metric_key": "assay_validated",
        "unit": "nM",
        "comparator": "at_most",
        "threshold": 100.0,
        "required_control_ids": ["positive_control", "vehicle_control"],
        "human_acceptance": {
            "decision": "accept",
            "reviewer": "assay-reviewer@example.org",
            "rationale": "Endpoint and threshold are frozen before outcome review.",
        },
        "policy_ranking_permitted": False,
        "execution_authority_granted": False,
        "autonomous_execution_permitted": False,
        "scientific_claim_upgrade_permitted": False,
    }


def fixed_cascade_experiment_decision_store() -> CampaignStore:
    campaign_id = "fixed_cascade_experiment_decision"
    state = LoopState(
        campaign_id=campaign_id,
        goal={"target": "kinase"},
        budget_total=Budget(),
    )
    route = Route(id="kinase_route", hypothesis="fixed cascade decision state")
    alpha = Compound(id="cmpd_alpha", smiles="CCO", route_id=route.id)
    beta = Compound(id="cmpd_beta", smiles="CCN", route_id=route.id)
    store = CampaignStore()
    campaign = LoopState(
        campaign_id=state.campaign_id,
        goal=state.goal,
        budget_total=state.budget_total,
    )
    store.append(_timed_event(EventKind.CAMPAIGN_CREATED, campaign_id, campaign))
    store.append(_timed_event(EventKind.ROUTE_OPENED, campaign_id, route))
    store.append(_timed_event(EventKind.CANDIDATE_INGESTED, campaign_id, alpha))
    store.append(_timed_event(EventKind.CANDIDATE_INGESTED, campaign_id, beta))
    for index, tier in enumerate(
        (
            EvidenceTier.L0_CHEM_VALID,
            EvidenceTier.L2_ADMET_PROXY,
            EvidenceTier.L3_BINDING_PROXY,
        )
    ):
        store.append(
            _timed_event(
                EventKind.TRUST_GATE_RESULT_RECORDED,
                campaign_id,
                TrustGateResult(
                    id=f"alpha_ready_{tier.value}",
                    subject_id=alpha.id,
                    observation_id=f"alpha_observation_{index}",
                    oracle_name=f"oracle_{tier.value}",
                    decision=TrustGateDecision.QUALIFIED,
                    evidence_tier=tier,
                    claim_permission=ClaimPermission(),
                    applicability=ApplicabilityAssessment(in_domain=True),
                    quality=QualityAssessment(passed=True),
                    conflict=ConflictAssessment(status="none"),
                    promotion=PromotionAdvice(recommended=False),
                ),
            )
        )
    store.append(
        _timed_event(
            EventKind.TRUST_GATE_RESULT_RECORDED,
            campaign_id,
            TrustGateResult(
                id="beta_ready_for_experiment",
                subject_id=beta.id,
                observation_id="beta_docking_observation",
                oracle_name="docking_proxy",
                decision=TrustGateDecision.NEEDS_PROMOTION,
                evidence_tier=EvidenceTier.L3_BINDING_PROXY,
                claim_permission=ClaimPermission(),
                applicability=ApplicabilityAssessment(in_domain=True),
                quality=QualityAssessment(passed=True),
                conflict=ConflictAssessment(status="none"),
                promotion=PromotionAdvice(
                    recommended=True,
                    next_action=ActionKind.SEND_TO_EXPERIMENT,
                    reason="qualified_docking_requires_experiment",
                    expected_cost=5.0,
                ),
            ),
        )
    )
    return store


def _timed_event(kind: EventKind, campaign_id: str, value) -> object:
    return replace(
        new_event(kind, campaign_id, contract_to_payload(value)),
        created_at="2026-07-10T02:00:00+00:00",
    )


def prospective_assay_campaign(endpoint_policy: dict):
    state = LoopState(
        campaign_id="prospective_loop_outcome_test",
        goal={"target": "kinase"},
        budget_total=Budget(experiment_slots=2, compute_units=10.0),
    )
    state.add_route(Route(id="kinase_route", hypothesis="prospective kinase route"))
    oracle = OracleSpec(
        name="prospective_assay_oracle",
        kind=OracleKind.ASSAY,
        trust=TrustProfile(
            fidelity="experimental",
            cost=5.0,
            latency="days",
            independence_from_generator="high",
            gameability="low",
            applicability_domain=("small_molecule",),
        ),
        anchors=(
            OracleAnchorSpec(
                name="assay_protocol_anchor",
                description="Frozen prospective assay protocol.",
                anchor_type="experimental_protocol",
                validation_basis="Qualified external laboratory assay.",
            ),
        ),
    )
    state.add_oracle(oracle)
    observations: dict[str, Observation] = {}
    tickets = {}
    for subject_id, assay_validated in (("cmpd_alpha", True), ("cmpd_beta", False)):
        state.add_compound(
            Compound(id=subject_id, smiles="CCO", route_id="kinase_route")
        )
        observation = Observation(
            id=f"obs_{subject_id}_assay",
            subject_id=subject_id,
            oracle_name=oracle.name,
            metrics={"assay_validated": assay_validated, "passed": True},
            cost_spent=5.0,
            provenance={
                "domain": ("small_molecule",),
                "source": "prospective_assay_result_import",
                "source_endpoint_policy_content_sha256": canonical_content_sha256(
                    endpoint_policy
                ),
                "source_result_sha256": f"result_{subject_id}_sha256",
                "source_output_sha256": f"output_{subject_id}_sha256",
                "assay_run_id": f"kinase_run_{subject_id}",
                "assay_site_id": "qualified_external_lab_A",
                "assay_protocol_id": endpoint_policy["assay_protocol_id"],
                "qc_status": "pass",
            },
        )
        state.add_observation(observation)
        ticket = TrustGate().evaluate(observation, oracle)
        state.add_trust_gate_result(ticket)
        observations[subject_id] = observation
        tickets[subject_id] = ticket

    store = CampaignStore()
    campaign = LoopState(
        campaign_id=state.campaign_id,
        goal=state.goal,
        budget_total=state.budget_total,
    )
    store.append(
        new_event(EventKind.CAMPAIGN_CREATED, state.campaign_id, contract_to_payload(campaign))
    )
    store.append(
        new_event(
            EventKind.ROUTE_OPENED,
            state.campaign_id,
            contract_to_payload(state.routes["kinase_route"]),
        )
    )
    store.append(
        new_event(EventKind.ORACLE_REGISTERED, state.campaign_id, contract_to_payload(oracle))
    )
    for subject_id in ("cmpd_alpha", "cmpd_beta"):
        store.append(
            new_event(
                EventKind.CANDIDATE_INGESTED,
                state.campaign_id,
                contract_to_payload(state.compounds[subject_id]),
            )
        )
        store.append(
            new_event(
                EventKind.OBSERVATION_RECORDED,
                state.campaign_id,
                contract_to_payload(observations[subject_id]),
            )
        )
        store.append(
            new_event(
                EventKind.TRUST_GATE_RESULT_RECORDED,
                state.campaign_id,
                contract_to_payload(tickets[subject_id]),
            )
        )
    return store, state, observations, tickets


def committed_evidence(
    records: list[dict],
    observations: dict[str, Observation],
    tickets: dict,
    endpoint_policy: dict,
) -> list[dict]:
    evidence = []
    for record in records:
        subject_id = record["subject_id"]
        observation = observations[subject_id]
        evidence.append(
            {
                "schema_version": "prospective_admission_commit_evidence.v1",
                "verification_status": "PASS",
                "commit_directory": f"synthetic_commit_{subject_id}",
                "commit_id": f"commit_{subject_id}",
                "admission_id": f"admission_{subject_id}",
                "consumption_id": record["consumption_id"],
                "observation_id": observation.id,
                "trust_gate_result_id": tickets[subject_id].id,
                "source_commit_intent_content_sha256": f"intent_{subject_id}",
                "source_bundle_content_sha256": f"bundle_{subject_id}",
                "source_bundle_audit_content_sha256": f"bundle_audit_{subject_id}",
                "source_import_content_sha256": f"import_{subject_id}",
                "source_result_sha256": observation.provenance["source_result_sha256"],
                "source_output_sha256": observation.provenance["source_output_sha256"],
                "source_endpoint_policy_content_sha256": canonical_content_sha256(
                    endpoint_policy
                ),
                "source_consumption_content_sha256": canonical_content_sha256(record),
                "source_derived_campaign_content_sha256": f"campaign_{subject_id}",
                "issue_codes": [],
                "issues": [],
                "execution_authority_granted": False,
                "autonomous_execution_permitted": False,
                "scientific_claim_upgrade_permitted": False,
                "summary_line": "synthetic test evidence",
            }
        )
    return evidence


def evidence_set(evidence: list[dict]) -> dict:
    if not evidence:
        return ProspectiveAdmissionCommitEvidenceSetBuilder().build_paths([]).to_dict()
    return {
        "schema_version": "prospective_admission_commit_evidence_set.v1",
        "verification_status": "PASS",
        "evidence": evidence,
        "source_evidence_content_sha256": canonical_content_sha256(evidence),
        "issue_codes": [],
        "execution_authority_granted": False,
        "autonomous_execution_permitted": False,
        "scientific_claim_upgrade_permitted": False,
        "summary_line": f"synthetic evidence set: evidence={len(evidence)}",
    }


def fidelity_set(
    protocol: dict,
    assignment: dict,
    assignment_verification: dict,
    records: list[dict],
) -> dict:
    if not records:
        return ProspectivePolicyFidelitySetBuilder().build_paths(
            "missing-protocol.json",
            "missing-assignment.json",
            "missing-assignment-verification.json",
            [],
            [],
        ).to_dict()
    assignment_by_subject = {
        item["subject_id"]: item for item in assignment["assignments"]
    }
    runtime_hash_by_arm = {
        item["arm_id"]: item["policy_runtime_content_sha256"]
        for item in protocol["arms"]
    }
    fidelity = []
    for record in records:
        item = assignment_by_subject[record["subject_id"]]
        fidelity.append(
            {
                "schema_version": "prospective_policy_fidelity.v1",
                "verification_status": "PASS",
                "receipt_id": f"synthetic_policy_receipt_{item['assignment_id']}",
                "assignment_id": item["assignment_id"],
                "arm_id": item["arm_id"],
                "subject_id": item["subject_id"],
                "policy_kind": "synthetic_test_policy",
                "source_protocol_content_sha256": canonical_content_sha256(protocol),
                "source_assignment_content_sha256": canonical_content_sha256(assignment),
                "source_assignment_verification_content_sha256": canonical_content_sha256(
                    assignment_verification
                ),
                "source_policy_runtime_content_sha256": runtime_hash_by_arm[item["arm_id"]],
                "source_decision_campaign_content_sha256": f"decision_campaign_{item['assignment_id']}",
                "source_policy_output_semantic_sha256": f"policy_output_{item['assignment_id']}",
                "selected_decision": {
                    "subject_id": item["subject_id"],
                    "to_action": "send_to_experiment",
                },
                "issue_codes": [],
                "issues": [],
                "policy_ranking_permitted": False,
                "execution_authority_granted": False,
                "autonomous_execution_permitted": False,
                "scientific_claim_upgrade_permitted": False,
                "summary_line": "synthetic policy fidelity",
            }
        )
    return {
        "schema_version": "prospective_policy_fidelity_set.v1",
        "verification_status": "PASS",
        "fidelity": fidelity,
        "source_fidelity_content_sha256": canonical_content_sha256(fidelity),
        "issue_codes": [],
        "policy_ranking_permitted": False,
        "execution_authority_granted": False,
        "autonomous_execution_permitted": False,
        "scientific_claim_upgrade_permitted": False,
        "summary_line": f"synthetic fidelity set: fidelity={len(fidelity)}",
    }


def make_report(
    protocol: dict,
    plan: dict,
    power_plan: dict,
    assignment: dict,
    assignment_verification: dict,
    endpoint_policy: dict,
    store: CampaignStore,
    ledger: list[dict],
    commit_evidence_set: dict,
    policy_fidelity_set: dict,
) -> dict:
    return {
        "schema_version": "prospective_loop_evaluation_outcome_report.v1",
        "report_id": "prospective_outcome_report_001",
        "reported_at": "2026-07-10T05:00:00+00:00",
        "source_protocol_content_sha256": canonical_content_sha256(protocol),
        "source_prospective_acquisition_plan_content_sha256": canonical_content_sha256(plan),
        "source_power_plan_content_sha256": canonical_content_sha256(power_plan),
        "source_assignment_content_sha256": canonical_content_sha256(assignment),
        "source_assignment_verification_content_sha256": canonical_content_sha256(
            assignment_verification
        ),
        "source_assay_endpoint_policy_content_sha256": canonical_content_sha256(
            endpoint_policy
        ),
        "source_admission_commit_evidence_content_sha256": canonical_content_sha256(
            commit_evidence_set["evidence"]
        ),
        "source_policy_fidelity_content_sha256": policy_fidelity_set[
            "source_fidelity_content_sha256"
        ],
        "source_campaign_content_sha256": campaign_hash(store),
        "source_consumption_ledger_content_sha256": canonical_content_sha256(ledger),
        "outcomes": [
            {
                "assignment_id": "assignment_baseline",
                "outcome_status": "admitted_assay_outcome",
                "consumption_id": "consumption_alpha",
                "primary_outcome_value": True,
            },
            {
                "assignment_id": "assignment_credibility",
                "outcome_status": "admitted_assay_outcome",
                "consumption_id": "consumption_beta",
                "primary_outcome_value": False,
            },
        ],
        "human_acceptance": {
            "decision": "accept",
            "reviewer": "outcome-reviewer@example.org",
            "reviewed_at": "2026-07-10T04:30:00+00:00",
            "rationale": "Reviewed all randomized subjects and bound receipts.",
        },
        "policy_ranking_permitted": False,
        "execution_authority_granted": False,
        "autonomous_execution_permitted": False,
        "scientific_claim_upgrade_permitted": False,
    }


def refresh_report_hashes(artifacts: OutcomeArtifacts) -> None:
    artifacts.report["source_campaign_content_sha256"] = campaign_hash(artifacts.store)
    artifacts.report["source_consumption_ledger_content_sha256"] = canonical_content_sha256(
        artifacts.ledger
    )
    artifacts.commit_evidence_set["source_evidence_content_sha256"] = canonical_content_sha256(
        artifacts.commit_evidence
    )
    artifacts.policy_fidelity_set["source_fidelity_content_sha256"] = canonical_content_sha256(
        artifacts.policy_fidelity_set["fidelity"]
    )
    artifacts.report["source_admission_commit_evidence_content_sha256"] = artifacts.commit_evidence_set[
        "source_evidence_content_sha256"
    ]
    artifacts.report["source_policy_fidelity_content_sha256"] = artifacts.policy_fidelity_set[
        "source_fidelity_content_sha256"
    ]


def verify_artifacts(
    artifacts: OutcomeArtifacts,
    *,
    verified_at: str = OUTCOME_VERIFIED_AT,
):
    return ProspectiveLoopEvaluationOutcomeReportVerifier().verify(
        artifacts.protocol,
        artifacts.plan,
        artifacts.power_plan,
        artifacts.assignment,
        artifacts.assignment_verification,
        artifacts.randomizations,
        artifacts.approvals,
        artifacts.endpoint_policy,
        artifacts.report,
        artifacts.ledger,
        artifacts.store,
        admission_commit_evidence_set=artifacts.commit_evidence_set,
        policy_fidelity_set=artifacts.policy_fidelity_set,
        verified_at=verified_at,
    )


def campaign_hash(store: CampaignStore) -> str:
    return canonical_content_sha256([event_to_dict(event) for event in store.events])


def write_json(path: Path, payload: dict) -> Path:
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "verify_prospective_loop_evaluation_outcome_report.py"


if __name__ == "__main__":
    unittest.main()
