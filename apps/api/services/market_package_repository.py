from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Mapping

from fastapi import HTTPException

from services.path_config import REPO_ROOT


def _ensure_monorepo_contracts_on_path() -> None:
    contracts_src = REPO_ROOT / "packages" / "market-contracts" / "src"
    if contracts_src.is_dir() and str(contracts_src) not in sys.path:
        sys.path.insert(0, str(contracts_src))


try:
    from siq_market_contracts import (
        market_package_paths,
        read_json as _read_contract_json,
        read_market_package_detail as _read_contract_market_package_detail,
        read_market_package_summary as _read_contract_market_package_summary,
    )
except ModuleNotFoundError:
    _ensure_monorepo_contracts_on_path()
    from siq_market_contracts import (
        market_package_paths,
        read_json as _read_contract_json,
        read_market_package_detail as _read_contract_market_package_detail,
        read_market_package_summary as _read_contract_market_package_summary,
    )


def _read_json_file(path: Path, default: Any = None) -> Any:
    try:
        return _read_contract_json(path, default)
    except Exception:
        return default


def _rel_or_abs(path: Path, repo_root: Path = REPO_ROOT) -> str:
    try:
        return str(path.relative_to(repo_root))
    except ValueError:
        return str(path)


def _market_code(value: str | None, market_wiki_roots: Mapping[str, Path]) -> str:
    market = str(value or "").upper()
    if market not in market_wiki_roots:
        raise HTTPException(status_code=400, detail="market must be one of US/HK/JP/KR/EU")
    return market


def iter_market_packages(market: str, market_wiki_roots: Mapping[str, Path]) -> list[Path]:
    root = market_wiki_roots[market]
    if not root.exists():
        return []
    patterns = ("*/*/*/*/manifest.json",) if market == "EU" else ("*/*/*/manifest.json",)
    package_dirs: list[Path] = []
    for pattern in patterns:
        package_dirs.extend(path.parent for path in root.glob(pattern))
    return sorted(package_dirs, key=lambda path: path.stat().st_mtime, reverse=True)


def markets_to_search(market: str | None, market_wiki_roots: Mapping[str, Path]) -> list[str]:
    if market:
        return [_market_code(market, market_wiki_roots)]
    return list(market_wiki_roots)


def read_market_package_summary(package_dir: Path) -> dict[str, Any]:
    package_dir = package_dir.resolve()
    return _read_contract_market_package_summary(package_dir, display_path=_rel_or_abs(package_dir))


def read_market_package_detail(package_dir: Path) -> dict[str, Any]:
    package_dir = package_dir.resolve()
    return _read_contract_market_package_detail(package_dir, display_path=_rel_or_abs(package_dir))


def find_market_package_by_filing_id(
    filing_id: str,
    *,
    market: str | None = None,
    market_wiki_roots: Mapping[str, Path],
) -> tuple[str, Path]:
    target = str(filing_id or "").strip()
    if not target:
        raise HTTPException(status_code=400, detail="filing_id is required")
    for code in markets_to_search(market, market_wiki_roots):
        for package_dir in iter_market_packages(code, market_wiki_roots):
            manifest = _read_json_file(package_dir / "manifest.json", {}) or {}
            if str(manifest.get("filing_id") or "") == target:
                return code, package_dir
    raise HTTPException(status_code=404, detail="Market evidence package not found")


def find_market_evidence(
    evidence_id: str,
    *,
    market: str | None = None,
    package_dir: Path | None = None,
    market_wiki_roots: Mapping[str, Path],
) -> tuple[str, Path, dict[str, Any]]:
    target = str(evidence_id or "").strip()
    if not target:
        raise HTTPException(status_code=400, detail="evidence_id is required")
    if package_dir is not None:
        manifest = _read_json_file(package_dir / "manifest.json", {}) or {}
        packages = [(str(manifest.get("market") or market or "").upper(), package_dir)]
    else:
        packages = [(code, path) for code in markets_to_search(market, market_wiki_roots) for path in iter_market_packages(code, market_wiki_roots)]
    for code, path in packages:
        source_map = _read_json_file(path / "qa" / "source_map.json", {}) or {}
        for entry in source_map.get("entries") or []:
            if isinstance(entry, dict) and str(entry.get("evidence_id") or "") == target:
                return code, path, entry
    raise HTTPException(status_code=404, detail="Evidence not found")
