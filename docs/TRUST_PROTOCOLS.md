# Minimal Trust Protocols

These protocols are the hard center of the drug loop. They are intentionally
small. Their job is to keep the loop scientific without turning the project into
a large platform.

## 1. Oracle Anchor Protocol

Every oracle must declare what it borrows credibility from.

This separates:

- verifier shell: the tool or check that executes;
- oracle anchor: the external reason we trust the result at all.

Minimal schema:

```yaml
oracle: CarsiDock
kind: docking
verifier_shell: "pose prediction and scoring adapter"
anchors:
  - name: crystal_pose_recovery
    anchor_type: retrospective_validation
    validation_basis: "ability to recover known ligand poses"
    known_gap: "pose recovery is not binding affinity"
  - name: assay_enrichment
    anchor_type: retrospective_validation
    validation_basis: "enrichment on known active/decoy sets"
    known_gap: "benchmark enrichment can fail on new target families"
```

Hard rule:

> If an oracle has no declared anchor, its output can be stored but cannot
> support a claim.

Do not overdo this. One or two honest anchors are better than a long list of
weak justifications.

## 2. Evidence Level Protocol

Evidence levels define what kind of claim an observation can support.

Initial tiers:

| Tier | Meaning | Example |
|---|---|---|
| L0 | chemically valid | canonical SMILES, valence, PAINS/basic filters |
| L1 | property-filter pass | QED, logP, SA, simple rule constraints |
| L2 | ADMET proxy | ADMET prediction inside applicability domain |
| L3 | binding proxy | docking/pose/interactions plausible |
| L4 | higher-fidelity support | rescoring, consensus, free-energy surrogate |
| L5 | human medicinal review | signed expert preference or rejection |
| L6 | experimental evidence | biochemical/cell assay or other wet-lab result |

These tiers are not absolute truth. They are claim permissions.

Example claim permissions:

```yaml
triage:
  required: [L0, L1]

credible_hit:
  required: [L0, L2, L3]
  unresolved_flags_not_allowed:
    - out_of_domain
    - missing_provenance

experiment_ready:
  required: [L0, L2, L3, L5]
  recommended: [L4]
  human_required: true

validated_hit:
  required: [L6]
```

Hard rule:

> `qualified` is not enough by itself. A claim must state which evidence tier it
> requires.

## Adapter Output Provenance

Every adapter observation should carry enough provenance for TrustGate and later
audit to reconstruct what was run.

Minimal observation provenance:

```yaml
source: deterministic_chem_filter
adapter: DeterministicChemFilterAdapter
adapter_version: 0.1.0
ruleset: deterministic_chem_filter_v1
domain:
  - small_molecule
input_hash: sha256_of_declared_input
output_hash: sha256_of_normalized_output
```

Minimal observation metrics:

```yaml
passed: true
checks:
  nonempty: true
  allowed_characters: true
failure_reasons: []
```

Hard rules:

> Adapter output without provenance can be stored, but TrustGate must not admit
> it as claim-supporting evidence.

> Adapter output should report failed checks explicitly. A boolean `passed`
> value without the checks that created it is not enough for audit.

CSV-backed observations should keep provenance in explicit columns rather than
burying it inside metric columns:

```text
source, adapter, adapter_version, ruleset, domain, input_hash, output_hash
```

Nested metric fields such as `checks` and `failure_reasons` should be serialized
as JSON so they can round-trip through `TableObservationAdapter` without losing
audit detail.

## 3. Trust-Gap Monitor

The loop must measure how much weaker oracles degrade when checked by stronger
oracles.

Minimal metrics:

```text
reversal_rate = weak-pass then strong-fail / promoted-to-strong
waste_rate    = expensive call spent on candidates that should have been stopped
conflict_rate = strong observation conflicts with weak observation / promoted
```

Examples:

- ADMET-pass molecules later rejected by human review for obvious liabilities.
- Docking-top molecules later rejected by pose inspection or free-energy proxy.
- Generator-favored scaffold repeatedly fails synthesis or ADMET checks.

Hard rule:

> Promotion policy should optimize against observed trust-gap metrics, not only
> against proxy score.

This is the simplest way to stop the loop from becoming a proxy-score machine.

## 4. Claim Protocol

A claim protocol defines what the system is allowed to say.

Minimal claim levels:

| Claim | Meaning |
|---|---|
| triage | worth cheap follow-up |
| credible_hit | has enough qualified proxy evidence to stay alive |
| experiment_ready | worth spending scarce experiment or expert budget |
| human_signed | final recommendation with explicit human judgement |

Example:

```yaml
claim: experiment_ready
requires:
  - L0_CHEM_VALID
  - L2_ADMET_PROXY
  - L3_BINDING_PROXY
  - L5_HUMAN_REVIEW
allows_unresolved:
  - possible_false_negative
forbids_unresolved:
  - missing_provenance
  - out_of_domain
human_decision: required
```

Hard rule:

> The system can draft claims. It cannot silently upgrade a candidate from
> "credible hit" to "experiment ready" without satisfying the declared protocol.

## How The Four Protocols Fit

```text
OracleAnchorSpec
  says why an oracle is trusted at all

EvidenceLevelProtocol
  says what kind of evidence an observation provides

TrustGapMonitor
  says whether weaker oracles are still worth using

ClaimProtocol
  says what the system is allowed to recommend
```

That is the minimum viable scientific spine. Everything else is adapter work.
