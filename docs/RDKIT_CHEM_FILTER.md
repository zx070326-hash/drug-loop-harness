# RDKit Chemical Filter Profile

`RDKitChemFilterAdapter` is the first local scientific-compute adapter in this
repository. It establishes only an L0 record that a declared small-molecule
SMILES was sanitized by a declared RDKit version and passed basic property
thresholds.

It does not predict target binding, pose quality, potency, ADMET, synthesis,
or experimental activity. Its QED value is recorded as a descriptor, not a
promotion score or pass/fail condition.

## Fixed Profile

| Check | Threshold |
|---|---|
| RDKit parse and sanitize | must pass |
| Molecular weight | at most 600 Da |
| cLogP | at most 5 |
| H-bond donors | at most 5 |
| H-bond acceptors | at most 10 |
| Rotatable bonds | at most 10 |

The output also records canonical SMILES, molecular weight, cLogP, HBD, HBA,
rotatable-bond count, ring count, and QED. These fields support inspection; they
do not broaden the oracle's scientific scope.

## Reproducibility Boundary

Every observation provenance payload includes:

```text
tool=rdkit
tool_version
adapter=RDKitChemFilterAdapter
adapter_version
ruleset
input_hash
output_hash
input_context.candidate_csv_sha256
```

The input hash covers compound id, source SMILES, configuration, ruleset, RDKit
version, and input context. The output hash covers normalized metrics. Input
context is nested and cannot overwrite fixed tool or hash fields.

`examples/tools/emit_rdkit_chem_filter_observation.py` fails if its declared
`--expected-rdkit-version` differs from the local installation. It emits one
normalized observation JSON object only on an exact match.

## Human-Triggered Smoke Test

The smoke command is an adapter verification step, not a promotion run:

```powershell
python -c "from rdkit import rdBase; print(rdBase.rdkitVersion)"
python E:\11\drug-loop-harness\scripts\run_rdkit_chem_filter_smoke.py --expected-rdkit-version <exact-version> --confirmed-by <reviewer> --subject-id cmpd_001 --output E:\11\drug-loop-harness\state\rdkit-chem-filter-smoke.json
```

It requires an explicit `--confirmed-by` record, builds fixed local commands,
and sends their outputs through `LocalCommandExecutor` and `TrustGate`. The
artifact records candidate-file SHA-256, command-template SHA-256, timeout,
tool version, tool configuration, and per-observation input/output hashes.

The artifact always states:

```text
promotion_policy_invoked=false
scientific_claim_upgrade_permitted=false
autonomous_execution_permitted=false
remote_or_live_oracle_calls=0
```

An invalid SMILES can yield an applied but TrustGate-rejected L0 observation. A
timeout, malformed command output, or version mismatch is an operational
failure: no observation crosses TrustGate and the command exits nonzero while
retaining a diagnostic smoke artifact.
