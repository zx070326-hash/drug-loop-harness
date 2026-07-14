from __future__ import annotations

import copy
import csv
import tempfile
import unittest
from pathlib import Path

from loop_harness.v2.fep_untouched_confirmation import (
    EXPECTED_PTP1B_ADVANCE_DECISION,
    KNOWN_OUTCOME_EXPOSURE,
    NO_KNOWN_OUTCOME_ACCESS,
    build_contamination_register,
    build_post_registration_exposure_record,
    build_untouched_confirmation_contract,
    build_untouched_target_selection,
    project_untouched_confirmation_state,
    validate_contamination_register,
    validate_post_registration_exposure_record,
    validate_untouched_target_selection,
)


COMMIT = "a" * 40
HASH = "b" * 64


class FepUntouchedConfirmationTest(unittest.TestCase):
    def test_selection_is_result_blind_deterministic_and_excludes_exposed_target(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            metadata = Path(directory) / "metadata.csv"
            _write_metadata(metadata)
            contamination = _contamination(metadata)
            selection = build_untouched_target_selection(
                metadata_path=metadata,
                contamination_register=contamination,
                benchmark_commit=COMMIT,
                candidate_input_blobs={
                    "jnk1": _blobs("jnk1"),
                    "bace1": _blobs("bace1"),
                    "cdk2": _blobs("cdk2"),
                },
                selection_seed="ptp1b-untouched-confirmation-v1",
            )

        validate_untouched_target_selection(selection)
        self.assertEqual(selection["selected_target"]["target_id"], "jnk1")
        self.assertEqual(selection["fallback_order"], ["bace1"])
        cdk2 = next(
            item for item in selection["excluded_targets"] if item["target_id"] == "cdk2"
        )
        self.assertIn("KNOWN_OUTCOME_EXPOSURE", cdk2["exclusion_reasons"])
        self.assertFalse(selection["selection_rule"]["outcome_values_used"])

    def test_contamination_register_cannot_relabel_known_exposure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            metadata = Path(directory) / "metadata.csv"
            _write_metadata(metadata)
            payload = _contamination(metadata)
        validate_contamination_register(payload)
        changed = copy.deepcopy(payload)
        changed["targets"]["cdk2"]["strict_untouched_eligible"] = True
        with self.assertRaisesRegex(ValueError, "contradicts"):
            validate_contamination_register(changed)

    def test_state_requires_input_qualification_protocol_and_upstream_go(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            metadata = Path(directory) / "metadata.csv"
            _write_metadata(metadata)
            contamination = _contamination(metadata)
            selection = build_untouched_target_selection(
                metadata_path=metadata,
                contamination_register=contamination,
                benchmark_commit=COMMIT,
                candidate_input_blobs={
                    "jnk1": _blobs("jnk1"),
                    "bace1": _blobs("bace1"),
                    "cdk2": _blobs("cdk2"),
                },
                selection_seed="ptp1b-untouched-confirmation-v1",
            )
        contract = build_untouched_confirmation_contract(
            selection=selection,
            selection_sha256="c" * 64,
            contamination_register_sha256="d" * 64,
        )
        initial = project_untouched_confirmation_state(
            contract=contract,
            input_audit=None,
            upstream_stage_decision=None,
        )
        self.assertEqual(initial["phase"], "INPUT_QUALIFICATION")
        qualified = project_untouched_confirmation_state(
            contract=contract,
            input_audit={"status": "ACCEPTED"},
            target_qualification={"status": "ACCEPTED"},
            target_protocol_lock_present=True,
            upstream_stage_decision=None,
        )
        self.assertEqual(qualified["phase"], "WAITING_FOR_UPSTREAM_DECISION")
        ready = project_untouched_confirmation_state(
            contract=contract,
            input_audit={"status": "ACCEPTED"},
            target_qualification={"status": "ACCEPTED"},
            target_protocol_lock_present=True,
            upstream_stage_decision={"decision": EXPECTED_PTP1B_ADVANCE_DECISION},
        )
        self.assertEqual(ready["phase"], "READY_FOR_SEPARATE_EXECUTION_AUTHORIZATION")
        self.assertFalse(ready["authority"]["simulation_execution_authority_granted"])
        no_go = project_untouched_confirmation_state(
            contract=contract,
            input_audit={"status": "ACCEPTED"},
            upstream_stage_decision={"decision": "REJECT_REPAIR_PROFILE_FOR_CONFIRMATION"},
        )
        self.assertEqual(no_go["phase"], "NOT_ACTIVATED_UPSTREAM_NO_GO")

    def test_post_registration_label_exposure_cannot_be_hidden(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            metadata = Path(directory) / "metadata.csv"
            _write_metadata(metadata)
            contamination = _contamination(metadata)
            selection = build_untouched_target_selection(
                metadata_path=metadata,
                contamination_register=contamination,
                benchmark_commit=COMMIT,
                candidate_input_blobs={
                    "jnk1": _blobs("jnk1"),
                    "bace1": _blobs("bace1"),
                    "cdk2": _blobs("cdk2"),
                },
                selection_seed="ptp1b-untouched-confirmation-v1",
            )
        exposure = build_post_registration_exposure_record(
            selection=selection,
            selection_sha256="e" * 64,
        )
        validate_post_registration_exposure_record(exposure)
        hidden = copy.deepcopy(exposure)
        hidden["scientific_impact"][
            "complete_protocol_development_label_blinding_claim_permitted"
        ] = True
        with self.assertRaisesRegex(ValueError, "understates"):
            validate_post_registration_exposure_record(hidden)


def _contamination(metadata: Path) -> dict:
    import hashlib

    metadata_hash = hashlib.sha256(metadata.read_bytes()).hexdigest()
    return build_contamination_register(
        benchmark_commit=COMMIT,
        metadata_sha256=metadata_hash,
        target_records=(
            {
                "target_id": "jnk1",
                "display_name": "JNK1",
                "outcome_access_status": NO_KNOWN_OUTCOME_ACCESS,
                "basis": "metadata only",
            },
            {
                "target_id": "bace1",
                "display_name": "BACE1",
                "outcome_access_status": NO_KNOWN_OUTCOME_ACCESS,
                "basis": "metadata only",
            },
            {
                "target_id": "cdk2",
                "display_name": "CDK2",
                "outcome_access_status": KNOWN_OUTCOME_EXPOSURE,
                "basis": "aggregate outcomes accessed",
            },
        ),
    )


def _blobs(target_id: str) -> dict[str, dict[str, object]]:
    return {
        kind: {
            "path": f"jacs_set/{target_id}/{target_id}_{suffix}",
            "git_blob_oid": character * 40,
            "size_bytes": index + 1,
        }
        for index, (kind, suffix, character) in enumerate(
            (
                ("edges", "edges.csv", "1"),
                ("ligands", "ligands.sdf", "2"),
                ("protein", "protein.pdb", "3"),
            )
        )
    }


def _write_metadata(path: Path) -> None:
    fields = (
        "Group name",
        "Group abbreviation",
        "Protein",
        "Input file naming scheme",
        "Reference PDB",
        "Number of nodes",
        "Dynamic range (kcal/mol)",
        "DOI of source",
    )
    rows = (
        ("FEP+ R-group set", "jacs_set", "CDK2", "cdk2", "1H1Q", 30, 4.2, "doi:x"),
        (
            "FEP+ R-group set",
            "jacs_set",
            "JNK1",
            "jnk1_manual_flips",
            "2GMX",
            38,
            3.4,
            "doi:x",
        ),
        ("FEP+ R-group set", "jacs_set", "BACE1", "bace", "4DJW", 36, 3.5, "doi:x"),
    )
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(fields)
        writer.writerows(rows)


if __name__ == "__main__":
    unittest.main()
