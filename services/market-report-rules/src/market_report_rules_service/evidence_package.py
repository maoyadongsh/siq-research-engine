from __future__ import annotations

import importlib
import sys
from pathlib import Path


def _ensure_monorepo_contracts_on_path() -> None:
    repo_root = Path(__file__).resolve().parents[4]
    contracts_src = repo_root / "packages" / "market-contracts" / "src"
    if contracts_src.is_dir() and str(contracts_src) not in sys.path:
        sys.path.insert(0, str(contracts_src))


def _import_contract_module():
    try:
        return importlib.import_module("siq_market_contracts.evidence_package")
    except ModuleNotFoundError as exc:
        if exc.name != "siq_market_contracts":
            raise
        _ensure_monorepo_contracts_on_path()
        return importlib.import_module("siq_market_contracts.evidence_package")


_contract_module = _import_contract_module()
CONTRACT_SOURCE_MODULE = str(Path(_contract_module.__file__).resolve())

__all__ = [name for name in dir(_contract_module) if not name.startswith("_")]
globals().update({name: getattr(_contract_module, name) for name in __all__})
