# ADMET Calibration Anchor

An ADMET endpoint policy is not calibrated merely because a reviewer named its
threshold. `AdmetCalibrationAnchorVerifier` verifies one predeclared external
holdout anchor by recomputing the policy's confusion matrix from two hash-bound
CSV artifacts: external labels and model predictions.

The anchor binds the exact profile and policy content hashes, profile/model/
endpoint-set identity, external target population, split id, validation time,
label semantics, minimum labeled count, sensitivity threshold, specificity
threshold, and a human review after validation completion.

The verifier requires exact prediction-label key coverage and rejects duplicate
keys, altered artifacts, non-external split kinds, post-hoc policy review,
unknown labels, non-finite predictions, missing endpoints, insufficient sample
size, failed metrics, or rejected human review.

```powershell
python E:\11\drug-loop-harness\scripts\verify_admet_calibration_anchor.py E:\path\to\admet-calibration-anchor.json --profile E:\path\to\admet-ai-profile.json --policy E:\path\to\admet-endpoint-policy.json --output E:\11\drug-loop-harness\state\admet-calibration-verification.json
```

`PASS` scopes the policy/profile proxy to the anchor's declared population; it
does not establish biological activity, universal ADMET validity, causal effect,
or a final drug-discovery claim. Pass the verification to the interpretation and
strict staging commands with `--calibration-verification` to mark the resulting
proxy `calibrated` for that scope. Without it, strict interpreted ADMET evidence
remains `uncalibrated` and receives a proxy-risk flag in TrustGate.
