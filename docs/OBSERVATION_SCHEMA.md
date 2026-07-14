# Observation CSV Schema

This schema is the narrow adapter contract for table-backed drug-loop evidence.
It is intentionally simple: every row is one normalized observation about one
subject, and every row must be auditable after it passes through TrustGate.

## Common Columns

Required:

| Column | Meaning |
|---|---|
| `id` | Stable observation id. |
| `subject_id` | Compound id or route id being observed. |
| `oracle_name` | Registered oracle name, such as `basic_chem_filter`, `table_admet`, `table_docking`, `table_rescoring`, or `human_review`. |

Optional control columns:

| Column | Meaning |
|---|---|
| `uncertainty` | Numeric uncertainty if the oracle reports it. |
| `cost_spent` | Cost already spent to obtain this observation. |
| `domain` | Pipe-separated applicability labels, for example `protein_ligand|non_covalent`. |

Provenance columns:

| Column | Meaning |
|---|---|
| `source` | Dataset, tool run, fixture, or adapter source. |
| `adapter` | Adapter implementation name. |
| `adapter_version` | Adapter implementation version. |
| `ruleset` | Rule or model version used by the adapter. |
| `input_hash` | Hash of declared input payload. |
| `output_hash` | Hash of normalized adapter output. |

`TableObservationAdapter` preserves the provenance columns above. All other
non-empty columns become metrics. JSON objects and arrays are parsed when a
cell begins with `{` or `[`.

## Oracle-Specific Metric Columns

### Chem Filter

Typical oracle name: `basic_chem_filter`

| Metric | Type | Meaning |
|---|---|---|
| `passed` | boolean | Whether the molecule passed the deterministic filter. |
| `checks` | JSON object | Named check results, all explicit. |
| `failure_reasons` | JSON array | Failed check names. |
| `heavy_atom_count` | integer | Approximate heavy atom count. |
| `approx_molecular_weight` | number | Lightweight approximate molecular weight. |

### ADMET

Typical oracle name: `table_admet`

| Metric | Type | Meaning |
|---|---|---|
| `passed` | boolean | Whether ADMET proxy constraints passed. |
| `hERG_risk` | string | Risk bucket or model label. |
| `solubility` | string/number | Solubility bucket or value. |

Additional endpoint columns are allowed as metrics, but the adapter should
record enough provenance to identify the model or source.

### Docking

Typical oracle name: `table_docking`

| Metric | Type | Meaning |
|---|---|---|
| `passed` | boolean | Whether the pose/score is usable as proxy evidence. |
| `score` | number | Docking score; lower is currently treated as stronger. |
| `pose_valid` | boolean | Whether the pose passed local sanity checks. |
| `steric_clash` | boolean | Whether severe clash was detected. |
| `ligand_strain_flag` | boolean | Whether ligand strain makes the pose suspect. |

### Rescoring / Free-Energy Surrogate

Typical oracle name: `table_rescoring`

| Metric | Type | Meaning |
|---|---|---|
| `passed` | boolean | Whether the stronger oracle supports the candidate. |
| `score` | number | Stronger score or surrogate free-energy estimate. |
| `pose_valid` | boolean | Optional pose check if applicable. |
| `steric_clash` | boolean | Optional clash flag. |
| `ligand_strain_flag` | boolean | Optional strain flag. |

### Human Review

Typical oracle name: `human_review`

| Metric | Type | Meaning |
|---|---|---|
| `decision` | string | `approve`, `reject`, or an equivalent normalized decision. |
| `passed` | boolean | Optional direct approval boolean. |

Human review is evidence, not automatic truth. A human rejection should remain
visible and should not be transformed into an experiment-ready claim.

## Hard Rules

- Missing provenance can be stored, but TrustGate may abstain from admitting it.
- `passed=true` without check detail is weak evidence for generated adapters.
- Domain labels must match the registered oracle applicability domain.
- Failed and rejected observations remain in the campaign state.
- New adapter-specific metric columns are allowed, but new provenance concepts
  should be added explicitly to this schema instead of hidden as metrics.
