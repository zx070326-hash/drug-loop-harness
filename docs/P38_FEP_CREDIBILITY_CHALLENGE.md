# P38 FEP Credibility Challenge

## Decision

The P38 challenge is complete and audited. The stage decision is:

`CONTINUE_TO_CREDIBILITY_ACTION_DESIGN_NO_ADAPTIVE_POLICY_WIN`

This separates three results:

- reduced-cost signal: supported at 229.5 of 306 work units;
- adaptive allocation advantage: not supported;
- early credibility signal: supported, but not converted into a credible action.

P38 is now a development and failure-analysis target. It cannot be reused as
confirmatory evidence after its per-edge outcomes have been inspected.

## Frozen Design

The challenge was declared before the 3.9 GB checkpoint archive was downloaded.
The declaration inherited the TYK2 policy weights, stop thresholds, action
ordering, 20 random seeds, and adaptive-value rule. An implementation lock then
bound 15 source files and seven evaluation inputs before cache access.

The public source archive matched its declared size of 3,907,025,066 bytes and
MD5 `ae436ca4d8a7d97224c45700e1ca23e5`. The normalized hidden cache contains:

- 51 edges and 34 ligands;
- three repeats for complex and solvent legs;
- 20 checkpoints per leg-repeat from 5% to 100%;
- 6,120 causal checkpoint observations;
- event-stream SHA256
  `99300f66f1bb810f7b869fb0bfa036f6d96fedbc5f92bad07994322e4505a09d`.

Policies did not receive experimental labels, official final overlap, or future
checkpoints. No LLM participated in policy decisions.

## Baseline

The fixed 306-work-unit replay reproduced all 306 official final leg values.
Maximum absolute disagreement was `2.77e-11 kcal/mol`.

The target is materially harder than TYK2:

- aligned node RMSE: `1.229 kcal/mol`;
- MUE: `0.970 kcal/mol`;
- Kendall tau-a: `0.305`;
- network cycle-fit RMSE: `0.610 kcal/mol`;
- mean between-repeat SD: `0.676 kT`.

The full reference also contains five edges with at least one official final
overlap below 0.03, covering 22 of 306 leg-repeats. Matching this reference is
therefore an efficiency comparison, not a credibility win.

## Policy Result

All four controls first met the target-relative noninferiority gate at 229.5
work units:

| Policy | Earliest pass | Fraction of full cost |
| --- | ---: | ---: |
| Uniform | 229.5 | 75% |
| Frozen warm-start | 229.5 | 75% |
| Diagnostic adaptive | 229.5 | 75% |
| Random majority | 229.5 | 75% |

At that budget 15 of 20 random runs passed. The diagnostic policy therefore
did not pass earlier than deterministic controls, and random success was not
below one half. The preregistered adaptive-value gate failed.

## Credibility Result

The common 10% warm-start diagnostic score achieved:

- AUROC `0.8283` for detecting edges with final overlap below 0.03;
- top-5 recall `0.60`;
- preregistered AUROC floor `0.70`: passed.

This is useful scientific signal, but the action response failed. At the
diagnostic policy's earliest passing budget:

- all five low-overlap edges had been promoted beyond warm start;
- all five had been fully sampled;
- none was stopped;
- none was repaired;
- the policy did not abstain.

The current diagnostic policy ranks difficult edges and spends more compute on
them. It does not know whether the correct response is more of the same
protocol, an extra independent repeat, a lambda-schedule or mapping repair, or
an explicit abstention. Risk detection and scientific action selection are
therefore distinct modules.

## Next Stage

The next implementation should add a frozen risk-to-action decision layer:

1. classify whether observed risk is compatible with reducible variance or a
   protocol pathology;
2. choose among continue, add repeat, repair protocol, and abstain;
3. assign repair cost and require a measurable post-repair credibility gain;
4. keep P38 as development evidence only;
5. preregister a new target before inspecting its checkpoint outcomes.

The existing public archive cannot demonstrate that a new repair works because
it contains only the original protocol trajectories. Repair effectiveness
requires a prospective OpenFE/OpenMM simulation or another cache containing
paired original and repaired protocols.

This risk-to-action layer was subsequently implemented and transferred without
numeric retuning to PTP1B. Its head-of-ranking enrichment transferred, but its
preregistered watchlist-recall gate failed. The audited result and the resulting
paired-repair requirement are recorded in
[`PTP1B_FEP_RISK_ACTION_CONFIRMATION.md`](PTP1B_FEP_RISK_ACTION_CONFIRMATION.md).

## Artifacts

- `state/p38-fep-challenge-declaration-v1.json`
- `state/p38-fep-implementation-lock-v1.json`
- `state/p38-fep-checkpoint-cache-manifest-v1.json`
- `state/p38-fep-fixed-baseline-report-v1.json`
- `state/p38-fep-policy-protocol-v1.json`
- `state/p38-fep-policy-curve-report-v1.json`
- `state/p38-fep-credibility-curve-report-v1.json`
- `state/p38-fep-stage-decision-v1.json`
- `state/p38-fep-implementation-snapshot-v1.zip`

The 92 deterministic compressed replay ledgers are under
`state/p38-fep-policy-ledgers-v1/`. All implementation/input lock hashes and
all 92 unique ledger hashes passed the final audit.
The 17-entry implementation snapshot preserves the exact locked source with
SHA256 `dc960c7d624dc87680e11c73932800a816e489a46a69010d01b495bc62bc4589`
so later risk-to-action development does not destroy stage reproducibility.

## Claim Ceiling

This stage supports a retrospective claim that early online diagnostics contain
signal for final overlap failures and that 75% of fixed protocol cost preserves
the declared P38 target-relative metrics.

It does not support adaptive-policy superiority, credible autonomous repair,
prospective GPU-hour savings, generalization to unseen targets, or molecular
validity.

## Pinned Sources

- OpenFE IndustryBenchmarks2024 commit:
  `ff41e5ad0fda89b352341e3f6511bee25db0000a`
- P38 checkpoint archive: `10.5281/zenodo.14616071`
- Schrodinger public binding-free-energy benchmark tag: `v2.0`
- JACS source DOI: `10.1021/ja512751q`
