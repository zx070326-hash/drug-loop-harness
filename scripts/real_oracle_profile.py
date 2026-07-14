"""Shared integrity checks for fixed local computational-oracle profiles."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


class ProfileIntegrityError(ValueError):
    pass


def load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ProfileIntegrityError(f"JSON object required: {path}")
    return payload


def canonical_json_hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def tree_hash(root: Path) -> tuple[str, int, int]:
    rows: list[tuple[str, str, int]] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or "__pycache__" in path.parts:
            continue
        rows.append(
            (
                path.relative_to(root).as_posix(),
                file_hash(path),
                path.stat().st_size,
            )
        )
    encoded = json.dumps(rows, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest(), len(rows), sum(row[2] for row in rows)


def resolve_workspace_path(workspace: Path, value: str) -> Path:
    path = (workspace / value).resolve()
    try:
        path.relative_to(workspace)
    except ValueError as exc:
        raise ProfileIntegrityError(f"profile path escapes workspace: {value}") from exc
    if not path.is_file() and not path.is_dir():
        raise ProfileIntegrityError(f"profile path is missing: {value}")
    return path


def verify_manifest_binding(manifest: dict[str, Any], profile: dict[str, Any]) -> None:
    binding = manifest.get("profile_binding")
    if not isinstance(binding, dict):
        raise ProfileIntegrityError("input manifest has no profile binding")
    expected = binding.get("profile_artifact_sha256")
    if expected != canonical_json_hash(profile):
        raise ProfileIntegrityError("profile artifact hash does not match input binding")
    for key in ("target_id", "endpoint_id"):
        if binding.get(key) != profile.get(key):
            raise ProfileIntegrityError(f"{key} does not match fixed profile")
        if key in manifest and manifest.get(key) != profile.get(key):
            raise ProfileIntegrityError(f"manifest {key} does not match fixed profile")
    if manifest.get("oracle_id") != profile.get("oracle_id"):
        raise ProfileIntegrityError("oracle_id does not match fixed profile")


def verify_file(workspace: Path, spec: dict[str, Any]) -> Path:
    path = resolve_workspace_path(workspace, str(spec["path"]))
    if not path.is_file() or file_hash(path) != spec.get("sha256"):
        raise ProfileIntegrityError(f"artifact hash mismatch: {spec.get('path')}")
    return path
