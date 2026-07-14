from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from loop_harness import OpenBenchmarkAdmissionAuditor


class OpenBenchmarkAdmissionAuditorTest(unittest.TestCase):
    def test_ready_for_cache_construction_when_required_source_is_verified(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            payload = root / "data.zip"
            payload.write_bytes(b"published source payload")

            audit = OpenBenchmarkAdmissionAuditor().audit(
                manifest_for(payload),
                asset_root=root,
                audited_at="2026-07-11T00:00:00+00:00",
            )

        self.assertEqual(audit.audit_status, "READY_FOR_ORACLE_CACHE_CONSTRUCTION")
        self.assertEqual(audit.assets[0].status, "verified")
        self.assertEqual(audit.cache_status, "missing_local_cache")
        self.assertFalse(audit.scientific_claim_upgrade_permitted)

    def test_ready_for_policy_replay_requires_a_hash_locked_cache(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            payload = root / "data.zip"
            cache = root / "state" / "cache.jsonl"
            payload.write_bytes(b"published source payload")
            cache.parent.mkdir()
            cache.write_text('{"candidate_id":"cmpd_1"}\n', encoding="utf-8")
            manifest = manifest_for(payload)
            manifest["oracle_cache"]["integrity"] = digest(cache, "sha256")

            audit = OpenBenchmarkAdmissionAuditor().audit(
                manifest,
                asset_root=root,
                audited_at="2026-07-11T00:00:00+00:00",
            )

        self.assertEqual(audit.audit_status, "READY_FOR_POLICY_REPLAY")
        self.assertEqual(audit.cache_status, "verified")

    def test_integrity_mismatch_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            payload = root / "data.zip"
            payload.write_bytes(b"tampered")
            manifest = manifest_for(payload)
            manifest["source_assets"][0]["integrity"]["value"] = "0" * 64
            manifest["source_assets"][0]["integrity"]["algorithm"] = "sha256"

            audit = OpenBenchmarkAdmissionAuditor().audit(
                manifest,
                asset_root=root,
                audited_at="2026-07-11T00:00:00+00:00",
            )

        self.assertEqual(audit.audit_status, "FAIL_INTEGRITY_MISMATCH")
        self.assertEqual(audit.assets[0].status, "integrity_mismatch")

    def test_invalid_intervention_fails_manifest_audit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            payload = root / "data.zip"
            payload.write_bytes(b"published source payload")
            manifest = manifest_for(payload)
            manifest["loop_intervention"]["generator_mode"] = "adaptive"

            audit = OpenBenchmarkAdmissionAuditor().audit(
                manifest,
                asset_root=root,
                audited_at="2026-07-11T00:00:00+00:00",
            )

        self.assertEqual(audit.audit_status, "FAIL_INVALID_MANIFEST")
        self.assertIn("generator_not_fixed", audit.issue_codes)

    def test_pending_human_review_is_not_treated_as_acceptance(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            payload = root / "data.zip"
            payload.write_bytes(b"published source payload")
            manifest = manifest_for(payload)
            manifest["human_acceptance"] = {
                "decision": "pending",
                "rationale": "A scientific reviewer has not signed the declaration.",
            }

            audit = OpenBenchmarkAdmissionAuditor().audit(
                manifest,
                asset_root=root,
                audited_at="2026-07-11T00:00:00+00:00",
            )

        self.assertEqual(audit.audit_status, "WAITING_FOR_HUMAN_REVIEW")
        self.assertEqual(audit.issue_codes, ())


def manifest_for(payload: Path) -> dict[str, object]:
    return {
        "schema_version": "open_benchmark_admission_manifest.v1",
        "scenario_id": "open_pilot",
        "phase": "retrospective_oracle_cache_replay",
        "source_assets": [
            {
                "asset_id": "source_data",
                "role": "fixed_candidate_pool",
                "source_url": "https://example.org/source-data",
                "license": "test-only",
                "local_path": payload.name,
                "integrity": digest(payload, "sha256"),
                "required_for_cache_construction": True,
            }
        ],
        "frozen_split": {
            "split_id": "published_split",
            "source_asset_id": "source_data",
            "source_field": "published label",
            "evaluation_holdout_rule": "hold out the published test partition",
            "frozen_before_policy_comparison": True,
        },
        "oracle_cache": {
            "source_asset_ids": ["source_data"],
            "local_path": "state/cache.jsonl",
            "action_kinds": ["dock", "rescore"],
            "precomputed_before_policy_comparison": True,
        },
        "loop_intervention": {
            "policy_only_intervention": True,
            "generator_mode": "fixed_or_out_of_scope",
            "all_policy_arms_use_same_cached_observations": True,
            "policy_tuning_on_evaluation_holdout_permitted": False,
        },
        "scientific_controls": {
            "policy_ranking_permitted": False,
            "execution_authority_granted": False,
            "autonomous_execution_permitted": False,
            "scientific_claim_upgrade_permitted": False,
        },
        "human_acceptance": {
            "decision": "accept",
            "reviewer": "reviewer@example.org",
            "reviewed_at": "2026-07-11T00:00:00+00:00",
            "rationale": "reviewed",
        },
    }


def digest(path: Path, algorithm: str) -> dict[str, str]:
    return {
        "algorithm": algorithm,
        "value": hashlib.new(algorithm, path.read_bytes()).hexdigest(),
    }
