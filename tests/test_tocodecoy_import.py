from __future__ import annotations

import csv
import json
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

from loop_harness.tocodecoy_import import TocoDecoyEvaluationPoolImporter


class TocoDecoyEvaluationPoolImporterTest(unittest.TestCase):
    def test_exports_label_blinded_candidates_and_separate_truth(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = write_archive(root / "data.zip")
            candidates = root / "candidates.csv"
            truth = root / "truth.jsonl"

            pool = TocoDecoyEvaluationPoolImporter().export(
                archive,
                target_id="TEST",
                selected_split_value=0,
                candidate_csv_path=candidates,
                heldout_truth_jsonl_path=truth,
            )
            with candidates.open(encoding="utf-8") as handle:
                candidate_rows = list(csv.DictReader(handle))
            truth_rows = [
                json.loads(line)
                for line in truth.read_text(encoding="utf-8").splitlines()
            ]

        self.assertEqual(pool.candidate_count, 2)
        self.assertEqual(pool.positive_count_held_out, 1)
        self.assertEqual(pool.negative_count_held_out, 1)
        self.assertNotIn("label", candidate_rows[0])
        self.assertEqual(candidate_rows[0]["id"], "tocodecoy:TEST:3")
        self.assertEqual(truth_rows[0]["label"], 1)
        self.assertFalse(pool.policy_access_to_heldout_labels_permitted)

    def test_rejects_unknown_target_and_invalid_split_field(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = write_archive(root / "data.zip")
            importer = TocoDecoyEvaluationPoolImporter()
            with self.assertRaisesRegex(ValueError, "target member is unavailable"):
                importer.export(
                    archive,
                    target_id="MISSING",
                    selected_split_value=0,
                    candidate_csv_path=root / "candidates.csv",
                    heldout_truth_jsonl_path=root / "truth.jsonl",
                )
            invalid_archive = write_archive(root / "invalid.zip", train_value="not_binary")
            with self.assertRaisesRegex(ValueError, "train must be binary"):
                importer.export(
                    invalid_archive,
                    target_id="TEST",
                    selected_split_value=0,
                    candidate_csv_path=root / "candidates.csv",
                    heldout_truth_jsonl_path=root / "truth.jsonl",
                )

    def test_hash_ranked_subset_is_label_blind_and_reproducible(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = write_archive(root / "data.zip")
            importer = TocoDecoyEvaluationPoolImporter()
            first = importer.export(
                archive,
                target_id="TEST",
                selected_split_value=0,
                candidate_csv_path=root / "first.csv",
                heldout_truth_jsonl_path=root / "first.jsonl",
                max_candidates=1,
                selection_seed="fixed-seed",
            )
            second = importer.export(
                archive,
                target_id="TEST",
                selected_split_value=0,
                candidate_csv_path=root / "second.csv",
                heldout_truth_jsonl_path=root / "second.jsonl",
                max_candidates=1,
                selection_seed="fixed-seed",
            )

        self.assertEqual(first.source_split_candidate_count, 2)
        self.assertEqual(first.candidate_count, 1)
        self.assertEqual(
            first.selection_method,
            "sha256_ranked_candidate_id_without_label_access",
        )
        self.assertEqual(first.candidate_csv_sha256, second.candidate_csv_sha256)

    def test_cli_writes_pool_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = write_archive(root / "data.zip")
            project_root = Path(__file__).resolve().parents[1]
            candidates = root / "candidates.csv"
            truth = root / "truth.jsonl"
            manifest = root / "pool.json"
            completed = subprocess.run(
                [
                    sys.executable,
                    str(project_root / "scripts" / "export_tocodecoy_evaluation_pool.py"),
                    "--source-archive", str(archive),
                    "--target", "TEST",
                    "--split-value", "0",
                    "--candidates-output", str(candidates),
                    "--heldout-truth-output", str(truth),
                    "--manifest-output", str(manifest),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            payload = json.loads(manifest.read_text(encoding="utf-8"))

        self.assertIn("label-blinded candidates", completed.stdout)
        self.assertEqual(payload["candidate_count"], 2)
        self.assertFalse(payload["policy_access_to_heldout_labels_permitted"])


def write_archive(path: Path, *, train_value: str = "0") -> Path:
    content = "\n".join(
        (
            "name,smile,label,train",
            "raw_train,CCO,0,1",
            f"raw_positive,CCN,1,{train_value}",
            f"raw_negative,CCC,0,{train_value}",
            "",
        )
    )
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("TDset/TEST_tocodecoys.csv", content)
    return path
