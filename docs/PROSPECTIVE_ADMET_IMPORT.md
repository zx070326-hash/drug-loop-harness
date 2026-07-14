# Prospective ADMET Result Import

`ProspectiveAdmetResultImporter` accepts only an already-produced ADMET result
that is tied to a receipt-selected subject and its prospective approval. It does
not contact an ADMET service, modify the campaign event store, run TrustGate, or
make a claim.

## Required Result Shape

```json
{
  "schema_version": "prospective_admet_result.v1",
  "result_id": "admet_result_001",
  "source_plan_content_sha256": "...",
  "source_randomization_verification_content_sha256": "...",
  "source_approval_content_sha256": "...",
  "request_id": "prospective_support_001",
  "subject_id": "cmpd_001",
  "oracle_name": "admet_oracle",
  "input_smiles_sha256": "...",
  "applicability_domain": ["small_molecule"],
  "started_at": "2026-07-15T00:00:00+00:00",
  "completed_at": "2026-07-15T01:00:00+00:00",
  "cost_spent": 2.0,
  "metrics": {"passed": true, "risk_score": 0.2},
  "model": {
    "name": "external_admet_model",
    "version": "registered-oracle-version",
    "endpoint_set_id": "admet_endpoints_v1",
    "model_sha256": "..."
  },
  "source_output": {"path": "admet-output.json", "sha256": "..."}
}
```

All timestamps require timezones. `source_output.path` resolves relative to the
result file and must match its declared SHA-256. The registered campaign oracle
must be `OracleKind.ADMET`; if it declares a version, the result model version
must match exactly.

## Import Boundary

```powershell
python E:\11\drug-loop-harness\scripts\import_prospective_admet_result.py E:\path\to\prospective-admet-result.json --plan E:\11\drug-loop-harness\state\prospective-acquisition-plan.json --randomization-verification E:\11\drug-loop-harness\state\external-randomization-verification.json --approval E:\11\drug-loop-harness\state\prospective-acquisition-approval.json --campaign-store E:\path\to\campaign-events.json --output E:\11\drug-loop-harness\state\prospective-admet-import.json
```

The importer checks plan, receipt, approval, candidate SMILES, model version,
domain, timestamps, cost, metrics, and raw-output artifact hash. `NORMALIZED`
means only that an observation draft is structurally bound to those declared
inputs:

```text
trust_gate_evaluated=false
execution_authority_granted=false
autonomous_execution_permitted=false
scientific_claim_upgrade_permitted=false
```

The observation remains outside campaign state. A separate human-triggered
admission must write it to the event ledger, run TrustGate, and then record the
prospective consumption receipt. Import alone cannot prove endpoint validity,
clinical relevance, or molecular safety.
