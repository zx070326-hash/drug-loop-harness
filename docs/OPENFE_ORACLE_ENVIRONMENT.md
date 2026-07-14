# OpenFE Oracle Environment

## Decision

OpenFE is the first high-fidelity physics engine for the loop. It provides a
modern, inspectable RBFE/ABFE execution boundary on OpenMM. It is not itself a
scientific claim and it is not admitted as an oracle until a target-specific
protocol and validation gate have been frozen.

LamNet is the closest current match to Tingjun Hou's alchemical free-energy
research. It should enter later as a learned surrogate or lambda-schedule
advisor, calibrated against independent OpenFE calculations and experimental
references. CaFE remains useful as a historical Hou-lab endpoint-free-energy
baseline, but it is not a replacement for alchemical FEP.

## Qualified Local Environment

- Runtime: WSL2, Ubuntu 26.04 LTS
- GPU: NVIDIA GeForce RTX 5070, 12227 MiB
- Driver: 595.95, exposing CUDA 13.2
- Prefix: `/opt/openfe-1.12.0`
- OpenFE: 1.12.0
- OpenMM: 8.4.0
- OpenMMTools: 0.26.0
- PyMBAR: 4.2.0
- GUFE: 1.12.0
- Python: 3.12.11

The fixed installer and SHA-256 are recorded in
`state/openfe-wsl-environment-v1.json`.

## Compatibility Adjustment

The upstream OpenFE 1.12.0 installer selected CUDA 13.3. The local WSL driver
exposed CUDA 13.2, so the first OpenMM CUDA probe failed with
`CUDA_ERROR_UNSUPPORTED_PTX_VERSION (222)`. Only the isolated environment's
CUDA runtime packages were pinned to 13.2. OpenFE, OpenMM, OpenMMTools, PyMBAR,
and GUFE were not changed.

## Verification

`python -m openmm.testInstallation` passed Reference, CPU, and CUDA force
evaluation, with every cross-platform force difference within tolerance.

The standard `openfe test` suite completed with:

- 2045 collected
- 1908 passed
- 137 skipped
- 0 failed
- 228 warnings
- 983.29 seconds

The standard suite intentionally omits tests gated by `openfe test --long`.
Environment qualification therefore means the software stack is operational;
it does not establish target-specific accuracy or convergence.

## Use

Run commands without changing the default Windows Python:

```powershell
wsl -d Ubuntu -- env PATH=/opt/openfe-1.12.0/bin:/usr/bin:/bin openfe --version
wsl -d Ubuntu -- /opt/openfe-1.12.0/bin/python -m openmm.testInstallation
```

## Next Scientific Gate

Before the harness may consume an OpenFE observation, define one public,
congeneric protein-ligand benchmark and freeze:

1. receptor structure, missing-residue treatment, protonation, waters, ions,
   and cofactors;
2. ligand stereochemistry, tautomer/protomer state, charge model, and force
   field;
3. atom mapping, network topology, lambda schedule, replicas, sampling length,
   and random seeds;
4. convergence diagnostics, cycle closure, overlap/effective-sample criteria,
   failure and abstention rules;
5. experimental reference provenance and preregistered accuracy metrics.

Until that gate passes, the environment is `environment_ready=true` and
`scientific_oracle_ready=false`.
