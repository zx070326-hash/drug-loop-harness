# Executor Output Schema

This file defines the JSON object a command-backed or external-style executor
must return before `BoundedLoopDriver` can consider it for TrustGate.

It is not a claim schema. It is a tool-output schema.

## Required Shape

Each executor result for one subject should emit one JSON object:

```json
{
  "id": "obs_tool_cmpd_001",
  "subject_id": "cmpd_001",
  "oracle_name": "table_docking",
  "metrics": {
    "passed": true,
    "score": -8.6
  },
  "uncertainty": 0.2,
  "cost_spent": 5.0,
  "provenance": {
    "source": "local_command_or_tool_name",
    "adapter": "AdapterClassName",
    "adapter_version": "0.1.0",
    "ruleset": "model_or_ruleset_version",
    "domain": ["protein_ligand", "non_covalent"],
    "input_hash": "sha256...",
    "output_hash": "sha256..."
  },
  "notes": "optional short note"
}
```

Required fields:

| Field | Meaning |
|---|---|
| `id` | Stable observation id. |
| `subject_id` | Subject the action requested. |
| `oracle_name` | Oracle expected for the action. |
| `metrics` | Oracle-specific normalized metrics object. |

Recommended fields:

| Field | Meaning |
|---|---|
| `cost_spent` | Local estimate of executor cost. |
| `uncertainty` | Numeric uncertainty if known. |
| `provenance` | Tool, version, domain, input hash, and output hash. |

## Driver-Level Rejections

The driver rejects structurally unsafe outputs before TrustGate:

| Condition | Trace status | Reason |
|---|---|---|
| Not JSON | `invalid` | `malformed_json_output` |
| JSON is not an object | `invalid` | `output_not_object` |
| Missing required fields | `invalid` | `missing_output_fields:...` |
| `metrics` is not an object | `invalid` | `metrics_not_object` |
| `provenance` is not an object | `invalid` | `provenance_not_object` |
| Subject mismatch | `invalid` | `subject_mismatch` |
| Oracle mismatch | `invalid` | `oracle_mismatch` |
| Command timeout | `timeout` | `command_timeout` |
| Command non-zero exit | `error` | `command_failed:<code>` |

Invalid, timeout, and error outputs cannot enter TrustGate and cannot support a
claim.

## TrustGate-Level Rejections

Some outputs are structurally valid but scientifically insufficient:

| Condition | TrustGate behavior |
|---|---|
| Missing provenance | abstain with `missing_provenance` flag |
| Missing applicability domain | abstain with `out_of_domain` flag |
| Failed local quality checks | rejected |
| Same-oracle conflict | conflicted |

These observations may remain visible in `LoopState` for audit, but they cannot
support a claim unless TrustGate admits them.

## Oracle Metric Profiles

### Chem Filter

```json
{
  "metrics": {
    "passed": true,
    "checks": {"nonempty": true},
    "failure_reasons": [],
    "heavy_atom_count": 18,
    "approx_molecular_weight": 250.3
  }
}
```

### ADMET

```json
{
  "metrics": {
    "passed": true,
    "hERG_risk": "low",
    "solubility": "ok"
  }
}
```

### Docking

```json
{
  "metrics": {
    "passed": true,
    "score": -8.6,
    "pose_valid": true,
    "steric_clash": false,
    "ligand_strain_flag": false
  }
}
```

### Rescoring / Free-Energy Surrogate

```json
{
  "metrics": {
    "passed": true,
    "score": -8.2
  }
}
```

### Human Review

```json
{
  "metrics": {
    "decision": "approve",
    "passed": true
  }
}
```

Human review still requires executor approval before the action can run.
