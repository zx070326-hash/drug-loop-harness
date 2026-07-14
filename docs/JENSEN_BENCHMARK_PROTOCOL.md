# Jensen Simulated-Online Benchmark Protocol

## Question

Within a fixed multi-fidelity acquisition budget, does a policy identify
high-value candidates more efficiently than an explicitly named baseline in a
hash-bound retrospective environment? This is an engineering question about
acquisition policy. It is not a claim that any candidate is active, safe,
synthesizable, or clinically useful.

## Fixed Design

`JensenBenchmarkSpec` is the protocol record. Its default design is:

- Targets: `FACTOR_D`, `CXCR4`, `HIF_PH`, `PARP1`, and `NR1A2`.
- Candidate cohorts: three non-overlapping windows beginning at offsets `0`,
  `64`, and `128`, with 64 candidates per window.
- Common starting data: one observation per oracle for every warm-start seed.
- Warm-start seeds: `17`, `37`, and `59`; they perturb the common starting
  observation only and are not independent biological replicates.
- Total budget: 12.0 cost units, with the same oracle cost contract in every
  arm.
- Arms: `heuristic`, `greedy`, and `jensen_tvr`; `heuristic` is the stated
  baseline unless the protocol record says otherwise.
- Primary endpoints: high-fidelity top-fraction recovery and high-fidelity
  simple regret. Cost delta is secondary.

The runner refuses a cohort window that cannot contain exactly the declared
number of candidates. It also refuses a comparison whose common warm start
cannot be funded before writing an action event.

## Validity Gates

Every policy arm gets a new ledger and executor. Its high-fidelity values are
revealed only by an admitted oracle action. The independent evaluator reads
the high-fidelity CSV after the arm stops, verifies the published training
archive MD5, checks the candidate set, and verifies that it did not mutate the
ledger.

An arm contributes to a pairwise cohort result only when both arms have:

- a passing trace evaluation;
- a valid shared-budget terminal condition; and
- no action-cap, approval, recovery, or `max_rounds_reached` termination.

The comparison fixes the budget ceiling and oracle cost table, not the exact
amount spent. Discrete oracle costs can leave different unspendable residuals;
the artifact therefore reports cost delta beside every effectiveness metric.

For each target-window cohort, the runner first averages paired deltas over
the declared warm-start seeds. It then reports the mean, median, minimum, and
maximum over cohorts. This prevents random warm starts from being counted as
independent experimental samples.

## Analysis Boundary

The artifact always sets `inferential_conclusion_permitted: false` and
`scientific_claim_upgrade_permitted: false`. It intentionally emits no
p-values or confidence intervals: repeated windows from a static retrospective
dataset and repeated warm starts do not establish an independent biological
sampling distribution. The raw arms and seed-averaged cohort rows remain in
the artifact so a later preregistered analysis can inspect heterogeneity rather
than rely on one aggregate.

## Run

```powershell
$env:PYTHONPATH='src'
python scripts\run_jensen_benchmark.py --output state\jensen-benchmark.json
```

Use a smaller command for a smoke test only:

```powershell
$env:PYTHONPATH='src'
python scripts\run_jensen_benchmark.py --targets FACTOR_D --candidate-limit 12 --cohort-offsets 0 12 --warm-start-seeds 17 37 --total-budget 3 --policies heuristic jensen_tvr --output state\jensen-benchmark-smoke.json
```

## What This Does Not Test

- Distribution shift, assay failure, synthesis throughput, or assay queueing.
- Generalization to new targets or prospective molecular discovery.
- The original authors' BayBE/BoTorch numerical implementation. The local
  `jensen_tvr` arm is a dependency-light ICM-Tanimoto decision-structure
  reference and marks itself `not_original_baybe`.
