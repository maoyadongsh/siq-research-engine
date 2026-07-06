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
        build_quality_gates as _build_contract_quality_gates,
        evidence_source_resolvability as _contract_evidence_source_resolvability,
        market_package_paths,
        read_json as _read_contract_json,
        read_market_package_detail as _read_contract_market_package_detail,
        read_market_package_summary as _read_contract_market_package_summary,
    )
except ModuleNotFoundError:
    _ensure_monorepo_contracts_on_path()
    from siq_market_contracts import (
        build_quality_gates as _build_contract_quality_gates,
        evidence_source_resolvability as _contract_evidence_source_resolvability,
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
    return market_code(value, market_wiki_roots)


def market_code(value: str | None, market_wiki_roots: Mapping[str, Path]) -> str:
    market = str(value or "").upper()
    if market not in market_wiki_roots:
        raise HTTPException(status_code=400, detail="market must be one of US/HK/JP/KR/EU")
    return market


def safe_under(root: Path, path: Path) -> Path:
    root_resolved = root.resolve()
    resolved = path.resolve()
    try:
        resolved.relative_to(root_resolved)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Path is outside the allowed evidence package root") from exc
    return resolved


def safe_market_package_path(
    market: str,
    value: str | None,
    *,
    repo_root: Path,
    market_wiki_roots: Mapping[str, Path],
) -> Path:
    if not value:
        raise HTTPException(status_code=400, detail="package_path is required")
    path = Path(value)
    if not path.is_absolute():
        path = repo_root / path
    package_dir = safe_under(market_wiki_roots[market], path)
    if not (package_dir / "manifest.json").is_file():
        raise HTTPException(status_code=404, detail="Market evidence package not found")
    return package_dir


def safe_us_sec_package_path(
    value: str | None,
    *,
    repo_root: Path,
    us_sec_wiki_root: Path,
) -> Path:
    if not value:
        raise HTTPException(status_code=400, detail="package_path is required")
    path = Path(value)
    if not path.is_absolute():
        path = repo_root / path
    package_dir = safe_under(us_sec_wiki_root, path)
    if not (package_dir / "manifest.json").is_file():
        raise HTTPException(status_code=404, detail="US SEC package not found")
    return package_dir


def safe_download_path(value: str | None, *, downloads_root: Path) -> Path:
    if not value:
        raise HTTPException(status_code=400, detail="download_relative_path is required")
    relative = Path(str(value))
    if relative.is_absolute() or ".." in relative.parts:
        raise HTTPException(status_code=400, detail="Invalid download_relative_path")
    root = downloads_root.resolve()
    resolved = (root / relative).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="download_relative_path is outside downloads root") from exc
    if not resolved.is_file():
        raise HTTPException(status_code=404, detail="download_relative_path not found")
    return resolved


def iter_market_packages(market: str, market_wiki_roots: Mapping[str, Path]) -> list[Path]:
    root = market_wiki_roots[market]
    if not root.exists():
        return []
    patterns = (
        "companies/*/reports/*/manifest.json",
        "*/*/*/*/manifest.json",
        "*/*/*/manifest.json",
    )
    package_dirs: list[Path] = []
    seen: set[Path] = set()
    for pattern in patterns:
        for manifest in root.glob(pattern):
            package_dir = manifest.parent
            if package_dir in seen:
                continue
            seen.add(package_dir)
            package_dirs.append(package_dir)
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


def build_quality_gates(package_dir: Path) -> dict[str, Any]:
    return _build_contract_quality_gates(package_dir.resolve())


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
        manifest = _read_json_file(path / "manifest.json", {}) or {}
        source_map = _read_json_file(path / "qa" / "source_map.json", {}) or {}
        if not isinstance(source_map, dict):
            source_map = {}
        entries = source_map.get("entries") or source_map.get("evidence") or []
        for entry in entries:
            if isinstance(entry, dict) and str(entry.get("evidence_id") or "") == target:
                payload = dict(entry)
                resolvability = _contract_evidence_source_resolvability(payload, manifest=manifest if isinstance(manifest, dict) else {}, package_dir=path)
                payload.setdefault("resolvable", resolvability.get("resolvable"))
                payload.setdefault("resolvability_kind", resolvability.get("kind"))
                payload.setdefault("resolvability_reason", resolvability.get("reason"))
                return code, path, payload
    raise HTTPException(status_code=404, detail="Evidence not found")
