# Prospective Admission Commit

`prospective_admission_commit` is the local durability protocol after a
`PASS_STAGED` admission bundle. It does not replace the source campaign event
store. Instead, it writes a new commit directory containing an immutable staged
bundle snapshot, the replay-audit receipt observed at commit preparation, a
derived campaign artifact, and a final receipt that makes that artifact
eligible for later reads.

## Protocol

For one human-confirmed commit id, the committer performs these ordered steps:

1. Re-audit the staged bundle against its original raw result and base campaign.
2. Write immutable `commit-intent.json` and `admission-bundle-audit.json`,
   binding the bundle, passing replay audit, base campaign, derived campaign,
   consumption record, approval id, and absolute ledger path.
3. Materialize `admission-bundle.json`, `campaign-events.json`, and
   `prospective-consumption.json` in the new commit directory. The source
   campaign file is not modified.
4. Acquire an exclusive local `<ledger>.commit.lock`, re-read the ledger, and
   append the exact candidate consumption record with `fsync` only when that
   approval id is absent.
5. Write `commit-receipt.json` only after the exact ledger row is durable.

The receipt is the commit marker. A downstream reader must use the derived
campaign artifact only when `audit_status=PASS_COMMITTED` verifies its intent,
persisted bundle snapshot, persisted staged-audit receipt, current base
campaign, candidate artifacts, ledger row, and receipt together. The snapshot
preserves the import artifact and its raw-result/output hashes; it does not
claim that a hash alone proves the biological result.

## Failure States

| Audit state | Meaning | Allowed response |
| --- | --- | --- |
| `PASS_COMMITTED` | Intent, bundle snapshot, staged-audit receipt, derived artifacts, final receipt, and one exact ledger row bind. | Read derived campaign as committed evidence state. |
| `RECOVERABLE_APPEND` | Intent and candidate artifacts exist; ledger has no row. | A human-confirmed recovery may append then write receipt. |
| `RECOVERABLE_FINALIZE` | Exact ledger row exists; final receipt is absent. | A human-confirmed recovery may write receipt only. |
| `REPAIR_REQUIRED` | Intent exists but candidate artifacts are absent. | Stop; inspect and repair explicitly. No automatic reconstruction. |
| `FAIL` | Hash, provenance, source state, ledger, or receipt conflict. | Do not recover or use derived evidence. |

The lock is deliberately fail-closed. A lock left by a crashed process is not
stolen or silently removed. It must be inspected under a separate operational
procedure before any recovery; otherwise the system cannot distinguish a stale
lock from another active committer.

This is a local single-writer recovery protocol, not a distributed transaction
or an exactly-once claim across independent machines or filesystems.

## Commands

Commit a staged package:

```powershell
python E:\11\drug-loop-harness\scripts\commit_prospective_admission_bundle.py --bundle E:\11\drug-loop-harness\state\prospective-admission-bundle.json --plan E:\11\drug-loop-harness\state\prospective-acquisition-plan.json --randomization-verification E:\11\drug-loop-harness\state\external-randomization-verification.json --approval E:\11\drug-loop-harness\state\prospective-acquisition-approval.json --campaign-store E:\path\to\base-campaign-events.json --ledger E:\11\drug-loop-harness\state\prospective-acquisition-consumptions.jsonl --commit-root E:\11\drug-loop-harness\state\prospective-admission-commits --reviewer committer@example.org --rationale "Verified the staged package before durable commit." --commit-id commit_20260716_001 --output E:\11\drug-loop-harness\state\prospective-admission-commit-outcome.json
```

Audit it:

```powershell
python E:\11\drug-loop-harness\scripts\audit_prospective_admission_commit.py --bundle E:\11\drug-loop-harness\state\prospective-admission-bundle.json --plan E:\11\drug-loop-harness\state\prospective-acquisition-plan.json --randomization-verification E:\11\drug-loop-harness\state\external-randomization-verification.json --approval E:\11\drug-loop-harness\state\prospective-acquisition-approval.json --campaign-store E:\path\to\base-campaign-events.json --ledger E:\11\drug-loop-harness\state\prospective-acquisition-consumptions.jsonl --commit-directory E:\11\drug-loop-harness\state\prospective-admission-commits\commit_20260716_001 --output E:\11\drug-loop-harness\state\prospective-admission-commit-audit.json
```

Only after an audit reports `RECOVERABLE_APPEND` or `RECOVERABLE_FINALIZE` may
a human run `recover_prospective_admission_commit.py`. It requires the same
source controls plus a fresh recovery reviewer and rationale.

For downstream loop components, use the resolver rather than treating the
presence of `campaign-events.json` as sufficient evidence of commit:

```powershell
python E:\11\drug-loop-harness\scripts\resolve_prospective_admission_commit.py --bundle E:\11\drug-loop-harness\state\prospective-admission-bundle.json --plan E:\11\drug-loop-harness\state\prospective-acquisition-plan.json --randomization-verification E:\11\drug-loop-harness\state\external-randomization-verification.json --approval E:\11\drug-loop-harness\state\prospective-acquisition-approval.json --campaign-store E:\path\to\base-campaign-events.json --ledger E:\11\drug-loop-harness\state\prospective-acquisition-consumptions.jsonl --commit-directory E:\11\drug-loop-harness\state\prospective-admission-commits\commit_20260716_001 --output E:\11\drug-loop-harness\state\prospective-admission-commit-resolution.json
```

Only `resolution_status=RESOLVED_COMMITTED_CAMPAIGN` exposes the derived
campaign path. Candidate-only, recoverable, and invalid commit directories
produce a rejected resolution instead of a state handle.

For a prospective outcome report, first produce the read-only evidence artifact
for each committed assay package. The outcome verifier consumes the same commit
directories and verifies its ordered evidence set. Copy the emitted
`source_evidence_content_sha256` into the outcome report's
`source_admission_commit_evidence_content_sha256` field before verification.

```powershell
python E:\11\drug-loop-harness\scripts\verify_prospective_admission_commit_evidence.py --commit-directory E:\11\drug-loop-harness\state\prospective-admission-commits\commit_20260716_001 --output E:\11\drug-loop-harness\state\prospective-admission-commit-evidence-001.json
```

Neither a commit nor recovery calls an oracle, changes the base campaign,
raises a TrustGate tier, or upgrades a scientific claim.
