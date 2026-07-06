#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PROFILES_ROOT = PROJECT_ROOT / "agents" / "hermes" / "profiles"
MANIFEST_PATH = PROFILES_ROOT / "manifest.json"

REQUIRED_PROFILE_FILES = {
    "config.yaml",
    "README.md",
    "SOUL.md",
    "IDENTITY.md",
    "BOOTSTRAP.md",
    "AGENTS.md",
    "HEARTBEAT.md",
    "TOOLS.md",
    "USER.md",
}
FORBIDDEN_RUNTIME_NAMES = {
    "__pycache__",
    "logs",
    "memories",
    "sandboxes",
    "platforms",
    "state.db",
    "response_store.db",
}
SHARED_PROFILE_IDS = {"shared", "siq_ic_shared"}


def _load_manifest() -> dict:
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def _profile_errors(profile_id: str) -> list[str]:
    profile_dir = PROFILES_ROOT / profile_id
    errors: list[str] = []
    if not profile_dir.is_dir():
        return [f"{profile_id}: missing profile directory"]
    if profile_id not in SHARED_PROFILE_IDS:
        missing = sorted(name for name in REQUIRED_PROFILE_FILES if not (profile_dir / name).is_file())
        if missing:
            errors.append(f"{profile_id}: missing required files: {', '.join(missing)}")
    for name in sorted(FORBIDDEN_RUNTIME_NAMES):
        if (profile_dir / name).exists():
            errors.append(f"{profile_id}: forbidden runtime artifact present: {name}")
    return errors


def validate_profiles() -> list[str]:
    manifest = _load_manifest()
    profiles = manifest.get("profiles") or []
    groups = manifest.get("groups") or {}
    errors: list[str] = []
    if not isinstance(profiles, list) or not profiles:
        errors.append("manifest: profiles must be a non-empty list")
        return errors

    profile_set = set(profiles)
    for profile_id in profiles:
        errors.extend(_profile_errors(str(profile_id)))

    for required_group in ("secondary_market", "primary_market_ic", "shared"):
        members = groups.get(required_group)
        if not isinstance(members, list) or not members:
            errors.append(f"manifest: missing or empty group {required_group}")
            continue
        unknown = sorted(str(item) for item in members if str(item) not in profile_set)
        if unknown:
            errors.append(f"manifest: group {required_group} references unknown profiles: {', '.join(unknown)}")

    grouped_business_profiles = set(groups.get("secondary_market") or []) | set(groups.get("primary_market_ic") or [])
    missing_from_groups = sorted(profile_set - SHARED_PROFILE_IDS - grouped_business_profiles)
    if missing_from_groups:
        errors.append(f"manifest: business profiles missing from market groups: {', '.join(missing_from_groups)}")

    return errors


def main() -> int:
    errors = validate_profiles()
    if errors:
        print("Hermes profile validation failed:")
        for error in errors:
            print(f"- {error}")
        return 1
    print("Hermes profile validation passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
