# ADMET Prospective Drift Monitor

The prospective drift monitor compares a later labeled cohort to a verified
external-holdout calibration anchor without changing the policy threshold or
anchor. It requires exact profile/policy/anchor hashes, a prospective split,
hash-bound later labels and predictions with exact key coverage, and fixed
criteria for labeled sample size, out-of-calibration-range fraction, prevalence
shift, sensitivity drop, and specificity drop.

It returns one of:

- `STABLE`: all declared diagnostics remain within their fixed criteria.
- `DRIFT_WARNING`: sufficient data exist but one or more declared diagnostics
  crossed a fixed boundary.
- `INSUFFICIENT_PROSPECTIVE_LABELS`: later labels are not yet sufficient for
  the declared monitor size requirement.
- `FAIL`: provenance, calibration anchor, split, CSV coverage, or criteria are
  invalid.

The monitor never recalibrates thresholds, edits an anchor, mutates campaign
state, or upgrades/downgrades a claim. A warning is a review/planning signal,
not proof that the underlying model is wrong.

```powershell
python E:\11\drug-loop-harness\scripts\monitor_admet_calibration_drift.py E:\path\to\prospective-drift-monitor.json --anchor E:\path\to\admet-calibration-anchor.json --profile E:\path\to\admet-ai-profile.json --policy E:\path\to\admet-endpoint-policy.json --output E:\11\drug-loop-harness\state\admet-prospective-drift-report.json
```

## Planning Use

The report can be supplied to the next-loop planner as a diagnostic input:

```powershell
python E:\11\drug-loop-harness\scripts\suggest_next_loop_actions.py --admet-drift-report E:\11\drug-loop-harness\state\admet-prospective-drift-report.json
```

The planner accepts only `admet_prospective_drift_report.v1` reports whose
online-rethresholding, execution-authority, autonomy, and claim-upgrade fields
are all explicitly `false`. A `DRIFT_WARNING` produces a human-confirmed review
hint before using the proxy to prioritize new promotions. `FAIL` produces a
repair hint, and insufficient labels produce a label-follow-up hint. Neither is
evidence that a molecule failed, that the calibrated proxy is universally
invalid, or that an existing TrustGate ticket should be rewritten.

`STABLE` deliberately adds no permission or executable action. The planner is
not a policy updater: it does not change the endpoint threshold, anchor,
campaign state, promotion budget, or scientific claim.
