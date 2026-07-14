# Executor Guard Contract

The loop may propose actions, but executors do not receive raw authority. Every
live executor should sit behind `ExecutorGuard` or an equivalent stricter guard.

This keeps the project local-first while preparing for real ADMET, docking,
synthesis, human-review, free-energy, and experiment executors.

## Guard Decision Shape

Every action receives one decision before execution:

| Decision | Meaning |
|---|---|
| `allow` | Executor may run under the declared rule. |
| `approval_required` | The action is valid but needs an exact approval token. |
| `denied` | The action is not allowed by policy. |
| `invalid_arguments` | The action shape is not executable. |

Executor failures after `allow` are also structured:

| Status | Meaning |
|---|---|
| `timeout` | Executor exceeded its allowed runtime. |
| `error` | Executor raised an internal error. |
| `rate_limited` | Upstream service refused rate. |
| `auth_expired` | Credentials or session expired. |

These statuses are loop observations in the operational sense. They are not
scientific observations and must not pass through TrustGate as evidence.

## Default Action Policy

| Action | Default | Risk class | Extra requirements |
|---|---|---|---|
| `generate` | allow | draft_only | none |
| `enumerate` | allow | compute_only | none |
| `filter` | allow | compute_only | none |
| `predict_admet` | allow | compute_only | none |
| `dock` | allow | compute_only | timeout recorded |
| `rescore` | allow | compute_only | timeout recorded |
| `run_free_energy` | approval_required | expensive_compute | `approval_id`, `idempotency_key` |
| `check_synthesis` | allow | compute_only | none |
| `ask_human` | approval_required | human_review | `approval_id` |
| `send_to_experiment` | approval_required | external_experiment | `approval_id`, `idempotency_key` |
| `terminate_route` | approval_required | state_change | `approval_id` |
| `open_route` | allow | state_change | none |

The default policy is intentionally conservative around scarce human review,
expensive free-energy calculations, and experimental action.

## Exact Approval

Approval is scoped to one action record. A vague statement like "go ahead" is
not a reusable approval. The action must carry an explicit approval field:

```json
{
  "approval_id": "approval_2026_07_10_cmpd_001_human_review"
}
```

Actions that can create costly duplicate external work must also carry:

```json
{
  "idempotency_key": "experiment_cmpd_001_v1_once"
}
```

## Driver Trace Semantics

`BoundedLoopDriver` preserves guard and executor statuses in action traces:

```text
applied
missing
skipped
invalid
approval_required
denied
invalid_arguments
timeout
error
```

Only `applied` traces can add scientific observations to `LoopState`.
`approval_required`, `denied`, `invalid_arguments`, `timeout`, and `error`
cannot support claims.

`LoopRunReport` summarizes:

- applied observations;
- missing observations;
- skipped observations;
- invalid observations;
- blocked actions;
- failed actions;
- per-round stop reasons.

## Local Command Executor

`LocalCommandExecutor` is the first external-style executor adapter. It remains
local and reproducible, but it behaves like a real tool boundary:

- the command spec is fixed by application code;
- no arbitrary shell string is accepted from policy output;
- each command must emit JSON for one normalized observation;
- malformed JSON becomes `invalid`;
- timeout becomes `timeout`;
- non-zero exit becomes `error`;
- only `observed` command results can be considered by `BoundedLoopDriver`;
- TrustGate still decides whether the observation is scientific evidence.

Expected command output shape:

```json
{
  "id": "obs_cmd_cmpd_001",
  "subject_id": "cmpd_001",
  "oracle_name": "basic_chem_filter",
  "metrics": {"passed": true},
  "cost_spent": 1.0,
  "provenance": {
    "source": "local_command_fixture",
    "domain": ["small_molecule"]
  }
}
```

## Hard Rules

- ExecutorGuard runs before side effects.
- Guard decisions are recorded in trace, not hidden as exceptions.
- Missing approval is a normal blocked action, not a crash.
- Executor errors become structured trace statuses.
- TrustGate remains the only evidence-admission boundary.
- ReportBuilder is read-only and cannot override guard or TrustGate decisions.
