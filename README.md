# Drug Loop Harness

This project is a loop-engineering harness for closed-loop drug discovery.

> **Research status:** private, pre-publication research preview. The repository
> currently has no open-source license. Results are development evidence, not
> claims of policy superiority, prospective discovery efficiency, or molecular
> activity. See [`docs/REPOSITORY_SCOPE.md`](docs/REPOSITORY_SCOPE.md) for the
> data and release boundary.

## Quick Start

From the repository root on Python 3.12:

```powershell
python -m pip install -r requirements.txt
$env:PYTHONPATH='src'
python -m pytest -q
python examples/table_campaign.py
```

The repository intentionally excludes downloaded benchmarks, virtual
environments, raw docking/FEP outputs, and active experiment state. Full Jensen
or OpenFE reproduction requires the external inputs documented by each frozen
protocol; their hashes remain recorded in the tracked artifacts.

The event-driven online acquisition core is documented in
[`docs/V2_ONLINE_LOOP_REFACTOR.md`](docs/V2_ONLINE_LOOP_REFACTOR.md). The older
campaign and replay modules below remain the v1 retrospective evaluation plane.
The v2.1 scientific-state and credibility semantics are recorded in
[`docs/V2_1_CREDIBILITY_SEMANTICS.md`](docs/V2_1_CREDIBILITY_SEMANTICS.md).
The controlled multi-fidelity comparison protocol is in
[`docs/JENSEN_BENCHMARK_PROTOCOL.md`](docs/JENSEN_BENCHMARK_PROTOCOL.md).
The completed frozen five-target promotion-policy budget curve and its limited
prospective-study decision are in
[`docs/JENSEN_PROMOTION_POLICY_CURVE.md`](docs/JENSEN_PROMOTION_POLICY_CURVE.md).
The oracle-specific failure ablation for reliability-aware TVR is in
[`docs/JENSEN_RELIABILITY_STRESS_ABLATION.md`](docs/JENSEN_RELIABILITY_STRESS_ABLATION.md).
The replayable v2 policy receipt and corrected campaign-level prospective study
handoff are in
[`docs/PROSPECTIVE_V2_POLICY_BRIDGE.md`](docs/PROSPECTIVE_V2_POLICY_BRIDGE.md).
The implementation design for exact reproduction, continuous beliefs,
credibility stress testing, and prospective live oracles is in
[`docs/FOUR_MODULE_ENGINEERING_DESIGN.md`](docs/FOUR_MODULE_ENGINEERING_DESIGN.md).
The qualified WSL/OpenFE environment and its scientific admission boundary are
recorded in [`docs/OPENFE_ORACLE_ENVIRONMENT.md`](docs/OPENFE_ORACLE_ENVIRONMENT.md).
The first target-specific FEP benchmark decision and experiment boundary are in
[`docs/TYK2_FEP_LOOP_BENCHMARK.md`](docs/TYK2_FEP_LOOP_BENCHMARK.md).
The completed preregistered P38 overlap challenge and its audited next-stage
decision are in
[`docs/P38_FEP_CREDIBILITY_CHALLENGE.md`](docs/P38_FEP_CREDIBILITY_CHALLENGE.md).
The completed PTP1B risk-to-action transfer stage, including its failed recall
gate and paired-repair next step, is in
[`docs/PTP1B_FEP_RISK_ACTION_CONFIRMATION.md`](docs/PTP1B_FEP_RISK_ACTION_CONFIRMATION.md).
The frozen PTP1B paired-repair oracle, operational OpenFE pilot, implementation
lock, and full-experiment gate are in
[`docs/PTP1B_PAIRED_REPAIR_ORACLE.md`](docs/PTP1B_PAIRED_REPAIR_ORACLE.md).

It is not a drug-discovery agent platform and not a wrapper around a fixed set
of tools. It is a small domain loop that studies how candidate molecules move
from exploratory generation to credible, signable recommendations under
fragile and expensive oracles.

## Core Stance

The loop is built around one question:

> Under a fixed compute, experiment, and human-review budget, which candidates
> deserve promotion from cheap, gameable proxy oracles to expensive, more
> trustworthy oracles?

The system separates three layers:

- Path layer: generators, heuristics, LLM planners, library search, and ML
  models may influence what to try next.
- Trust layer: oracle observations are normalized, checked for applicability,
  conflict, provenance, and gaming risk before they can support claims.
- Claim layer: multi-objective tradeoffs without a clean objective oracle are
  explicitly exposed to human judgement.

## First Development Target

The first implementation target is the loop contract:

- how a compound, scaffold route, oracle, action, and observation are represented;
- how evidence accumulates into a loop state;
- how promotion decisions are recorded;
- how credibility flags are attached before any final recommendation is made.

Concrete tools such as RDKit, ADMETlab, docking engines, free-energy surrogates,
human review, or wet-lab assays should enter only through adapter interfaces.

## Current Runtime Spine

The first executable spine is now present:

- `events.py` defines append-only campaign events.
- `store.py` rebuilds `LoopState` from an event log and supports JSON round-trip.
- `evidence_graph.py` materializes compound-level evidence views.
- `trust_gate.py` turns normalized observations into `TrustGateResult` tickets.
- `adapters/table.py` loads candidates and observations from CSV tables.
- `adapters/chem.py` provides a deterministic local chem-filter adapter that
  emits TrustGate-ready observations with provenance hashes.
- `policies.py` creates explicit promotion decisions and action records.
  It includes fixed-cascade, staged-promotion, proxy-aware, and trust-gap-aware
  policies.
- `claims.py` evaluates tiered claim protocols against admitted evidence and
  blocks unverified proxy claims when trust-gap risk is high.
- `trust_gap.py` measures weak-oracle reversal and high-fidelity waste,
  including scaffold-local trust gaps.
- `metrics.py` computes loop and policy metrics from recorded evidence,
  including scaffold-aware repeat-waste and rescue signals.
- `replay.py` compares policies without inventing unobserved oracle results.
- `driver.py` runs one bounded policy/executor loop and records applied,
  missing, skipped, invalid, budget-stopped, and coverage-consumption action
  traces. Coverage-authorized actions require a post-TrustGate durable sink.
- `executor_guard.py` validates action arguments, approval requirements,
  idempotency keys, and executor failures before live executors can run.
- `adapters/command.py` provides a local command executor that accepts only
  fixed command specs and requires structured observation JSON.
- `benchmark.py` applies explicitly cached oracle observations for
  counterfactual replay and records per-round replay traces.
- `experiments.py` summarizes replay traces into policy-ablation experiment
  tables and budget-curve reports.
- `reports.py` turns campaign state, claims, metrics, and replay results into an
  auditable report object.
- `experiment_grader.py` independently grades command-backed promotion reports
  as `PASS`, `FAIL`, or `HUMAN` from manifest completeness, trace integrity,
  budget-curve coverage, and proxy-vs-high-fidelity evidence.
- `experiment_review.py` records explicit human `accept`, `reject`, or `defer`
  decisions for `HUMAN` grade issues, linked back to the run id.
  It also summarizes recent human-review records into unresolved-review hot
  state.
- `experiment_archive.py` writes compact durable run records containing report
  path, grade path, verdict, budget limits, policy names, and key issue codes.
- `experiment_state.py` summarizes the recent tail of the JSONL run ledger into
  a compact hot-state artifact for the next human or agent loop turn.
- `examples/table_campaign.py` runs a table-backed campaign without external
  chemistry dependencies.
- `examples/chem_filter_adapter.py` demonstrates a local adapter producing
  observations that pass through TrustGate.
- `scripts/run_chem_filter.py` applies the deterministic chem filter to a
  candidate CSV and writes round-trippable observation CSV.
- `scripts/run_composed_campaign.py` composes generated L0 chem-filter
  observations with table-backed downstream observations and writes a campaign
  report.
- `scripts/run_loop_driver.py` runs a single bounded policy/executor loop from
  local observation cache CSVs and writes a loop trace report.
- `scripts/run_command_executor_demo.py` runs the same bounded loop through a
  fixed local command-backed chem-filter tool and writes a loop trace report.
- `scripts/run_command_promotion_experiment.py` compares fixed-cascade,
  trust-aware, and trust-gap-aware promotion policies through command-backed
  ADMET, docking, and rescoring observations under the same budget or budget
  curve.
- `scripts/grade_command_promotion_report.py` grades a command-backed promotion
  report without executing any oracle or policy.
- `scripts/run_command_promotion_cycle.py` runs the promotion experiment,
  grades the report, writes a latest run record, and appends a JSONL archive
  record.
- `scripts/create_human_review_record.py` creates a durable review record for
  `HUMAN` experiment-grade issues without upgrading scientific claims.
- `scripts/summarize_human_reviews.py` reads recent human-review records and
  writes unresolved-review hot state.
- `scripts/summarize_experiment_runs.py` reads recent experiment-run ledger
  records and writes a compact summary JSON.
- `scripts/suggest_next_loop_actions.py` converts the latest matching run and
  human-review hot state into advisory evidence-gap actions; it never executes
  an oracle or upgrades a scientific claim.
- `scripts/audit_trace_coverage.py` distinguishes absent trace rows from
  recorded, budget-limited observations and reconciles declared round counts.
- `scripts/plan_oracle_cache_coverage.py` compiles recorded missing observations
  into human-approval-gated cache-coverage requests without executing an oracle.
- `scripts/create_coverage_approval_record.py` records a human accept, reject,
  or defer decision for each exact cache-coverage request; accept requires a
  rationale, positive budget, and expiry.
- `scripts/summarize_coverage_approvals.py` writes compact hot state for active
  coverage approvals, including expiry-aware request ids and authorized budget.
- `scripts/check_coverage_authorization.py` is a read-only check of whether one
  exact future action is covered by an active approval record.
- `scripts/summarize_coverage_consumptions.py` compacts the durable record of
  one-use approvals whose returned observations have already passed TrustGate.
- `scripts/reconcile_coverage_consumption.py` checks active approvals against
  consumed request ids, duplicates, and orphaned consumption records.
- `scripts/run_coverage_runtime.py` composes the ledger-backed gate and
  post-TrustGate sink for selected human-approved requests against a declared
  local observation cache; it never calls a live oracle.
- `scripts/audit_coverage_runtime_receipts.py` read-only reconciles one
  coverage-runtime trace against durable consumption receipts.
- `scripts/run_coverage_runtime_cycle.py` runs local cache coverage, audits its
  receipt chain, and archives only a passing control-plane record.
- `scripts/summarize_coverage_runtime_archives.py` emits compact coverage
  runtime hot state from approval, consumption, archive, and audit artifacts.
- `scripts/suggest_next_loop_actions.py` can optionally read that hot state to
  add advisory coverage-control repair or preparation hints.
- `scripts/check_coverage_runtime_preflight.py` read-only checks one proposed
  local-cache action against exact approval, full-ledger consumption, and hot
  state; it cannot authorize or execute the action.
- `scripts/run_rdkit_chem_filter_smoke.py` is an explicitly human-triggered
  smoke test for the version-pinned local RDKit L0 chemistry adapter; it writes
  tool/input/output provenance without running a promotion policy.
- `scripts/verify_docking_input_manifest.py` read-only verifies the exact
  receptor, pocket, ligand, and engine-config artifacts before a future docking
  adapter can be considered; it cannot launch docking.
- `scripts/normalize_docking_result.py` accepts only an existing, hash-bound
  docking result and pose artifact; its default uncalibrated oracle abstains in
  TrustGate instead of turning a score into a binding claim.
- `scripts/probe_docking_engines.py` checks only local `--version` capability
  for known docking binaries; availability is not docking permission or evidence.
- `scripts/verify_docking_calibration_anchor.py` verifies target/engine-bound,
  human-accepted docking calibration evidence before an imported result can use
  a calibrated oracle anchor.
- `scripts/summarize_docking_readiness.py` compacts docking environment, input,
  result, and calibration controls into an advisory planner state with no
  execution authority.
- `scripts/compose_evidence_gaps.py` composes campaign claim gaps with optional
  candidate-bound docking readiness, producing non-executing promotion-preparation
  state rather than a tool queue.
- `scripts/select_evidence_gap_budget.py` ranks only composer-eligible evidence
  gaps under a declared scalar budget and scaffold cap. Its manifest is a
  non-executing selection proposal, not an action queue or authorization.
- `scripts/replay_evidence_gap_selection.py` replays one manifest-validated
  selection from observations already present in a separate event-store cache;
  it reports cache absence explicitly and never invokes an oracle.
- `scripts/compare_evidence_gap_selection.py` pairs a frozen selection and a
  promotion policy on the same initial state, budget, and cache. It emits a
  non-comparability report on coverage failure and never ranks policies itself.
- `scripts/compare_evidence_gap_policy_curve.py` repeats that comparison over a
  strictly increasing, pre-declared budget grid and preserves comparability per
  point instead of hiding unsupported budgets in an aggregate.
- `scripts/audit_evidence_gap_acquisition_support.py` checks whether compared
  cache rows declare their acquisition mechanism, target population, and
  positive inclusion support; it never turns historical coverage into a causal
  policy claim.
- `scripts/plan_prospective_acquisition_support.py` converts inadequate support
  into budgeted candidate pools for independent external randomization; it does
  not select an individual molecule, authorize an acquisition, or invoke a tool.
- `scripts/verify_external_randomization_receipt.py` verifies one human-approved
  external randomization receipt against a prospective pool hash. A pass permits
  only later human preparation, never acquisition execution or claim upgrades.
- `scripts/create_prospective_acquisition_approval.py` and
  `scripts/check_prospective_acquisition_preflight.py` bind one receipt-selected
  subject to an exact human preparation approval; the preflight remains read-only
  and cannot become an executor token.
- `scripts/record_prospective_acquisition_consumption.py` and
  `scripts/audit_prospective_acquisition_consumption.py` bind an already-returned
  observation and TrustGate ticket to one consumed prospective approval. A
  rejected ticket still consumes permission; neither command invokes an adapter.
- `scripts/import_prospective_admet_result.py` verifies a hash-bound external
  ADMET result against the prospective control chain and emits an observation
  draft only; it does not write campaign state or call TrustGate.
- `scripts/stage_prospective_admission_bundle.py` re-verifies one external
  ADMET result, stages its TrustGate ticket and prospective-consumption draft,
  and writes one human-confirmed package without changing campaign state or a
  consumption ledger. `scripts/audit_prospective_admission_bundle.py` replays
  that package from source before any future commit.
- `scripts/commit_prospective_admission_bundle.py` performs the separate local
  intent/materialization/ledger/receipt protocol, writing a derived campaign
  artifact rather than replacing the source campaign. Audit and recovery
  commands expose partial states without silently treating them as committed;
  `resolve_prospective_admission_commit.py` exposes a derived campaign path
  only after the complete chain passes audit.
- `scripts/run_prospective_admet_ai.py` provides a human-confirmed local
  ADMET-AI command boundary with intent, manifest/version checks, raw artifacts,
  timeout/failure receipts, and no direct campaign or claim mutation.
- `scripts/interpret_prospective_admet_endpoints.py` turns a verified raw CSV
  into a result draft only through a reviewed, hash-bound endpoint policy; the
  strict admission option replays execution and policy before evidence staging.
- `scripts/verify_admet_calibration_anchor.py` recomputes external-holdout
  sensitivity/specificity from hash-bound labels and predictions before a policy
  can mark a proxy result calibrated.
- `scripts/monitor_admet_calibration_drift.py` compares later labeled cohorts
  with a fixed calibrated policy and emits diagnostics without online retuning.
- `scripts/suggest_next_loop_actions.py` can optionally consume that report as
  a read-only planning input: warnings, monitor failures, and insufficient
  labels become human-confirmed diagnostic hints, while `STABLE` never grants
  promotion, execution, threshold, or claim authority.
- `scripts/register_prospective_loop_evaluation_protocol.py` freezes a
  human-accepted, two-arm prospective comparison against the exact acquisition
  plan before outcomes are observed; it does not execute, rank policies, or
  upgrade scientific claims.
- `scripts/verify_prospective_loop_evaluation_protocol.py` rechecks that plan
  binding, fixed policy hashes, assay-level primary outcome, analysis plan,
  adaptation controls, and bias controls without granting any authority.
- `scripts/verify_prospective_loop_evaluation_arm_assignment.py` rechecks an
  externally generated, pre-outcome arm allocation against the frozen protocol,
  plan, randomization-verification, and approval chain; it cannot allocate
  subjects, execute, rank policies, or upgrade claims.
- `scripts/run_table_campaign.py` runs the fixed CSV fixture and writes a JSON
  report.
- `examples/data/conflict_*.csv` provides a second fixture with stronger-oracle
  rejection, human rejection, and budget-limited replay coverage.
- `examples/data/scaffold_false_positive_*.csv` provides a fixture where one
  scaffold family repeatedly looks good under docking and fails under rescoring.
- `examples/data/multi_fidelity_promotion_*.csv` provides a command-backed
  promotion fixture with cheap proxy positives, stronger-oracle reversals, and
  one rescued scaffold.

## Files

- `docs/ARCHITECTURE.md` defines the loop shape and layer boundaries.
- `docs/CURRENT_ARCHITECTURE.md` shows the current executable blueprint.
- `docs/COMPONENT_ARCHITECTURE.md` defines implementable components.
- `docs/TRUST_PROTOCOLS.md` defines the minimal trust protocols.
- `docs/OBSERVATION_SCHEMA.md` defines the common CSV observation contract.
- `docs/EXECUTOR_GUARD.md` defines the executor permission and failure
  semantics before live tools are enabled.
- `docs/EXECUTOR_OUTPUT_SCHEMA.md` defines JSON output contracts for
  command-backed and external-style executors.
- `docs/RDKIT_CHEM_FILTER.md` defines the version-pinned local RDKit L0 profile,
  its output provenance, and its scientific limits.
- `docs/DOCKING_INPUT_MANIFEST.md` defines target-specific structural inputs
  and their hash-bound verification before docking is enabled.
- `docs/MULTITARGET_DETERMINISTIC_REPLICATION.md` defines the current
  label-blind multi-target, multi-seed deterministic replay and its admission,
  cache, truth-freeze, and aggregation boundaries.
- `docs/PROSPECTIVE_ADMET_IMPORT.md` defines the receipt-bound external ADMET
  result shape, provenance checks, and draft-only import boundary.
- `docs/PROSPECTIVE_ADMISSION_BUNDLE.md` defines the human-confirmed staged
  package, its replay audit, and its explicit non-transactional boundary.
- `docs/PROSPECTIVE_ADMISSION_COMMIT.md` defines commit intent, single-writer
  ledger append, receipt semantics, and the audited recovery state machine.
- `docs/PROSPECTIVE_ADMET_EXECUTION.md` defines the local ADMET-AI profile,
  execution-attempt artifacts, failure semantics, and interpretation boundary.
- `docs/PROSPECTIVE_ADMET_INTERPRETATION.md` defines endpoint units, row
  binding, threshold semantics, strict importer replay, and calibration limits.
- `docs/ADMET_CALIBRATION_ANCHOR.md` defines external-holdout calibration,
  coverage, acceptance metrics, and calibrated proxy scope.
- `docs/ADMET_PROSPECTIVE_DRIFT.md` defines later-cohort drift diagnostics and
  the explicit prohibition on online threshold re-estimation.
- `docs/PROSPECTIVE_LOOP_EVALUATION_PROTOCOL.md` defines the pre-registered,
  two-arm prospective evaluation control, outcome-report verification,
  predeclared primary analysis, power/replication planning, interpretation
  readiness, and non-claim boundary.
- `docs/PROSPECTIVE_ASSAY_IMPORT.md` defines the policy-bound external assay
  receipt import boundary, including QC, controls, units, and raw-output hashes.
- `docs/EXPERIMENT_PROTOCOL.md` defines policy-comparison rules and
  anti-benchmark-gaming checks.
- `docs/IMPLEMENTATION_PLAN.md` defines the build sequence.
- `src/loop_harness/contracts.py` defines the first contract model.
- `src/loop_harness/experiment_review.py` defines the human-review decision
  record model.
- `src/loop_harness/experiment_state.py` defines the hot-state run-ledger
  summary model.
- `src/loop_harness/planning_hints.py` defines review-aware, non-executing
  next-loop planning hints.
- `src/loop_harness/trace_audit.py` defines the read-only trace-coverage audit.
- `src/loop_harness/coverage_plan.py` defines approval-gated oracle-cache
  coverage requests derived from recorded missing observations.
- `src/loop_harness/coverage_approval.py` defines the durable human approval
  record, hot-state summary, and executor gate for exact coverage requests.
- `src/loop_harness/coverage_consumption.py` defines evidence-backed, single-use
  approval consumption records, a ledger-backed pre-execution gate, the
  post-TrustGate runtime sink, and their reconciliation model.
- `src/loop_harness/v2/fep_checkpoint_cache.py` verifies the published TYK2
  archive and its extracted inner ZIP, then normalizes OpenFE real-time MBAR
  checkpoints into deterministic, label-free JSONL events.
- `scripts/normalize_tyk2_fep_checkpoint_cache.py` materializes the read-only
  TYK2 replay cache and a hash-bound manifest without invoking OpenFE or
  granting policy-ranking authority.
- `src/loop_harness/v2/fep_replay.py` provides the paired-leg action gate,
  causal hidden-cache executor, append-only replay ledger, resumable state
  projector, edge aggregation, and deterministic fixed full protocol.
- `src/loop_harness/v2/fep_evaluation.py` independently checks fixed-protocol
  cost and coverage, official leg-DG concordance, network consistency, and
  evaluator-only experimental metrics.
- `src/loop_harness/v2/fep_policy_stage.py` freezes and executes the common
  warm start plus uniform, random, frozen-diagnostic, and online-diagnostic
  budget-allocation policies without exposing labels or future checkpoints.
- `src/loop_harness/v2/fep_policy_evaluation.py` evaluates partial FEP networks
  against predeclared non-inferiority gates and an evaluator-only full-cache
  reference.
- `scripts/run_tyk2_fep_fixed_baseline.py` runs the complete 132-work-unit TYK2
  retrospective baseline without exposing future checkpoints or labels.
- `scripts/register_tyk2_fep_policy_protocol.py` writes the immutable pre-outcome
  TYK2 policy protocol; `scripts/run_tyk2_fep_policy_curve.py` executes 92
  protocol-bound replays and writes compressed auditable ledgers.

## Run

From the project root:

```powershell
$env:PYTHONPATH='E:\11\drug-loop-harness\src'
python E:\11\drug-loop-harness\examples\table_campaign.py
python E:\11\drug-loop-harness\examples\chem_filter_adapter.py
python E:\11\drug-loop-harness\scripts\run_chem_filter.py --candidates E:\11\drug-loop-harness\examples\data\chem_filter_candidates.csv --output E:\11\drug-loop-harness\examples\data\chem_filter_observations.csv
python E:\11\drug-loop-harness\scripts\run_composed_campaign.py --generated-chem-filter-output E:\11\drug-loop-harness\examples\data\composed_chem_filter_observations.csv --output E:\11\drug-loop-harness\examples\data\composed_campaign_report.json
python E:\11\drug-loop-harness\scripts\run_loop_driver.py --output E:\11\drug-loop-harness\examples\data\loop_run_report.json
python E:\11\drug-loop-harness\scripts\run_loop_driver.py --enforce-guard --output E:\11\drug-loop-harness\examples\data\guarded_loop_run_report.json
python E:\11\drug-loop-harness\scripts\run_command_executor_demo.py --output E:\11\drug-loop-harness\examples\data\command_executor_report.json
python E:\11\drug-loop-harness\scripts\run_command_executor_demo.py --enforce-guard --output E:\11\drug-loop-harness\examples\data\command_executor_guarded_report.json
python E:\11\drug-loop-harness\scripts\run_command_promotion_experiment.py --budget-curve 48 56 64 --output E:\11\drug-loop-harness\examples\data\command_promotion_experiment_report.json
python E:\11\drug-loop-harness\scripts\grade_command_promotion_report.py E:\11\drug-loop-harness\examples\data\command_promotion_experiment_report.json --output E:\11\drug-loop-harness\examples\data\command_promotion_experiment_grade.json
python E:\11\drug-loop-harness\scripts\run_command_promotion_cycle.py --budget-curve 48 56 64
python E:\11\drug-loop-harness\scripts\create_human_review_record.py
python E:\11\drug-loop-harness\scripts\summarize_human_reviews.py
python E:\11\drug-loop-harness\scripts\summarize_experiment_runs.py
python E:\11\drug-loop-harness\scripts\suggest_next_loop_actions.py
python E:\11\drug-loop-harness\scripts\audit_trace_coverage.py --run-record E:\11\drug-loop-harness\examples\data\command_promotion_experiment_run_record.json
python E:\11\drug-loop-harness\scripts\plan_oracle_cache_coverage.py --run-record E:\11\drug-loop-harness\examples\data\command_promotion_experiment_run_record.json
python E:\11\drug-loop-harness\scripts\create_coverage_approval_record.py
python E:\11\drug-loop-harness\scripts\summarize_coverage_approvals.py
python E:\11\drug-loop-harness\scripts\summarize_coverage_consumptions.py
python E:\11\drug-loop-harness\scripts\reconcile_coverage_consumption.py
python E:\11\drug-loop-harness\scripts\run_coverage_runtime.py
python E:\11\drug-loop-harness\scripts\audit_coverage_runtime_receipts.py E:\11\drug-loop-harness\state\coverage-runtime-report.json
python E:\11\drug-loop-harness\scripts\run_coverage_runtime_cycle.py
python E:\11\drug-loop-harness\scripts\summarize_coverage_runtime_archives.py
python E:\11\drug-loop-harness\scripts\check_coverage_runtime_preflight.py --request-id coverage_request_example --budget-reserved 8 --output E:\11\drug-loop-harness\state\coverage-runtime-preflight.json
python E:\11\drug-loop-harness\scripts\run_rdkit_chem_filter_smoke.py --expected-rdkit-version <exact-version> --confirmed-by <reviewer> --subject-id cmpd_001 --output E:\11\drug-loop-harness\state\rdkit-chem-filter-smoke.json
python E:\11\drug-loop-harness\scripts\verify_docking_input_manifest.py E:\path\to\docking-input.json --output E:\11\drug-loop-harness\state\docking-input-manifest-verification.json
python E:\11\drug-loop-harness\scripts\normalize_docking_result.py E:\path\to\docking-result.json --verification E:\11\drug-loop-harness\state\docking-input-manifest-verification.json --output E:\11\drug-loop-harness\state\docking-result-normalization.json
python E:\11\drug-loop-harness\scripts\probe_docking_engines.py --output E:\11\drug-loop-harness\state\docking-engine-capabilities.json
python E:\11\drug-loop-harness\scripts\verify_docking_calibration_anchor.py E:\path\to\calibration-anchor.json --output E:\11\drug-loop-harness\state\docking-calibration-anchor-verification.json
python E:\11\drug-loop-harness\scripts\summarize_docking_readiness.py --output E:\11\drug-loop-harness\state\docking-readiness.json
python E:\11\drug-loop-harness\scripts\compose_evidence_gaps.py --campaign-store E:\path\to\campaign-events.json --docking-readiness E:\11\drug-loop-harness\state\docking-readiness.json --output E:\11\drug-loop-harness\state\evidence-gaps.json
python E:\11\drug-loop-harness\scripts\suggest_next_loop_actions.py --evidence-gap-summary E:\11\drug-loop-harness\state\evidence-gaps.json
python E:\11\drug-loop-harness\scripts\register_prospective_loop_evaluation_protocol.py E:\path\to\evaluation-declaration.json --prospective-acquisition-plan E:\11\drug-loop-harness\state\prospective-acquisition-plan.json --output E:\11\drug-loop-harness\state\prospective-loop-evaluation-protocol.json
python E:\11\drug-loop-harness\scripts\verify_prospective_loop_evaluation_protocol.py E:\11\drug-loop-harness\state\prospective-loop-evaluation-protocol.json --prospective-acquisition-plan E:\11\drug-loop-harness\state\prospective-acquisition-plan.json --output E:\11\drug-loop-harness\state\prospective-loop-evaluation-verification.json
python E:\11\drug-loop-harness\scripts\verify_prospective_loop_evaluation_arm_assignment.py E:\11\drug-loop-harness\state\prospective-loop-evaluation-protocol.json --prospective-acquisition-plan E:\11\drug-loop-harness\state\prospective-acquisition-plan.json --assignment E:\path\to\external-arm-assignment.json --randomization-verification E:\path\to\randomization-verification-a.json --approval E:\path\to\prospective-approval-a.json --output E:\11\drug-loop-harness\state\prospective-loop-arm-assignment-verification.json
python E:\11\drug-loop-harness\scripts\run_command_promotion_experiment.py --enforce-guard --output E:\11\drug-loop-harness\examples\data\command_promotion_experiment_guarded_report.json
python E:\11\drug-loop-harness\scripts\run_table_campaign.py
python E:\11\drug-loop-harness\scripts\run_table_campaign.py --candidates E:\11\drug-loop-harness\examples\data\conflict_candidates.csv --observations E:\11\drug-loop-harness\examples\data\conflict_observations_round_1.csv E:\11\drug-loop-harness\examples\data\conflict_observations_round_2.csv --cache-budget-limit 8
python E:\11\drug-loop-harness\scripts\run_table_campaign.py --candidates E:\11\drug-loop-harness\examples\data\conflict_candidates.csv --observations E:\11\drug-loop-harness\examples\data\conflict_observations_round_1.csv E:\11\drug-loop-harness\examples\data\conflict_observations_round_2.csv --cache-budget-curve 4 8 18
python E:\11\drug-loop-harness\scripts\run_table_campaign.py --candidates E:\11\drug-loop-harness\examples\data\scaffold_false_positive_candidates.csv --observations E:\11\drug-loop-harness\examples\data\scaffold_false_positive_observations_round_1.csv E:\11\drug-loop-harness\examples\data\scaffold_false_positive_observations_round_2.csv --max-cache-rounds 7 --cache-budget-curve 60
python E:\11\drug-loop-harness\scripts\normalize_tyk2_fep_checkpoint_cache.py
python E:\11\drug-loop-harness\scripts\run_tyk2_fep_fixed_baseline.py
python E:\11\drug-loop-harness\scripts\register_tyk2_fep_policy_protocol.py
python E:\11\drug-loop-harness\scripts\run_tyk2_fep_policy_curve.py
python -m unittest discover -s E:\11\drug-loop-harness\tests -v
```
