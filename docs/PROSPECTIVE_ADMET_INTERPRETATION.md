# Prospective ADMET Endpoint Interpretation

`ProspectiveAdmetEndpointInterpreter` is the scientific semantics boundary
between a raw ADMET-AI CSV and `prospective_admet_result.v1`. It does not infer
meaning from column names, select favorable endpoints, or supply hidden
thresholds.

## Reviewed Policy

Start from `examples/admet_endpoint_interpretation_policy.template.json`. A
policy binds to the exact execution-profile content hash and declares the
model/profile/endpoint-set identity, molecular domain, exact raw CSV row
binding, every endpoint's unit/direction/threshold/rationale, and a human
reviewer with a review time. The review must predate the execution receipt's
actual command start.

Only `at_most` and `at_least` comparisons are supported. Every declared
endpoint must be present exactly once in the uniquely bound CSV row and parse
to a finite number. There is no hidden unit conversion or missing-value
imputation.

## Outcomes

- A valid policy evaluation whose endpoint rule fails produces a result draft
  with `metrics.passed=false`. This is valid negative proxy evidence.
- Missing columns, ambiguous row binding, non-finite values, profile/policy
  mismatch, post-hoc review, invalid execution receipt, or raw artifact drift
  produce `REJECTED` and no result draft.

The result carries execution-intent, execution-receipt, profile, and policy
hashes. Its model hash is the reviewed model-manifest hash; its `cost_spent`
comes from the execution receipt's predeclared accounting rule, never from an
approval budget ceiling.

## Commands

```powershell
python E:\11\drug-loop-harness\scripts\interpret_prospective_admet_endpoints.py --attempt-directory E:\11\drug-loop-harness\state\prospective-admet-attempts\admet_attempt_20260716_001 --profile E:\path\to\admet-ai-profile.json --policy E:\path\to\admet-endpoint-policy.json --plan E:\11\drug-loop-harness\state\prospective-acquisition-plan.json --randomization-verification E:\11\drug-loop-harness\state\external-randomization-verification.json --approval E:\11\drug-loop-harness\state\prospective-acquisition-approval.json --campaign-store E:\path\to\committed-campaign-events.json --output E:\11\drug-loop-harness\state\prospective-admet-interpretation.json --result-output E:\11\drug-loop-harness\state\prospective-admet-result.json
python E:\11\drug-loop-harness\scripts\audit_prospective_admet_interpretation.py --interpretation E:\11\drug-loop-harness\state\prospective-admet-interpretation.json --attempt-directory E:\11\drug-loop-harness\state\prospective-admet-attempts\admet_attempt_20260716_001 --profile E:\path\to\admet-ai-profile.json --policy E:\path\to\admet-endpoint-policy.json --plan E:\11\drug-loop-harness\state\prospective-acquisition-plan.json --randomization-verification E:\11\drug-loop-harness\state\external-randomization-verification.json --approval E:\11\drug-loop-harness\state\prospective-acquisition-approval.json --campaign-store E:\path\to\committed-campaign-events.json --output E:\11\drug-loop-harness\state\prospective-admet-interpretation-audit.json
```

For strict admission, pass the execution intent, receipt, profile, and policy
to `stage_prospective_admission_bundle.py`. It replays the interpretation before
creating an Observation draft, retains the context in the bundle, and replays
it again in later bundle, commit, and resolver audits.

When a passing external calibration verification exists, pass it with
`--calibration-verification` to both interpretation/audit commands and strict
staging. Otherwise the result is explicitly `uncalibrated` and remains a
proxy-risk-marked ADMET signal.

`PASS_INTERPRETATION` and `PASS_STAGED` prove reproducibility of the control
chain, not endpoint calibration or biological truth. The remaining scientific
requirement is an external validation anchor with predeclared splits,
population, units, metrics, acceptance criteria, and review.
