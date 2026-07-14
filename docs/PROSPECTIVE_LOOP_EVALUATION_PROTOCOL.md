# Prospective Loop Evaluation Protocol

This protocol freezes a falsifiable comparison between a fixed baseline loop
policy and a fixed credibility-aware loop policy before prospective assay
outcomes are observed. It is designed to test a narrow question:

> Under the same declared compute, experiment, and human-review budget, does a
> credibility-aware promotion policy increase the prospectively assay-validated
> rate per randomized promotion episode, with acceptable uncertainty and
> failure visibility?

It is a research-design control, not a new oracle, executor, promotion token,
or scientific-claim mechanism.

The current registration schema is
`prospective_loop_evaluation_protocol.v5`. Earlier `v1` through `v3`
declarations did not bind a planned assay site through to admitted outcome
provenance; `v4` declarations did not independently recompute power-plan
arithmetic. They must be re-registered rather than silently reused. Earlier
`v1` and `v2` declarations also did not bind an exact prospective power plan
to the registered study. Earlier `v1` declarations used a
budget-denominated primary outcome that is not compatible with the fixed
Newcombe-Wilson proportion interval; they must be re-registered under the
explicit randomized-episode rate definition rather than silently reused.

## What Is Frozen

Registration binds the declaration to the exact content hash of one
`prospective_acquisition_plan.v1` artifact. The plan must prohibit agent
subject selection and require independent external randomization plus human
approval for every request.

The causal unit is one `candidate_promotion_episode` from fixed candidate
pools, not an entire discovery campaign. The protocol therefore studies only
the promotion-policy decision given the same pre-randomized candidate context;
it does not support a claim that one generator, target hypothesis, or complete
drug-discovery system is better.

The registrar requires:

- exactly two distinct arms with roles `baseline` and `credibility_aware`;
- an explicit candidate-promotion-only study scope, with independent allocation
  from fixed candidate pools;
- a content hash for each fixed `policy_spec`, which must instantiate under the
  same deterministic runtime contract used by treatment-fidelity replay;
- a content-hashed local policy-runtime manifest for each arm, so later replay
  fails closed if the implementation, contract, or runtime semantics drift;
- one shared declared-cost budget plus an equal, fixed experiment-budget cap
  for each arm;
- an assay-validated primary outcome,
  `prospective_assay_validated_candidate_rate`, with randomized promotion
  episodes as its denominator, an exact boolean observation metric key, and a
  positive value;
- predeclared credibility metrics: prospective promotion precision, cost per
  assay-validated candidate, proxy reversal rate, and unresolved credibility
  rate;
- a Newcombe-Wilson 95% interval for the primary effect, explicit missing-
  outcome reporting, and exploratory treatment of secondary metrics;
- no online retuning, policy-parameter updates, arm switching, or
  outcome-dependent early stopping;
- independent arm assignment before outcomes, blinded outcome assessment, and
  reporting of every randomized subject; and
- human acceptance before registration.

The generated protocol forcibly sets policy ranking, execution authority,
autonomous execution, and scientific-claim upgrade fields to `false`.

## Power And Replication Planning

Before constructing a new protocol, create an a priori power-plan artifact from
`examples/prospective_loop_evaluation_power_plan.template.json`:

```powershell
python E:\11\drug-loop-harness\scripts\plan_prospective_loop_evaluation_power.py E:\path\to\power-plan-declaration.json --output E:\11\drug-loop-harness\state\prospective-loop-evaluation-power-plan.json
```

The planner uses the declared baseline assay-positive rate, minimum meaningful
absolute rate difference, two-sided alpha, and target power to calculate the
per-arm episode requirement under a transparent normal approximation for two
independent proportions. It also checks the declared per-arm assay budget.
This is an a priori planning approximation, not a post-hoc observed-power
calculation and not an inferential result.

The declaration must identify the target population and exact primary endpoint
(assay oracle, metric key, positive value, and randomized-episode denominator),
the planned assay site, and require a separately identified replication cohort, independent
assay site, external arm randomization, and human acceptance. A
`READY_FOR_PROTOCOL_REGISTRATION` result means only that the stated sample and
budget are internally feasible. It grants no protocol-registration authority,
execution authority, policy ranking, or scientific claim. The resulting plan
must still be reviewed while constructing a new hash-bound protocol and
acquisition plan. Its content hash, ID, target population, primary endpoint,
planned assay site, per-arm budget, and pre-registration review time are frozen
into that protocol. Each later admitted assay result must report the exact same
site in its provenance. Before registration, the harness independently
recomputes the plan's sample-size requirement, derived budgets, assumed rate,
and readiness status; a structurally plausible but arithmetically altered plan
cannot be used as a protocol input.

Audit a generated plan directly when transferring it between systems:

```powershell
python E:\11\drug-loop-harness\scripts\verify_prospective_loop_evaluation_power_plan.py E:\11\drug-loop-harness\state\prospective-loop-evaluation-power-plan.json --output E:\11\drug-loop-harness\state\prospective-loop-evaluation-power-plan-verification.json
```

## Register And Verify

Start from
`examples/prospective_loop_evaluation_declaration.template.json`, review it,
and register it against an already-created prospective acquisition plan:

```powershell
python E:\11\drug-loop-harness\scripts\register_prospective_loop_evaluation_protocol.py E:\path\to\evaluation-declaration.json --prospective-acquisition-plan E:\11\drug-loop-harness\state\prospective-acquisition-plan.json --power-plan E:\11\drug-loop-harness\state\prospective-loop-evaluation-power-plan.json --output E:\11\drug-loop-harness\state\prospective-loop-evaluation-protocol.json
```

Before consuming later outcomes, or whenever artifacts are transferred, verify
the frozen protocol against the exact same acquisition-plan content:

```powershell
python E:\11\drug-loop-harness\scripts\verify_prospective_loop_evaluation_protocol.py E:\11\drug-loop-harness\state\prospective-loop-evaluation-protocol.json --prospective-acquisition-plan E:\11\drug-loop-harness\state\prospective-acquisition-plan.json --power-plan E:\11\drug-loop-harness\state\prospective-loop-evaluation-power-plan.json --output E:\11\drug-loop-harness\state\prospective-loop-evaluation-verification.json
```

The verifier fails closed when the plan population or hash changes, an arm's
policy specification is changed after registration, a required credibility
metric is removed, the primary outcome is weakened to a proxy, or any
adaptation/bias/authority control is altered.

## Arm Assignment Control

After protocol registration and before any prospective outcome is observed,
an independent external service or witnessed procedure must produce an
`prospective_loop_evaluation_arm_assignment.v1` artifact. The harness does not
create this artifact. It must bind every assignment to:

- the frozen protocol and prospective-plan hashes;
- one accepted, unexpired single-subject prospective approval;
- that approval's exact passing external-randomization verification;
- an assay request in the plan;
- one protocol arm; and
- a durable independent arm-randomization source and allocation algorithm.

The artifact declares that outcomes were not observed at assignment, outcome
assessment is blinded to the arm, and no assignment is used twice. Both arms
must be represented, with allocation imbalance no greater than one subject.

Verify it with every source randomization verification and approval that it
uses:

```powershell
python E:\11\drug-loop-harness\scripts\verify_prospective_loop_evaluation_arm_assignment.py E:\11\drug-loop-harness\state\prospective-loop-evaluation-protocol.json --prospective-acquisition-plan E:\11\drug-loop-harness\state\prospective-acquisition-plan.json --power-plan E:\11\drug-loop-harness\state\prospective-loop-evaluation-power-plan.json --assignment E:\path\to\external-arm-assignment.json --randomization-verification E:\path\to\randomization-verification-a.json --randomization-verification E:\path\to\randomization-verification-b.json --approval E:\path\to\prospective-approval-a.json --approval E:\path\to\prospective-approval-b.json --output E:\11\drug-loop-harness\state\prospective-loop-arm-assignment-verification.json
```

A pass still has `comparison_analysis_permitted=false`: it validates only that
the comparison was allocated before outcomes under the declared controls. It
does not authorize acquisition, create a result, select a policy winner, or
upgrade a claim. Its `verified_at` must be no earlier than the assignment's
`assigned_at`; an apparent verification that predates the allocation fails.

## Outcome Report Control

After externally produced assay results have entered the append-only campaign
store and passed through TrustGate, prepare one
`prospective_loop_evaluation_outcome_report.v1` artifact for the frozen
assignment set. This is a report of outcome availability, not an analysis.

For external wet-lab receipts, use the policy-bound import path in
`docs/PROSPECTIVE_ASSAY_IMPORT.md` before human-controlled event admission.
It normalizes endpoint value, unit, QC, controls, raw-output hash, and approval
binding into an observation draft; it does not bypass TrustGate or create an
outcome directly.

The report binds its own content to the exact hashes of the protocol, plan,
verified assignment, frozen assay endpoint policy, campaign event store, and
prospective-consumption ledger, and committed-admission evidence set. The
verifier also receives that exact endpoint policy and requires every admitted
or unresolved assay observation to carry its policy hash plus a matching assay
oracle, assay-protocol identifier, run ID, site ID, and passing QC provenance.
For every consumed outcome, exactly one committed admission directory must bind
its bundle snapshot, staged replay audit, import, derived campaign, consumption
draft, and final receipt to the same ledger observation and ticket. A boolean
endpoint alone, or a manually written campaign observation, is not evidence.
The report's `source_admission_commit_evidence_content_sha256` is the
`source_evidence_content_sha256` emitted by the evidence-set command for those
same commit directories.
For every consumed episode, the report also binds a matching replayed policy
decision receipt. Build the receipt set as documented in
`docs/PROSPECTIVE_POLICY_FIDELITY.md` and copy its
`source_fidelity_content_sha256` into
`source_policy_fidelity_content_sha256` before outcome verification.
Every randomized assignment must appear exactly once as one of:

- `admitted_assay_outcome`: one matching prospective-consumption receipt,
  assay observation, TrustGate ticket, and boolean primary endpoint exist;
- `invalid_or_unresolved_assay_outcome`: a receipt exists but its evidence is
  not admissible or the endpoint is unresolved, with a human-readable reason;
- `missing_outcome`: no matching consumption receipt exists, with a reason.

A missing outcome is reported separately and is never silently converted to a
negative endpoint. Conversely, an admissible boolean assay result cannot be
relabelled unresolved or missing. The verifier also rejects reused/duplicate
consumption receipts, a receipt that predates the pre-outcome assignment,
receipt-to-observation or TrustGate mismatch, endpoint-value substitution, and
any authority field set to `true`. It also reports actual experimental spend by
arm and rejects a result report whose declared per-arm or total experiment
budget is exceeded.

Verify the report against its complete input chain:

```powershell
python E:\11\drug-loop-harness\scripts\verify_prospective_loop_evaluation_outcome_report.py E:\11\drug-loop-harness\state\prospective-loop-evaluation-protocol.json --prospective-acquisition-plan E:\11\drug-loop-harness\state\prospective-acquisition-plan.json --power-plan E:\11\drug-loop-harness\state\prospective-loop-evaluation-power-plan.json --assignment E:\path\to\external-arm-assignment.json --assignment-verification E:\11\drug-loop-harness\state\prospective-loop-arm-assignment-verification.json --randomization-verification E:\path\to\randomization-verification-a.json --randomization-verification E:\path\to\randomization-verification-b.json --approval E:\path\to\prospective-approval-a.json --approval E:\path\to\prospective-approval-b.json --assay-endpoint-policy E:\path\to\assay-endpoint-policy.json --outcome-report E:\path\to\prospective-outcome-report.json --consumption-ledger E:\path\to\prospective-consumptions.jsonl --campaign-store E:\path\to\campaign.json --admission-commit-directory E:\11\drug-loop-harness\state\prospective-admission-commits\commit_assay_001 --admission-commit-directory E:\11\drug-loop-harness\state\prospective-admission-commits\commit_assay_002 --policy-decision-receipt E:\path\to\policy-decision-receipt-baseline.json --policy-decision-campaign-store E:\path\to\pre-outcome-campaign-baseline.json --policy-decision-receipt E:\path\to\policy-decision-receipt-credibility.json --policy-decision-campaign-store E:\path\to\pre-outcome-campaign-credibility.json --output E:\11\drug-loop-harness\state\prospective-loop-outcome-verification.json
```

A pass means only that every reported status is ledger- and evidence-bound.
The outcome verifier also recomputes the external arm-assignment verification
from the supplied randomization-verification and approval artifacts at the
persisted verification timestamp; a standalone `PASS` JSON cannot substitute
for this raw control chain. Its own `verified_at` must be no earlier than the
outcome report and every consumed assay result; this final audit time is
separate from the earlier assignment-verification time.
It deliberately leaves `predeclared_analysis_computed=false`, policy ranking
disabled, all execution authority disabled, and scientific-claim upgrade
disabled. Computing the registered effect estimate and interval is a separate,
later analysis boundary.

## Predeclared Analysis

`analyze_prospective_loop_evaluation.py` is that separate, deterministic
analysis boundary. It accepts only an exact outcome report and a passing
outcome verification whose hash binds that report, protocol, and assignment.
It rechecks the registered primary-analysis method, interval method, and
missing-outcome rule before producing an artifact. Its `analyzed_at` must be
at or after the outcome verifier's `verified_at`; an analysis cannot be
backdated ahead of the evidence audit it consumes.

The only implemented estimand is the predeclared difference in assay-validated
candidate rates per randomized promotion episode:

```text
credibility_aware arm rate - baseline arm rate
```

It reports each arm's randomized count, admitted endpoint count, positive
endpoint count, Wilson 95% bounds, and a Newcombe-Wilson 95% interval for that
rate difference. The frozen per-arm experimental cost is audited separately;
cost per assay-validated candidate remains a secondary metric. It performs no
p-value search, secondary-metric analysis,
subgroup analysis, stopping decision, policy ranking, or claim upgrade.

Most importantly, an outcome report with any missing or unresolved episode
returns `INCOMPLETE_OUTCOMES` and no risk difference or interval. This is not a
complete-case analysis: missingness remains visible and blocks the primary
comparison until the protocol is amended through a separate controlled path.

```powershell
python E:\11\drug-loop-harness\scripts\analyze_prospective_loop_evaluation.py E:\11\drug-loop-harness\state\prospective-loop-evaluation-protocol.json --assignment E:\path\to\external-arm-assignment.json --outcome-report E:\path\to\prospective-outcome-report.json --outcome-verification E:\11\drug-loop-harness\state\prospective-loop-outcome-verification.json --output E:\11\drug-loop-harness\state\prospective-loop-evaluation-analysis.json
```

## Interpretation Readiness

An estimated interval is not enough to interpret a policy comparison. Check it
against the human-accepted power plan before any human replication review:

```powershell
python E:\11\drug-loop-harness\scripts\check_prospective_loop_interpretation_readiness.py E:\11\drug-loop-harness\state\prospective-loop-evaluation-protocol.json --assignment E:\path\to\external-arm-assignment.json --analysis E:\11\drug-loop-harness\state\prospective-loop-evaluation-analysis.json --power-plan E:\11\drug-loop-harness\state\prospective-loop-evaluation-power-plan.json --output E:\11\drug-loop-harness\state\prospective-loop-interpretation-readiness.json
```

The gate hash-binds the protocol, assignment, analysis, and power plan;
rechecks equal per-arm experiment budget; and compares actual randomized counts
to the planned a priori requirement. Its `evaluated_at` must be at or after
both the analysis `analyzed_at` and power plan `planned_at`, so a readiness
artifact cannot precede either input. A structurally valid but small study
returns `INSUFFICIENT_COMPLETED_EPISODES`, not a comparative interpretation.
Only a sufficiently completed first study reaches
`READY_FOR_HUMAN_REPLICATION_REVIEW`, which still means that independent
replication and human review are outstanding. The gate never selects a policy
winner, permits policy interpretation, or upgrades a scientific claim.

## Predeclared Independent Replication

An intent to replicate is insufficient if the replication protocol is chosen
after the first allocation or after early results become visible. Before the
source study's first arm assignment, register a second `v4` protocol for the
cohort named by the source power plan's `replication_cohort_id`, using a
different predeclared assay site. It must preserve the same policy arm runtime
hashes and primary endpoint definition.

Create a reviewed declaration from
`examples/prospective_loop_evaluation_replication_design.template.json`, then
verify the complete raw chain:

```powershell
python E:\11\drug-loop-harness\scripts\verify_prospective_loop_evaluation_replication_design.py --source-protocol E:\path\to\source-protocol.json --source-acquisition-plan E:\path\to\source-plan.json --source-power-plan E:\path\to\source-power-plan.json --source-assignment E:\path\to\source-assignment.json --replication-protocol E:\path\to\replication-protocol.json --replication-acquisition-plan E:\path\to\replication-plan.json --replication-power-plan E:\path\to\replication-power-plan.json --declaration E:\path\to\replication-design-declaration.json --output E:\11\drug-loop-harness\state\prospective-loop-replication-design-verification.json
```

A `PREDECLARED_INDEPENDENT_REPLICATION_DESIGN` pass proves only the design
binding: distinct cohort IDs and planned sites, matching fixed policy arms and
primary endpoint, and pre-assignment registration. It does not execute either
study, establish actual assay results, authorize a policy interpretation, or
upgrade a scientific claim.

## Completed Replication Evidence

After both studies independently reach complete, verified outcomes and their
separate deterministic analyses, join them to the passing design verification:

```powershell
python E:\11\drug-loop-harness\scripts\verify_prospective_loop_evaluation_replication_evidence.py --source-protocol E:\path\to\source-protocol.json --source-power-plan E:\path\to\source-power-plan.json --source-assignment E:\path\to\source-assignment.json --source-outcome-verification E:\path\to\source-outcome-verification.json --source-analysis E:\path\to\source-analysis.json --replication-protocol E:\path\to\replication-protocol.json --replication-power-plan E:\path\to\replication-power-plan.json --replication-assignment E:\path\to\replication-assignment.json --replication-outcome-verification E:\path\to\replication-outcome-verification.json --replication-analysis E:\path\to\replication-analysis.json --replication-design-verification E:\path\to\replication-design-verification.json --output E:\11\drug-loop-harness\state\prospective-loop-replication-evidence.json
```

The verifier requires each analysis to bind its own complete passing outcome
verification, protocol, power plan, and assignment, and requires the design
verification to bind the same source and replication protocols. It exposes only
whether the two predeclared primary effects have the same nonzero direction or
an unresolved/opposite direction. Both statuses require human interpretation;
neither selects a policy winner, pools studies, or upgrades a scientific claim.

## Human Replication Interpretation

Completed replication evidence still has no automatic scientific conclusion.
A named reviewer may record only a conclusion compatible with the verified
direction relation: concordant nonzero directions may recommend additional
research; opposite directions require investigation; zero or undetermined
directions remain inconclusive. None of these statements ranks policies,
permits execution, or upgrades a scientific claim.

Start from
`examples/prospective_loop_evaluation_replication_interpretation.template.json`:

```powershell
python E:\11\drug-loop-harness\scripts\record_prospective_loop_evaluation_replication_interpretation.py E:\11\drug-loop-harness\state\prospective-loop-replication-evidence.json E:\path\to\replication-interpretation-declaration.json --output E:\11\drug-loop-harness\state\prospective-loop-replication-interpretation.json
python E:\11\drug-loop-harness\scripts\verify_prospective_loop_evaluation_replication_interpretation.py E:\11\drug-loop-harness\state\prospective-loop-replication-interpretation.json E:\11\drug-loop-harness\state\prospective-loop-replication-evidence.json E:\path\to\replication-interpretation-declaration.json --output E:\11\drug-loop-harness\state\prospective-loop-replication-interpretation-verification.json
```

The verifier rebuilds the record from the exact evidence and declaration,
binds both content hashes, and rejects a verification timestamp before review.
This is an accountable human follow-up record, not a path around prospective
registration, independent replication, assay evidence, or TrustGate.

## Interpretation Boundary

A `PASS` means only that the supplied protocol and plan remain structurally
consistent with the frozen evaluation design. It does not establish that either
policy is better, that external randomization occurred, that a result was
measured correctly, or that a molecule is effective or safe.

Actual future outcome evidence still needs the existing external-randomization,
human-approval, prospective-consumption, result-import, TrustGate, admission,
and commit controls. The verified outcome report and deterministic analysis now
bind evidence to the frozen arm assignment and calculate the fixed primary
estimand without permitting online policy updates. Any broader interpretation,
replication, or decision remains a separate human-reviewed research step; the
interpretation-readiness gate makes sample-size insufficiency explicit before
that step.
