# Jensen Reliability-Aware TVR Stress Ablation

## Decision

The frozen five-target ablation supports carrying the reliability-aware TVR
policy into prospective study design. It does not establish policy superiority,
molecular activity, or prospective discovery performance.

Frozen artifacts:

- Protocol: `state/jensen-reliability-stress-protocol-v1.json`
- Protocol SHA-256: `ea66b7c4b88362173a82d1c8f9b1c14d3329735a4051b527caf28a1fd3f94d98`
- Evaluation: `state/jensen-reliability-stress-evaluation-v1.json`
- Evaluation SHA-256: `280450c4f020457d34a8b8ddc50ff4a5ee8f8db4c8c08a043b22eb5ab3b07781`
- Decision: `SUPPORT_CREDIBILITY_AWARE_POLICY_FOR_PROSPECTIVE_DESIGN`

## Scientific Question

Does discounting TVR by observed oracle invalidity reduce cost wasted on failed
oracles while preserving qualified high-fidelity discovery performance?

The challenger multiplies the frozen local TVR priority by:

```text
(valid_measurements + 1) / (attempts + 1)
```

The factor is exactly `1` for an all-valid history. This creates a clean-trace
identity check: reliability awareness must not change the base policy when no
invalidity has been observed.

## Frozen Design

- Five targets: FACTOR_D, CXCR4, HIF_PH, PARP1, and NR1A2
- Three previously unused 64-candidate windows per target
- Three common warm-start seeds per window
- Two policies: Jensen-style TVR and reliability-aware TVR
- Four deterministic scenarios: clean and isolated 35% failure at docking,
  single-point, or dose-response fidelity
- Equal total budget of 12 cost units
- 360 policy arms in total
- Scientific unit: target (`n=5`), not window, seed, or action

Each arm has an immutable compressed ledger. Failures use pair-stable common
random numbers keyed by candidate and oracle, so policy-specific action IDs do
not change the failure realization.

## Evaluator Correction

The earlier Jensen truth evaluator treated any high-fidelity result event as an
observation, including a failed request. That is not a credible discovery
metric under oracle failure.

The stress evaluator therefore counts only TrustGate-qualified high-fidelity
measurements. A failed high-fidelity request:

- incurs its returned cost;
- does not recover a top candidate;
- does not enter the best-observed value;
- cannot reduce regret;
- receives full-window truth-range regret when no qualified high-fidelity
  observation exists.

The report verifier rebuilds every ledger and independently recomputes these
metrics before accepting a scenario report.

## Result

The predeclared gate passed:

| Check | Result |
|---|---:|
| Clean paired tasks with identical action sequence and metrics | 45 / 45 |
| Qualified failure contamination | 0 |
| Targets with lower failed cost | 5 / 5 |
| Median failed-cost reduction | 0.1041 cost units |
| Median simple-regret harm | -0.0064 |
| Maximum target simple-regret harm | 0.0059 |
| Median top-recovery difference | 0.0000 |

Negative regret harm means a small descriptive improvement. PARP1 was the one
target with positive regret harm (`0.0059`), still below the frozen `0.10`
single-target guard. All leave-one-target-out views retained four failed-cost
wins out of four targets.

## Interpretation Boundary

This is deterministic synthetic failure inside the same public Jensen benchmark
family. It tests whether the loop consumes its own reliability state correctly;
it does not test correlated failure, calibration drift, latency, route-local
failure, real assay noise, or wet-lab transfer. The local TVR implementation is
also a dependency-light reference implementation, not the original BayBE
implementation.

The legal next step is prospective design, not a stronger retrospective claim.
That transition is recorded in `PROSPECTIVE_V2_POLICY_BRIDGE.md`.
