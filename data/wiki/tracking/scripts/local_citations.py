#!/usr/bin/env python3
"""Backward-compatible shim that re-exports the canonical citation resolver.

The canonical implementation now lives in the SIQ project shared scripts.

This shim exists so that legacy tracking modules importing from this path
continue to work. New code should import from the shared path directly.

Backup of the previous full copy: local_citations.py.bak-pre-shim-20260527
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[4]
_CANDIDATES = (
    _PROJECT_ROOT / "agents" / "hermes" / "profiles" / "shared" / "scripts" / "local_citations.py",
    _PROJECT_ROOT / "data" / "hermes" / "home" / "profiles" / "shared" / "scripts" / "local_citations.py",
    Path("/home/maoyd/.hermes/profiles/shared/scripts/local_citations.py"),
)
_CANONICAL = next((path for path in _CANDIDATES if path.exists()), _CANDIDATES[0])

# Load the canonical module under a unique alias to avoid colliding with
# this shim file itself when sys.path includes both directories.
_spec = importlib.util.spec_from_file_location(
    "_finsight_local_citations_canonical", _CANONICAL
)
_module = importlib.util.module_from_spec(_spec)
sys.modules.setdefault("_finsight_local_citations_canonical", _module)
_spec.loader.exec_module(_module)

# Re-export every public attribute from the canonical module.
globals().update({k: v for k, v in vars(_module).items() if not k.startswith("__")})
