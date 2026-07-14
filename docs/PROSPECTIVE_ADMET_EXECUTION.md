# Prospective ADMET-AI Execution

`ProspectiveAdmetExecutionRunner` is the local execution boundary for one
human-confirmed ADMET-AI command-line attempt. It uses the documented
`admet_predict --data_path ... --save_path ... --smiles_column ...` interface
from the [ADMET-AI project](https://github.com/swansonk14/admet_ai). The
project documents local installation through `pip install admet-ai`; this
harness does not install packages, download models, or call the public web
server on its own.

## Scope

The runner writes a new attempt directory with:

- `execution-intent.json`, written before any provider command;
- `input.csv`, containing exactly one approved subject id and SMILES;
- model-version command stdout/stderr and prediction command stdout/stderr;
- the raw `predictions.csv` only when the local provider produced a non-empty
  output; and
- `execution-receipt.json`, including every artifact hash and command outcome.

It does not modify the campaign event store, prospective-consumption ledger, or
approval record. It also does not parse ADMET endpoints into `metrics`, choose
thresholds, set `passed`, run TrustGate, or upgrade a claim.

## Profile

Start from:

- `examples/admet_ai_execution_profile.template.json`
- `examples/admet_ai_model_manifest.template.json`

The profile fixes the prediction command prefix, a separate version command,
expected package version, endpoint-set identifier, timeout, and a hash-bound
local model-manifest file. The manifest must be prepared and reviewed for the
specific local installation; a declared package name or endpoint set alone is
not model provenance.

`accounted_cost_per_attempt` and `cost_accounting_basis` are a predeclared
budget-accounting rule for an invoked prediction command. The runner rejects a
profile whose amount exceeds the single-subject approved budget. A blocked or
unavailable attempt that never starts `admet_predict` records zero spend rather
than consuming the profile's cost.

Use absolute paths for `model_manifest_path`. The runner rehashes that file
before it invokes the version or prediction command. A missing or changed
manifest creates `BLOCKED_PROFILE`, not a prediction.

## Invocation

```powershell
python E:\11\drug-loop-harness\scripts\run_prospective_admet_ai.py --profile E:\path\to\admet-ai-profile.json --plan E:\11\drug-loop-harness\state\prospective-acquisition-plan.json --randomization-verification E:\11\drug-loop-harness\state\external-randomization-verification.json --approval E:\11\drug-loop-harness\state\prospective-acquisition-approval.json --campaign-store E:\path\to\committed-campaign-events.json --attempt-root E:\11\drug-loop-harness\state\prospective-admet-attempts --reviewer operator@example.org --rationale "Reviewed request, local profile, manifest, and planned endpoint set." --attempt-id admet_attempt_20260716_001
```

The command requires a current accepted preparation approval and a new human
execution confirmation. `UNAVAILABLE`, `TIMED_OUT`, `FAILED`, and
`BLOCKED_*` outcomes are operational facts, not negative ADMET evidence.

Audit the result without rerunning the provider:

```powershell
python E:\11\drug-loop-harness\scripts\audit_prospective_admet_execution.py --attempt-directory E:\11\drug-loop-harness\state\prospective-admet-attempts\admet_attempt_20260716_001 --profile E:\path\to\admet-ai-profile.json --plan E:\11\drug-loop-harness\state\prospective-acquisition-plan.json --randomization-verification E:\11\drug-loop-harness\state\external-randomization-verification.json --approval E:\11\drug-loop-harness\state\prospective-acquisition-approval.json --campaign-store E:\path\to\committed-campaign-events.json --output E:\11\drug-loop-harness\state\prospective-admet-execution-audit.json
```

`PASS_CONTROL` proves only that the local control chain and bytes still match.
It does not assess endpoint calibration, applicability, biological relevance,
or prediction truth.

## Interrupted Attempts

If an intent exists without a receipt, the audit returns
`INCOMPLETE_REQUIRES_INCIDENT_REVIEW`. The process may have been interrupted
before, during, or after an external command; the harness must not retry that
approval automatically. Treat it as an execution incident and establish what
the provider actually received before choosing a new acquisition action.

## Deferred Interpretation Boundary

The next component must turn a specific raw endpoint set into a
`prospective_admet_result.v1` only through a reviewed interpretation policy:
endpoint selection, units, direction, missing-value behavior, applicability
criteria, `passed` rule, uncertainty semantics, and profile/calibration scope.
That policy is scientific content and cannot be hidden inside the command
adapter.
