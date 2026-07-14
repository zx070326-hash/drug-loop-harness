# From CALYPSO Loop to Drug Discovery Loop

The CALYPSO engineering flow is a reference implementation of loop engineering,
not a template to copy node by node.

The transferable pattern is:

> exploratory path decisions are allowed upstream, but conclusions must pass
> through a hard trust layer before they can become signable claims.

## Structural Mapping

| CALYPSO flow | Drug loop equivalent | Role |
|---|---|---|
| Candidate ingestion from CALYPSO/literature | Molecule ingestion from library, generator, literature, human design | Generator/path layer |
| `which_candidates` knob | Candidate prioritizer | Only chooses what to spend budget on |
| DFT job submission | Oracle action execution: filter, ADMET, docking, rescoring, free energy, synthesis, assay | Tool execution |
| Poll and terminal classification | Adapter-level status classification | Deterministic operational harness |
| SCF recovery ladder | Tool-specific retry/recovery policy | Execution reliability, not scientific conclusion |
| Geometry injection with human gate | Human-approved rescue or manual medicinal chemistry intervention | Explicit human intervention point |
| Alpha store | Raw evidence ledger | Append-only record of all observations, including failures |
| Pillar-5 trust gate | Oracle qualification gate | Converts raw observations into qualified evidence |
| Beta conclusion layer | Qualified evidence state | Only admitted observations can support claims |
| Three-axis judgement | Multi-objective drug candidate judgement | Binding, ADMET, synthesis, novelty, human preference |
| D7 human truth position | Medicinal chemist / experimental PI truth position | Claim-level judgement where no clean scalar oracle exists |
| MLIP concurrent learning | Surrogate active learning / model retraining | Future cheap layer that must be trained only on admitted evidence |
| Operations loop | Experiment/computation monitoring loop | Observability and recovery |

## What Transfers Directly

### 1. Path Layer Is Allowed To Be Messy

In CALYPSO, LLM touches only `which_candidates`. In drug discovery, LLMs,
generators, and heuristic search may choose candidate molecules, scaffold routes,
or next actions.

They must not directly write scientific claims.

### 2. Raw Evidence Is Not Conclusion

In CALYPSO, alpha stores everything, including failed and toxic rows. In drug
discovery, raw ADMET predictions, docking scores, failed conformer generation,
assay failures, and human rejects should all be stored.

Rejected evidence is part of the loop memory.

### 3. Trust Gate Is The Center

In CALYPSO, alpha-to-beta admission is the root of scientific credibility. In
drug discovery, the equivalent gate asks:

- Is this compound inside the oracle's applicability domain?
- Is the observation independent of the generator or previous proxy?
- Does this observation conflict with other evidence?
- Is this score likely to be gamed?
- Does the claim require promotion to a higher-fidelity oracle?
- Is provenance complete enough to support a claim?

### 4. Human In Truth Position Is A Feature

CALYPSO marks D7 as the largest implicit truth position because multi-axis
recommendation has no clean objective oracle.

Drug discovery has the same issue. Final recommendation often requires human
judgement over tradeoffs such as potency, toxicity, novelty, patentability,
synthesis, and assay strategy. The harness should expose that position instead
of hiding it behind a fake scalar score.

### 5. Future Surrogates Must Be Fed Only Qualified Evidence

CALYPSO's future MLIP loop trains only from admitted rows. The drug analogue is:

- train QSAR, ADMET, docking surrogate, or preference models only on qualified
  evidence;
- preserve model lineage;
- require applicability-domain checks before using the model to promote
  candidates;
- treat uncertainty as an abstention signal, not as a target to optimize.

## New Drug Loop Spine

```text
candidate ingestion
  -> path prioritization
  -> oracle action execution
  -> raw observation ledger
  -> trust qualification
  -> evidence graph update
  -> promotion decision
  -> claim protocol / human judgement
```

## Main Difference From CALYPSO

CALYPSO has DFT as a relatively strong physical oracle. Drug discovery has a
weaker and more fragmented oracle stack:

- docking can be gamed;
- ADMET predictors are domain-limited;
- free-energy estimates are expensive and still approximate;
- synthesis checks are incomplete;
- human review is valuable but subjective;
- wet-lab assay is stronger but scarce and noisy.

Therefore the drug loop must invest more design effort in oracle mapping,
conflict handling, and promotion policy.
