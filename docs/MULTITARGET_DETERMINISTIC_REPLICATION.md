# Multi-Target Deterministic Replication

`state/multitarget-policy-replication-plan.v2.json` defines the current
retrospective replication. It supersedes the earlier three-target draft after
the MAPK1 template pair failed the required pose-recovery gate.

The admitted panel is deliberately narrow:

- ALDH1: `4WP7` and `4WPN`.
- VDR: `3A2I` and `3A2J`.
- Three label-blind TocoDecoy cohort seeds per target.
- Four fixed policy arms and budgets `32`, `64`, `128`, and `256`.

This is multi-target because it contains two distinct target classes. It is
not broad evidence that a policy transfers across drug discovery generally.

## Scope Of This Replication

This is not a paper-accurate reproduction or head-to-head evaluation of a
six-agent drug-discovery system. It does not run an LLM, a molecule generator,
or a multi-agent planner. The name `hou_tocodecoy_aldh1_replication_v1` is a
frozen cohort-selection seed retained for compatibility with the earlier ALDH1
pilot; it is not provenance for an external six-agent implementation.

The estimand is narrower: with a fixed, label-blind candidate cohort and the
same precomputed multi-fidelity oracle cache, how does a fixed promotion policy
allocate its budget and preserve failure visibility? A six-agent system can be
introduced later as a separately frozen path-layer proposal source, then
evaluated through this same cache and promotion contract. It must not be mixed
into the present policy comparison.

ALDH1 L1 uses the RDKit-parseable RCSB `3SR` reference ligand as a cheap
promotion feature. Its L3/L4 receptor calibration uses the separately
hash-bound LIT-PCBA `4WP7`/`4WPN` template artifacts. These are distinct
evidence roles: neither makes the other a binding or activity result.

## Admission Boundary

Each selected receptor template has a Vina 1.2.7 pose-recovery artifact with
the frozen protocol:

- seed `20260712`;
- exhaustiveness `8`;
- `num_modes=1`;
- `energy_range=3`;
- CPU `1` per Vina process;
- `max_evals=50000`;
- Open Babel heavy-atom RMSD no greater than `2.0` Angstrom.

Run the read-only admission audit before building a cache:

```powershell
$env:PYTHONPATH = 'src'
python scripts\audit_multitarget_replication_plan.py --plan state\multitarget-policy-replication-plan.v2.json
```

The audit verifies current template artifacts and their Vina configurations;
it does not execute docking or grant a scientific claim.

## Cache Construction

Export the six candidate/truth pairs once. The candidate CSVs are label blind.

```powershell
$env:PYTHONPATH = 'src'
python scripts\export_multitarget_replication_pools.py --plan state\multitarget-policy-replication-plan.v2.json --output-root state\multitarget-policy-replication-pools-v2
```

Construct one cache per target using the deterministic union of its three
candidate cohorts:

```powershell
python scripts\build_multitarget_target_oracle_cache.py --target-id ALDH1 --run-dir external\cache_runs\aldh1_multitarget_v2 --parallel-workers 4
python scripts\build_multitarget_target_oracle_cache.py --target-id VDR --run-dir external\cache_runs\vdr_multitarget_v2 --parallel-workers 4
```

The builder has no held-out-truth argument. It creates L0, L1, deterministic
3D/Meeko preparation state, two L3 Vina caches, and L4 ensemble consistency.
Preparation and docking failures remain explicit observations. Interrupted
runs can be resumed only against the same frozen plan and candidate union:

```powershell
python scripts\build_multitarget_target_oracle_cache.py --target-id ALDH1 --run-dir external\cache_runs\aldh1_multitarget_v2 --parallel-workers 4 --resume
```

Before any cell sees its held-out truth, audit the finished target caches:

```powershell
python scripts\audit_multitarget_target_oracle_cache.py --target-cache-manifest external\cache_runs\aldh1_multitarget_v2\target_oracle_cache_manifest.json --output external\cache_runs\aldh1_multitarget_v2\target_oracle_cache_audit.json
python scripts\audit_multitarget_target_oracle_cache.py --target-cache-manifest external\cache_runs\vdr_multitarget_v2\target_oracle_cache_manifest.json --output external\cache_runs\vdr_multitarget_v2\target_oracle_cache_audit.json
```

## Cell Finalization

After a target cache is complete, finalize every target-seed cell. This first
projects the label-blind cache, writes and hashes `policy_selections.json`, and
only then hashes and opens that cell's held-out truth file.

```powershell
python scripts\finalize_multitarget_replication_cell.py --target-id ALDH1 --selection-seed hou_tocodecoy_aldh1_replication_v1
python scripts\finalize_multitarget_replication_cell.py --target-id ALDH1 --selection-seed multitarget_policy_replication_seed_b_v1
python scripts\finalize_multitarget_replication_cell.py --target-id ALDH1 --selection-seed multitarget_policy_replication_seed_c_v1
python scripts\finalize_multitarget_replication_cell.py --target-id VDR --selection-seed hou_tocodecoy_aldh1_replication_v1
python scripts\finalize_multitarget_replication_cell.py --target-id VDR --selection-seed multitarget_policy_replication_seed_b_v1
python scripts\finalize_multitarget_replication_cell.py --target-id VDR --selection-seed multitarget_policy_replication_seed_c_v1
```

## Aggregation Boundary

Only after all six cells exist, aggregate them:

```powershell
python scripts\aggregate_multitarget_policy_replication.py
```

The report uses target-seed paired contrasts and target-level summaries. Cohort
seeds can overlap, so they are sensitivity checks, not independent biological
replicates. The aggregation does not produce a global policy winner or any
claim about binding, potency, ADMET, selectivity, or efficacy.
