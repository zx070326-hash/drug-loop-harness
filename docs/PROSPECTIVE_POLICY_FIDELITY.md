# Prospective Policy Decision Fidelity

An arm assignment proves allocation before outcomes; it does not by itself
prove that the registered promotion policy produced the later experimental
decision. `ProspectivePolicyDecisionFidelityVerifier` is the read-only replay
boundary for that missing treatment-fidelity fact.

One `prospective_policy_decision_receipt.v1` binds a randomized assignment to:

- the frozen protocol, arm assignment, and arm `policy_spec` hashes;
- a named, immutable campaign state whose every event timestamp is no later
  than `decided_at` and has no assay outcome for any subject in the current
  externally assigned study set;
- the full semantic policy-output hash, excluding random action IDs; and
- one selected policy decision for the assigned subject.

The verifier replays only implemented policy semantics. It currently supports:

- `fixed_cascade` with an explicit non-empty `cascade` of `ActionKind` values;
- `trust_gap_aware` with only `max_actions`, `min_multiplier`, and
  `min_value_per_cost` parameters.

The protocol registrar uses this same runtime contract, so an unsupported policy
kind or unimplemented declared parameter cannot be registered. The fidelity
verifier also fails closed for malformed or legacy artifacts. It is not
acceptable to silently ignore a frozen policy field while claiming treatment
fidelity.

Each registered arm also freezes a `prospective_policy_runtime_manifest.v1` and
its content hash. The manifest identifies the runtime contract and content
hashes for the local policy, contract, and replay modules. Receipt recording,
replay verification, and final outcome verification all require the same
runtime hash, so a code change cannot silently re-label a different policy
implementation as the registered intervention.

Record the deterministic decision from the frozen arm policy and an immutable
pre-outcome campaign state. This writes evidence only: it does not execute the
selected action or authorize an assay.

```powershell
python E:\11\drug-loop-harness\scripts\record_prospective_policy_decision_receipt.py --protocol E:\11\drug-loop-harness\state\prospective-loop-evaluation-protocol.json --assignment E:\path\to\external-arm-assignment.json --assignment-verification E:\11\drug-loop-harness\state\prospective-loop-arm-assignment-verification.json --decision-campaign-store E:\path\to\pre-outcome-campaign.json --assignment-id assignment_baseline --decided-at 2026-07-10T02:30:00+00:00 --output E:\11\drug-loop-harness\state\policy-decision-receipt-baseline.json
```

The recorder refuses a campaign event after `decided_at`, any assay outcome for
the current assignment set, a decision before assignment, an unknown
assignment, or a subject for which the frozen policy has zero or more than one
decision. It also requires the independent arm-assignment verification to be
`PASS` and hash-bound to the same protocol and assignment. Assay evidence for
an unassigned historical subject remains admissible policy context. Its receipt
is subsequently checked by an independent replay verifier. The receipt also
requires `assigned_at <= assignment_verification.verified_at <= decided_at`;
an assignment verification performed after the policy decision is not valid
evidence that allocation preceded the intervention.

```powershell
python E:\11\drug-loop-harness\scripts\verify_prospective_policy_decision_fidelity.py E:\11\drug-loop-harness\state\prospective-loop-evaluation-protocol.json --assignment E:\path\to\external-arm-assignment.json --assignment-verification E:\11\drug-loop-harness\state\prospective-loop-arm-assignment-verification.json --receipt E:\path\to\policy-decision-receipt.json --decision-campaign-store E:\path\to\pre-outcome-campaign.json --output E:\11\drug-loop-harness\state\prospective-policy-fidelity.json
```

A `PASS` means only that the declared policy replayed to the reported decision
on the supplied pre-outcome state. It does not execute the decision, authorize
the assay, rank policies, establish that the policy was followed through every
later state transition, or upgrade a scientific claim.

Build the set used by one outcome report before preparing that report. Copy its
`source_fidelity_content_sha256` into the report's
`source_policy_fidelity_content_sha256` field.

```powershell
python E:\11\drug-loop-harness\scripts\build_prospective_policy_fidelity_set.py E:\11\drug-loop-harness\state\prospective-loop-evaluation-protocol.json --assignment E:\path\to\external-arm-assignment.json --assignment-verification E:\11\drug-loop-harness\state\prospective-loop-arm-assignment-verification.json --receipt E:\path\to\policy-decision-receipt-baseline.json --decision-campaign-store E:\path\to\pre-outcome-campaign-baseline.json --receipt E:\path\to\policy-decision-receipt-credibility.json --decision-campaign-store E:\path\to\pre-outcome-campaign-credibility.json --output E:\11\drug-loop-harness\state\prospective-policy-fidelity-set.json
```
