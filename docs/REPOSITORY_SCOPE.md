# Repository Scope

## Status

This repository is a private research preview. No open-source license is
currently granted. The code and results should not be redistributed or cited as
a validated drug-discovery system without the owner's permission.

## Included

- the v1 and v2 loop-engineering source code;
- policy, TrustGate, ledger, receipt, and evaluation tests;
- scripts that register, run, and verify bounded experiments;
- architecture and experiment-design documentation;
- the small frozen Jensen policy-curve and reliability-stress protocols and
  aggregate evaluations;
- the prospective v2 design handoff and its verification.

## Excluded

The following stay on the local research workstation and are ignored by Git:

- downloaded Jensen, TocoDecoy, LIT-PCBA, Zenodo FEP, receptor, and ligand data;
- external repositories, wheels, and virtual environments;
- docking poses, molecular dynamics trajectories, OpenFE payloads, and raw
  simulation results;
- active PTP1B and JNK1 scheduler/runtime state;
- large replay ledgers, oracle caches, logs, and intermediate reports;
- held-out labels and evaluator-only truth not explicitly approved for release.

These exclusions avoid accidental third-party redistribution, GitHub file-size
violations, leakage from active experiments, and presentation of incomplete
runtime state as a scientific result.

## Reproduction Boundary

The source and small example fixtures support local code review and core tests.
Full Jensen and FEP reproduction requires independently obtaining the cited
datasets and rebuilding the qualified environments described in the experiment
documents. Frozen artifact hashes remain in the protocols so downloaded inputs
can be checked against the development run.

The current results are development evidence. They do not establish policy
superiority, prospective discovery efficiency, or molecular activity.
