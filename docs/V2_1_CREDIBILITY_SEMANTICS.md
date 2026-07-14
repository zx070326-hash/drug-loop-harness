# V2.1 Credibility Semantics

## Purpose

V2.1 changes the scientific semantics of the online loop without changing its
single-loop, event-sourced runtime boundary. The action is now explicitly:

`(candidate, oracle, endpoint, scientific_question)`

The change prevents an unfavorable but valid observation from being treated as
an invalid measurement and prevents a conventional screening funnel from
silently becoming an execution constraint.

## Three Separate Decisions

| Question | Component | Outcome |
| --- | --- | --- |
| May this action execute? | `DecisionGate` | budget, exact approval, allowed oracle, execution prerequisites |
| Is this result a valid measurement? | `EvidenceGate` | qualified, rejected, abstain, conflicted |
| Is the measured value favorable? | `ScientificOutcome` | favorable, unfavorable, continuous, unspecified |

`EvidenceGate` does not inspect `passed` or a label to decide measurement
validity. A QC-valid negative assay result is qualified evidence and may update
beliefs. Failed execution, incomplete provenance, missing anchors, and empty
metrics remain non-qualifying observations.

`ClaimGate` remains read-only. Evidence sufficiency does not by itself make a
positive biological claim.

## Oracle Dependency Types

`OracleSpec` has three dependency fields with distinct meanings:

| Field | Runtime effect |
| --- | --- |
| `execution_prerequisites` | Hard gate. The action is absent until the prerequisite measurement is qualified. Use only for physical or technical requirements such as synthesis before assay. |
| `promotion_predecessors` | Soft policy context. The action remains legal; policies may prefer it after lower-cost information is available. |
| `prerequisites` | Legacy v2 compatibility field. Existing ledgers interpret it as a hard execution prerequisite. New contracts must use the explicit field. |

This allows a multifidelity policy to call a high-fidelity assay directly when
its expected decision value warrants the cost, as in the Jensen MF-BO setting.

## State Model

`CampaignSpec.endpoints` declares the target product profile endpoints. A
candidate's scientific state is stored as `BeliefState(candidate_id,
endpoint_id)`. The old `snapshot.beliefs[candidate_id]` view is retained only
for the primary endpoint; new policy code must call `snapshot.belief_for`.

The state additionally tracks two different reliability dimensions:

- `failure_models[(route_id, oracle_id)]`: probability an action cannot
  execute successfully.
- `reliability_models[(oracle_id, endpoint_id)]`: probability an attempted
  oracle returns a qualified measurement, using a Beta(1,1) posterior.

The baseline policy discounts expected utility by measurement-validity
probability. It is still a transparent heuristic, not a calibrated knowledge
gradient or a scientific result.

## Event Invariants

Every v2.1 result must have all of the following append-only events:

1. `oracle_result_recorded`
2. `failure_model_updated`
3. `evidence_assessed`
4. `oracle_reliability_updated`
5. zero or more `belief_updated`

`TraceEvaluator` enforces this only for campaigns whose `schema_version` is
2.1 or later. Missing `schema_version` is interpreted as v2.0 so old ledgers
remain readable and can be audited under their original invariant set.

## Jensen MF-BO Reference Fixture

`JensenMFBOFixtureLoader` creates a hash-bound simulated-online environment
from the published three-fidelity ChEMBL tables of McDonald et al. It exposes
three legal measurements per candidate:

- `docking`, relative cost 0.01
- `single_point`, relative cost 0.2
- `dose_response`, relative cost 1.0

All values remain executor-owned until the exact action is admitted. The loader
does not implement the authors' BayBE/Tanimoto-GP/TVR algorithm. It provides a
stable environment in which that baseline and future policies can be compared
without leaking high-fidelity values.

The repository currently supplies the five retrospective targets reported in
the publication: Factor-D, CXCR4, HIF-PH, PARP1, and NR1A2.

Run the bounded environment with:

```powershell
$env:PYTHONPATH='src'
python scripts\run_jensen_mfbo_fixture_smoke.py --target-id FACTOR_D --candidate-limit 32 --steps 12 --policy jensen_tvr
```

The runner records `original_tvr_reproduced: false` in its report, so its
outputs cannot be mislabeled as an original-policy reproduction.

## Fixed-Start Policy Comparison

`JensenPolicyComparisonRunner` creates one new ledger per policy arm, executes
the same random-seed warm-start actions in every arm, then runs the requested
continuation policy. `JensenTruthEvaluator` is called only after each arm has
stopped. It verifies the source archive again and calculates high-fidelity
top-fraction recovery, simple regret, cost spent, and budget to the first
top-fraction candidate without mutating the ledger.

```powershell
$env:PYTHONPATH='src'
python scripts\run_jensen_policy_comparison.py --target-id FACTOR_D --candidate-limit 64
```

One target, one cohort, and one seed are an engineering smoke only. The result
file explicitly prohibits a statistical conclusion; target-level repeated
cohorts and a predeclared analysis plan remain required.

The comparison runner defaults to continuing each arm until it reaches the
shared budget boundary or another terminal state. Supplying
`--continuation-steps` produces an action-capped trace and writes
`fixed_budget_comparison_permitted: false`.

`fixed_budget_comparison_permitted` is true only when the action cap is absent
and the arm stops because its budget is exhausted, no legal actions remain, or
every remaining proposal exceeds the residual budget. A `max_rounds_reached`,
approval, or recovery stop remains an invalid comparison run. The common
warm-start cost is preflighted before an arm writes any action event.

For the predeclared multi-target, multi-window protocol and its analysis
boundary, see [Jensen Simulated-Online Benchmark Protocol](JENSEN_BENCHMARK_PROTOCOL.md).

## Deferred Work

- `JensenStyleTVRPolicy` ports the published decision structure (high-fidelity
  EI target plus TVR/cost) with a dependency-light NumPy ICM-Tanimoto GP. It is
  an experimental reference, not a numerical reproduction of the authors'
  BayBE/BoTorch fitting, Monte Carlo batching, or 25% warm-start protocol.
- The artifact-backed continuous ICM/Tanimoto posterior path is now available
  through `ContinuousPosteriorFitter` and can be consumed by TVR. Full
  calibration curves, independent model-family ablations, and exact
  BayBE/BoTorch reproduction remain pending.
- Candidate-dependent cost, time, drift, and scaffold-local reliability are
  planned stressors after the reference baseline is reproducible.
