from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

SCHEMA_VERSION = "siq.immutable_paths.v1"
PROJECT_ROOT_LABEL = "${SIQ_PROJECT_ROOT}"
MARKETS = ("us", "hk", "jp", "kr", "eu")
FINAL_STATUSES = {"complete", "completed", "final", "finalized", "pass", "passed", "ready"}
MAX_MANIFEST_BYTES = 8 * 1024 * 1024


class ImmutableRegistryError(RuntimeError):
    """Base error for registry construction and writes."""


class ImmutableRegistrySecurityError(ImmutableRegistryError):
    """Raised when a source or output path escapes its approved root."""


@dataclass(frozen=True)
class RegistryBuild:
    payload: dict[str, Any]
    content: bytes
    digest: str


def _sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _clean_text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _status(value: Any) -> str:
    return str(value or "").strip().lower()


def _valid_sha256(value: Any) -> bool:
    text = _clean_text(value)
    return bool(text and len(text) == 64 and all(char in "0123456789abcdef" for char in text.lower()))


def _artifact_records_valid(records: Any) -> bool:
    if not isinstance(records, dict) or not records:
        return False
    return all(
        isinstance(item, dict)
        and item.get("exists") is True
        and _valid_sha256(item.get("sha256"))
        for item in records.values()
    )


def _market_hashes_valid(records: Any, package_dir: Path) -> bool:
    if not isinstance(records, dict) or not records:
        return False
    for relative, digest in records.items():
        candidate = Path(str(relative))
        if candidate.is_absolute() or ".." in candidate.parts or not _valid_sha256(digest):
            return False
        target = package_dir / candidate
        if not target.is_file() or target.is_symlink():
            return False
    return True


def _reject_symlink_components(path: Path, root: Path) -> None:
    root = root.absolute()
    path = path.absolute()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ImmutableRegistrySecurityError("path is outside the approved root") from exc
    current = path
    while True:
        if current.is_symlink():
            raise ImmutableRegistrySecurityError("symlink is not allowed in immutable registry paths")
        if current == root:
            break
        current = current.parent


def secure_existing_path(path: Path, root: Path) -> Path:
    if not root.exists():
        raise ImmutableRegistrySecurityError("approved root does not exist")
    _reject_symlink_components(root, root)
    _reject_symlink_components(path, root)
    try:
        resolved_root = root.resolve(strict=True)
        resolved = path.resolve(strict=True)
        resolved.relative_to(resolved_root)
    except (FileNotFoundError, ValueError) as exc:
        raise ImmutableRegistrySecurityError("path cannot be resolved inside the approved root") from exc
    return resolved


def repository_path(path: Path, project_root: Path) -> str:
    resolved = secure_existing_path(path, project_root)
    return resolved.relative_to(project_root.resolve(strict=True)).as_posix()


def _read_json_object(path: Path, *, project_root: Path) -> tuple[dict[str, Any] | None, str]:
    resolved = secure_existing_path(path, project_root)
    stat_result = resolved.stat()
    if not resolved.is_file():
        raise ImmutableRegistrySecurityError("registry source must be a regular file")
    if stat_result.st_size > MAX_MANIFEST_BYTES:
        return None, "manifest_too_large"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None, "manifest_invalid"
    if not isinstance(payload, dict):
        return None, "manifest_not_object"
    return payload, "ok"


def _company_reports(company: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    result: dict[str, Mapping[str, Any]] = {}
    reports = company.get("reports")
    if not isinstance(reports, list):
        return result
    for item in reports:
        if not isinstance(item, dict):
            continue
        report_id = _clean_text(item.get("report_id"))
        if report_id:
            result[report_id] = item
    return result


def _company_report_finalized(report: Mapping[str, Any]) -> bool:
    if _status(report.get("status")) == "ready":
        return True
    if (
        _status(report.get("wiki_ingestion_status")) == "ready"
        and report.get("wiki_ingestion_ready") is True
    ):
        return True
    return _status(report.get("retrieval_status")) == "ready" and report.get("wiki_ready") is True


def _identity(**values: Any) -> dict[str, str]:
    return {key: text for key, value in values.items() if (text := _clean_text(value)) is not None}


def _combined_digest(*digests: str) -> str:
    return _sha256_bytes(b"\0".join(item.encode("ascii") for item in digests))


class _RegistryBuilder:
    def __init__(self, *, project_root: Path, wiki_root: Path, generated_at: str | None) -> None:
        self.project_root = secure_existing_path(project_root, project_root)
        self.wiki_root = secure_existing_path(wiki_root, project_root)
        self.generated_at = generated_at
        self.entries: list[dict[str, Any]] = []
        self.skipped: Counter[str] = Counter()
        self.sources: dict[str, str] = {}

    def _source(self, path: Path) -> str:
        resolved = secure_existing_path(path, self.project_root)
        stat_result = resolved.stat()
        if not resolved.is_file() or resolved.is_symlink():
            raise ImmutableRegistrySecurityError("registry source must be a regular file")
        if stat_result.st_size > MAX_MANIFEST_BYTES:
            raise ImmutableRegistryError("registry source manifest exceeds the size limit")
        relative = repository_path(resolved, self.project_root)
        digest = _sha256_file(resolved)
        self.sources[relative] = digest
        return digest

    def _read(self, path: Path) -> dict[str, Any] | None:
        resolved = secure_existing_path(path, self.project_root)
        if not resolved.is_file() or resolved.is_symlink():
            raise ImmutableRegistrySecurityError("registry source must be a regular file")
        if resolved.stat().st_size > MAX_MANIFEST_BYTES:
            self.skipped["manifest_too_large"] += 1
            return None
        self._source(resolved)
        payload, reason = _read_json_object(resolved, project_root=self.project_root)
        if payload is None:
            self.skipped[reason] += 1
        return payload

    def _append(self, entry: dict[str, Any]) -> None:
        self.entries.append(entry)

    def _iter_company_dirs(self, companies_root: Path) -> Iterable[Path]:
        if not companies_root.exists():
            return []
        secure_existing_path(companies_root, self.project_root)
        return sorted(
            (path for path in companies_root.iterdir() if path.is_dir() or path.is_symlink()),
            key=lambda item: item.name,
        )

    def _company_context(self, company_dir: Path) -> tuple[dict[str, Any], str] | None:
        secure_existing_path(company_dir, self.project_root)
        company_path = company_dir / "company.json"
        if not company_path.exists() and not company_path.is_symlink():
            self.skipped["company_manifest_missing"] += 1
            return None
        company = self._read(company_path)
        if company is None:
            return None
        return company, self.sources[repository_path(company_path, self.project_root)]

    def collect_cn(self) -> None:
        for company_dir in self._iter_company_dirs(self.wiki_root / "companies"):
            context = self._company_context(company_dir)
            if context is None:
                continue
            company, company_digest = context
            reports = _company_reports(company)
            reports_root = company_dir / "reports"
            if not reports_root.exists():
                continue
            secure_existing_path(reports_root, self.project_root)
            for report_dir in sorted(
                (path for path in reports_root.iterdir() if path.is_dir() or path.is_symlink()),
                key=lambda item: item.name,
            ):
                secure_existing_path(report_dir, self.project_root)
                manifest_path = report_dir / "artifact_manifest.json"
                if not manifest_path.exists() and not manifest_path.is_symlink():
                    self.skipped["report_manifest_missing"] += 1
                    continue
                manifest = self._read(manifest_path)
                if manifest is None:
                    continue
                report = reports.get(report_dir.name)
                if report is None or not _company_report_finalized(report):
                    self.skipped["company_report_not_finalized"] += 1
                    continue
                core = manifest.get("core")
                artifacts = manifest.get("artifacts")
                core_ready = (
                    isinstance(core, dict)
                    and core.get("ready") is True
                    and _status(core.get("status")) == "ready"
                    and _valid_sha256(core.get("bundle_sha256"))
                    and _artifact_records_valid(artifacts)
                )
                legacy_ready = (
                    manifest.get("schema_version") == 1
                    and _valid_sha256(report.get("artifact_bundle_sha256"))
                    and _artifact_records_valid(artifacts)
                )
                if not core_ready and not legacy_ready:
                    self.skipped["artifact_manifest_not_ready"] += 1
                    continue
                task_id = report.get("task_id") or manifest.get("task_id")
                if (
                    not _clean_text(task_id)
                    or _clean_text(manifest.get("task_id")) != _clean_text(task_id)
                ):
                    self.skipped["stable_identity_or_digest_missing"] += 1
                    continue
                manifest_digest = self.sources[repository_path(manifest_path, self.project_root)]
                identity = _identity(
                    market="CN",
                    company_id=company.get("company_id") or company_dir.name.split("-", 1)[0],
                    report_id=report_dir.name,
                    task_id=task_id,
                )
                self._append(
                    {
                        "path": repository_path(report_dir, self.project_root),
                        "kind": "finalized_report",
                        "owner": "ingestion",
                        "identity": identity,
                        "source_manifest": repository_path(manifest_path, self.project_root),
                        "manifest_sha256": manifest_digest,
                        "finalization_sha256": _combined_digest(manifest_digest, company_digest),
                        "recursive": True,
                    }
                )

    def collect_market(self, market: str) -> None:
        companies_root = self.wiki_root / market / "companies"
        for company_dir in self._iter_company_dirs(companies_root):
            context = self._company_context(company_dir)
            if context is None:
                continue
            company, company_digest = context
            reports = _company_reports(company)
            reports_root = company_dir / "reports"
            if not reports_root.exists():
                continue
            secure_existing_path(reports_root, self.project_root)
            for report_dir in sorted(
                (path for path in reports_root.iterdir() if path.is_dir() or path.is_symlink()),
                key=lambda item: item.name,
            ):
                secure_existing_path(report_dir, self.project_root)
                manifest_path = report_dir / "manifest.json"
                if not manifest_path.exists() and not manifest_path.is_symlink():
                    self.skipped["report_manifest_missing"] += 1
                    continue
                manifest = self._read(manifest_path)
                if manifest is None:
                    continue
                manifest_report_id = _clean_text(manifest.get("report_id"))
                if manifest_report_id and manifest_report_id != report_dir.name:
                    self.skipped["report_identity_mismatch"] += 1
                    continue
                report_id = manifest_report_id or report_dir.name
                manifest_market = _status(manifest.get("market")).replace("_SEC", "")
                if manifest_market and manifest_market != market:
                    self.skipped["market_identity_mismatch"] += 1
                    continue
                report = reports.get(report_id)
                if report is None or not _company_report_finalized(report):
                    self.skipped["company_report_not_finalized"] += 1
                    continue
                package_path = _clean_text(report.get("package_path"))
                if package_path and package_path != repository_path(report_dir, self.project_root):
                    self.skipped["company_package_path_mismatch"] += 1
                    continue
                artifact_hashes = manifest.get("artifact_hashes")
                if _status(manifest.get("quality_status")) != "pass" or not _market_hashes_valid(artifact_hashes, report_dir):
                    self.skipped["market_package_not_finalized"] += 1
                    continue
                company_id = manifest.get("company_id") or company.get("company_id")
                filing_id = manifest.get("filing_id")
                parse_run_id = manifest.get("parse_run_id")
                if not all(_clean_text(value) for value in (company_id, report_id, filing_id, parse_run_id)):
                    self.skipped["stable_identity_or_digest_missing"] += 1
                    continue
                if not all(_valid_sha256(value) for value in artifact_hashes.values()):
                    self.skipped["artifact_hash_invalid"] += 1
                    continue
                manifest_digest = self.sources[repository_path(manifest_path, self.project_root)]
                self._append(
                    {
                        "path": repository_path(report_dir, self.project_root),
                        "kind": "finalized_report",
                        "owner": "ingestion",
                        "identity": _identity(
                            market=market.upper(),
                            company_id=company_id,
                            report_id=report_id,
                            filing_id=filing_id,
                            parse_run_id=parse_run_id,
                        ),
                        "source_manifest": repository_path(manifest_path, self.project_root),
                        "manifest_sha256": manifest_digest,
                        "finalization_sha256": _combined_digest(manifest_digest, company_digest),
                        "recursive": True,
                    }
                )

    def collect_deal_snapshots(self) -> None:
        deals_root = self.wiki_root / "deals"
        if not deals_root.exists():
            return
        secure_existing_path(deals_root, self.project_root)
        for deal_dir in sorted(
            (path for path in deals_root.iterdir() if path.is_dir() or path.is_symlink()),
            key=lambda item: item.name,
        ):
            secure_existing_path(deal_dir, self.project_root)
            snapshots_root = deal_dir / "evidence" / "snapshots"
            if not snapshots_root.exists():
                continue
            secure_existing_path(snapshots_root, self.project_root)
            for snapshot_dir in sorted(
                (path for path in snapshots_root.iterdir() if path.is_dir() or path.is_symlink()),
                key=lambda item: item.name,
            ):
                secure_existing_path(snapshot_dir, self.project_root)
                candidates = (snapshot_dir / "snapshot_manifest.json", snapshot_dir / "manifest.json")
                manifest_path = next((path for path in candidates if path.exists() or path.is_symlink()), None)
                if manifest_path is None:
                    self.skipped["deal_snapshot_manifest_missing"] += 1
                    continue
                manifest = self._read(manifest_path)
                if manifest is None:
                    continue
                schema = _clean_text(manifest.get("schema_version"))
                hashes = manifest.get("artifact_hashes")
                finalized = manifest.get("finalized") is True or bool(_clean_text(manifest.get("finalized_at")))
                if (
                    schema not in {"siq.deal_evidence_snapshot.v1", "siq_deal_evidence_snapshot_v1"}
                    or _status(manifest.get("status")) not in FINAL_STATUSES
                    or not finalized
                    or not isinstance(hashes, dict)
                    or not hashes
                    or not all(_valid_sha256(value) for value in hashes.values())
                ):
                    self.skipped["deal_snapshot_not_finalized"] += 1
                    continue
                deal_id = _clean_text(manifest.get("deal_id"))
                snapshot_id = _clean_text(manifest.get("snapshot_id"))
                if not deal_id or not snapshot_id:
                    self.skipped["stable_identity_or_digest_missing"] += 1
                    continue
                if deal_id != deal_dir.name or snapshot_id != snapshot_dir.name:
                    self.skipped["deal_snapshot_identity_mismatch"] += 1
                    continue
                manifest_digest = self.sources[repository_path(manifest_path, self.project_root)]
                self._append(
                    {
                        "path": repository_path(snapshot_dir, self.project_root),
                        "kind": "deal_evidence_snapshot",
                        "owner": "deal_evidence",
                        "identity": _identity(deal_id=deal_id, snapshot_id=snapshot_id),
                        "source_manifest": repository_path(manifest_path, self.project_root),
                        "manifest_sha256": manifest_digest,
                        "finalization_sha256": manifest_digest,
                        "recursive": True,
                    }
                )

    def finish(self) -> RegistryBuild:
        entries_by_path: dict[str, dict[str, Any]] = {}
        for entry in self.entries:
            path = entry["path"]
            previous = entries_by_path.get(path)
            if previous is not None and previous != entry:
                raise ImmutableRegistryError("conflicting immutable registry entries")
            entries_by_path[path] = entry
        entries = [entries_by_path[path] for path in sorted(entries_by_path)]
        by_kind = Counter(entry["kind"] for entry in entries)
        source_lines = [f"{path}\0{digest}" for path, digest in sorted(self.sources.items())]
        payload = {
            "schema_version": SCHEMA_VERSION,
            "generated_at": self.generated_at,
            "project_root": PROJECT_ROOT_LABEL,
            "wiki_root": repository_path(self.wiki_root, self.project_root),
            "source_digest": _sha256_bytes("\n".join(source_lines).encode("utf-8")),
            "entries": entries,
            "summary": {
                "entry_count": len(entries),
                "by_kind": dict(sorted(by_kind.items())),
                "skipped_by_reason": dict(sorted(self.skipped.items())),
            },
        }
        content = render_registry(payload)
        return RegistryBuild(payload=payload, content=content, digest=_sha256_bytes(content))


def render_registry(payload: Mapping[str, Any]) -> bytes:
    return (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def build_immutable_registry(
    *,
    project_root: Path,
    wiki_root: Path,
    generated_at: str | None = None,
) -> RegistryBuild:
    builder = _RegistryBuilder(project_root=project_root, wiki_root=wiki_root, generated_at=generated_at)
    builder.collect_cn()
    for market in MARKETS:
        builder.collect_market(market)
    builder.collect_deal_snapshots()
    return builder.finish()


def _secure_output_path(path: Path, project_root: Path) -> Path:
    project_root = project_root.resolve(strict=True)
    if ".." in path.parts:
        raise ImmutableRegistrySecurityError("output path is outside the project root")
    candidate = (path if path.is_absolute() else project_root / path).absolute()
    try:
        candidate.relative_to(project_root)
    except ValueError as exc:
        raise ImmutableRegistrySecurityError("output path is outside the project root") from exc
    current = candidate
    while current != project_root:
        if current.exists() and current.is_symlink():
            raise ImmutableRegistrySecurityError("symlink is not allowed in output paths")
        current = current.parent
    resolved = candidate.resolve(strict=False)
    try:
        resolved.relative_to(project_root)
    except ValueError as exc:
        raise ImmutableRegistrySecurityError("output path is outside the project root") from exc
    return resolved


def _atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def write_registry(
    build: RegistryBuild,
    *,
    project_root: Path,
    output: Path,
    digest_output: Path,
) -> tuple[Path, Path]:
    output = _secure_output_path(output, project_root)
    digest_output = _secure_output_path(digest_output, project_root)
    if output == digest_output:
        raise ImmutableRegistryError("registry and digest outputs must be different files")
    relative_output = output.relative_to(project_root.resolve(strict=True)).as_posix()
    digest_content = f"{build.digest}  {relative_output}\n".encode("ascii")
    _atomic_write(output, build.content)
    _atomic_write(digest_output, digest_content)
    return output, digest_output
