"""MinerU import candidates response helpers."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from pathlib import Path


SCHEMA_MINERU_IMPORT_CANDIDATES = "mineru_import_candidates_v1"


def build_mineru_import_candidates_payload(
    allowed_roots: Iterable[Path],
    candidates: Sequence[dict[str, object]],
) -> dict:
    return {
        "schema_version": SCHEMA_MINERU_IMPORT_CANDIDATES,
        "allowed_roots": [str(root) for root in allowed_roots],
        "candidates": list(candidates),
    }
