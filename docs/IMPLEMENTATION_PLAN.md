# Implementation Plan

This plan gives the project enough structure to build without turning it into a
large platform. The order is opinionated: build the evidence spine first, then
tools, then policies, then replay and evaluation.

## Build Principle

The loop should be implemented as an event-driven evidence machine:

```text
event log -> evidence graph -> policy decision -> tool action -> trust result
```

Concrete tools are replaceable. The evidence contract is not.

## Phase 1: Evidence Spine

Goal:

Make one campaign able to ingest compounds, record observations, run TrustGate,
and update evidence state.

Deliverables:

- event vocabulary and event dataclasses;
- in-memory CampaignStore;
- EvidenceGraph materializer;
- TrustGateResult persistence;
- simple JSON export/import for campaign state.

Status:

- Initial in-memory implementation exists in `events.py`, `store.py`, and
  `evidence_graph.py`.
- JSON round-trip is covered by `tests/test_spine.py`.

Key implementation files:

```text
src/loop_harness/events.py
src/loop_harness/store.py
src/loop_harness/evidence_graph.py
src/loop_harness/trust_gate.py
```

Acceptance criteria:

- a compound can be ingested;
- a raw observation can be recorded;
- TrustGate can issue qualified/rejected/conflicted/abstain;
- state can be reconstructed from events;
- rejected evidence remains visible.

Deliberately not included:

- database server;
- UI;
- real docking or ADMET adapters.

## Phase 2: TrustGate Core

Goal:

Turn TrustGate from a schema into a working admission function.

Inputs:

- Observation;
- OracleSpec;
- existing evidence for the same compound and route;
- ClaimProtocol list;
- optional TrustGapMetric history.

Outputs:

- TrustGateResult.

Status:

- Initial readable admission logic exists in `trust_gate.py`.
- It handles provenance, applicability, evidence tier assignment, local quality,
  same-oracle conflict checks, claim permission, and promotion advice.

Initial checks:

1. Provenance check  
   Reject or abstain if required input/output hashes or adapter metadata are
   missing.

2. Applicability check  
   Abstain if the observation is outside the oracle domain.

3. Evidence tier assignment  
   Map oracle kind to evidence tier.

4. Quality check  
   Run oracle-kind-specific sanity checks.

5. Conflict check  
   Compare with existing stronger or same-tier evidence.

6. Claim permission check  
   Map the result to triage, credible_hit, experiment_ready, or human_signed.

7. Promotion advice  
   Suggest stronger oracle when the observation is promising but fragile.

Acceptance criteria:

- TrustGate can process at least chem_filter, ADMET, docking, and human_review
  observations;
- every decision includes a reason;
- no observation can support a claim without a tier and permission.

Deliberately not included:

- complex probabilistic calibration;
- conformal prediction implementation;
- automatic human review UI.

## Phase 3: Minimal Tool Adapters

Goal:

Attach enough tools to run a real loop without coupling the harness to any one
tool.

Status:

- Initial table-backed adapters exist in `adapters/table.py`.
- They load candidate compounds and normalized observations from CSV.
- A deterministic local chem-filter adapter exists in `adapters/chem.py`.
- It emits TrustGate-ready observations with adapter metadata, ruleset,
  applicability domain, input hash, output hash, checks, and failure reasons.
- `examples/table_campaign.py` now uses these adapters instead of hand-written
  observations.
- `examples/chem_filter_adapter.py` demonstrates direct adapter-to-TrustGate
  execution.

Adapter priority:

1. ChemFilterAdapter  
   Deterministic chemistry filters from SMILES or SDF.

2. TableADMETAdapter  
   Reads ADMET-like predictions from CSV/JSON. This lets us test the loop before
   depending on a remote service.

3. DockingScoreAdapter  
   Reads docking-like scores and optional pose-quality flags from CSV/JSON.

4. HumanReviewAdapter  
   Reads structured human decisions.

Later adapters:

- ADMETlab API;
- CarsiDock/KarmaDock command adapter;
- GNINA/Vina adapter;
- free-energy surrogate adapter;
- assay result importer.

Acceptance criteria:

- all adapters return raw observations;
- normalizer converts them to common Observation objects;
- TrustGate handles every adapter result.

Deliberately not included:

- direct dependency on RDKit or docking engines in the core package;
- hidden network calls.

## Phase 4: Policy Engine

Goal:

Make promotion decisions explicit and comparable.

Status:

- Initial policy module exists in `policies.py`.
- Implemented `FixedCascadePolicy`, `GreedyProxyPolicy`, `TrustAwarePolicy`, and
  `TrustGapAwarePolicy`.
- Implemented `StagedPromotionPolicy` for fair promotion-policy comparisons
  after a shared cheap-evidence bootstrap.
- `TrustAwarePolicy` suppresses redundant actions when the action's evidence tier
  is already present for a subject.
- `TrustGapAwarePolicy` discounts promotion value using recorded weak-to-strong
  oracle reversal, conflict, and waste rates. It can prefer scaffold-local
  trust-gap history over global history.

Initial policies:

1. FixedCascadePolicy  
   Always runs filters, ADMET, docking, then human review in order.

2. GreedyProxyPolicy  
   Promotes top proxy scores.

3. TrustAwarePolicy  
   Promotes candidates by proxy signal, applicability, quality, flags, and cost.

4. TrustGapAwarePolicy  
   Promotes candidates by proxy value discounted by observed trust-gap risk.

5. StagedPromotionPolicy  
   Runs a fixed cheap-evidence bootstrap, then delegates high-fidelity promotion
   choices to a promotion policy.

Policy input:

- EvidenceGraph;
- BudgetMeter;
- OracleRegistry;
- RouteMemory;
- TrustGapMetric history.

Policy output:

- PromotionDecision;
- ActionRecord.

Acceptance criteria:

- the same candidate set can be run through at least two policies;
- every action has an explicit policy reason;
- policy cannot spend budget that does not exist.
- trust-gap-aware promotion changes ranking when a weak oracle has high
  reversal or waste history.

Deliberately not included:

- LLM planner;
- Bayesian optimization;
- deep active learning model.

## Phase 5: Claim Protocol

Goal:

Generate tiered recommendations without hiding the human truth position.

Status:

- Initial claim evaluator exists in `claims.py`.
- It evaluates `ClaimProtocol` requirements against admitted TrustGate evidence.
- It reports supporting evidence, missing tiers, missing oracle kinds,
  unresolved flags, and human-review requirements.
- It blocks unverified weak-oracle proxy claims when relevant trust-gap history
  indicates high false-positive risk.

Claim levels:

- triage;
- credible_hit;
- experiment_ready;
- human_signed.

ClaimEvaluator must:

- list supporting evidence;
- list missing evidence;
- list unresolved flags;
- request human review when required;
- refuse upgrade if protocol requirements are not met.

Acceptance criteria:

- a compound with only docking evidence cannot become experiment_ready;
- a compound with missing provenance cannot become credible_hit;
- human_signed claims include the human decision event.

Current coverage:

- `tests/test_claims.py` covers credible-hit satisfaction, experiment-ready
  refusal without human review, and experiment-ready satisfaction with human
  review.

## Phase 6: Replay, Metrics, And Reports

Goal:

Evaluate loop policies under the same budget.

Status:

- Initial metric calculator exists in `metrics.py`.
- Metrics include scaffold-aware high-fidelity action diversity,
  repeated-scaffold waste, and rescued-scaffold counts.
- Initial offline replay runner exists in `replay.py`.
- Replay compares planned actions and metrics against already recorded evidence;
  it does not invent missing counterfactual oracle results.
- Initial trust-gap calculator exists in `trust_gap.py`.
- Trust-gap calculation supports both global and scaffold-local scopes.
- Initial oracle-cache replay runner exists in `benchmark.py`.
- Oracle-cache replay records per-round traces for planned actions, cached
  observations, missing counterfactuals, TrustGate decisions, and stop reasons.
- Oracle-cache replay can refresh trust-gap metrics after each replay round
  using only evidence already observed in that replay.
- Experiment summaries exist in `experiments.py`.
- Budget-curve summaries and action sequences exist in `experiments.py`.
- Initial structured report builder exists in `reports.py`.
- `examples/table_campaign.py` uses `ReportBuilder` for its console output.
- `scripts/run_table_campaign.py` runs the fixed CSV fixture and writes JSON
  reports.
- `scripts/run_table_campaign.py` supports budget-limited oracle-cache replay.
- `scripts/run_table_campaign.py` supports budget-curve oracle-cache replay.
- A second conflict fixture exists under `examples/data/conflict_*.csv`.
- A scaffold false-positive fixture exists under
  `examples/data/scaffold_false_positive_*.csv`.

Replay modes:

1. Recorded replay  
   Replays only observations already present in the campaign.

2. Oracle-cache replay  
   Uses cached observations for candidates/actions previously evaluated.

3. Retrospective benchmark replay  
   Uses a fixed benchmark table where counterfactual oracle results are known.

Metrics:

- cost to first credible hit;
- credible hit rate;
- high-fidelity waste rate;
- scaffold repeat-waste rate;
- rescued-scaffold rate;
- oracle reversal rate;
- oracle conflict rate;
- scaffold diversity;
- claim upgrade rate;
- abstention rate.

Report outputs:

- per-compound evidence tickets;
- strongest satisfied claim level;
- missing evidence by claim protocol;
- policy replay actions and planned cost;
- JSON-ready campaign report.

Acceptance criteria:

- two policies can be compared on the same campaign log;
- missing counterfactuals are explicit, not silently imputed.
- report generation cannot upgrade a claim or override TrustGate.

Current coverage:

- `tests/test_metrics_replay.py` covers claim-rate metrics and offline replay
  for fixed cascade vs trust-aware promotion.
- `tests/test_adapters_and_policies.py` covers deterministic chem-filter adapter
  output and TrustGate admission/rejection behavior.
- `tests/test_adapters_and_policies.py` covers trust-gap-aware ranking.
- `tests/test_adapters_and_policies.py` covers staged promotion, scaffold-local
  trust-gap policy behavior, and non-repetition of rejected expensive oracle
  results.
- `tests/test_claims.py` covers trust-gap risk blocking unverified proxy claims.
- `tests/test_reports.py` covers evidence, claim, replay, and JSON-ready report
  summaries.
- `tests/test_trust_gap.py` covers reversal, conflict, and high-fidelity waste.
- `tests/test_benchmark.py` covers oracle-cache replay and missing
  counterfactual reporting, including per-round traces.
- `tests/test_benchmark.py` also covers experiment summaries from replay traces.
- `tests/test_benchmark.py` covers budget-limited oracle-cache replay.
- `tests/test_benchmark.py` covers budget-curve replay across multiple budget
  limits.
- `tests/test_runner.py` covers the conflict fixture and budget-limit warnings.
- `tests/test_runner.py` covers budget-curve JSON output and scaffold
  false-positive trace differences, including scaffold-repeat waste and
  rescued-scaffold metrics.
- `tests/test_experiment_grader.py` covers `PASS`, `FAIL`, and `HUMAN` grading
  cases for command-backed promotion reports and the grader CLI.

## Phase 7: Drug-Discovery Loop Run

Goal:

Run one complete campaign with a small but real candidate table.

Recommended first campaign shape:

```text
100 to 1000 candidate molecules
  -> chem filters
  -> ADMET-like table
  -> docking-like table
  -> TrustGate
  -> TrustAwarePolicy
  -> human review file
  -> claim report
```

The first campaign can use table adapters. The purpose is to test loop semantics,
not tool integration.

## File Roadmap

```text
src/loop_harness/contracts.py        existing contracts
src/loop_harness/events.py           event definitions
src/loop_harness/store.py            append-only campaign store
src/loop_harness/evidence_graph.py   state materialization
src/loop_harness/trust_gate.py       admission logic
src/loop_harness/adapters/base.py    adapter interface
src/loop_harness/adapters/table.py   CSV/JSON adapter
src/loop_harness/policies.py         fixed, greedy, trust-aware
src/loop_harness/claims.py           claim evaluator
src/loop_harness/replay.py           replay runner
src/loop_harness/benchmark.py        oracle-cache replay runner
src/loop_harness/experiments.py      policy experiment summaries
src/loop_harness/metrics.py          loop metrics
src/loop_harness/trust_gap.py        trust-gap calculator
src/loop_harness/reports.py          campaign report builder
```

## Design Freedom

The project should stay free in three places:

1. Evidence representation  
   If graph state becomes too heavy, materialized tables are acceptable as long
   as provenance and route memory remain intact.

2. TrustGate rules  
   Initial rules may be simple and explicit. The project should prefer readable
   admission logic over premature probabilistic sophistication.

3. Policy strategy  
   Policies should be swappable. The harness should judge them by same-budget
   credible outcomes, not by architectural elegance.

## Non-Goals For Now

- building a drug discovery SaaS;
- supporting every oracle type;
- automating wet-lab integration;
- optimizing with an LLM-as-judge;
- implementing a full database before the event semantics are stable;
- treating top docking score as a final result.

## Next Coding Step

The repeatable table campaign now exists. The next useful slice is to make
policy experiments more discriminating without adding a large platform.

Completed slice:

```text
examples/data/
  added repeated scaffold-level false-positive fixture

docs/EXPERIMENT_PROTOCOL.md
  added multi-policy budget-curve comparison convention
```

Why this slice:

- it makes trust-gap-driven promotion choices auditable round by round;
- it gives the project a reproducible policy-ablation story;
- it tests whether budget constraints change policy conclusions;
- it keeps the research object centered on loop decisions rather than tool
  integration volume.

Completed slice:

```text
src/loop_harness/metrics.py
  added scaffold-aware waste and rescued-scaffold metrics

docs/EXPERIMENT_PROTOCOL.md
  defined how to report scaffold-local rescue vs global oracle pessimism
```

Why this slice:

- summary metrics still under-report the difference between "same scaffold
  repeated waste" and "different scaffold exploration";
- it turns action-sequence differences into first-class experiment metrics;
- it keeps credibility evaluation ahead of tool integration.

Completed slice:

```text
src/loop_harness/adapters/
  added a deterministic chem-filter adapter with local validation output

docs/TRUST_PROTOCOLS.md
  defined minimal adapter-output provenance requirements
```

Why this slice:

- the loop now has enough replay/evaluation structure to benefit from a real
  local tool adapter;
- a deterministic chem filter is low risk and keeps the first real integration
  inside the TrustGate contract;
- provenance requirements should be nailed down before adding remote or
  stochastic oracles.

Completed slice:

```text
scripts/
  added run_chem_filter.py for candidate CSV -> observation CSV

examples/data/
  added chem_filter_candidates.csv as a candidate-only L0 fixture

src/loop_harness/adapters/table.py
  preserves adapter/ruleset/hash provenance and parses JSON metrics
```

Why this slice:

- it makes the adapter usable from the command line without writing Python;
- it starts replacing hand-authored L0 fixture observations with reproducible
  adapter-generated observations;
- it keeps the tool boundary narrow before adding heavier chemistry packages.

Completed slice:

```text
scripts/
  added run_composed_campaign.py for generated L0 plus downstream observations

docs/
  added OBSERVATION_SCHEMA.md for ADMET, docking, rescoring, synthesis, and
  human review columns

examples/data/
  added downstream_observations.csv without hand-authored L0 chem-filter rows
```

Why this slice:

- it turns isolated adapter output into a complete loop input;
- it keeps the next real engineering question on state and promotion policy,
  not on installing large chemistry stacks;
- it gives future ADMET or docking adapters a stable evidence contract before
  they become live tools.

Completed slice:

```text
src/loop_harness/
  added BoundedLoopDriver and ObservationCacheExecutor for local bounded loop
  execution

tests/
  added trace-level eval cases for budget stops and false-success prevention
```

Why this slice:

- it moves the harness from batch composition toward autonomous loop flow;
- it still keeps all side effects local and replayable;
- it gives loop autonomy an auditable trace before adding live ADMET or docking
  services.

Completed slice:

```text
scripts/
  added run_loop_driver.py for single-policy bounded loop traces

src/loop_harness/reports.py
  added compact LoopRunReport view for driver traces
```

Why this slice:

- it exposes the autonomous loop runtime without asking users to write Python;
- it separates one-policy live-loop traces from multi-policy benchmark reports;
- it creates the report surface needed before any external tool executor is
  allowed.

Completed slice:

```text
src/loop_harness/
  added ExecutorGuard and GuardedActionExecutor for action validation,
  approval requirements, idempotency checks, and executor failure traces

docs/
  added EXECUTOR_GUARD.md defining local/autonomous vs approval-gated actions
```

Why this slice:

- the loop runtime now exists, so the next risk is uncontrolled executor power;
- guard contracts should be defined before adding remote or heavyweight tools;
- it keeps scientific autonomy bounded by auditable permission and failure
  semantics.

Completed slice:

```text
src/loop_harness/adapters/
  added LocalCommandExecutor and LocalCommandSpec as the first local-only
  external-style executor boundary

tests/
  added trace evals for valid command output, malformed output, and timeout
```

Why this slice:

- the harness now has permission semantics; the next step is testing them
  against an executor boundary that behaves like a real tool;
- local command/file execution keeps the system reproducible before any remote
  ADMET, docking, or lab connector is introduced;
- malformed-output tests will prevent tool wrappers from turning bad payloads
  into scientific evidence.

Completed slice:

```text
docs/
  added EXECUTOR_OUTPUT_SCHEMA.md for command-backed ADMET, docking,
  rescoring, synthesis, and human-review adapters

tests/
  added malformed/untrustworthy-output regression cases for wrong oracle,
  missing provenance, and out-of-domain tool output
```

Why this slice:

- the command boundary now exists, so the next risk is schema drift by oracle
  type;
- wrong-subject and wrong-oracle outputs are common integration failures that
  must never become claim-supporting evidence;
- provenance and domain failures should be visible before live tools are added.

Completed slice:

```text
scripts/
  add a small local-command loop demo that uses LocalCommandExecutor through
  GuardedActionExecutor and writes a LoopRunReport

examples/tools/
  added emit_chem_filter_observation.py as the first command-backed tool profile
```

Why this slice:

- it proves the command executor can be used by a user-facing runner, not only
  unit tests;
- chem-filter is low risk and already deterministic, so it is the safest first
  command-backed profile;
- the same runner shape can later wrap ADMET, docking, and rescoring commands.

Completed slice:

```text
examples/data/
  add a small multi-fidelity promotion fixture with cheap ADMET/docking
  positives, stronger-oracle reversals, and one rescued candidate

scripts/
  add a command-backed promotion runner that compares fixed cascade,
  trust-aware, and trust-gap-aware promotion under the same budget

examples/tools/
  added emit_cached_observation.py as a generic CSV-cache observation emitter
```

Why this slice:

- the core research question is no longer whether a tool can be called, but
  whether promotion policy changes credible discovery efficiency;
- a deliberately small fixture can expose false positives, missed rescues, and
  budget waste without adding a large chemistry stack;
- it keeps development centered on Loop State Representation, Promotion Policy,
  and Credibility-aware Evaluation.

Completed slice:

```text
scripts/
  extend the command-backed promotion runner to a budget curve across at least
  three budget limits

docs/
  add a compact experiment manifest section recording fixed policy parameters,
  oracle cache path, budget curve, and interpretation warnings
```

Why this slice:

- the current command-backed promotion comparison demonstrates the mechanism at
  one budget, but a paper-grade claim should not rest on one budget point;
- budget curves expose whether trust-gap-aware rescue is robust or only appears
  at a convenient cutoff;
- an explicit manifest makes the loop run easier to reproduce before replacing
  CSV-cache commands with heavier ADMET, docking, or rescoring tools.

Completed slice:

```text
src/loop_harness/
  add a report-level experiment grader that returns PASS / FAIL / HUMAN for
  promotion experiment reports

scripts/
  add a small command that grades command_promotion_experiment_report.json
```

Why this slice:

- the loop now emits richer reports, but scientific credibility still needs an
  independent evaluator boundary;
- trace grading should catch missing budget curves, hidden failures, scalar-only
  conclusions, blocked actions, failed commands, and proxy-only wins before a
  report is used as evidence;
- this keeps generator, executor, and evaluator responsibilities separated
  without turning the project into a large platform.

Completed slice:

```text
src/loop_harness/
  add a compact experiment archive record that stores report path, grade path,
  verdict, budget limits, and key interpretation issues

scripts/
  add a command that runs promotion experiment, grades it, and writes one
  manifest-like run record
```

Why this slice:

- the loop now has generator, executor, report, and evaluator surfaces, but run
  history is still scattered across separate JSON artifacts;
- a tiny archive record gives the project durable loop state without adding a
  database or scheduler;
- it prepares for later manual repeated runs and weekly audit without increasing
  autonomy.

Completed slice:

```text
state/
  generated experiment-runs-summary.json as a compact hot-state artifact

scripts/
  added summarize_experiment_runs.py to read recent experiment-run records and
  emit a compact summary line

src/loop_harness/
  added experiment_state.py with ExperimentRunLedgerSummarizer
```

Why this slice:

- the run archive now exists, so the next risk is unbounded log growth;
- a summary command will keep the loop agent-legible without forcing future
  runs to read every historical record;
- it follows the hot/warm/cold state discipline before any scheduling is added.

Completed slice:

```text
src/loop_harness/
  added experiment_review.py with ExperimentHumanReviewRecorder

scripts/
  added create_human_review_record.py to convert HUMAN grade issues into
  explicit accept, reject, or defer notes linked to the run_id

tests/
  added review-record tests for deferred drafts, rationale-required
  accept/reject decisions, selected issue decisions, and CLI persistence
```

Why this slice:

- the grader now correctly refuses to over-interpret proxy-only or
  budget-sensitive results, but HUMAN verdicts need a durable response path;
- a small review record preserves scientific judgement without adding a UI;
- it lets the next loop distinguish unresolved uncertainty from a human-audited
  methodological limitation.

Completed slice:

```text
state/
  generated human-reviews-summary.json as compact unresolved-review hot state

scripts/
  added summarize_human_reviews.py so new loop turns can see unresolved HUMAN
  review burden without reading the full review ledger

src/loop_harness/
  extended experiment_review.py with HumanReviewLedgerSummarizer
```

Why this slice:

- human-review records now exist, but the next agent still needs a compact way
  to see unresolved review state;
- this keeps human judgement in the loop state without requiring long JSONL
  scans;
- it prepares the manual Lv1 loop for later weekly audit without scheduling.

Completed slice:

```text
src/loop_harness/
  added planning_hints.py with ReviewAwarePlanningAdvisor and a typed
  NextLoopPlan / NextLoopActionHint contract

scripts/
  added suggest_next_loop_actions.py to read the two hot-state artifacts and
  persist state/next-loop-plan.json

tests/
  added planning-hint coverage for current unresolved issues, accepted-review
  suppression, missing state, run-id mismatch, and CLI persistence
```

Why this slice:

- the project now records run verdicts and human-review state, but the next
  loop turn still needs a principled bridge from unresolved evidence problems
  to action selection;
- this keeps autonomy moving through evidence gaps instead of tool-count
  expansion;
- it turns HUMAN review from a stop sign into a structured next-step signal
  without allowing automatic scientific claim upgrades;
- it uses latest-record fields rather than aggregate counts for active planning,
  so older issues cannot silently reopen a resolved review decision.

Completed slice:

```text
src/loop_harness/
  added trace_audit.py with TraceCoverageAuditor and typed trace-gap sections
  that distinguish missing trace rows from recorded missing observations

scripts/
  added audit_trace_coverage.py for report or run-record selected audits

tests/
  added trace audit coverage for budget-limited observations, untraced planned
  actions, missing reports, and CLI persistence
```

Why this slice:

- a strict audit showed the original `missing_action_trace` label was conflating
  complete trace rows with budget-limited observations;
- the grader now emits `incomplete_action_trace_coverage` only when planned
  action counts do not match rows, and `recorded_missing_observation` when a
  trace exists but its observation is absent;
- it remains local, replayable, and non-decisional.

Completed slice:

```text
src/loop_harness/
  added coverage_plan.py with OracleCacheCoveragePlanner and typed, grouped
  compound-oracle coverage requests

scripts/
  added plan_oracle_cache_coverage.py to compile missing trace rows from a
  report/run record into state/oracle-cache-coverage-plan.json

tests/
  added coverage-plan tests for grouping, unmapped actions, missing reports,
  and CLI persistence
```

Why this slice:

- after trace accounting is verified, the remaining uncertainty is now a
  concrete coverage question, not a generic loop failure;
- grouping repeated `(compound, action, oracle, reason)` occurrences turns
  budget-sensitive replay into a bounded human decision package;
- the plan records which evidence is absent but does not authorize a new oracle
  run or turn absence into a negative result.

Completed slice:

```text
src/loop_harness/
  added coverage_approval.py with immutable request-bound human approvals,
  expiry-aware hot-state summary, and CoverageApprovalGate

scripts/
  added create_coverage_approval_record.py,
  summarize_coverage_approvals.py, and check_coverage_authorization.py

tests/
  added approval tests for required acceptance fields, exact-match permission,
  expiry, tampering, over-budget actions, blocked inner executors, summaries,
  and CLI persistence
```

Why this slice:

- the coverage plan now identifies exactly four rescore requests, but a plan is
  not a budget authorization;
- an explicit approval record is the bridge between scientific judgment,
  allowed spend, and future guarded execution;
- each approval is bounded to one request, one budget, and one expiry, so the
  promotion policy cannot silently grant itself more oracle budget;
- gate checks are mechanical and occur before the executor is invoked, while
  TrustGate remains mandatory after a new observation is returned.

Completed slice:

```text
src/loop_harness/
  added coverage_consumption.py with evidence-backed consumption records,
  consumption-ledger summaries, and approval/consumption reconciliation

scripts/
  added summarize_coverage_consumptions.py and
  reconcile_coverage_consumption.py for manual hot-state inspection

tests/
  added consumption tests for TrustGate-bound receipt creation, duplicate use,
  missing tickets, gate blocking, ledger summaries, reconciliation, and CLI
  persistence
```

Why this slice:

- the current gate blocks unapproved work, and consumption now prevents an
  approved single-observation request from being reused after evidence arrives;
- linking authorization, execution trace, and TrustGate ticket makes a future
  rerun auditable without treating permission as evidence;
- this remains a local control-plane slice before enabling any live oracle.

Completed slice:

```text
src/loop_harness/
  added CoverageConsumptionSink and a CoverageAdmissionSink driver boundary
  immediately after TrustGate admits a coverage-approved observation

tests/
  added local cache-backed runtime coverage for single-use consumption,
  missing-sink execution blocking, sink failure termination, and isolation from
  ordinary actions
```

Why this slice:

- consumption records are now part of the actual runtime transition rather
  than a manually assembled post-processing artifact;
- the local fixture verifies the exact approval-to-evidence chain without
  calling a real oracle;
- no sink can accidentally be applied to ordinary actions, and a failed receipt
  stops the run before it can be treated as an interpretable experiment.

Completed slice:

```text
src/loop_harness/
  added LedgerBackedCoverageApprovalGate to reload the full matching durable
  consumption ledger immediately before each coverage action

tests/
  added same-round duplicate-request, unavailable-ledger, and long-ledger
  regression coverage
```

Why this slice:

- the gate now reads the same durable source of truth as the sink instead of a
  caller-supplied snapshot;
- authorization reads full history, while compact summaries remain restricted
  to human/agent inspection;
- the serial driver cannot make a second same-request executor call after the
  first action has been consumed.

Completed slice:

```text
scripts/
  added run_coverage_runtime.py to compose plan-bound actions, the ledger-backed
  gate, declared local cache, TrustGate, and post-admission sink

tests/
  added CLI coverage for accepted, rerun-consumed, deferred, and malformed-ledger
  cases without enabling any remote oracle
```

Why this slice:

- the complete authorization-to-evidence path is now reproducible outside unit
  tests without adding an external tool adapter;
- the runner deliberately turns current `defer` records into blocked traces,
  not cache evidence or an implicit budget approval;
- its explicit local-cache marker prevents a fixture exercise from being
  represented as an executed scientific result.

Completed slice:

```text
src/loop_harness/
  added CoverageRuntimeReceiptAuditor to reconcile a runtime trace with the
  durable receipt by plan, approval, request, action, observation, and ticket

scripts/
  added audit_coverage_runtime_receipts.py for read-only runtime receipt audits
```

Why this slice:

- a trace field saying `recorded` is now checked against its external receipt,
  rather than accepted as a self-assertion;
- per-run action ids make an old receipt distinguishable from a denied retry;
- the audit remains read-only and does not broaden oracle or claim authority.

Completed slice:

```text
src/loop_harness/
  added CoverageRuntimeArchiver and immutable archive records binding runtime
  and audit hashes, plan/approval ids, actions, and receipt ids

scripts/
  added run_coverage_runtime_cycle.py to run, audit, and archive only PASS
  local-cache coverage control paths
```

Why this slice:

- runtime traces and receipt audits now remain linked in durable history;
- a failed audit is retained only as a diagnostic and cannot silently enter the
  archive used by future loop planning;
- the archive explicitly distinguishes an auditable local cache exercise from
  experimental or biological validation.

Completed slice:

```text
src/loop_harness/
  added CoverageRuntimeArchiveSummarizer with active approvals, full-ledger
  consumption truth, archive-tail history, and latest audit failure state

scripts/
  added summarize_coverage_runtime_archives.py for non-executing hot state
```

Why this slice:

- later loop decisions can now read compact archive history without scanning an
  unbounded JSONL file;
- active/unconsumed authorization state still comes from the strict full
  consumption ledger, rather than an unsafe archive-tail approximation;
- the result is non-executing and preserves the separation between control
  evidence and scientific evidence.

Completed slice:

```text
src/loop_harness/
  extended ReviewAwarePlanningAdvisor with optional coverage-runtime hot state
  and typed advisory repair/runtime-preparation hints

scripts/
  extended suggest_next_loop_actions.py with --coverage-runtime-summary
```

Why this slice:

- the next loop can now distinguish an unconsumed human approval from a
  receipt-audit or ledger failure without inferring a scientific result;
- coverage state remains subordinate to the existing run/review planning
  contract and cannot create advice when core evidence state is unusable;
- all added hints are advisory and explicitly require human confirmation.

Completed slice:

```text
src/loop_harness/
  added CoverageRuntimePreflightEvaluator and a typed, non-authoritative
  CoverageRuntimePreflight result

scripts/
  added check_coverage_runtime_preflight.py for one proposed local-cache action

tests/
  added deferred/forged-hint, consumed-ledger, invalid-ledger, hot-state
  mismatch, and ready-state control-transition cases
```

Why this slice:

- planning can surface coverage state without giving an agent a permission
  token; the typed result distinguishes preparation from execution authority;
- the evaluator rereads full durable consumption state, so an old hot-state
  summary cannot resurrect a consumed approval;
- the runner's ledger-backed gate remains the final executor boundary even
  after a ready preflight result.

Completed slice:

```text
src/loop_harness/
  added RDKitChemFilterAdapter, declared L0 thresholds, an honest oracle
  profile, strict RDKit version matching, and provenance-bound observations

scripts/
  added emit_rdkit_chem_filter_observation.py and
  run_rdkit_chem_filter_smoke.py for fixed human-triggered local execution

tests/
  added malformed-output, timeout, version-mismatch, invalid-SMILES, and
  provenance-binding cases
```

Why this slice:

- a real molecular-computation call now crosses the same command, normalization,
  TrustGate, and audit boundaries as future scientific tools;
- version, input, configuration, command, timeout, and output are all explicit,
  while the adapter's scientific scope remains restricted to L0 triage;
- error cases remain diagnosable but cannot silently become evidence.

Completed slice:

```text
src/loop_harness/
  added DockingInputManifestVerifier with target, receptor-preparation,
  reference-ligand/box, ligand, and engine-configuration controls

scripts/
  added verify_docking_input_manifest.py as a read-only manifest boundary

tests/
  added altered-receptor, missing-ligand, invalid-box, valid-manifest, and
  invalid-JSON cases
```

Why this slice:

- docking's scientific object is now explicit before an engine can be called;
- altered structural inputs fail closed without producing a docking score or L3
  evidence;
- the boundary remains read-only, so a structurally valid manifest cannot be
  treated as launch permission or binding validation.

Completed slice:

```text
src/loop_harness/
  added DockingResultNormalizer with manifest/target/engine/subject/timestamp/
  pose binding and a default unanchored docking oracle

scripts/
  added normalize_docking_result.py to import existing result artifacts only

tests/
  added stale-manifest, post-execution verification, pose-hash mismatch, wrong-
  target, malformed-score, and unanchored-TrustGate cases
```

Why this slice:

- input integrity alone does not make an L3 binding proxy; the result must bind
  back to its verified target context and a specific pose artifact;
- a normalized docking result remains deliberately untrusted without a declared
  calibration anchor, preventing a structurally well-formed score from becoming
  a scientific claim;
- this creates a typed import boundary while no local docking engine is present.

Completed slice:

```text
src/loop_harness/
  added DockingEngineCapabilityProbe with fixed no-input version commands and
  non-scientific environment diagnostics

scripts/
  added probe_docking_engines.py for known local docking engines

tests/
  added available, unavailable, timeout, failed-version-command, and CLI cases
```

Why this slice:

- a local engine absence is now an explicit environment state rather than a
  fictitious negative molecular result;
- even a detected engine remains separated from scientific inputs, execution
  authority, and evidence admission;
- this keeps tool installation diagnosis out of the promotion and credibility
  pathways.

Completed slice:

```text
src/loop_harness/
  added DockingCalibrationAnchorVerifier and calibrated_docking_oracle for
  target/engine/scoring-bound, human-accepted validation evidence

scripts/
  added verify_docking_calibration_anchor.py and optional calibration-anchor
  application in normalize_docking_result.py

tests/
  added pose-recovery/enrichment, missing-artifact, deferred-review, target-
  mismatch, and calibrated-TrustGate cases
```

Why this slice:

- a docking proxy now has an explicit, reviewable reason to be trusted for one
  target/engine profile rather than a hidden calibration switch;
- mismatched or unreviewed anchors cannot override the unanchored abstain path;
- a matching anchor enables promotion evidence only, preserving the distinction
  between proxy support and a final scientific claim.

Completed slice:

```text
src/loop_harness/
  added DockingReadinessSummarizer with environment/input/result/calibration
  state transitions and no execution or claim authority

scripts/
  added summarize_docking_readiness.py and optional planner input support

tests/
  added unavailable-engine, unanchored-result, calibrated-proxy, binding-
  mismatch, CLI, and planner-transition cases
```

Why this slice:

- the loop policy can now distinguish "run is impossible", "result is
  untrusted", and "proxy merits promotion" from compact durable state;
- the four docking controls are joined without converting planner output into
  execution permission;
- this connects tool credibility to promotion policy rather than merely adding
  more artifact validators.

Completed slice:

```text
src/loop_harness/
  added EvidenceGapComposer using existing ClaimEvaluator and TrustGate advice
  with candidate-bound docking-readiness constraints

scripts/
  added compose_evidence_gaps.py and optional advisory-planner input support

tests/
  added unanchored/calibrated docking, independent ADMET, event-store CLI, and
  planner-advisory transition cases
```

Why this slice:

- loop state can now answer which declared evidence gap is worth addressing
  next across existing oracle kinds without treating tool availability as truth;
- the docking readiness pattern is a concrete constrained input, not a generic
  permission framework;
- candidate-level state connects credibility controls to the central promotion-
  policy question while preserving TrustGate and human boundaries.

Recommended next slice:

```text
src/loop_harness/
  add a prospective calibration-monitor that compares later admitted outcomes
  against the anchor's predeclared population/split scope and emits drift or
  non-comparability diagnostics without re-estimating thresholds online

scripts/
  add a read-only calibration-monitor command that writes a drift diagnostic,
  never a new calibration threshold or claim mutation

tests/
  add population drift, endpoint missingness, insufficient prospective labels,
  calibration-hash mismatch, and no-online-rethresholding cases
```

Why this slice:

- `EvidenceGapBudgetSelector` now chooses only composer-eligible candidates
  under a fixed scalar budget and scaffold cap, while preserving a hard
  distinction between selection and execution;
- it uses declared TrustGate promotion cost and evidence-resolution priority,
  not incomparable raw proxy scores, so its output remains a testable policy
  hypothesis rather than a hidden scientific objective;
- `EvidenceGapSelectionReplayRunner` now verifies the source evidence-gap
  content hash and recomputes the selection before applying any cache row;
- it binds the selected registered oracle and canonical cache content to an
  isolated one-shot replay, so a cache miss remains explicit rather than being
  converted into a negative molecular outcome;
- `EvidenceGapPolicyComparator` now gives a selector and existing policy the
  same initial state, selector-declared budget, and cache release, then records
  a first-class non-comparability verdict when coverage is incomplete;
- even a mechanically comparable pair stays human-interpretable only, so the
  harness cannot silently convert action-count or proxy-rate differences into a
  strategy winner;
- `EvidenceGapPolicyComparisonCurveRunner` now preserves comparator results at
  every pre-declared budget point; partial cache coverage cannot disappear into
  a curve average;
- an all-complete curve still does not establish causal or generalizable policy
  superiority when cached observations were acquired selectively;
- `AcquisitionSupportAuditor` now checks cache-hash binding, acquisition
  mechanism, target-population scope, and positive inclusion support only for
  observations actually applied by a comparison;
- complete acquisition metadata remains insufficient for automatic causal or
  policy conclusions, so the audit ends in human causal review rather than a
  synthetic policy ranking;
- `ProspectiveAcquisitionPlanner` now turns inadequate support into a bounded
  action/oracle/scaffold pool design, preserving every candidate pool hash while
  withholding individual subject selection from the agent;
- zero budget, blocked evidence gaps, unavailable oracle surface, and invalid
  audits produce explicit unresolved planning states rather than a fake repair;
- `ExternalRandomizationReceiptVerifier` now requires exact plan/pool/subject
  binding, an allowed independent source kind, and accepted human review before
  it allows preparation of a future acquisition request;
- a passing receipt remains deliberately weaker than authorization: it cannot
  call an oracle, consume budget, update campaign state, or upgrade a claim;
- `ProspectiveAcquisitionApprovalRecorder` now adds exact subject/action/oracle/
  budget/expiry human preparation approval on top of a verified randomization
  receipt;
- its read-only preflight remains deliberately weaker than executor permission,
  preserving the distinction between selection, preparation, acquisition, and
  scientific evidence;
- `ProspectiveAcquisitionConsumptionRecorder` now binds an already-recorded
  observation and TrustGate ticket to one append-only, single-use approval
  consumption receipt, including rejected tickets;
- its independent audit proves the control-chain binding only and remains
  unable to validate external scientific truth or call an adapter;
- `ProspectiveAdmetResultImporter` now verifies an already-produced external
  result against exact subject, approval, model, SMILES, timing, domain, cost,
  and raw artifact provenance before producing a raw observation draft;
- a normalized draft remains outside the campaign ledger and TrustGate, so it
  cannot silently become a new evidence event or claim;
- the next boundary is an explicitly human-triggered admission bundle that
  stages event-store update and consumption receipt without claiming a
  cross-file transaction that the local filesystem cannot provide;
- `ProspectiveAdmissionBundleBuilder` now realizes that staging boundary: it
  re-imports the source artifact, runs TrustGate, derives event-store content,
  and creates a candidate single-use consumption record under a second human
  review; its separate auditor can reproduce the complete package from source;
- the staged package never changes campaign or ledger state and labels every
  execution, autonomy, and scientific-claim field false, keeping its later
  commit/recovery transition an explicit engineering responsibility;
- `ProspectiveAdmissionCommitter` now implements that protocol using immutable
  intent/bundle-snapshot/staged-audit/candidate/receipt artifacts, a derived
  campaign file, exact ledger record comparison, a fail-closed local
  single-writer lock, and human-confirmed recovery for the two
  post-materialization interruption states;
- only an audited `PASS_COMMITTED` receipt can make the derived campaign
  eligible for later state reads; intent-only, candidate-only, and conflict
  states remain explicit control diagnostics rather than partial evidence;
- `ProspectiveAdmissionCommitResolver` now converts that convention into a
  programmatic read guard, exposing a campaign path only after replaying the
  full commit audit and rejecting every recoverable or invalid partial state;
- `ProspectiveAdmetExecutionRunner` now provides a human-triggered local
  ADMET-AI attempt boundary with intent-first storage, profile/model-manifest
  verification, provider version evidence, raw CSV/stdout/stderr hashes, and
  explicit unavailable/timeout/failure receipts; it never maps endpoints to a
  scientific metric or bypasses the existing importer and TrustGate chain;
- `ProspectiveAdmetEndpointInterpreter` now adds the reviewed scientific
  semantics layer: raw CSV row binding, units, endpoint threshold direction,
  missingness failure, pre-execution policy review, and exact replay are all
  explicit; valid threshold failures remain negative result drafts, while
  malformed interpretation emits no result;
- strict `ProspectiveAdmetResultImporter` and admission staging now bind and
  replay execution intent/receipt/profile/policy content, so a result file
  cannot claim an endpoint policy without reproducing from its raw provider
  artifacts during bundle audit;
- `AdmetCalibrationAnchorVerifier` now recomputes external-holdout endpoint
  sensitivity/specificity from exact-coverage, hash-bound labels and
  predictions, requiring a pre-execution-reviewed policy, predeclared split,
  acceptance thresholds, and post-validation human acceptance; only a passing
  anchor changes strict result provenance from `uncalibrated` to `calibrated`;
- eligibility constraints must be part of comparison, otherwise a policy can
  win on proxy score while ignoring credibility blockers.

Completed slice:

```text
src/loop_harness/
  added EvidenceGapBudgetSelector and evidence_gap_selection.v1 manifest
  with budget, declared-cost, TrustGate-priority, and scaffold constraints

scripts/
  added select_evidence_gap_budget.py, which writes a selection proposal only

tests/
  added declared-ticket-cost, heterogeneous-score, scaffold-cap, blocked-
  docking, and CLI non-execution cases
```

Completed slice:

```text
src/loop_harness/
  added EvidenceGapSelectionReplayRunner and
  evidence_gap_selection_replay.v1 with manifest recomputation, content-hash
  binding, isolated cache application, and explicit counterfactual absence

scripts/
  added replay_evidence_gap_selection.py for cache-only event-store replay

tests/
  added changed-gap, forged-selection, missing-cache, input-state-isolation,
  and CLI replay cases
```

Completed slice:

```text
src/loop_harness/
  added EvidenceGapPolicyComparator and
  evidence_gap_policy_comparison.v1 with same-state/cache/budget binding and
  no automatic policy ranking

scripts/
  added compare_evidence_gap_selection.py with explicit action-to-oracle and
  fixed-cascade configuration

tests/
  added comparable, policy-cache-missing, calibration-blocked, invalid-manifest,
  and CLI comparison cases
```

Completed slice:

```text
src/loop_harness/
  added EvidenceGapPolicyComparisonCurveRunner and
  evidence_gap_policy_comparison_curve.v1 with per-budget manifest generation,
  same-state/cache binding, and retained non-comparability reasons

scripts/
  added compare_evidence_gap_policy_curve.py for pre-declared budget grids

tests/
  added complete-curve, partial-cache-per-budget, invalid-grid, and CLI curve
  cases
```

Completed slice:

```text
src/loop_harness/
  added AcquisitionSupportAuditor and acquisition_support_audit.v1 with cache
  binding, applied-trace extraction, target-population, and inclusion-support
  checks

scripts/
  added audit_evidence_gap_acquisition_support.py as a read-only artifact audit

tests/
  added documented-support, missing-metadata, out-of-scope population,
  cache-hash mismatch, and CLI audit cases
```

Completed slice:

```text
src/loop_harness/
  added ProspectiveAcquisitionPlanner and prospective_acquisition_plan.v1 with
  stratum pools, stable pool request hashes, budget caps, and no agent subject
  selection

scripts/
  added plan_prospective_acquisition_support.py as a non-executing pool planner

tests/
  added multi-stratum budget, stable-request, documented-support, blocked-gap,
  invalid-audit, zero-budget, and CLI plan cases
```

Completed slice:

```text
src/loop_harness/
  added ExternalRandomizationReceiptVerifier and
  external_randomization_verification.v1 with plan/pool/quota/source/human-
  review binding and preparation-only semantics

scripts/
  added verify_external_randomization_receipt.py as a fail-closed verifier

tests/
  added valid receipt, pool mismatch, duplicate/out-of-pool selection,
  non-independent source, missing human acceptance, and CLI verification cases
```

Completed slice:

```text
src/loop_harness/
  added ProspectiveAcquisitionApprovalRecorder and
  ProspectiveAcquisitionPreflightEvaluator with exact receipt/plan/request/
  subject/action/oracle/budget/expiry binding and preparation-only semantics

scripts/
  added create_prospective_acquisition_approval.py and
  check_prospective_acquisition_preflight.py

tests/
  added valid preparation, expired approval, failed receipt, deferred decision,
  subject/action/budget mismatch, recorder expiry validation, and CLI flow cases
```

Completed slice:

```text
src/loop_harness/
  added ProspectiveAcquisitionConsumptionRecorder, strict ledger, and receipt
  auditor with preflight-at-consumption, actual-cost, observation, TrustGate,
  and one-use approval binding

scripts/
  added record_prospective_acquisition_consumption.py and
  audit_prospective_acquisition_consumption.py

tests/
  added admitted/rejected ticket consumption, duplicate approval, cost/ticket
  mismatch, tampered ledger, and CLI record/audit cases
```

Completed slice:

```text
src/loop_harness/
  added ProspectiveAdmetResultImporter and prospective_admet_import.v1 with
  receipt/approval/SMILES/model/domain/timestamp/raw-output provenance checks

scripts/
  added import_prospective_admet_result.py for draft-only result normalization

tests/
  added valid draft import, altered raw output, approval-time/subject mismatch,
  model/SMILES mismatch, and CLI import cases
```

Completed slice:

```text
src/loop_harness/
  extended ReviewAwarePlanningAdvisor and next_loop_plan.v1 with an optional
  ADMET prospective-drift diagnostic summary

scripts/
  extended suggest_next_loop_actions.py with --admet-drift-report

tests/
  added warning, stable, insufficient-label, forged-authority, and CLI
  advisory-only cases
```

The drift report remains a credibility diagnostic outside promotion policy. It
cannot tune thresholds online, rewrite TrustGate output, consume budget, launch
an oracle, or upgrade a scientific claim.

Completed slice:

```text
src/loop_harness/
  added ProspectiveLoopEvaluationProtocolRegistrar and independent verifier for
  a hash-bound, two-arm prospective loop-study declaration

scripts/
  added register_prospective_loop_evaluation_protocol.py and
  verify_prospective_loop_evaluation_protocol.py

tests/
  added fixed-arm, plan/population drift, post-registration retuning,
  assay-primary/metric completeness, online-adaptation rejection, and CLI cases
```

Why this slice:

- it turns "the loop is better" from a replay claim into a falsifiable
  prospective study question about candidate-promotion policy, with a fixed
  assay-level endpoint;
- it locks the comparison arms, plan, budget, analysis, and anti-bias controls
  before later outcomes are observed, while exposing any later mutation;
- verification remains deliberately weaker than evidence admission, execution,
  policy ranking, or scientific conclusion.

Recommended next slice:

```text
src/loop_harness/
  bind each admitted prospective outcome and its randomized arm assignment to a
  passing frozen protocol, then calculate only the predeclared per-arm metrics
  and effect-size interval

tests/
  add wrong-arm, unregistered-outcome, incomplete-randomized-subject, outcome
  timing, and analysis-hash drift cases
```

Completed slice:

```text
src/loop_harness/
  added ProspectiveLoopEvaluationArmAssignmentVerifier for external,
  pre-outcome allocation binding

scripts/
  added verify_prospective_loop_evaluation_arm_assignment.py

tests/
  added valid external allocation, selected-subject/approval mismatch,
  observed-outcome declaration, arm-completeness, forged-authority, and CLI
  cases
```

Why this slice:

- it makes the randomization and allocation boundary explicit before an outcome
  can be attached to either policy arm;
- it joins the new study protocol to the existing random-selection and
  single-subject human-approval controls instead of creating a parallel trust
  path;
- its pass remains non-executing and non-analytical, so it cannot be used to
  manufacture a strategy winner from a balanced-looking allocation alone.

Completed slice:

```text
src/loop_harness/
  added ProspectiveLoopEvaluationOutcomeReportVerifier for hash-bound outcome
  completeness, evidence-to-assignment validation, frozen assay-policy
  provenance validation, and committed-admission evidence binding

scripts/
  added verify_prospective_loop_evaluation_outcome_report.py

tests/
  added admitted endpoint, visible missingness, endpoint and assay-policy
  provenance tampering,
  assignment completeness, wrong-subject receipt, post-assignment timing,
  hidden-valid-result, and CLI cases
```

Why this slice:

- it closes the evidentiary chain from independent assignment through the
  consumption ledger, campaign observation, TrustGate ticket, frozen assay
  endpoint policy, assay run/site/QC provenance, and retained committed-admission
  evidence, without treating a raw observation as a study outcome;
- it makes selective outcome reporting machine-detectable: every randomized
  episode is classified exactly once and known admissible outcomes cannot be
  relabelled as missing or unresolved;
- it remains non-analytical and non-executing, so this verification cannot
  manufacture a strategy winner or a drug-discovery claim.

Recommended next slice:

```text
src/loop_harness/
  calculate only the protocol-declared per-arm endpoint estimand and
  Newcombe-Wilson interval from a passing outcome verification, with all
  missingness and unresolved outcomes carried forward visibly

tests/
  add analysis input/hash drift, missingness denominator, interval edge-case,
  and prohibition-on-policy-ranking cases
```

Completed slice:

```text
src/loop_harness/
  added ProspectiveLoopEvaluationAnalyzer for the sole predeclared two-arm
  primary endpoint estimand and Newcombe-Wilson interval

scripts/
  added analyze_prospective_loop_evaluation.py

tests/
  added complete-endpoint, zero-event arm, incomplete-outcome, post-verification
  report drift, and CLI cases
```

Why this slice:

- it turns a valid outcome report into a reproducible effect estimate without
  adding a general statistics engine or a policy optimizer;
- it refuses complete-case substitution when outcome visibility is incomplete,
  preserving missingness as a first-class credibility result;
- it fixes the effect direction and interval construction in code while keeping
  the artifact non-decisional and non-autonomous.

Follow-up correction:

- the earlier protocol-registration correction established
  `prospective_loop_evaluation_protocol.v2` with the assay-validated rate as
  its primary endpoint:
  the primary endpoint is the assay-validated rate per randomized promotion
  episode, which matches the Newcombe-Wilson analysis; cost efficiency remains
  a secondary outcome;
- the frozen shared budget now includes an equal per-arm experimental cap, and
  outcome verification reports and enforces actual experimental spend by arm.

Recommended next slice:

```text
research protocol and pilot data
  define the minimum externally replicated prospective study size, assay
  replication rule, and human-reviewed interpretation report needed before any
  comparative policy claim is considered
```

Research-design correction completed:

- external arm assignment alone establishes allocation but not that the frozen
  promotion policy was the intervention that selected the later assay action;
- `ProspectivePolicyDecisionFidelityVerifier` now replays a hash-bound,
  pre-outcome decision receipt for implemented `fixed_cascade` and
  `trust_gap_aware` policy semantics, rejecting any declared runtime parameter
  that the harness cannot execute;
- `ProspectivePolicyDecisionReceiptRecorder` now derives that receipt only
  from the frozen protocol, exact external arm assignment, its passing
  independent verification, and one rebuildable pre-outcome campaign state;
  recorder and verifier bind the same assignment-verification hash and neither
  can execute an action;
- each registered arm now carries a content-hashed local policy-runtime
  manifest; protocol, receipt, replay, and outcome verification reject runtime
  source drift instead of treating changed implementation semantics as the
  original experimental intervention;
- policy receipts now enforce a decision-state time cutoff and isolate assay
  outcomes for every subject in the current external assignment set, while
  retaining unassigned historical evidence as lawful loop context; this blocks
  post-hoc event insertion and cross-arm outcome feedback from contaminating
  treatment-fidelity replay;
- independent arm-assignment verification is now temporally bound between the
  external assignment and the policy decision, so a verification issued after
  intervention cannot retrospectively establish allocation order;
- arm-assignment verification itself now rejects a `verified_at` that predates
  `assigned_at`, making the lower bound an invariant of the source verifier
  rather than only a downstream policy-receipt control;
- final outcome verification now receives the original randomization and
  approval control artifacts, recomputes arm-assignment verification at its
  persisted timestamp, and rejects a standalone `PASS` artifact that does not
  reproduce from those inputs;
- final outcome verification now also has its own audit timestamp and requires
  it to follow the outcome report plus every consumed assay result, preventing
  an earlier assignment-control timestamp from being reused as a future-result
  audit;
- predeclared analysis now requires `analyzed_at` to follow the passing outcome
  verification, and interpretation readiness requires `evaluated_at` to follow
  both the analysis and the a priori power plan; this keeps the comparative
  evidence chain temporally non-retroactive through the read-only conclusion
  boundary;
- protocol registration now uses `prospective_loop_evaluation_protocol.v3` and
  requires an exact `prospective_loop_evaluation_power_plan.v2`: target
  population, assay endpoint definition, per-arm budget, human acceptance, and
  planning time must agree and precede registration. The same power-plan hash
  now flows through assignment, outcome verification, and interpretation
  readiness, rejecting substitution by a later or merely plausible plan;
- protocol registration now uses `prospective_loop_evaluation_protocol.v4` and
  power plans use `prospective_loop_evaluation_power_plan.v3`: the planned
  assay site is frozen with the endpoint and outcome verification rejects an
  admitted assay result produced at another site;
- `ProspectiveLoopEvaluationReplicationDesignVerifier` now makes the required
  independent replication concrete before source allocation. It verifies a
  human-accepted second protocol for the source plan's named replication cohort,
  different planned assay site, equivalent fixed policy intervention, matching
  primary endpoint, and pre-assignment timing, while remaining strictly
  non-executing and non-decisional;
- `ProspectiveLoopEvaluationReplicationEvidenceVerifier` now joins completed
  source and replication analyses only after each binds a passing complete
  outcome verification, its own protocol/power-plan/assignment chain, and the
  same verified predeclared replication design. Direction concordance remains a
  human-review fact; it never becomes a pooled estimate, policy winner, or
  scientific-claim upgrade;
- protocol registration now uses `prospective_loop_evaluation_protocol.v5` and
  consumes only a power plan that passes independent arithmetic verification.
  `ProspectiveLoopEvaluationPowerPlanVerifier` recomputes required episodes,
  assumed rate, derived budgets, feasibility, and authority controls, so a
  plan cannot be made registration-ready by editing derived fields;
- the outcome regression suite now carries actual fixed-cascade and
  trust-gap-aware pre-outcome states through receipt recording and independent
  fidelity-set construction to consumed `send_to_experiment` outcome gates.
  Assay admission/commit remains independently integration-tested rather than
  being replaced by a synthetic policy-fidelity fixture;
- outcome verification now requires exactly one passing fidelity receipt per
  consumed randomized episode, with assignment/arm/subject binding and a
  selected action matching the consumed experimental action; comparative
  analysis therefore fails closed when the policy intervention is unproven.

Completed slice:

```text
src/loop_harness/
  added ProspectiveLoopEvaluationPowerPlanner for a human-accepted a priori
  two-independent-proportions sample-size and budget-feasibility plan

scripts/
  added plan_prospective_loop_evaluation_power.py

tests/
  added ready, underpowered, infeasible-budget, replication-control, and CLI
  cases
```

Why this slice:

- it makes baseline rate, minimum meaningful effect, alpha, target power, and
  replication assumptions explicit before a future protocol is constructed;
- it prevents a small, convenient batch from being misrepresented as a
  sufficiently powered comparative study;
- it remains a planning advisory rather than a protocol registrar, result
  analyzer, execution tool, or scientific conclusion.

Completed slice:

```text
src/loop_harness/
  added ProspectiveLoopEvaluationInterpretationReadinessGate to separate an
  estimable first study from one that is sufficiently complete for human
  replication review

scripts/
  added check_prospective_loop_interpretation_readiness.py

tests/
  added insufficient-episode, hash-drift, and CLI cases
```

Why this slice:

- it prevents a visible effect estimate from being mistaken for enough evidence
  to interpret a policy comparison;
- it binds power assumptions, completed randomized count, frozen budget, and
  source hashes into a single non-decisional readiness artifact;
- it still leaves independent replication and human review as explicit future
  scientific work.

Completed slice:

```text
src/loop_harness/
  added ProspectiveAssayResultImporter for external wet-lab receipt to
  policy-bound Observation draft normalization

scripts/
  added import_prospective_assay_result.py

tests/
  added positive endpoint, valid negative endpoint, QC/unit failure,
  raw-output/approval tampering, and CLI cases
```

Why this slice:

- it closes the gap between an external assay result and the existing TrustGate
  / prospective-consumption controls without letting laboratory output define
  the study endpoint itself;
- it preserves a scientifically valid negative assay result as admissible
  evidence while rejecting measurement-quality failures;
- it remains a draft-only importer, so future external evidence still passes
  through human-controlled event admission and TrustGate.

Completed slice:

```text
src/loop_harness/
  added ProspectiveAssayAdmissionBundleBuilder and auditor adapters over the
  shared staged admission / recoverable commit state machine

scripts/
  added stage_prospective_assay_admission_bundle.py and
  audit_prospective_assay_admission_bundle.py

tests/
  added staged observation/ticket/consumption replay and CLI audit cases
```

Why this slice:

- it carries a real assay receipt from a policy-bound draft into the same
  human-confirmed, append-only evidence path used by the rest of the harness;
- it avoids duplicating a second transactional protocol for assay evidence;
- it leaves final durable commit, policy evaluation, and scientific
  interpretation separately controlled.
