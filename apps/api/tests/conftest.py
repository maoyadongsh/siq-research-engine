import sys
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


@pytest.fixture(autouse=True)
def _disable_live_openshell_runtime_selection(monkeypatch):
    """Keep unit tests independent from the operator's live runtime switch."""

    monkeypatch.setenv("SIQ_HERMES_RUNTIME_SELECTION_ENABLED", "0")
