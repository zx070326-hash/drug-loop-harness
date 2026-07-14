# Docking Input Manifest

`docking_input_manifest.v1` identifies the exact scientific object a future
docking adapter would receive. It exists because a docking score cannot be
interpreted when receptor preparation, pocket definition, ligand preparation,
or engine configuration is ambiguous.

The current verifier checks the manifest only. It does not inspect a pose, run a
docking engine, issue an execution permission, or create L3 binding evidence.

## Required Shape

```json
{
  "schema_version": "docking_input_manifest.v1",
  "manifest_id": "docking_manifest_example",
  "created_at": "2026-07-10T00:00:00+00:00",
  "target": {
    "target_id": "target_name",
    "receptor_structure_id": "PDB:example",
    "prepared_receptor": {"path": "receptor.pdbqt", "sha256": "..."},
    "receptor_preparation": {
      "tool_name": "preparation_tool",
      "tool_version": "exact-version",
      "protocol_id": "preparation_protocol_id"
    }
  },
  "binding_site": {
    "coordinate_system": "angstrom",
    "center": [10.0, 20.0, 30.0],
    "size": [20.0, 20.0, 20.0],
    "reference_ligand": {
      "id": "LIG",
      "path": "reference_ligand.pdbqt",
      "sha256": "..."
    }
  },
  "engine": {
    "name": "docking_engine",
    "version": "exact-version",
    "scoring_function": "declared_function",
    "random_seed": 7,
    "exhaustiveness": 8,
    "timeout_seconds": 900,
    "parameters": {"path": "engine.conf", "sha256": "..."}
  },
  "ligands": [
    {
      "subject_id": "cmpd_001",
      "source_smiles_sha256": "...",
      "prepared_ligand": {"path": "cmpd_001.pdbqt", "sha256": "..."}
    }
  ]
}
```

Relative artifact paths resolve from the manifest directory. All artifact hashes
must be lowercase SHA-256 digests. A ligand subject id may occur once only.

## Verification

```powershell
python E:\11\drug-loop-harness\scripts\verify_docking_input_manifest.py E:\path\to\docking-input.json --output E:\11\drug-loop-harness\state\docking-input-manifest-verification.json
```

The verifier checks JSON/schema structure, timezone-aware creation time, target
and preparation identifiers, angstrom box coordinates, positive box dimensions,
engine declaration, artifact existence, and every declared SHA-256 digest. It
reports `PASS` or `FAIL` and writes a diagnostic record on either path.

The verification artifact always declares:

```text
execution_authority_granted=false
docking_execution_permitted=false
autonomous_execution_permitted=false
scientific_claim_upgrade_permitted=false
```

`PASS` means only that the declared input artifacts are internally consistent at
verification time. It does not show that the receptor is biologically relevant,
the box is appropriate, the protonation state is correct, the engine is locally
installed, a pose is valid, or a docking score predicts binding. Those require
a later version-bound docking execution adapter and independent credibility
checks.

## Result Import Boundary

An external docking engine may later write a `docking_result.v1` record. The
current project can normalize that already-existing record without launching an
engine:

```powershell
python E:\11\drug-loop-harness\scripts\normalize_docking_result.py E:\path\to\docking-result.json --verification E:\11\drug-loop-harness\state\docking-input-manifest-verification.json --output E:\11\drug-loop-harness\state\docking-result-normalization.json
```

The result must bind to the verified `manifest_id` and manifest SHA-256, target,
subject, engine name/version, score semantics, execution timestamps, and a pose
artifact SHA-256. The normalizer also rechecks the current input manifest and
its artifacts. It rejects a manifest altered after verification, a verification
written after docking began, a wrong target or engine, malformed score fields,
or an absent/altered pose.

`NORMALIZED` means an observation has the required structural provenance. It is
not a binding claim. The CLI deliberately creates an unanchored docking oracle,
so its `TrustGate` ticket is `abstain` until the campaign supplies a genuine
target-specific calibration anchor, such as an independently documented pose-
recovery or prospective validation protocol. The imported result never grants
execution authority or upgrades a scientific claim by itself.

## Local Engine Capability

Check only whether common local engines respond to their fixed `--version`
command:

```powershell
python E:\11\drug-loop-harness\scripts\probe_docking_engines.py --output E:\11\drug-loop-harness\state\docking-engine-capabilities.json
```

The probe knows `vina`, `smina`, `qvina2`, and `gnina`. It does not accept a
receptor, ligand, pocket, result, or arbitrary command. It reports each engine
as `available`, `unavailable`, `timeout`, `version_command_failed`, or
`probe_os_error`. `--require-available` changes the process exit status when no
engine is detected, but does not change the evidence or execution permissions.

An available binary proves only that a local version command responded. It does
not validate engine configuration, target compatibility, input integrity, pose
quality, calibration, or binding activity. The probe artifact keeps docking
execution and scientific-claim upgrades disabled.

## Calibration Anchor

To move an imported result beyond the default unanchored `abstain`, create a
`docking_calibration_anchor.v1` and verify it separately:

```powershell
python E:\11\drug-loop-harness\scripts\verify_docking_calibration_anchor.py E:\path\to\calibration-anchor.json --output E:\11\drug-loop-harness\state\docking-calibration-anchor-verification.json
```

An anchor binds one target and receptor structure to one engine name/version and
scoring function. It supports either `pose_recovery` with dataset/protocol,
case count, recovered count, RMSD threshold, and a hash-bound results artifact;
or `prospective_enrichment` with active/decoy counts, enrichment factor, and
its artifact. It requires non-empty limitations. A `human_review` record is
optional claim-escalation metadata: its presence, absence, or `defer` decision
does not block local docking, cache construction, or counterfactual replay.

Apply a verified anchor only during result import:

```powershell
python E:\11\drug-loop-harness\scripts\normalize_docking_result.py E:\path\to\docking-result.json --verification E:\11\drug-loop-harness\state\docking-input-manifest-verification.json --calibration-anchor E:\path\to\calibration-anchor.json --output E:\11\drug-loop-harness\state\docking-result-normalization.json
```

The anchor must exactly match the imported result's target, engine name/version,
and scoring function. On mismatch, the import remains structurally normalized
but its TrustGate ticket stays `abstain` and the CLI exits nonzero. On a match,
TrustGate can return `needs_promotion`; this makes the docking observation an
L3 proxy eligible for further evidence flow, not a final binding or drug claim.

## Loop Readiness State

Summarize engine capability, input verification, result normalization, and
calibration application into the non-executing state that an agent or planner
can read:

```powershell
python E:\11\drug-loop-harness\scripts\summarize_docking_readiness.py --engine-capability E:\11\drug-loop-harness\state\docking-engine-capabilities.json --input-manifest-verification E:\11\drug-loop-harness\state\docking-input-manifest-verification.json --result-normalization E:\11\drug-loop-harness\state\docking-result-normalization.json --output E:\11\drug-loop-harness\state\docking-readiness.json
```

Relevant readiness states include:

| State | Planner interpretation |
|---|---|
| `docking_engine_unavailable` | Environment repair, not molecular negative evidence. |
| `docking_inputs_verified_awaiting_result` | Inputs are ready for declared local cache execution. |
| `docking_proxy_cacheable_uncalibrated` | Freeze the raw, hash-bound score cache and compare policies; retain the uncalibrated limitation. |
| `docking_calibration_rejected` | Repair target/engine/scoring anchor binding. |
| `calibrated_proxy_needs_promotion` | A calibrated L3 proxy can be considered for bounded higher-fidelity follow-up. |
| `docking_result_verification_mismatch` | Repair result-to-verification binding before interpretation. |

Readiness distinguishes internal, declared local computation from consequential
actions. A verified manifest plus an available local engine may permit
autonomous cache construction; a normalized, hash-bound result may permit
autonomous cache replay. Neither permission upgrades a scientific claim or
authorizes external spend, wet-lab work, or publication-level efficacy claims.
