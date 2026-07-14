# Jensen Promotion-Policy Budget Curve

## Decision

The frozen five-target development experiment is complete. Its predeclared
decision is:

`SUPPORT_PROSPECTIVE_POLICY_STUDY_DESIGN`

This means the state-dependent promotion policy has enough cross-target signal
to justify a separately preregistered prospective comparison. It does not mean
the policy is generally superior, that the local TVR implementation reproduces
the original BayBE implementation, or that any selected molecule is active.

## Scientific Question

Under the same simulated-online multi-fidelity budget, does the frozen local
Jensen-style targeted-variance-reduction policy reduce high-fidelity simple
regret more efficiently than the frozen greedy endpoint-belief control?

The intervention is the promotion policy. The candidate windows, warm start,
oracle cache, oracle costs, budget ceilings, and evaluator are shared.

## Frozen Design

- Targets: `FACTOR_D`, `CXCR4`, `HIF_PH`, `PARP1`, and `NR1A2`.
- Three non-overlapping 64-candidate windows per target: offsets 0, 64, and 128.
- Three paired warm-start seeds per window: 17, 37, and 59.
- Policies: heuristic cost-aware, greedy endpoint-belief, and local Jensen TVR.
- Budgets: 3, 6, 9, and 12 cost units.
- Total arms: 5 x 3 x 3 x 3 x 4 = 540.
- Primary scientific unit: target, `n=5`.
- Nested repeated tasks: candidate windows.
- Nuisance repeats: warm-start seeds.

Aggregation was frozen as seed within window, window within target, then
normalized trapezoidal AUC over budget. No window or seed was treated as an
independent biological replicate, and no p-value or confidence interval was
produced.

The primary gate required all trace and budget controls to pass, TVR to reduce
simple-regret AUC on at least four of five targets, median target-level
reduction of at least 0.02, median top-recovery AUC noninferiority of at least
-0.05, and no single-target regret-AUC harm greater than 0.10.

## Result

All 540 arms passed the frozen trace, source-integrity, and shared-budget
checks. Against the greedy control:

- target regret-AUC wins: 4 of 5;
- median regret-AUC reduction: 0.02596;
- median top-recovery AUC advantage: 0.05556;
- maximum single-target regret-AUC harm: 0.01499;
- every leave-one-target-out descriptive view preserved the development
  thresholds.

Target heterogeneity is material:

| Target | Regret-AUC reduction | Top-recovery AUC advantage |
| --- | ---: | ---: |
| FACTOR_D | -0.01499 | -0.08333 |
| CXCR4 | 0.02596 | 0.08333 |
| HIF_PH | 0.08082 | -0.05556 |
| PARP1 | 0.02561 | 0.05556 |
| NR1A2 | 0.26790 | 0.12963 |

TVR also spent about 0.145 to 0.160 more cost-AUC units than greedy, while
remaining within the same budget ceilings. The result is therefore a modest,
heterogeneous efficiency signal, not a dominant-policy result. FACTOR_D is an
explicit harm case and must remain visible in the prospective design.

## Registration Lineage

Protocol v1 and v2 were superseded before any budget report was written. Their
pre-write validators rejected Python tuple representations that had not yet
been normalized to JSON lists. Both failed attempts, source snapshots, and
zero-report evidence states are retained. V3 changed only complete-result JSON
normalization; no policy, source, budget, threshold, endpoint, or claim ceiling
changed.

- Frozen v3 protocol SHA256:
  `f50bccc6e07d3e654aa08f3caef2c62591a00835a6c3c5d9b330650665655ba6`
- Evaluation SHA256:
  `c936aa384f88594142265a351c2fe3c3e39c4aff1c910f0c2d70093ce1722902`

## Interpretation Boundary

This experiment is retrospective and simulated-online. Prior Jensen pilot,
calibration, and stress artifacts existed, so it is not an untouched
confirmation. The five targets are heterogeneous but come from one published
benchmark family. The local `jensen_tvr` policy is explicitly not the original
authors' BayBE/BoTorch implementation, whose archived trace could not be
exactly reproduced.

The result supports only the next design decision: compare the frozen TVR and
greedy policies in a separately registered prospective acquisition study with
independent allocation, blinded outcome admission, equal experimental budget,
complete missing-outcome accounting, and a distinct replication cohort. The
existing prospective evaluation control plane can carry that study; this
retrospective result grants no assay execution authority.

## Artifacts

- `state/jensen-policy-curve-protocol-v3.json`
- `state/jensen-policy-curve-runs-v3/budget-3.json`
- `state/jensen-policy-curve-runs-v3/budget-6.json`
- `state/jensen-policy-curve-runs-v3/budget-9.json`
- `state/jensen-policy-curve-runs-v3/budget-12.json`
- `state/jensen-policy-curve-evaluation-v3.json`
- `state/jensen-policy-curve-protocol-v1-supersession.json`
- `state/jensen-policy-curve-protocol-v2-supersession.json`
