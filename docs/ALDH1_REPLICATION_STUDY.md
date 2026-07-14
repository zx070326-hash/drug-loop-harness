# ALDH1 Replication Study

`state/aldh1-loop-replication-plan.json` is the pre-declared construction
contract for the next ALDH1 retrospective policy-replay study.

## Fixed Inputs

- `state/tocodecoy-aldh1-replication-pool.json` exports 2,048 candidates from
  the published ALDH1 test split by a label-blind SHA-256 selection rule.
- `external/cache_runs/tocodecoy_aldh1_replication_v1/`
  contains the L1 reference-ligand cache. Its reference is `RCSB:4WP7:3SR`;
  Morgan-Tanimoto is a promotion feature only.
- L3 is bounded Vina 1.2.7 docking against `4WP7`.
- L4 is a `4WP7`/`4WPN` receptor-ensemble consistency observation. Both
  templates passed the same bounded pose-recovery threshold of 2.0 Angstrom.

## Replay Contract

Policies may read candidates, L0, and L1 to choose promotions. They may not
read L3, L4, or held-out labels before their selection record is frozen.
All policy arms consume one frozen cache and use the same budget curve:
`32, 64, 128, 256`.

The L4 adapter marks missing secondary-template results and template-score
disagreements explicitly. It provides structural robustness evidence only; it
does not establish binding or biological activity.

## Evaluation

`RetrospectivePolicyCredibilityEvaluator` accepts only frozen selections before
joining held-out labels. It reports active discovery rate, L3/L4 missing rate,
and deterministic bootstrap confidence intervals at each budget point. It
always leaves policy ranking and scientific-claim upgrades disabled.

The replication cache is not complete until all 2,048 candidates have explicit
L3 and L4 states, including preparation failures and timeouts. No partial cache
may be used to rank policies.
