from __future__ import annotations

import json
import os
from pathlib import Path
from uuid import uuid4

from ..artifact_hash import json_ready
from .contracts import ArtifactRef


class ArtifactIntegrityError(ValueError):
    """Raised when an artifact is missing or no longer matches its reference."""


class ContentAddressedArtifactStore:
    """Small local artifact store used by models, jobs, and read-only evaluators."""

    def __init__(self, root: str | Path, *, producer_version: str = "store_v1") -> None:
        self.root = Path(root).resolve()
        self.producer_version = producer_version

    def put_bytes(
        self,
        data: bytes,
        *,
        media_type: str,
        producer_version: str | None = None,
    ) -> ArtifactRef:
        if not media_type:
            raise ValueError("media_type is required")
        import hashlib

        digest = hashlib.sha256(data).hexdigest()
        relative = Path(digest[:2]) / digest[2:]
        target = self.root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            if target.read_bytes() != data:
                raise ArtifactIntegrityError("content-addressed path contains different bytes")
        else:
            temporary = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
            try:
                temporary.write_bytes(data)
                os.replace(temporary, target)
            finally:
                if temporary.exists():
                    temporary.unlink()
        return ArtifactRef(
            artifact_id=f"artifact_{digest[:20]}",
            relative_path=relative.as_posix(),
            sha256=digest,
            media_type=media_type,
            byte_size=len(data),
            producer_version=producer_version or self.producer_version,
        )

    def put_json(
        self,
        payload,
        *,
        media_type: str = "application/json",
        producer_version: str | None = None,
    ) -> ArtifactRef:
        encoded = json.dumps(
            json_ready(payload),
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return self.put_bytes(
            encoded,
            media_type=media_type,
            producer_version=producer_version,
        )

    def read_bytes(self, reference: ArtifactRef) -> bytes:
        path = self._resolve(reference)
        data = path.read_bytes()
        import hashlib

        if len(data) != reference.byte_size or hashlib.sha256(data).hexdigest() != reference.sha256:
            raise ArtifactIntegrityError(f"artifact hash mismatch: {reference.artifact_id}")
        return data

    def path_for(self, reference: ArtifactRef) -> Path:
        """Return a verified absolute path for a fixed command profile."""

        return self._resolve(reference)

    def read_json(self, reference: ArtifactRef):
        try:
            return json.loads(self.read_bytes(reference).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ArtifactIntegrityError(f"artifact is not valid JSON: {reference.artifact_id}") from exc

    def _resolve(self, reference: ArtifactRef) -> Path:
        path = (self.root / reference.relative_path).resolve()
        try:
            path.relative_to(self.root)
        except ValueError as exc:
            raise ArtifactIntegrityError("artifact path escapes the store root") from exc
        if not path.is_file():
            raise ArtifactIntegrityError(f"artifact missing: {reference.artifact_id}")
        return path
