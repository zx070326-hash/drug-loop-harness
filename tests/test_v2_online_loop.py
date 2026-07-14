from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest

from loop_harness.artifact_hash import canonical_content_sha256
from loop_harness.v2.jensen_mfbo_fixture import load_jensen_high_fidelity_truth
from loop_harness.v2 import (
    ActionSpace,
    ArtifactIntegrityError,
    AsyncJobController,
    CacheOracleExecutor,
    CampaignLedger,
    CampaignSpec,
    CandidateSpec,
    ClaimDisposition,
    ClaimGate,
    ClaimProtocol,
    ContentAddressedArtifactStore,
    PosteriorCalibrationEvaluator,
    CacheLiveOracleAdapter,
    ContinuousPosteriorFitter,
    EndpointSpec,
    EventKind,
    GreedyEndpointBeliefPolicy,
    HiddenOracleRecord,
    JensenMFBOFixtureLoader,
    JensenCalibrationRunner,
    JensenCalibrationSpec,
    JensenBenchmarkRunner,
    JensenBenchmarkSpec,
    JensenPolicyComparisonRunner,
    JensenStyleTVRPolicy,
    JensenStressBenchmarkRunner,
    JensenStressBenchmarkSpec,
    JensenTruthEvaluator,
    JobControlError,
    JsonCommandLiveAdapter,
    JsonCommandProfile,
    LoopRuntime,
    MorganLogisticEnsembleUpdater,
    MultitargetOnlineFixtureLoader,
    OracleResultStatus,
    OracleSpec,
    ExperimentProtocolSpec,
    PosteriorSnapshot,
    PosteriorRepository,
    PolicyProposalBatch,
    ReferenceAcquisitionPolicy,
    ReferenceConcordanceEvaluator,
    ReferencePolicyRunner,
    record_posterior_fit,
    SubprocessReferencePolicy,
    StateProjector,
    StressCredibilityEvaluator,
    StressOracleExecutor,
    StressScenarioSpec,
    TraceEvaluator,
    ValueOfInformationPolicy,
    apply_common_warm_start,
    register_stress_scenario,
)


PROVENANCE = {
    "adapter": "test_cache",
    "adapter_version": "1",
    "input_hash": "input",
    "output_hash": "output",
}


class V2OnlineLoopTest(unittest.TestCase):
    def test_json_command_live_adapter_normalizes_success_and_bad_output(self) -> None:
        success_script = (
            "import json,sys; p=json.load(open(sys.argv[1])); "
            "print(json.dumps({'candidate_id':p['candidate_id'],"
            "'oracle_id':p['oracle_id'],'metrics':{'passed':True}}))"
        )
        invalid_script = "print('not-json')"
        for script, expected_status in ((success_script, OracleResultStatus.SUCCESS), (invalid_script, OracleResultStatus.FAILED)):
            ledger = _ledger()
            action = ActionSpace().enumerate(
                StateProjector().project(ledger),
                policy_version="command-test",
            )[0]
            with tempfile.TemporaryDirectory() as directory:
                store = ContentAddressedArtifactStore(directory)
                adapter = JsonCommandLiveAdapter(
                    JsonCommandProfile(
                        adapter_id="json_command_test",
                        adapter_version="v1",
                        command=(sys.executable, "-c", script, "{input_path}"),
                        accounted_cost=action.expected_cost,
                    ),
                    store,
                )
                controller = AsyncJobController(adapter)
                job = controller.prepare(ledger, action)
                controller.dispatch(ledger, job.job_id)
                controller.poll(ledger, job.job_id)
            state = StateProjector().project(ledger)
            result = next(iter(state.results.values()))
            self.assertIs(result.status, expected_status)
            self.assertEqual(TraceEvaluator().evaluate(ledger).status, "PASS")

    def test_json_command_profile_binds_target_and_calibration_artifacts(self) -> None:
        script = (
            "import json,sys; p=json.load(open(sys.argv[1])); "
            "print(json.dumps({'candidate_id':p['candidate_id'],'oracle_id':p['oracle_id'],"
            "'metrics':{'passed':True}}))"
        )
        ledger = _ledger()
        action = ActionSpace().enumerate(
            StateProjector().project(ledger),
            policy_version="bound-command-test",
        )[0]
        with tempfile.TemporaryDirectory() as directory:
            store = ContentAddressedArtifactStore(directory)
            profile_ref = store.put_json({"profile_id": "dock-v1", "target_id": "TARGET"})
            calibration_ref = store.put_json({"calibration_id": "dock-cal-v1", "status": "qualified"})
            adapter = JsonCommandLiveAdapter(
                JsonCommandProfile(
                    adapter_id="bound_json_command_test",
                    adapter_version="v1",
                    command=(sys.executable, "-c", script, "{input_path}"),
                    accounted_cost=action.expected_cost,
                    target_id="TARGET",
                    endpoint_id=action.endpoint_id,
                    profile_artifact=profile_ref,
                    calibration_artifact=calibration_ref,
                    require_calibration=True,
                ),
                store,
            )
            controller = AsyncJobController(adapter)
            job = controller.prepare(ledger, action)
            controller.dispatch(ledger, job.job_id)
            controller.poll(ledger, job.job_id)
            result = next(iter(StateProjector().project(ledger).results.values()))
            self.assertEqual(result.provenance["profile_artifact_sha256"], profile_ref.sha256)
            self.assertEqual(result.provenance["calibration_artifact_sha256"], calibration_ref.sha256)

            wrong_target = JsonCommandLiveAdapter(
                JsonCommandProfile(
                    adapter_id="wrong_target_command_test",
                    adapter_version="v1",
                    command=(sys.executable, "-c", script, "{input_path}"),
                    accounted_cost=action.expected_cost,
                    target_id="OTHER_TARGET",
                ),
                store,
            )
            with self.assertRaisesRegex(JobControlError, "target_mismatch"):
                wrong_target.prepare(action, StateProjector().project(ledger))

    def test_json_command_adapter_recovers_receipt_after_process_restart(self) -> None:
        script = (
            "import json,sys; p=json.load(open(sys.argv[1])); "
            "print(json.dumps({'candidate_id':p['candidate_id'],'oracle_id':p['oracle_id'],"
            "'metrics':{'passed':True}}))"
        )
        ledger = _ledger()
        action = ActionSpace().enumerate(
            StateProjector().project(ledger),
            policy_version="restart-command-test",
        )[0]
        with tempfile.TemporaryDirectory() as directory:
            store = ContentAddressedArtifactStore(directory)
            profile = JsonCommandProfile(
                adapter_id="restart_json_command_test",
                adapter_version="v1",
                command=(sys.executable, "-c", script, "{input_path}"),
                accounted_cost=action.expected_cost,
            )
            first_adapter = JsonCommandLiveAdapter(profile, store)
            first_controller = AsyncJobController(first_adapter)
            job = first_controller.prepare(ledger, action)
            # Simulate a crash after the external process completed but before
            # the controller appended its dispatch event.
            first_adapter.dispatch(job)

            restarted_controller = AsyncJobController(
                JsonCommandLiveAdapter(profile, store)
            )
            restarted_controller.dispatch(ledger, job.job_id)
            receipt = restarted_controller.poll(ledger, job.job_id)

        self.assertEqual(receipt.status.value, "succeeded")
        state = StateProjector().project(ledger)
        self.assertEqual(next(iter(state.results.values())).status, OracleResultStatus.SUCCESS)
        self.assertEqual(TraceEvaluator().evaluate(ledger).status, "PASS")

    def test_stress_benchmark_keeps_realization_seed_out_of_public_report(self) -> None:
        workspace = Path(__file__).resolve().parents[1]
        result = JensenStressBenchmarkRunner().run(
            workspace=str(workspace),
            spec=JensenStressBenchmarkSpec(
                base=JensenBenchmarkSpec(
                    target_ids=("FACTOR_D",),
                    candidate_limit=4,
                    candidate_offsets=(0,),
                    warm_start_seeds=(17,),
                    total_budget=3.0,
                    policy_names=("heuristic", "jensen_tvr"),
                ),
                scenarios=(
                    StressScenarioSpec(
                        scenario_id="test-stress",
                        scenario_version="v1",
                        realization_seed=12345,
                        failure_rate=0.1,
                    ),
                ),
            ),
        )
        self.assertEqual(len(result.arms), 2)
        self.assertEqual(len(result.summaries), 2)
        self.assertTrue(all(arm.run.fixed_budget_comparison_permitted for arm in result.arms))
        self.assertNotIn('"realization_seed":', json.dumps(result.to_dict()))

    def test_reference_concordance_reports_action_and_rank_mismatch(self) -> None:
        result = ReferenceConcordanceEvaluator().compare(
            (("a", "b", "c"), ("b", "a")),
            (("a", "b", "c"), ("a", "b")),
        )
        self.assertEqual(result.status, "FAIL")
        self.assertEqual(result.exact_action_steps, 1)
        self.assertIn("action_sequence_mismatch", result.issue_codes)
        self.assertEqual(result.rank_comparisons, 4)

    def test_async_job_controller_recovers_and_settles_cache_job(self) -> None:
        workspace = Path(__file__).resolve().parents[1]
        fixture = JensenMFBOFixtureLoader().load(
            workspace=workspace,
            target_id="FACTOR_D",
            candidate_limit=4,
            total_budget=5.0,
            max_rounds=4,
        )
        action = ActionSpace().enumerate(
            StateProjector().project(fixture.ledger),
            policy_version="async-test",
        )[0]
        with tempfile.TemporaryDirectory() as directory:
            store = ContentAddressedArtifactStore(directory)
            adapter = CacheLiveOracleAdapter(fixture.executor, store)
            controller = AsyncJobController(adapter)
            job = controller.prepare(fixture.ledger, action)
            self.assertEqual(controller.recoverable_job_ids(fixture.ledger), (job.job_id,))
            controller.dispatch(fixture.ledger, job.job_id)
            receipt = controller.poll(fixture.ledger, job.job_id)
            state = StateProjector().project(fixture.ledger)

        self.assertEqual(receipt.status.value, "succeeded")
        self.assertFalse(state.pending_action_ids)
        self.assertEqual(controller.recoverable_job_ids(fixture.ledger), ())
        self.assertIn(job.job_id, state.job_receipts)
        self.assertEqual(TraceEvaluator().evaluate(fixture.ledger).status, "PASS")
        with self.assertRaisesRegex(JobControlError, "already_terminated"):
            controller.dispatch(fixture.ledger, job.job_id)

    def test_async_job_controller_updates_verified_posterior_after_settlement(self) -> None:
        workspace = Path(__file__).resolve().parents[1]
        fixture = JensenMFBOFixtureLoader().load(
            workspace=workspace,
            target_id="FACTOR_D",
            candidate_limit=4,
            total_budget=5.0,
            max_rounds=4,
        )
        action = ActionSpace().enumerate(
            StateProjector().project(fixture.ledger),
            policy_version="async-posterior-test",
        )[0]
        with tempfile.TemporaryDirectory() as directory:
            store = ContentAddressedArtifactStore(directory)
            controller = AsyncJobController(
                CacheLiveOracleAdapter(fixture.executor, store),
                posterior_fitter=ContinuousPosteriorFitter(),
                posterior_store=store,
                posterior_endpoint_ids=("normalized_potency",),
            )
            job = controller.prepare(fixture.ledger, action)
            controller.dispatch(fixture.ledger, job.job_id)
            controller.poll(fixture.ledger, job.job_id)
            state = StateProjector().project(fixture.ledger)
            snapshot_id = state.active_posterior_snapshot_ids["normalized_potency"]
            posterior = state.posterior_snapshots[snapshot_id]

            self.assertEqual(posterior.training_observation_ids, (action.action_id,))
            self.assertEqual(len(PosteriorRepository(store).values(posterior)), 12)
            self.assertEqual(TraceEvaluator().evaluate(fixture.ledger).status, "PASS")

    def test_stress_failure_is_deterministic_and_does_not_update_belief(self) -> None:
        ledger = _ledger()
        scenario = StressScenarioSpec(
            scenario_id="failure-stress",
            scenario_version="v1",
            realization_seed=99,
            failure_rate=1.0,
        )
        register_stress_scenario(ledger, scenario)
        runtime = LoopRuntime(
            StressOracleExecutor(
                CacheOracleExecutor(
                    {("a", "cheap"): _record("a", "cheap", passed=True, label=1, cost=1.0)}
                ),
                scenario,
            )
        )
        result = runtime.step(ledger)
        state = StateProjector().project(ledger)

        observation = next(iter(state.results.values()))
        self.assertIs(observation.status, OracleResultStatus.FAILED)
        self.assertEqual(state.beliefs["a"].mean, 0.5)
        self.assertEqual(state.reliability_for("cheap", "hidden_binary_activity").valid_measurements, 0)
        self.assertNotIn("realization_seed", ledger.to_json())
        self.assertEqual(TraceEvaluator().evaluate(ledger).status, "PASS")
        self.assertEqual(StressCredibilityEvaluator().evaluate(ledger).status, "PASS")

    def test_stress_continuous_value_is_reproducible_for_paired_fixture(self) -> None:
        workspace = Path(__file__).resolve().parents[1]
        fixture_a = JensenMFBOFixtureLoader().load(
            workspace=workspace,
            target_id="FACTOR_D",
            candidate_limit=4,
            total_budget=5.0,
            max_rounds=4,
        )
        fixture_b = JensenMFBOFixtureLoader().load(
            workspace=workspace,
            target_id="FACTOR_D",
            candidate_limit=4,
            total_budget=5.0,
            max_rounds=4,
        )
        action_a = ActionSpace().enumerate(
            StateProjector().project(fixture_a.ledger),
            policy_version="stress-test",
        )[0]
        action_b = ActionSpace().enumerate(
            StateProjector().project(fixture_b.ledger),
            policy_version="stress-test",
        )[0]
        scenario = StressScenarioSpec(
            scenario_id="noise-stress",
            scenario_version="v1",
            realization_seed=123,
            noise_scale=0.2,
            scaffold_bias_scale=0.1,
            drift_per_attempt=0.03,
        )
        result_a = StressOracleExecutor(fixture_a.executor, scenario).execute(action_a)
        result_b = StressOracleExecutor(fixture_b.executor, scenario).execute(action_b)
        self.assertEqual(result_a.outcome.value, result_b.outcome.value)
        self.assertEqual(result_a.metrics["value"], result_b.metrics["value"])
        self.assertEqual(result_a.provenance["stress_manifest_sha256"], scenario.public_manifest_sha256)

    def test_continuous_posterior_is_artifact_backed_and_qualified_only(self) -> None:
        workspace = Path(__file__).resolve().parents[1]
        fixture = JensenMFBOFixtureLoader().load(
            workspace=workspace,
            target_id="FACTOR_D",
            candidate_limit=4,
            total_budget=5.0,
            max_rounds=8,
        )
        with tempfile.TemporaryDirectory() as directory:
            store = ContentAddressedArtifactStore(directory)
            fitter = ContinuousPosteriorFitter()
            prior = fitter.fit(
                StateProjector().project(fixture.ledger),
                store,
                endpoint_id="normalized_potency",
            )
            self.assertEqual(len(PosteriorRepository(store).values(prior)), 12)
            record_posterior_fit(fixture.ledger, prior)

            LoopRuntime(
                fixture.executor,
                posterior_fitter=fitter,
                posterior_store=store,
                posterior_endpoint_ids=("normalized_potency",),
            ).step(fixture.ledger)
            state = StateProjector().project(fixture.ledger)
            posterior = state.posterior_snapshots[
                state.active_posterior_snapshot_ids["normalized_potency"]
            ]
            self.assertEqual(len(posterior.training_observation_ids), 1)
            values = PosteriorRepository(store).values(posterior)
            self.assertEqual(len(values), 12)
            _, _, truth = load_jensen_high_fidelity_truth(
                workspace=workspace,
                target_id="FACTOR_D",
                candidate_limit=4,
            )
            calibration = PosteriorCalibrationEvaluator(PosteriorRepository(store)).evaluate(
                posterior,
                high_fidelity_oracle_id="dose_response",
                truth=truth,
            )
            self.assertEqual(calibration.candidate_count, 4)
            self.assertIsNotNone(calibration.negative_log_predictive_density)
            projected = StateProjector().project(fixture.ledger)
            self.assertEqual(
                projected.active_posterior_snapshot_ids["normalized_potency"],
                posterior.snapshot_id,
            )
            self.assertEqual(TraceEvaluator().evaluate(fixture.ledger).status, "PASS")

    def test_tvr_policy_can_consume_verified_posterior_snapshot(self) -> None:
        workspace = Path(__file__).resolve().parents[1]
        fixture = JensenMFBOFixtureLoader().load(
            workspace=workspace,
            target_id="FACTOR_D",
            candidate_limit=4,
            total_budget=5.0,
            max_rounds=4,
        )
        with tempfile.TemporaryDirectory() as directory:
            store = ContentAddressedArtifactStore(directory)
            repository = PosteriorRepository(store)
            policy = JensenStyleTVRPolicy(posterior_repository=repository)
            runtime = LoopRuntime(
                fixture.executor,
                policy=policy,
                posterior_fitter=ContinuousPosteriorFitter(),
                posterior_store=store,
                posterior_endpoint_ids=("normalized_potency",),
            )
            self.assertEqual(runtime.step(fixture.ledger).status, "completed")
            state = StateProjector().project(fixture.ledger)
            actions = ActionSpace().enumerate(
                state,
                policy_version=policy.policy_version,
            )
            scores = policy.rank(state, actions)
            self.assertTrue(scores)
            self.assertTrue(scores[0].rationale.startswith("verified_posterior_tvr_per_cost"))

    def test_jensen_calibration_runner_emits_snapshot_curve_without_claim_upgrade(self) -> None:
        workspace = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            result = JensenCalibrationRunner().run(
                workspace=str(workspace),
                artifact_root=directory,
                spec=JensenCalibrationSpec(
                    target_ids=("FACTOR_D",),
                    candidate_limit=4,
                    warm_start_seeds=(17,),
                    continuation_steps=1,
                    total_budget=5.0,
                ),
            )
        self.assertEqual(len(result.rows), 2)
        self.assertEqual([row.snapshot_label for row in result.rows], ["warm_start", "continuation_1"])
        self.assertEqual(result.rows[0].metrics.candidate_count, 3)
        self.assertEqual(len(result.curve), 2)
        self.assertIn("No molecular efficacy", result.analysis_boundary)
        self.assertNotIn("realization_seed", json.dumps(result.to_dict()))

    def test_content_addressed_artifact_store_verifies_bytes_and_posterior_event(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = ContentAddressedArtifactStore(directory, producer_version="test_store_v1")
            reference = store.put_json({"mean": 0.7, "variance": 0.1})
            self.assertEqual(store.read_json(reference)["mean"], 0.7)

            ledger = _ledger()
            ledger.append(EventKind.RAW_ARTIFACT_RECORDED, {"artifact": reference})
            posterior = PosteriorSnapshot(
                snapshot_id="posterior-1",
                endpoint_id="hidden_binary_activity",
                model_spec_id="test_model_v1",
                training_observation_ids=(),
                training_set_sha256=reference.sha256,
                candidate_set_sha256=reference.sha256,
                model_artifact=reference,
                prediction_artifact=reference,
                diagnostics_artifact=reference,
                fit_status="success",
            )
            ledger.append(EventKind.POSTERIOR_FIT_RECORDED, {"snapshot": posterior})
            snapshot = StateProjector().project(ledger)
            self.assertEqual(snapshot.active_posterior_snapshot_ids["hidden_binary_activity"], "posterior-1")

            path = Path(directory) / reference.relative_path
            path.write_bytes(b"tampered")
            with self.assertRaises(ArtifactIntegrityError):
                store.read_bytes(reference)

    def test_frozen_experiment_protocol_round_trips_in_campaign_state(self) -> None:
        ledger = _ledger()
        protocol = ExperimentProtocolSpec(
            protocol_id="reference-protocol",
            protocol_version="v1",
            target_ids=("TARGET",),
            candidate_cohort_ids=("cohort-1",),
            policy_ids=("baseline", "reference"),
            randomization_seed=11,
            total_budget=10.0,
            max_rounds=4,
            primary_metrics=("simple_regret",),
            secondary_metrics=("top_recovery",),
        )
        ledger.append(EventKind.EXPERIMENT_PROTOCOL_REGISTERED, protocol)

        snapshot = StateProjector().project(ledger)
        self.assertEqual(snapshot.protocol, protocol)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "protocol-ledger.json"
            ledger.dump(path)
            restored = CampaignLedger.load("online-test", path)
        self.assertEqual(StateProjector().project(restored), snapshot)

    def test_reference_policy_proposal_is_hash_bound_and_records_only_legal_actions(self) -> None:
        ledger = _ledger()
        seen = {}

        class StubReferencePolicy:
            policy_id = "reference_stub"
            policy_version = "reference_stub_v1"

            def recommend(self, payload):
                seen["payload"] = payload
                action_id = payload.request.legal_action_ids[0]
                return PolicyProposalBatch(
                    proposal_id="proposal-1",
                    policy_id=self.policy_id,
                    policy_version=self.policy_version,
                    request_sha256=canonical_content_sha256(payload.request),
                    proposed_action_ids=(action_id,),
                )

        result = ReferencePolicyRunner(StubReferencePolicy()).propose(
            ledger,
            deterministic_seed=17,
            record_valid_proposal=True,
        )

        self.assertTrue(result.validation.accepted)
        self.assertEqual(result.validation.accepted_action_ids, result.proposal.proposed_action_ids)
        self.assertEqual(len(seen["payload"].observations), 0)
        self.assertEqual(
            set(result.proposal.proposed_action_ids),
            set(result.request.legal_action_ids).intersection(result.proposal.proposed_action_ids),
        )
        snapshot = StateProjector().project(ledger)
        self.assertIn("proposal-1", snapshot.policy_proposals)

    def test_reference_policy_unknown_action_is_rejected_without_ledger_mutation(self) -> None:
        ledger = _ledger()
        before = ledger.content_sha256()

        class InvalidReferencePolicy:
            policy_id = "invalid_reference"
            policy_version = "invalid_reference_v1"

            def recommend(self, payload):
                return PolicyProposalBatch(
                    proposal_id="proposal-invalid",
                    policy_id=self.policy_id,
                    policy_version=self.policy_version,
                    request_sha256=canonical_content_sha256(payload.request),
                    proposed_action_ids=("act_not_legal",),
                )

        result = ReferencePolicyRunner(InvalidReferencePolicy()).propose(
            ledger,
            deterministic_seed=17,
            record_valid_proposal=True,
        )

        self.assertFalse(result.validation.accepted)
        self.assertIn("proposal_contains_unknown_action", result.validation.issue_codes)
        self.assertEqual(ledger.content_sha256(), before)
        self.assertFalse(StateProjector().project(ledger).policy_proposals)

    def test_subprocess_reference_policy_uses_canonical_json_boundary(self) -> None:
        script = (
            "import hashlib,json,sys; "
            "p=json.load(sys.stdin); "
            "raw=json.dumps(p['request'],ensure_ascii=True,sort_keys=True,separators=(',',':')); "
            "h=hashlib.sha256(raw.encode('utf-8')).hexdigest(); "
            "print(json.dumps({'schema_version':'reference_policy_output.v1',"
            "'policy_id':'subprocess_stub','policy_version':'subprocess_stub_v1',"
            "'proposal_id':'subprocess-proposal','request_sha256':h,"
            "'proposed_action_ids':[p['request']['legal_action_ids'][0]]}))"
        )
        adapter = SubprocessReferencePolicy(
            (sys.executable, "-c", script),
            policy_id="subprocess_stub",
            policy_version="subprocess_stub_v1",
        )
        result = ReferencePolicyRunner(adapter).propose(
            _ledger(),
            deterministic_seed=23,
        )

        self.assertTrue(result.validation.accepted)
        self.assertEqual(result.proposal.proposal_id, "subprocess-proposal")

    def test_isolated_jensen_worker_is_deterministic_on_factor_d_warm_start(self) -> None:
        workspace = Path(__file__).resolve().parents[1]
        reference_python = (
            workspace
            / "external"
            / "jensen-mfbo-2025"
            / "reference_env"
            / ".venv"
            / "Scripts"
            / "python.exe"
        )
        if not reference_python.is_file():
            self.skipTest("isolated Jensen reference environment is not installed")
        fixture = JensenMFBOFixtureLoader().load(
            workspace=workspace,
            target_id="FACTOR_D",
            candidate_limit=8,
            total_budget=5.0,
            max_rounds=8,
        )
        apply_common_warm_start(fixture, seed_per_oracle=1, seed=17)
        adapter = SubprocessReferencePolicy(
            (
                str(reference_python),
                str(workspace / "scripts" / "run_jensen_baybe_reference_worker.py"),
            ),
            policy_id="jensen_baybe_reference",
            policy_version="jensen_baybe_reference_v1",
            cwd=workspace,
            timeout_seconds=120.0,
        )
        runner = ReferencePolicyRunner(adapter)
        before = fixture.ledger.content_sha256()
        first = runner.propose(fixture.ledger, deterministic_seed=17)
        second = runner.propose(fixture.ledger, deterministic_seed=17)

        self.assertTrue(first.validation.accepted)
        self.assertTrue(second.validation.accepted)
        self.assertEqual(first.proposal.proposed_action_ids, second.proposal.proposed_action_ids)
        self.assertEqual(first.request.state_sha256, second.request.state_sha256)
        self.assertEqual(fixture.ledger.content_sha256(), before)

        runtime = LoopRuntime(
            fixture.executor,
            policy=ReferenceAcquisitionPolicy(
                adapter,
                fixture.ledger,
                deterministic_seed=17,
            ),
        )
        step = runtime.step(fixture.ledger)
        self.assertEqual(step.status, "completed")
        self.assertTrue(StateProjector().project(fixture.ledger).policy_proposals)
        self.assertEqual(TraceEvaluator().evaluate(fixture.ledger).status, "PASS")

    def test_hidden_truth_is_revealed_only_after_admitted_action(self) -> None:
        ledger = _ledger()
        executor = CacheOracleExecutor(
            {
                ("a", "cheap"): _record("a", "cheap", passed=True, label=1, cost=1.0),
                ("b", "cheap"): _record("b", "cheap", passed=True, label=0, cost=1.0),
            }
        )

        before = ledger.to_json()
        self.assertNotIn('"label"', before)
        result = LoopRuntime(executor).step(ledger)

        self.assertEqual(result.status, "completed")
        self.assertEqual(len(result.action_ids), 1)
        self.assertIn('"label": 1', ledger.to_json())
        self.assertNotIn('"label": 0', ledger.to_json())
        self.assertEqual(result.snapshot.budget_spent, 1.0)
        self.assertEqual(result.snapshot.budget_reserved, 0.0)
        self.assertFalse(result.snapshot.pending_action_ids)

    def test_first_observation_changes_the_second_acquisition(self) -> None:
        positive = _ledger()
        negative = _ledger()
        positive_runtime = LoopRuntime(
            CacheOracleExecutor(
                {
                    ("a", "cheap"): _record("a", "cheap", passed=True, label=1, cost=1.0),
                    ("a", "expensive"): _record("a", "expensive", passed=True, label=1, cost=2.0),
                    ("b", "cheap"): _record("b", "cheap", passed=True, label=0, cost=1.0),
                }
            ),
            policy=GreedyEndpointBeliefPolicy(),
        )
        negative_runtime = LoopRuntime(
            CacheOracleExecutor(
                {
                    ("a", "cheap"): _record("a", "cheap", passed=False, label=0, cost=1.0),
                    ("a", "expensive"): _record("a", "expensive", passed=True, label=1, cost=2.0),
                    ("b", "cheap"): _record("b", "cheap", passed=True, label=0, cost=1.0),
                }
            ),
            policy=GreedyEndpointBeliefPolicy(),
        )

        positive_runtime.step(positive)
        negative_runtime.step(negative)
        positive_second = positive_runtime.step(positive)
        negative_second = negative_runtime.step(negative)

        positive_action = positive_second.snapshot.actions[positive_second.action_ids[0]]
        negative_action = negative_second.snapshot.actions[negative_second.action_ids[0]]
        self.assertEqual((positive_action.candidate_id, positive_action.oracle_id), ("a", "expensive"))
        self.assertEqual((negative_action.candidate_id, negative_action.oracle_id), ("b", "cheap"))

    def test_ledger_round_trip_rebuild_is_deterministic(self) -> None:
        ledger = _ledger()
        LoopRuntime(
            CacheOracleExecutor(
                {("a", "cheap"): _record("a", "cheap", passed=True, label=1, cost=1.0)}
            )
        ).step(ledger)
        projector = StateProjector()
        expected = projector.project(ledger)

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ledger.json"
            ledger.dump(path)
            restored = CampaignLedger.load("online-test", path)

        self.assertEqual(restored.content_sha256(), ledger.content_sha256())
        self.assertEqual(projector.project(restored), expected)

    def test_executor_exception_becomes_failed_result(self) -> None:
        class BrokenExecutor:
            def execute(self, _action):
                raise RuntimeError("provider unavailable")

        ledger = _ledger()
        result = LoopRuntime(BrokenExecutor()).step(ledger)

        self.assertEqual(result.status, "completed")
        oracle_result = next(iter(result.snapshot.results.values()))
        self.assertIs(oracle_result.status, OracleResultStatus.FAILED)
        self.assertIn("executor_exception:RuntimeError", oracle_result.failure_reason or "")
        self.assertEqual(result.snapshot.budget_spent, 0.0)
        self.assertFalse(result.snapshot.pending_action_ids)

    def test_acquired_labels_refit_beliefs_for_unobserved_candidates(self) -> None:
        ledger = CampaignLedger("surrogate-test")
        ledger.append(
            EventKind.CAMPAIGN_CREATED,
            CampaignSpec(
                campaign_id="surrogate-test",
                target_id="TARGET",
                scientific_goal="test online target-local surrogate",
                primary_endpoint="hidden_binary_activity",
                total_budget=10.0,
                max_rounds=8,
                allowed_oracle_ids=("assay",),
            ),
        )
        molecules = {
            "a": ("CCO", 1),
            "b": ("c1ccccc1", 0),
            "c": ("CCCO", 1),
            "d": ("c1ccncc1", 0),
            "e": ("CCCCO", 1),
            "f": ("c1ccoc1", 0),
        }
        records = {}
        for candidate_id, (smiles, label) in molecules.items():
            ledger.append(
                EventKind.CANDIDATE_REGISTERED,
                CandidateSpec(candidate_id, smiles),
            )
            records[(candidate_id, "assay")] = HiddenOracleRecord(
                candidate_id=candidate_id,
                oracle_id="assay",
                metrics={"label": label},
                provenance=PROVENANCE,
                actual_cost=1.0,
            )
        ledger.append(
            EventKind.ORACLE_REGISTERED,
            OracleSpec(
                oracle_id="assay",
                evidence_tier="l6_experimental",
                expected_cost=1.0,
                anchor_id="assay-anchor-v1",
            ),
        )
        runtime = LoopRuntime(
            CacheOracleExecutor(records),
            belief_updater=MorganLogisticEnsembleUpdater(
                training_oracle_ids=("assay",),
                min_training_size=4,
                ensemble_size=16,
            ),
        )

        for _ in range(4):
            result = runtime.step(ledger)
            self.assertEqual(result.status, "completed")

        snapshot = StateProjector().project(ledger)
        unobserved = set(molecules) - {candidate_id for candidate_id, _ in snapshot.results}
        self.assertTrue(unobserved)
        for candidate_id in unobserved:
            belief = snapshot.beliefs[candidate_id]
            self.assertEqual(
                belief.model_version,
                "morgan_logistic_bootstrap_v1_uncalibrated",
            )
            self.assertEqual(belief.evidence_count, 4)
        self.assertEqual(
            unobserved,
            set(molecules) - {candidate_id for candidate_id, _ in snapshot.results},
        )

    def test_route_failure_model_redirects_the_next_acquisition(self) -> None:
        ledger = CampaignLedger("failure-test")
        ledger.append(
            EventKind.CAMPAIGN_CREATED,
            CampaignSpec(
                campaign_id="failure-test",
                target_id="TARGET",
                scientific_goal="learn route-level execution failure",
                primary_endpoint="proxy",
                total_budget=5.0,
                max_rounds=4,
                allowed_oracle_ids=("cheap",),
            ),
        )
        ledger.append(
            EventKind.CANDIDATE_REGISTERED,
            CandidateSpec("a", "CCO", route_id="route-1"),
        )
        ledger.append(
            EventKind.CANDIDATE_REGISTERED,
            CandidateSpec("b", "CCN", route_id="route-1"),
        )
        ledger.append(
            EventKind.CANDIDATE_REGISTERED,
            CandidateSpec("c", "CCC", route_id="route-2"),
        )
        ledger.append(
            EventKind.ORACLE_REGISTERED,
            OracleSpec(
                oracle_id="cheap",
                evidence_tier="l1_proxy",
                expected_cost=1.0,
                anchor_id="cheap-anchor-v1",
            ),
        )
        records = {
            ("a", "cheap"): HiddenOracleRecord(
                candidate_id="a",
                oracle_id="cheap",
                metrics={},
                provenance=PROVENANCE,
                actual_cost=0.0,
                status=OracleResultStatus.FAILED,
                failure_reason="embedding_failed",
            ),
            ("b", "cheap"): _record("b", "cheap", passed=True, label=1, cost=1.0),
            ("c", "cheap"): _record("c", "cheap", passed=True, label=1, cost=1.0),
        }
        runtime = LoopRuntime(CacheOracleExecutor(records))

        first = runtime.step(ledger)
        second = runtime.step(ledger)

        first_action = first.snapshot.actions[first.action_ids[0]]
        second_action = second.snapshot.actions[second.action_ids[0]]
        self.assertEqual(first_action.candidate_id, "a")
        self.assertEqual(second_action.candidate_id, "c")
        failure_state = second.snapshot.failure_models[("route-1", "cheap")]
        self.assertEqual((failure_state.attempts, failure_state.failures), (1, 1))
        self.assertAlmostEqual(failure_state.posterior_failure_probability, 2 / 3)

    def test_claim_gate_cannot_upgrade_missing_evidence(self) -> None:
        ledger = _ledger()
        result = LoopRuntime(
            CacheOracleExecutor(
                {("a", "cheap"): _record("a", "cheap", passed=True, label=1, cost=1.0)}
            )
        ).step(ledger)
        gate = ClaimGate()

        denied = gate.evaluate(
            "a",
            ClaimProtocol("validated_hit", ("l6_experimental",)),
            result.snapshot,
        )
        needs_human = gate.evaluate(
            "a",
            ClaimProtocol("experiment_ready", ("l1_proxy",), human_required=True),
            result.snapshot,
        )

        self.assertIs(denied.disposition, ClaimDisposition.DENIED)
        self.assertEqual(denied.missing_tiers, ("l6_experimental",))
        self.assertIs(needs_human.disposition, ClaimDisposition.NEEDS_HUMAN)

    def test_trace_evaluator_is_read_only_and_rejects_pending_action(self) -> None:
        complete = _ledger()
        LoopRuntime(
            CacheOracleExecutor(
                {("a", "cheap"): _record("a", "cheap", passed=True, label=1, cost=1.0)}
            )
        ).step(complete)
        event_count = len(complete.events)
        report = TraceEvaluator().evaluate(complete)

        self.assertEqual(report.status, "PASS")
        self.assertEqual(len(complete.events), event_count)
        self.assertFalse(report.policy_ranking_permitted)
        self.assertFalse(report.scientific_claim_upgrade_permitted)

        incomplete = _ledger()
        snapshot = StateProjector().project(incomplete)
        policy = ValueOfInformationPolicy()
        action = ActionSpace().enumerate(
            snapshot,
            policy_version=policy.policy_version,
        )[0]
        incomplete.append(EventKind.ACTION_PROPOSED, action)
        incomplete.append(
            EventKind.ACTION_ADMITTED,
            {"action_id": action.action_id, "reason": "test_pending_action"},
        )
        failed = TraceEvaluator().evaluate(incomplete)

        self.assertEqual(failed.status, "FAIL")
        self.assertIn("admitted_action_missing_result", failed.issue_codes)
        self.assertIn("pending_actions_remain", failed.issue_codes)

    def test_exact_action_approval_can_resume_without_duplicate_proposal(self) -> None:
        ledger = CampaignLedger("approval-test")
        ledger.append(
            EventKind.CAMPAIGN_CREATED,
            CampaignSpec(
                campaign_id="approval-test",
                target_id="TARGET",
                scientific_goal="approval-gated assay",
                primary_endpoint="assay_activity",
                total_budget=2.0,
                max_rounds=2,
                allowed_oracle_ids=("assay",),
            ),
        )
        ledger.append(EventKind.CANDIDATE_REGISTERED, CandidateSpec("a", "CCO"))
        ledger.append(
            EventKind.ORACLE_REGISTERED,
            OracleSpec(
                oracle_id="assay",
                evidence_tier="l6_experimental",
                expected_cost=1.0,
                anchor_id="assay-anchor-v1",
                approval_required=True,
            ),
        )
        runtime = LoopRuntime(
            CacheOracleExecutor(
                {("a", "assay"): _record("a", "assay", passed=True, label=1, cost=1.0)}
            )
        )

        paused = runtime.step(ledger)
        self.assertEqual(paused.status, "approval_required")
        action_id = next(iter(paused.snapshot.actions))
        resumed = runtime.step(ledger, approved_action_ids=frozenset({action_id}))

        self.assertEqual(resumed.status, "completed")
        self.assertEqual(resumed.action_ids, (action_id,))
        proposed = [
            event for event in ledger.events if event.kind is EventKind.ACTION_PROPOSED
        ]
        self.assertEqual(len(proposed), 1)

    def test_budget_gate_stops_when_every_action_is_unaffordable(self) -> None:
        ledger = CampaignLedger("budget-test")
        ledger.append(
            EventKind.CAMPAIGN_CREATED,
            CampaignSpec(
                campaign_id="budget-test",
                target_id="TARGET",
                scientific_goal="hard budget enforcement",
                primary_endpoint="proxy",
                total_budget=1.0,
                max_rounds=2,
                allowed_oracle_ids=("expensive",),
            ),
        )
        ledger.append(EventKind.CANDIDATE_REGISTERED, CandidateSpec("a", "CCO"))
        ledger.append(
            EventKind.ORACLE_REGISTERED,
            OracleSpec(
                oracle_id="expensive",
                evidence_tier="l4_proxy",
                expected_cost=2.0,
                anchor_id="expensive-anchor-v1",
            ),
        )

        result = LoopRuntime(CacheOracleExecutor({})).step(ledger)

        self.assertEqual(result.status, "stopped")
        self.assertEqual(result.stop_reason, "all_proposed_actions_rejected")
        self.assertEqual(result.snapshot.budget_spent, 0.0)
        self.assertEqual(result.snapshot.round_index, 0)

    def test_negative_result_is_valid_evidence_and_updates_belief(self) -> None:
        ledger = _ledger()
        result = LoopRuntime(
            CacheOracleExecutor(
                {("a", "cheap"): _record("a", "cheap", passed=False, label=0, cost=1.0)}
            )
        ).step(ledger)

        assessment = result.snapshot.assessments[("a", "cheap")]
        observation = result.snapshot.results[("a", "cheap")]
        self.assertEqual(assessment.disposition.value, "qualified")
        self.assertEqual(observation.outcome.direction.value, "unfavorable")
        self.assertEqual(result.snapshot.belief_for("a", "hidden_binary_activity").mean, 0.0)

    def test_invalid_measurement_updates_reliability_but_not_belief(self) -> None:
        ledger = _ledger()
        result = LoopRuntime(
            CacheOracleExecutor(
                {
                    ("a", "cheap"): HiddenOracleRecord(
                        candidate_id="a",
                        oracle_id="cheap",
                        metrics={"label": 1},
                        provenance={},
                        actual_cost=1.0,
                    )
                }
            )
        ).step(ledger)

        assessment = result.snapshot.assessments[("a", "cheap")]
        reliability = result.snapshot.reliability_for("cheap", "hidden_binary_activity")
        belief = result.snapshot.belief_for("a", "hidden_binary_activity")
        self.assertEqual(assessment.disposition.value, "abstain")
        self.assertEqual((reliability.attempts, reliability.valid_measurements), (1, 0))
        self.assertAlmostEqual(reliability.posterior_validity_probability, 1 / 3)
        self.assertEqual(belief.evidence_count, 0)
        self.assertEqual(TraceEvaluator().evaluate(ledger).status, "PASS")

    def test_execution_prerequisite_remains_hard_but_promotion_is_soft(self) -> None:
        ledger = _ledger()
        state = StateProjector().project(ledger)
        actions = ActionSpace().enumerate(state, policy_version="test")
        self.assertIn(("a", "expensive"), {(a.candidate_id, a.oracle_id) for a in actions})

        constrained = CampaignLedger("execution-requirement-test")
        constrained.append(
            EventKind.CAMPAIGN_CREATED,
            CampaignSpec(
                campaign_id="execution-requirement-test",
                target_id="TARGET",
                scientific_goal="test hard execution constraint",
                primary_endpoint="activity",
                total_budget=4.0,
                max_rounds=2,
                allowed_oracle_ids=("synthesis", "assay"),
            ),
        )
        constrained.append(EventKind.CANDIDATE_REGISTERED, CandidateSpec("a", "CCO"))
        constrained.append(
            EventKind.ORACLE_REGISTERED,
            OracleSpec("synthesis", "synthesis", 1.0, anchor_id="synthesis-v1"),
        )
        constrained.append(
            EventKind.ORACLE_REGISTERED,
            OracleSpec(
                "assay",
                "l6_experimental",
                2.0,
                execution_prerequisites=("synthesis",),
                anchor_id="assay-v1",
            ),
        )
        actions = ActionSpace().enumerate(
            StateProjector().project(constrained),
            policy_version="test",
        )
        self.assertEqual({action.oracle_id for action in actions}, {"synthesis"})

    def test_endpoint_beliefs_are_isolated(self) -> None:
        ledger = CampaignLedger("endpoint-test")
        ledger.append(
            EventKind.CAMPAIGN_CREATED,
            CampaignSpec(
                campaign_id="endpoint-test",
                target_id="TARGET",
                scientific_goal="separate efficacy and safety state",
                primary_endpoint="efficacy",
                endpoints=(EndpointSpec("efficacy"), EndpointSpec("safety", direction="minimize")),
                total_budget=2.0,
                max_rounds=2,
                allowed_oracle_ids=("safety_oracle",),
            ),
        )
        ledger.append(EventKind.CANDIDATE_REGISTERED, CandidateSpec("a", "CCO"))
        ledger.append(
            EventKind.ORACLE_REGISTERED,
            OracleSpec(
                "safety_oracle",
                "l2_safety",
                1.0,
                endpoint_id="safety",
                anchor_id="safety-v1",
            ),
        )
        result = LoopRuntime(
            CacheOracleExecutor(
                {("a", "safety_oracle"): _record("a", "safety_oracle", passed=False, label=0, cost=1.0)}
            )
        ).step(ledger)
        self.assertEqual(result.snapshot.belief_for("a", "safety").mean, 0.0)
        self.assertEqual(result.snapshot.belief_for("a", "efficacy").mean, 0.5)

    def test_hash_bound_fixture_keeps_truth_hidden_without_l4_hard_gate(self) -> None:
        workspace = Path(__file__).resolve().parents[1]
        fixture = MultitargetOnlineFixtureLoader().load(
            workspace=workspace,
            target_id="ALDH1",
            selection_seed="hou_tocodecoy_aldh1_replication_v1",
            candidate_limit=8,
            total_budget=120.0,
            max_rounds=60,
        )
        self.assertNotIn('"label"', fixture.ledger.to_json())
        initial_actions = ActionSpace().enumerate(
            StateProjector().project(fixture.ledger),
            policy_version="test",
        )
        self.assertIn(
            "heldout_label",
            {action.oracle_id for action in initial_actions},
        )

        runtime = LoopRuntime(
            fixture.executor,
            belief_updater=MorganLogisticEnsembleUpdater(
                training_oracle_ids=("heldout_label",),
            ),
        )
        for _ in range(60):
            result = runtime.step(fixture.ledger)
            if result.status != "completed":
                break

        snapshot = StateProjector().project(fixture.ledger)
        negative_l4_candidate = "tocodecoy:ALDH1:257470"
        l4 = snapshot.assessments[(negative_l4_candidate, "l4")]
        self.assertEqual(l4.disposition.value, "qualified")
        self.assertEqual(
            snapshot.results[(negative_l4_candidate, "l4")].outcome.direction.value,
            "unfavorable",
        )
        self.assertIn((negative_l4_candidate, "heldout_label"), snapshot.results)
        self.assertEqual(
            sum(oracle_id == "heldout_label" for _, oracle_id in snapshot.results),
            8,
        )
        self.assertEqual(TraceEvaluator().evaluate(fixture.ledger).status, "PASS")

    def test_jensen_mfbo_fixture_is_hash_bound_and_online(self) -> None:
        workspace = Path(__file__).resolve().parents[1]
        fixture = JensenMFBOFixtureLoader().load(
            workspace=workspace,
            target_id="FACTOR_D",
            candidate_limit=4,
            total_budget=5.0,
            max_rounds=4,
        )
        self.assertEqual(fixture.source_record, "10.5281/zenodo.13983042")
        self.assertEqual(
            fixture.source_csv_sha256,
            "2c183e605f6c9726028552c6fc183390c360742748cbed6442b1c37ca8622e03",
        )
        self.assertNotIn('"value"', fixture.ledger.to_json())
        initial = ActionSpace().enumerate(
            StateProjector().project(fixture.ledger),
            policy_version="test",
        )
        self.assertEqual(len(initial), 12)
        self.assertEqual(
            {action.oracle_id for action in initial},
            {"docking", "single_point", "dose_response"},
        )

        result = LoopRuntime(fixture.executor).step(fixture.ledger)
        observation = next(iter(result.snapshot.results.values()))
        self.assertIsNotNone(observation.outcome)
        self.assertEqual(observation.outcome.direction.value, "continuous")
        self.assertEqual(TraceEvaluator().evaluate(fixture.ledger).status, "PASS")

    def test_jensen_style_tvr_policy_warm_starts_then_selects_tvr_action(self) -> None:
        workspace = Path(__file__).resolve().parents[1]
        fixture = JensenMFBOFixtureLoader().load(
            workspace=workspace,
            target_id="FACTOR_D",
            candidate_limit=8,
            total_budget=5.0,
            max_rounds=8,
        )
        runtime = LoopRuntime(fixture.executor, policy=JensenStyleTVRPolicy())
        for _ in range(4):
            result = runtime.step(fixture.ledger)
            self.assertEqual(result.status, "completed")

        snapshot = StateProjector().project(fixture.ledger)
        self.assertEqual(
            {oracle_id for _, oracle_id in snapshot.results},
            {"docking", "single_point", "dose_response"},
        )
        fourth_action = snapshot.actions[result.action_ids[0]]
        self.assertIn("tvr", runtime.policy.policy_version)
        self.assertEqual(TraceEvaluator().evaluate(fixture.ledger).status, "PASS")

    def test_jensen_comparison_uses_common_start_and_read_only_truth_evaluation(self) -> None:
        workspace = Path(__file__).resolve().parents[1]
        fixture = JensenMFBOFixtureLoader().load(
            workspace=workspace,
            target_id="FACTOR_D",
            candidate_limit=12,
            total_budget=5.0,
            max_rounds=8,
        )
        warm_pairs = apply_common_warm_start(fixture, seed_per_oracle=1, seed=17)
        before_hash = fixture.ledger.content_sha256()
        metrics = JensenTruthEvaluator().evaluate(
            fixture.ledger,
            workspace=str(workspace),
            target_id="FACTOR_D",
            candidate_limit=12,
            top_fraction=0.25,
        )
        self.assertEqual(fixture.ledger.content_sha256(), before_hash)
        self.assertEqual(metrics.high_fidelity_action_count, 1)
        self.assertFalse(metrics.scientific_claim_upgrade_permitted)

        runs = JensenPolicyComparisonRunner().run(
            workspace=str(workspace),
            target_id="FACTOR_D",
            candidate_limit=12,
            total_budget=5.0,
            continuation_steps=None,
            seed_per_oracle=1,
            seed=17,
            top_fraction=0.25,
        )
        self.assertEqual(len(runs), 3)
        self.assertTrue(all(run.warm_start_action_pairs == warm_pairs for run in runs))
        self.assertTrue(all(run.trace_status == "PASS" for run in runs))
        self.assertTrue(all(run.fixed_budget_comparison_permitted for run in runs))
        self.assertTrue(
            all(
                run.terminal_reason
                in {
                    "budget_exhausted",
                    "all_proposed_actions_rejected",
                    "no_available_actions",
                }
                for run in runs
            )
        )

    def test_jensen_common_warm_start_fails_before_partial_execution_when_unfunded(self) -> None:
        workspace = Path(__file__).resolve().parents[1]
        fixture = JensenMFBOFixtureLoader().load(
            workspace=workspace,
            target_id="FACTOR_D",
            candidate_limit=8,
            total_budget=1.0,
            max_rounds=8,
        )
        with self.assertRaisesRegex(ValueError, "cannot fund the common warm start"):
            apply_common_warm_start(fixture, seed_per_oracle=1, seed=17)
        self.assertEqual(len(fixture.ledger.events), 12)

    def test_jensen_benchmark_aggregates_seed_averaged_paired_cohorts(self) -> None:
        workspace = Path(__file__).resolve().parents[1]
        result = JensenBenchmarkRunner().run(
            workspace=str(workspace),
            spec=JensenBenchmarkSpec(
                target_ids=("FACTOR_D",),
                candidate_limit=8,
                candidate_offsets=(0, 8),
                warm_start_seeds=(17, 37),
                total_budget=3.0,
                policy_names=("heuristic", "jensen_tvr"),
                baseline_policy="heuristic",
            ),
        )
        self.assertEqual(len(result.arms), 8)
        self.assertEqual(len(result.paired_cohorts), 2)
        self.assertEqual(len(result.pairwise_summaries), 1)
        self.assertEqual(result.pairwise_summaries[0].paired_cohort_count, 2)
        self.assertTrue(
            all(arm.run.fixed_budget_comparison_permitted for arm in result.arms)
        )
        self.assertFalse(result.inferential_conclusion_permitted)
        self.assertFalse(result.scientific_claim_upgrade_permitted)


def _ledger() -> CampaignLedger:
    ledger = CampaignLedger("online-test")
    ledger.append(
        EventKind.CAMPAIGN_CREATED,
        CampaignSpec(
            campaign_id="online-test",
            target_id="TARGET",
            scientific_goal="test state-dependent acquisition",
            primary_endpoint="hidden_binary_activity",
            total_budget=10.0,
            max_rounds=4,
            allowed_oracle_ids=("cheap", "expensive"),
        ),
    )
    ledger.append(EventKind.CANDIDATE_REGISTERED, CandidateSpec("a", "CCO"))
    ledger.append(EventKind.CANDIDATE_REGISTERED, CandidateSpec("b", "CCN"))
    ledger.append(
        EventKind.ORACLE_REGISTERED,
        OracleSpec(
            oracle_id="cheap",
            evidence_tier="l1_proxy",
            expected_cost=1.0,
            anchor_id="cheap-anchor-v1",
        ),
    )
    ledger.append(
        EventKind.ORACLE_REGISTERED,
        OracleSpec(
            oracle_id="expensive",
            evidence_tier="l6_experimental",
            expected_cost=2.0,
            promotion_predecessors=("cheap",),
            anchor_id="expensive-anchor-v1",
        ),
    )
    return ledger


def _record(
    candidate_id: str,
    oracle_id: str,
    *,
    passed: bool,
    label: int,
    cost: float,
) -> HiddenOracleRecord:
    return HiddenOracleRecord(
        candidate_id=candidate_id,
        oracle_id=oracle_id,
        metrics={"passed": passed, "label": label},
        provenance=PROVENANCE,
        actual_cost=cost,
    )


if __name__ == "__main__":
    unittest.main()
