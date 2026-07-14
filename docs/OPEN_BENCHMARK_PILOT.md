# Open Benchmark Pilot

The first formal research scenario is `hou_tocodecoy_policy_replay_v1`.
It studies a narrow, falsifiable question: under a fixed candidate pool,
published split, and frozen multi-fidelity oracle cache, does the
credibility-aware promotion policy allocate a fixed budget more effectively
than a fixed cascade? It does not test whether a new molecular generator is
better, and it does not establish biological activity or a drug-discovery
claim.

The current declaration is a review-pending draft at
`state/hou-tocodecoy-admission-manifest.json`; the reusable starting point is
`examples/resgen_tocodecoy_loop_replay_admission.template.json`. It binds the
published TocoDecoy archive to the source split, while recording ResGen as the
next-stage fixed-generator integration rather than contaminating the first
policy comparison with generator differences.

## Asset Boundary

The source study identifies the TocoDecoy archive at Zenodo record `6476753`.
It is CC BY 4.0, its expected `data.zip` MD5 is
`24b33b402ccc27b29272bf16d5495b01`, and the archive is about 873 MB. The first
experiment needs that archive locally under
`external/tocodecoy/data.zip`. Do not treat a successful download as a valid
input until the digest is verified.

ResGen is deliberately not required for this first replay. Its public code is
MIT-licensed, and its processed data release is about 3.3 GB. When generation
is added, pin the ResGen source revision and use it identically for both policy
arms. A later generator comparison is a different intervention and must be
registered as a separate experiment.

## Read-Only Admission Audit

After a named scientific reviewer accepts the declaration and the declared
source asset is acquired, run:

```powershell
python E:\11\drug-loop-harness\scripts\audit_open_benchmark_admission.py E:\11\drug-loop-harness\state\hou-tocodecoy-admission-manifest.json --asset-root E:\11\drug-loop-harness --output E:\11\drug-loop-harness\state\hou-tocodecoy-admission-audit.json
```

The audit has four meaningful states:

- `WAITING_FOR_LOCAL_SOURCE_ASSETS`: an expected local public artifact is not
  present.
- `WAITING_FOR_HUMAN_REVIEW`: the scenario remains a draft without a named
  scientific review; it is not eligible for asset admission.
- `READY_FOR_ORACLE_CACHE_CONSTRUCTION`: source data and split are verified;
  construct all declared oracle observations before comparing policies.
- `WAITING_FOR_FROZEN_ORACLE_CACHE`: a cache exists but has no locked digest.
- `READY_FOR_POLICY_REPLAY`: sources and one fixed cache are integrity-verified.

`FAIL_INVALID_MANIFEST` and `FAIL_INTEGRITY_MISMATCH` fail closed. The command
never downloads data, calls docking/ADMET/free-energy tools, changes a policy,
or upgrades a scientific claim.

## Cache Construction Contract

The cache must be materialized before either policy runs. It must preserve
candidate identifier, target/pocket identifier, source split, oracle name and
version, input hash, observation timestamp, raw-output hash, normalized value,
and failure/missing status. Add its exact digest to `oracle_cache.integrity` in
the accepted declaration, re-run the audit, and only then use
`OracleCacheReplayRunner` / `BudgetCurveRunner`.

The source split is reserved for replay evaluation. Promotion-policy thresholds,
TrustGate rules, and oracle ordering must be selected without tuning on the
declared evaluation holdout. Any later wet-lab or prospective comparison stays
under `docs/PROSPECTIVE_LOOP_EVALUATION_PROTOCOL.md` and is not licensed by this
retrospective admission.

## TocoDecoy Split Export

TocoDecoy contains a published `train` field and a ground-truth `label`. The
policy must never receive that label. Export one explicitly selected target and
split into two separate files before cache construction:

```powershell
python E:\11\drug-loop-harness\scripts\export_tocodecoy_evaluation_pool.py --source-archive E:\11\drug-loop-harness\external\tocodecoy\data.zip --target REPLACE_WITH_TARGET --split-value 0 --max-candidates 10000 --selection-seed target_loop_pilot_v1 --candidates-output E:\11\drug-loop-harness\state\tocodecoy-candidates.csv --heldout-truth-output E:\11\drug-loop-harness\state\tocodecoy-heldout-truth.jsonl --manifest-output E:\11\drug-loop-harness\state\tocodecoy-evaluation-pool.json
```

The candidate CSV is compatible with `TableCandidateAdapter` and contains no
label column. The held-out JSONL retains the source-row identity and labels
only for final retrospective evaluation. A target must be specified explicitly:
the archive is too large and heterogeneous to become one uncontrolled campaign.
When a target split is larger than the requested cohort, selection is a stable
SHA-256 rank of source-row identity and a declared seed; labels are not read to
choose the cohort.
TocoDecoy alone does not include receptor structures; use separately hashed
official LIT-PCBA structural inputs before constructing a docking cache.
