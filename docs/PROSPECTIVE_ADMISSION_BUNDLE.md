# Prospective Admission Bundle

`prospective_admission_bundle.v1` is the controlled boundary between a
normalized external ADMET result and a future durable campaign update. It is a
single JSON package, not a transaction coordinator.

## Inputs

The builder receives:

- the original `prospective_admet_result.v1` file and its raw output artifact;
- the prospective acquisition plan, verified external randomization, and exact
  human preparation approval;
- the current immutable campaign event-store artifact;
- a second human admission decision, reviewer, rationale, and timezone-aware
  timestamp; and
- optionally, the existing strict prospective consumption ledger for a
  single-use approval check.

It rebuilds the campaign state, re-verifies the external result, runs
`TrustGate`, and produces one package with:

- the normalized source-import artifact and its content hash;
- a candidate `ObservationRecorded` event and `TrustGateResultRecorded` event
  on a derived campaign snapshot;
- a candidate prospective-consumption record that binds the result to its
  one-time approval; and
- base and derived campaign hashes plus all control-artifact hashes.

The TrustGate ticket is deterministic for the supplied admission id. This
makes a later audit a genuine replay rather than a comparison of two randomly
named tickets.

## Semantics

`bundle_status=staged_for_explicit_commit` means only that the source can be
re-read and the full candidate package can be recomputed from the supplied
base campaign and control artifacts. It does **not** mean that any event store
or consumption ledger was changed.

All staged bundles set these fields to `false`:

- `ledger_append_performed`
- `execution_authority_granted`
- `autonomous_execution_permitted`
- `scientific_claim_upgrade_permitted`

An accepted human admission may still contain a rejected TrustGate ticket. The
external action is then represented by a candidate consumption record, while
the observation is not admitted as usable evidence. A positive ticket remains
only the tier and claim permission assigned by TrustGate; the bundle never
raises that claim level.

If result provenance, current campaign binding, ledger readability, duplicate
approval, or consumption binding fails, the builder writes a `rejected`
diagnostic package with no staged events and no consumption draft.

## Commands

Stage a package without changing either durable store:

```powershell
python E:\11\drug-loop-harness\scripts\stage_prospective_admission_bundle.py E:\path\to\external-admet-result.json --plan E:\11\drug-loop-harness\state\prospective-acquisition-plan.json --randomization-verification E:\11\drug-loop-harness\state\external-randomization-verification.json --approval E:\11\drug-loop-harness\state\prospective-acquisition-approval.json --campaign-store E:\path\to\campaign-events.json --ledger E:\11\drug-loop-harness\state\prospective-acquisition-consumptions.jsonl --reviewer reviewer@example.org --rationale "Reviewed the raw source and staged TrustGate result." --admission-id admission_20260716_001 --output E:\11\drug-loop-harness\state\prospective-admission-bundle.json
```

Audit it by re-reading the original source result and rebuilding the candidate
package:

```powershell
python E:\11\drug-loop-harness\scripts\audit_prospective_admission_bundle.py --bundle E:\11\drug-loop-harness\state\prospective-admission-bundle.json --plan E:\11\drug-loop-harness\state\prospective-acquisition-plan.json --randomization-verification E:\11\drug-loop-harness\state\external-randomization-verification.json --approval E:\11\drug-loop-harness\state\prospective-acquisition-approval.json --campaign-store E:\path\to\campaign-events.json --ledger E:\11\drug-loop-harness\state\prospective-acquisition-consumptions.jsonl --output E:\11\drug-loop-harness\state\prospective-admission-bundle-audit.json
```

`PASS_STAGED` is an internal reproducibility result. It is not a commit result,
an external execution permission, or evidence that the prediction is true.

## Commit Boundary

The campaign event store and the append-only consumption ledger are separate
files, so a sequential write cannot honestly promise atomic admission. The
next command is therefore an explicit, recoverable commit protocol: it writes a
hash-bound intent, materializes a new derived campaign artifact, appends the
exact consumption record once under a single-writer lock, then writes a final
commit receipt. Incomplete states remain visible for repair rather than being
silently loaded as committed evidence. See
`docs/PROSPECTIVE_ADMISSION_COMMIT.md`.
