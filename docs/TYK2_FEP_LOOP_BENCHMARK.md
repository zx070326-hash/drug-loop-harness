# TYK2 FEP Loop Benchmark

## Decision

Use the OpenFE IndustryBenchmarks2024 public benchmark family, with the JACS
TYK2 system as the first target-specific FEP qualification benchmark.

TYK2 is a positive-control and engineering-acceptance system. It is not the
sole evidence for a promotion-policy claim. A later challenge system must be
selected prospectively after this acceptance stage, without using its policy
outcomes to choose the target.

## Why TYK2 First

The pinned public inputs contain:

- 16 congeneric, net-neutral ligands;
- a 4.28 kcal/mol experimental binding-free-energy range;
- a single prepared 4GIH receptor structure;
- 24 experimental reference edges forming a connected graph with cycle rank 9;
- no additional ligand-file remediation in the benchmark preparation record.

The public OpenFE 1.0.1 result cache contains:

- 22 calculated edges over all 16 ligands;
- a connected network with cycle rank 7;
- three complete solvent and complex repeats for every edge;
- 132 populated leg-repeat free-energy estimates;
- minimum off-diagonal MBAR overlap 0.0532 and no value below 0.03.

The pinned Zenodo archive is staged locally and its MD5 matches the published
checksum. Without fully extracting the 4.64 GB inner archive, its inventory
shows:

- 22 edges x 2 legs x 3 repeats = 132 leg-repeat directories;
- one real-time analysis record for every leg-repeat;
- 20 checkpoints per record, from 5% to 100% in 5% increments;
- checkpointed free energy, MBAR standard error, uncorrelated-sample count,
  equilibration length, statistical inefficiency, runtime, and throughput.

This supports a read-only, time-resolved replay of `EXTEND_EDGE`, `STOP_EDGE`,
and `ADD_REPEAT` decisions before spending GPU time. The archive does not
contain raw coordinate trajectories such as `.nc` or `.xtc`, so it cannot test
new lambda schedules or substitute for prospective local simulations.

This is small enough for controlled local reruns and large enough to exercise
network connectivity, redundant edges, cycle closure, repeat allocation, and
ranking under a finite budget.

## Why This Is a Frontier-Relevant Loop

The 2026 collaborative OpenFE assessment shows that fixed extra sampling and
fixed three-repeat execution often have diminishing average returns, while
mapping difficulty, overlap failure, and private-project performance remain
material problems. Its reported convergence thresholds are useful diagnostics,
but the authors explicitly caution that they are not universal decision rules.

The benchmark therefore should not ask only whether OpenFE reproduces a final
RMSE. It should ask whether a controller can use partial-run evidence to spend
the next unit of compute on the edge, repeat, or repair action with the highest
expected value while preserving calibrated uncertainty and network validity.

## Alternatives Considered

| System | Nodes in public JACS metadata | Role | Reason not first |
| --- | ---: | --- | --- |
| TYK2 | 16 | acceptance control | selected |
| CDK2 | 16 | second confirmation | useful LamNet optimization example, but adds a second protocol before the first is qualified |
| MCL1 | 69 in source metadata | scale/stress | substantially larger and more expensive |
| P38 | 34 | poor-overlap challenge | useful later, but mixes oracle admission with known convergence failures |
| PTP1B | 23 | charge/convergence challenge | adds charge and protocol complexity too early |

The current public PyMBAR4 aggregate contains low-overlap results for P38 and
PTP1B, while the TYK2 cache is well behaved. A challenge system will therefore
be needed before making a general policy claim.

## Scientific Question

Under a fixed GPU-hour budget, can a credibility-aware controller decide which
alchemical edge to start, continue, repeat, stop, or repair while reaching a
predeclared level of network accuracy and ranking reliability at lower cost
than a fixed full-length, three-repeat protocol?

This is an inner high-fidelity loop inside the broader drug-discovery loop. It
does not replace compound promotion across chemistry, ADMET, docking, FEP, and
assay stages. It makes the expensive FEP oracle itself interruptible,
observable, and budget-aware.

## Stage 0 Objective

Stage 0 qualifies the target-specific OpenFE boundary. It must:

1. preserve the public OpenFE 1.0.1 cache as a distinct oracle profile;
2. plan the TYK2 network with the local OpenFE 1.12.0 environment;
3. rerun one high-overlap sentinel edge and one lower-overlap sentinel edge;
4. normalize per-leg and per-repeat outputs into immutable observations;
5. check MBAR overlap, repeat disagreement, cycle closure, network
   connectivity, and comparison with experimental values;
6. demonstrate restart, idempotency, budget settlement, and abstention on an
   incomplete or nonconverged edge.

Candidate sentinel edges from the public cache are:

- high-overlap control: `jmc_23 -> jmc_27`, minimum overlap 0.2256;
- lower-overlap control: `ejm_31 -> ejm_44`, minimum overlap 0.0532.

These choices are operational controls, not evidence that either edge is
intrinsically easy or difficult under OpenFE 1.12.0.

## Stage 1 Objective

After Stage 0 passes, replay budgeted policies against cached observations.
Experimental values remain hidden from policies and are exposed only to the
evaluator.

The read-only cache normalizer and causal replay kernel are implemented. The
kernel exposes only the next admitted pair of complex and solvent checkpoints,
settles cost from the revealed observations, reconstructs state from an
append-only ledger, and keeps experimental values evaluator-only. The fixed
full-length three-repeat policy now reproduces the public result cache.

Normalized artifacts:

- `state/tyk2-fep-checkpoint-cache-v1.jsonl`: 2,640 checkpoint observations;
- `state/tyk2-fep-checkpoint-cache-manifest-v1.json`: source-chain,
  inventory, event-stream hash, diagnostic availability, and authority limits;
- event-stream SHA256:
  `8f8d9cd1b949d89fcdcd03e91c1d191c6a4a87aa697f9342de92da6deebe0b54`.

This cache is ready as a hidden executor observation source. It does not by
itself authorize policy ranking or scientific claim upgrades.

Fixed-baseline artifacts:

- `state/tyk2-fep-fixed-baseline-ledger-v1.json`: 6,602 deterministic events;
- `state/tyk2-fep-fixed-baseline-report-v1.json`: read-only reproduction and
  descriptive scientific evaluation;
- 1,320 settled paired actions, 2,640 revealed checkpoints, and exactly 132.0
  work units;
- 132 official leg-repeat DG comparisons with maximum absolute error
  `6.47e-13 kcal/mol`;
- connected 16-node/22-edge network, aligned experimental node RMSE
  `0.521 kcal/mol`, MUE `0.413 kcal/mol`, Kendall tau-a `0.683`, and network
  cycle-fit RMSE `0.141 kcal/mol`.

The conversion is frozen at `kBT(298.15 K) = 0.5924849497137639 kcal/mol`, and
edge orientation is `complex DG - solvent DG`. Online checkpoint standard
errors are not relabeled as the official PyMBAR4 `dDG`: the latter, together
with overlap, requires a separate `u_ln/N_l` reanalysis layer.

The common warm start, budget grid, partial-state evaluator, non-inferiority
criteria, and four policy arms are now frozen and executed. The immediate live
execution gate remains generation and review of a local OpenFE 1.12.0 TYK2
network plan before either sentinel edge is dispatched.

Actions available to a policy are:

- `START_EDGE`
- `EXTEND_EDGE`
- `ADD_REPEAT`
- `STOP_EDGE`
- `REPAIR_NETWORK`
- `ABSTAIN`

The initial comparison arms are:

- fixed full-length, three-repeat OpenFE protocol;
- random allocation under the same budget;
- static mapping/difficulty heuristic;
- credibility-aware adaptive allocation.

LamNet is added only after these arms are frozen. Its TYK2 target-specific
checkpoint is not a valid generalization test because target data may have
influenced training. The leave-one-target-out checkpoint may be evaluated as a
prior, and all overlap between LamNet data and evaluation edges must be
declared.

## Stage 1 Result

The pre-outcome protocol is
`state/tyk2-fep-policy-protocol-v1.json` with SHA256
`09be994f27c85bc82574406b8b2941c1dfc1efc6fe6f73393a24056b4d2aaf64`.
It freezes:

- a paired 10% checkpoint warm start on repeat 0 for every edge, costing 4.4
  work units;
- budgets of 33, 66, 99, and 132 work units;
- uniform, random-legal, frozen-warm-start, and diagnostic-aware policies;
- 20 fixed random seeds;
- RMSE, Kendall tau-a, cycle-fit, connectivity, edge-coverage, and abstention
  gates;
- an adaptive-value rule that requires diagnostic allocation to pass at least
  one budget step earlier than both deterministic controls and while fewer than
  half of random replicates pass.

The completed report is
`state/tyk2-fep-policy-curve-report-v1.json` with SHA256
`2e05856748763e1b3db789d4ae2d7adafc0e0873693887f1162fa19913b599ea`.
All 92 replay ledgers are stored as deterministic gzip artifacts under
`state/tyk2-fep-policy-ledgers-v1/`; compressed and uncompressed hashes were
verified against the report.

Primary result:

- diagnostic-aware allocation first passed at 33 work units;
- frozen warm-start allocation also passed at 33 work units;
- 17 of 20 random allocations passed at 33 work units, pass fraction 0.85 with
  a Wilson 95% interval of 0.64 to 0.95;
- uniform allocation first passed at 66 work units because its 33-unit
  cycle-fit RMSE was 0.2034, just above the frozen 0.20 gate;
- the predeclared adaptive-value gate therefore failed.

At 33 work units the diagnostic policy had node RMSE 0.492 kcal/mol, Kendall
tau-a 0.733, cycle-fit RMSE 0.139 kcal/mol, and edge-DDG RMSE to the hidden full
cache of 0.178 kcal/mol. Relative to uniform, its cycle-fit RMSE was lower by
0.0647 and edge-to-full RMSE lower by 0.0487 kcal/mol, but its experimental node
RMSE was higher by 0.0200 kcal/mol. Relative to the frozen static policy, the
same differences were -0.0572, -0.0162, and +0.0287 kcal/mol. These are useful
secondary convergence signals, not a primary policy win.

The frozen stage decision is
`GO_REDUCED_COST_SIGNAL_NO_ADAPTIVE_DIFFERENTIATION`. TYK2 supports the claim
that substantial early stopping can preserve the declared positive-control
metrics. It does not show that online adaptation is needed: the system is too
easy for that distinction, and partial experimental RMSE can improve by chance
relative to the full-cache endpoint.

The next scientific gate is to prospectively register one challenge system
before inspecting its policy outcomes. P38 is the cleaner first candidate for
an overlap/convergence challenge; PTP1B mixes that question with charge and
protocol complexity. The challenge protocol must preserve the current policies
and primary decision rules or declare any changes before replay. MBAR overlap
from `u_ln/N_l` should be added before upgrading the policy name from
diagnostic-aware to credibility-aware.

## Evaluation Contract

Primary loop endpoint:

- GPU-hour-equivalent cost required to reach a preregistered accuracy and
  credibility target.

Scientific outcomes:

- pairwise delta-delta-G RMSE and MUE;
- Kendall tau and rank correlation;
- top-k ligand recovery and sign accuracy;
- uncertainty coverage and calibration.

Credibility outcomes:

- connected-network coverage;
- repeat disagreement;
- MBAR overlap and effective-sample diagnostics;
- cycle-closure inconsistency;
- failed, incomplete, and abstained edges;
- result sensitivity to cache/profile version.

Policy comparisons must report paired cost curves and uncertainty intervals,
not only the best final point.

## Claim Ceiling

A passing TYK2 study may support:

> The OpenFE adapter and budgeted FEP decision loop are operational and can be
> evaluated reproducibly on a well-curated congeneric benchmark.

It cannot by itself support:

- autonomous drug discovery;
- molecular efficacy;
- superiority on prospective medicinal-chemistry programs;
- general superiority of one policy;
- generalization of LamNet to unseen targets.

## Pinned Sources

- OpenFE IndustryBenchmarks2024 commit:
  `ff41e5ad0fda89b352341e3f6511bee25db0000a`
- Public result cache DOI: `10.5281/zenodo.14430985`
- JACS source DOI: `10.1021/ja512751q`
- Current collaborative OpenFE assessment DOI: `10.1021/acs.jcim.6c00089`
- LamNet DOI: `10.1093/nsr/nwaf559`
- LamNet source commit: `29a17993f2110ed1fcb35fc1fa1eac5bf0751309`

The machine-readable selection record is
`state/tyk2-fep-benchmark-selection-v1.json`.

## Local Cache Provenance

- Outer archive:
  `external/benchmarks/zenodo-14430985/jacs_set_tyk2.zip`
- Bytes: `1684887279`
- Published and local MD5: `d458cc05dc14cde589b7d38913378bbc`
- Checksum status: `PASS`
- Inner archive:
  `external/benchmarks/zenodo-14430985/archive/jacs_set_tyk2/results_data.zip`
- Inner uncompressed bytes: `4640186335`
