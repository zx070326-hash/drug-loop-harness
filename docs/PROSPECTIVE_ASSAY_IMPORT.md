# Prospective Assay Result Import

`ProspectiveAssayResultImporter` is the bridge from an already-produced
external assay receipt to a campaign `Observation` draft. It is deliberately
read-only: it does not contact a laboratory, append campaign events, run
TrustGate, consume an approval, or make a scientific claim.

## Endpoint Policy

Before any result is imported, an assay scientist reviews one
`prospective_assay_endpoint_policy.v1` artifact. Start from
`examples/prospective_assay_endpoint_policy.template.json` and bind it to the
registered prospective protocol hash. The policy fixes:

- the registered assay oracle and assay-protocol identifier;
- the raw metric key and exact unit;
- comparator and threshold used to derive the protocol's boolean primary
  endpoint;
- required named controls; and
- human acceptance with all authority fields remaining false.

The importer computes the protocol metric, normally `assay_validated`, from
the raw endpoint. It never trusts a laboratory-supplied endpoint label as the
study outcome.

## External Receipt

Start from `examples/prospective_assay_result.template.json`. A receipt binds
the plan, external randomization verification, single-subject approval, and
endpoint-policy hashes. It must also identify the campaign compound by SMILES
hash, give the assay run/site/protocol identifiers and timezone-aware run
times, declare passing run QC and all named controls, and point to a raw output
artifact with an exact SHA-256 hash.

A threshold failure is a valid negative assay endpoint, not a failed assay
measurement: the normalized observation contains
`{"assay_validated": false, "passed": true}`. A failed control, failed QC,
unit mismatch, altered raw output, budget excess, source mismatch, or
pre-approval run creates no observation draft.

## Import Boundary

```powershell
python E:\11\drug-loop-harness\scripts\import_prospective_assay_result.py E:\path\to\prospective-assay-result.json --endpoint-policy E:\path\to\assay-endpoint-policy.json --protocol E:\11\drug-loop-harness\state\prospective-loop-evaluation-protocol.json --plan E:\11\drug-loop-harness\state\prospective-acquisition-plan.json --randomization-verification E:\path\to\randomization-verification.json --approval E:\path\to\prospective-approval.json --campaign-store E:\path\to\campaign-events.json --output E:\11\drug-loop-harness\state\prospective-assay-import.json
```

`NORMALIZED` means only that the receipt has produced a policy-bound
observation draft. A later human-controlled admission appends it to campaign
state, runs TrustGate, and records the approval-consumption receipt used by the
prospective outcome verifier.

## Human-Controlled Admission

Stage the normalized assay receipt through the shared prospective admission
protocol. This produces a non-committed package containing the observation,
TrustGate ticket, and prospective-consumption draft; it does not alter the base
campaign or append the ledger.

```powershell
python E:\11\drug-loop-harness\scripts\stage_prospective_assay_admission_bundle.py E:\path\to\prospective-assay-result.json --endpoint-policy E:\path\to\assay-endpoint-policy.json --protocol E:\11\drug-loop-harness\state\prospective-loop-evaluation-protocol.json --plan E:\11\drug-loop-harness\state\prospective-acquisition-plan.json --randomization-verification E:\path\to\randomization-verification.json --approval E:\path\to\prospective-approval.json --campaign-store E:\path\to\campaign-events.json --reviewer assay-scientist@example.org --rationale "Reviewed bound assay receipt before staging." --output E:\11\drug-loop-harness\state\prospective-assay-admission-bundle.json
```

Replay-audit the bundle with the same endpoint policy and protocol before using
the existing generic commit command:

```powershell
python E:\11\drug-loop-harness\scripts\audit_prospective_assay_admission_bundle.py --bundle E:\11\drug-loop-harness\state\prospective-assay-admission-bundle.json --endpoint-policy E:\path\to\assay-endpoint-policy.json --protocol E:\11\drug-loop-harness\state\prospective-loop-evaluation-protocol.json --plan E:\11\drug-loop-harness\state\prospective-acquisition-plan.json --randomization-verification E:\path\to\randomization-verification.json --approval E:\path\to\prospective-approval.json --campaign-store E:\path\to\campaign-events.json --output E:\11\drug-loop-harness\state\prospective-assay-admission-bundle-audit.json
```

Only a `PASS_STAGED` bundle may enter
`commit_prospective_admission_bundle.py`, which retains its independent human
commit confirmation and recoverable local ledger semantics. Neither staging nor
audit selects a policy winner or upgrades a scientific claim.
