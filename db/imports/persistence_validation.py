from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class PersistenceValidation:
    manifest: dict[str, Any]
    warnings: list[str]


def validate_package_for_persistence(package_dir: Path, validation: Any, *, market: str) -> PersistenceValidation:
    """Apply structural-only validation for durable Wiki/PostgreSQL storage.

    Extraction coverage, evidence verification, source-domain verification,
    and quality status are retained as warnings. They are promotion concerns,
    not reasons to discard a readable financial report.
    """
    manifest_path = package_dir / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SystemExit(f"Invalid evidence package structure: unreadable manifest.json: {exc}") from exc
    if not isinstance(manifest, dict):
        raise SystemExit("Invalid evidence package structure: manifest.json must be an object")

    actual_market = str(manifest.get("market") or "").upper()
    if actual_market != market.upper():
        raise SystemExit(f"manifest market must be {market}; got {actual_market or '<missing>'}")
    missing = [field for field in ("company_id", "ticker", "filing_id") if not manifest.get(field)]
    if missing:
        raise SystemExit("Invalid evidence package structure: required identity fields missing: " + ", ".join(missing))

    errors = getattr(validation, "errors", [])
    return PersistenceValidation(manifest=manifest, warnings=[str(item) for item in errors or []])
