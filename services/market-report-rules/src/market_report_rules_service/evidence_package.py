from __future__ import annotations

import sys
from pathlib import Path


def _ensure_monorepo_contracts_on_path() -> None:
    repo_root = Path(__file__).resolve().parents[4]
    contracts_src = repo_root / "packages" / "market-contracts" / "src"
    if contracts_src.is_dir():
        sys.path.insert(0, str(contracts_src))


try:
    from siq_market_contracts.evidence_package import *  # noqa: F401,F403
except ModuleNotFoundError:
    _ensure_monorepo_contracts_on_path()
    try:
        from siq_market_contracts.evidence_package import *  # noqa: F401,F403
    except ModuleNotFoundError:
        from ._legacy_evidence_package import *  # noqa: F401,F403
