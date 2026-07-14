from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import asdict, is_dataclass
from enum import Enum
from typing import Any


def canonical_content_sha256(value: Any) -> str:
    """Hash structured artifact content independently of JSON whitespace or key order."""

    canonical = json.dumps(
        json_ready(value),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def oracle_cache_content_sha256(
    oracle_cache: Mapping[tuple[str, str], Any],
) -> str:
    """Hash logical oracle-cache rows while preserving subject/oracle bindings."""

    return canonical_content_sha256(
        [
            {
                "subject_id": subject_id,
                "oracle_name": oracle_name,
                "observation": observation,
            }
            for (subject_id, oracle_name), observation in sorted(oracle_cache.items())
        ]
    )


def json_ready(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return json_ready(asdict(value))
    if isinstance(value, Mapping):
        return {str(key): json_ready(item) for key, item in value.items()}
    if isinstance(value, set):
        return [json_ready(item) for item in sorted(value, key=str)]
    if isinstance(value, tuple | list):
        return [json_ready(item) for item in value]
    return value
