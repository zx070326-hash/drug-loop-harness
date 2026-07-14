# V2 Online Loop Refactor

## Decision

The scientific core is an event-driven acquisition loop. Its action unit is:

`(candidate, oracle, endpoint, scientific_question)`

The loop is valid only when a newly acquired observation can change the next
action. Retrospective replay remains useful, but it is a read-only evaluation
plane rather than the runtime core.

## Runtime Boundary

One `LoopRuntime.step` performs this sequence:

1. Rebuild the current campaign state from the append-only ledger.
2. Enumerate actions whose physical execution prerequisites are qualified.
3. Rank actions with an explicit acquisition policy.
4. Apply the hard decision gate for budget, prerequisites, approval, and exact
   action identity.
5. Execute one admitted oracle action and record its result.
6. Update the route-oracle failure model.
7. Qualify, reject, or abstain on measurement validity through the evidence gate.
8. Update candidate beliefs from the newly available evidence.
9. Complete the round and rebuild state before the next decision.

The policy proposes. The decision gate admits execution. The evidence gate
decides whether a result is usable evidence. The claim gate decides what can be
said. None of the gates ranks the next action.

## Components

| Component | Responsibility |
| --- | --- |
| `v2/contracts.py` | Typed campaign, candidate, oracle, action, result, evidence, belief, failure, and claim contracts |
| `v2/ledger.py` | Append-only facts with deterministic identifiers and JSON round-trip |
| `v2/state.py` | Deterministic state projection and budget/pending-action invariants |
| `v2/action_space.py` | Structurally valid candidate-oracle-question actions |
| `v2/policy.py` | Transparent baseline value-of-information ranking |
| `v2/gates.py` | Separate decision, evidence, and claim permissions |
| `v2/executor.py` | Oracle boundary; the cache executor reveals only the admitted record |
| `v2/surrogate.py` | Target-local Morgan/logistic ensemble updated only from declared label oracles |
| `v2/failure.py` | Route-oracle execution-failure posterior used by later decisions |
| `v2/runtime.py` | Bounded single-loop orchestration |
| `v2/evaluation.py` | Independent, read-only trace integrity evaluation |
| `v2/multitarget_fixture.py` | Hash-verified bridge from admitted retrospective cache cells |

LLMs are not in the trusted core. A future LLM or multi-agent system may submit
typed action proposals, but it cannot bypass action enumeration, gates, the
ledger, or claim protocols.

## Current Verification

The ALDH1 simulated-online fixture verifies all candidate and oracle artifacts
against the declared SHA256 values before the run. Held-out labels live in the
executor fixture and are absent from initial state. They are revealed only
after the exact `heldout_label` action has passed all prerequisites and the
decision gate.

An eight-candidate, 60-step smoke produced 47 complete acquisitions with no
trace-integrity issue. Seven labels were revealed. The eighth candidate was
intentionally blocked because its L4 ensemble check failed, so it never became
eligible for final-label acquisition.

Run the smoke with:

```powershell
$env:PYTHONPATH='src'
python scripts\run_v2_simulated_online_smoke.py --candidate-limit 8 --steps 60 --total-budget 120
```

## Scientific Limits

This implementation establishes an auditable online-loop contract; it does not
yet establish that the baseline policy discovers better molecules.

- The Morgan/logistic uncertainty is an uncalibrated engineering baseline.
- Hidden truth is policy-isolated but still resides in the same process.
- Cache-backed execution validates causality and leakage controls, not
  prospective laboratory performance.
- The completed smoke is not a policy comparison, statistical result, or
  molecule-validity claim.
- Scientific evidence still requires predefined endpoints, independent policy
  comparisons, repeated seeds or cohorts, uncertainty intervals, and ideally a
  prospective oracle or assay.

V2.1 separates measurement validity from outcome direction and hard execution
requirements from soft promotion preferences. See
[`V2_1_CREDIBILITY_SEMANTICS.md`](V2_1_CREDIBILITY_SEMANTICS.md) for the
contract and migration details.

The next research experiment should compare acquisition policies under the
same hash-bound candidate pool, oracle costs, stopping rules, and hidden-label
protocol. Primary outcomes should be discovery yield or regret versus total
cost, accompanied by calibration, failure, and trace-integrity measures.
