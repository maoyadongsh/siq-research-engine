"""Regression checks for portable Hermes profile assets."""

from __future__ import annotations

import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
PROFILES_ROOT = REPO_ROOT / "agents" / "hermes" / "profiles"


def test_manifest_registers_all_business_profile_variants() -> None:
    manifest = json.loads((PROFILES_ROOT / "manifest.json").read_text(encoding="utf-8"))
    profiles = set(manifest["profiles"])
    multi_market = {
        "siq_analysis_multi_market",
        "siq_factchecker_multi_market",
        "siq_tracking_multi_market",
    }
    assert multi_market <= profiles
    assert multi_market <= set(manifest["groups"]["secondary_market_multi_market"])


def test_profile_runtime_assets_have_no_developer_machine_paths() -> None:
    candidates = [
        *PROFILES_ROOT.glob("*/config.yaml"),
        *PROFILES_ROOT.glob("*/profile.yaml"),
        *PROFILES_ROOT.glob("*/scripts/*.py"),
        *PROFILES_ROOT.glob("shared/scripts/*.py"),
    ]
    for path in candidates:
        text = path.read_text(encoding="utf-8")
        assert "/home/maoyd/siq-research-engine" not in text, path
        assert "arthurmao.synology.me" not in text, path


def test_gateway_exports_portable_roots_and_supports_multi_market() -> None:
    gateway = (REPO_ROOT / "scripts" / "hermes" / "run_gateway.sh").read_text(encoding="utf-8")
    for variable in ("SIQ_PROJECT_ROOT", "SIQ_WIKI_ROOT", "SIQ_HERMES_HOME"):
        assert f"export {variable}" in gateway
    for profile in (
        "siq_analysis_multi_market",
        "siq_factchecker_multi_market",
        "siq_tracking_multi_market",
    ):
        assert profile in gateway
    assert "SIQ_PUBLIC_ORIGIN is required" in gateway
