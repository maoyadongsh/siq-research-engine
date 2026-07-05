#!/usr/bin/env python3
"""Remove JP annual-report downloads that are not EDINET YUHO filings.

The JP annual-report pipeline now treats EDINET 有価証券報告書 (form=yuho)
as the canonical complete annual report. Older issuer/integrated reports
should not remain in the download list or pdf-parser artifacts.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class DeleteCandidate:
    pdf_path: Path
    metadata_path: Path | None
    source_id: str
    form: str
    ticker: str | None
    company_name: str | None
    report_end: str | None
    file_name: str
    content_sha256: str | None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--year", type=int, default=2025, help="Report-end year to clean.")
    parser.add_argument(
        "--jp-download-root",
        type=Path,
        default=REPO_ROOT / "data/market-report-finder/downloads/JP",
        help="JP download root.",
    )
    parser.add_argument(
        "--pdf-parser-root",
        type=Path,
        default=REPO_ROOT / "data/pdf-parser",
        help="pdf-parser data root.",
    )
    parser.add_argument(
        "--tasks-db",
        type=Path,
        default=REPO_ROOT / "data/pdf-parser/db/tasks.db",
        help="pdf-parser tasks database.",
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=REPO_ROOT / "data/market-report-finder/jp_non_yuho_cleanup_manifest.json",
        help="Cleanup manifest path.",
    )
    parser.add_argument("--apply", action="store_true", help="Actually delete files and database rows.")
    return parser.parse_args()


def load_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def report_year(value: str | None) -> int | None:
    if not value or len(value) < 4:
        return None
    try:
        return int(value[:4])
    except ValueError:
        return None


def resolve_path(value: str | None, fallback: Path) -> Path:
    if not value:
        return fallback
    path = Path(value)
    return path if path.is_absolute() else (REPO_ROOT / path)


def discover_delete_candidates(jp_download_root: Path, year: int) -> list[DeleteCandidate]:
    candidates: list[DeleteCandidate] = []
    seen: set[Path] = set()

    for metadata_path in sorted(jp_download_root.rglob("*.metadata.json")):
        payload = load_json(metadata_path)
        candidate = payload.get("candidate") if isinstance(payload, dict) else {}
        downloaded = payload.get("downloaded_file") if isinstance(payload, dict) else {}
        if not isinstance(candidate, dict) or not isinstance(downloaded, dict):
            continue
        if str(candidate.get("market") or "").upper() != "JP":
            continue
        if str(candidate.get("report_family") or candidate.get("report_type") or "").lower() != "annual":
            continue
        if report_year(str(candidate.get("report_end") or "")) != year:
            continue

        source_id = str(candidate.get("source_id") or "").strip().lower()
        form = str(candidate.get("form") or "").strip().lower()
        if source_id == "edinet" and form == "yuho":
            continue

        fallback_pdf_path = Path(str(metadata_path)[: -len(".metadata.json")])
        pdf_path = resolve_path(downloaded.get("saved_path"), fallback_pdf_path)
        candidates.append(
            DeleteCandidate(
                pdf_path=pdf_path,
                metadata_path=metadata_path,
                source_id=source_id,
                form=form,
                ticker=str(candidate.get("ticker") or "") or None,
                company_name=str(candidate.get("company_name") or "") or None,
                report_end=str(candidate.get("report_end") or "") or None,
                file_name=str(downloaded.get("file_name") or pdf_path.name),
                content_sha256=str(downloaded.get("content_sha256") or "") or None,
            )
        )
        seen.add(pdf_path.resolve())

    for pdf_path in sorted(jp_download_root.rglob("*.pdf")):
        try:
            resolved = pdf_path.resolve()
        except OSError:
            resolved = pdf_path
        if resolved in seen:
            continue
        if "issuer_annual_report" not in pdf_path.name:
            continue
        if str(year) not in pdf_path.name:
            continue
        candidates.append(
            DeleteCandidate(
                pdf_path=pdf_path,
                metadata_path=None,
                source_id="issuer_annual_report",
                form="unknown",
                ticker=None,
                company_name=pdf_path.parents[2].name if len(pdf_path.parents) > 2 else None,
                report_end=None,
                file_name=pdf_path.name,
                content_sha256=None,
            )
        )

    return candidates


def references_deleted(value: Any, deleted_paths: set[str], deleted_names: set[str], deleted_sha: set[str]) -> bool:
    if isinstance(value, str):
        return value in deleted_paths or Path(value).name in deleted_names or value in deleted_sha
    if isinstance(value, dict):
        for key in ("saved_path", "metadata_path", "file_name", "content_sha256"):
            item = value.get(key)
            if isinstance(item, str) and references_deleted(item, deleted_paths, deleted_names, deleted_sha):
                return True
        return False
    return False


def prune_index(value: Any, deleted_paths: set[str], deleted_names: set[str], deleted_sha: set[str]) -> tuple[Any, int]:
    removed = 0
    if isinstance(value, dict):
        pruned: dict[str, Any] = {}
        for key, item in value.items():
            if references_deleted(item, deleted_paths, deleted_names, deleted_sha):
                removed += 1
                continue
            next_item, next_removed = prune_index(item, deleted_paths, deleted_names, deleted_sha)
            removed += next_removed
            pruned[key] = next_item
        return pruned, removed
    if isinstance(value, list):
        pruned_list: list[Any] = []
        for item in value:
            if references_deleted(item, deleted_paths, deleted_names, deleted_sha):
                removed += 1
                continue
            next_item, next_removed = prune_index(item, deleted_paths, deleted_names, deleted_sha)
            removed += next_removed
            pruned_list.append(next_item)
        return pruned_list, removed
    return value, 0


def update_download_indexes(
    jp_download_root: Path,
    delete_candidates: list[DeleteCandidate],
    *,
    apply: bool,
) -> list[dict[str, Any]]:
    deleted_paths = {
        str(path)
        for candidate in delete_candidates
        for path in (candidate.pdf_path.resolve(), candidate.metadata_path.resolve() if candidate.metadata_path else None)
        if path is not None
    }
    deleted_names = {
        name
        for candidate in delete_candidates
        for name in (candidate.file_name, candidate.pdf_path.name, candidate.metadata_path.name if candidate.metadata_path else None)
        if name
    }
    deleted_sha = {candidate.content_sha256 for candidate in delete_candidates if candidate.content_sha256}
    updates: list[dict[str, Any]] = []

    for index_path in sorted(jp_download_root.rglob(".download_index.json")):
        payload = load_json(index_path)
        pruned, removed = prune_index(payload, deleted_paths, deleted_names, deleted_sha)
        if not removed:
            continue
        updates.append({"index_path": str(index_path), "removed_entries": removed})
        if apply:
            index_path.write_text(json.dumps(pruned, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    return updates


def load_task_matches(tasks_db: Path, delete_candidates: list[DeleteCandidate]) -> list[dict[str, Any]]:
    if not tasks_db.is_file():
        return []

    deleted_names = {candidate.file_name for candidate in delete_candidates}
    deleted_names.update(candidate.pdf_path.name for candidate in delete_candidates)
    deleted_sha = {candidate.content_sha256 for candidate in delete_candidates if candidate.content_sha256}
    matches: list[dict[str, Any]] = []

    con = sqlite3.connect(tasks_db)
    con.row_factory = sqlite3.Row
    try:
        for row in con.execute("select task_id, filename, upload_path, markdown_path, file_sha256 from tasks"):
            filename = row["filename"] or ""
            file_sha256 = row["file_sha256"] or ""
            upload_path = row["upload_path"] or ""
            if filename in deleted_names or Path(filename).name in deleted_names or (file_sha256 and file_sha256 in deleted_sha):
                matches.append(dict(row))
                continue
            if any(name and name in upload_path for name in deleted_names):
                matches.append(dict(row))
    finally:
        con.close()

    return matches


def task_artifact_paths(pdf_parser_root: Path, task: dict[str, Any]) -> list[Path]:
    task_id = str(task.get("task_id") or "").strip()
    paths: list[Path] = []
    if task_id:
        for child in ("results", "output", "cache", "reports"):
            paths.append(pdf_parser_root / child / task_id)
        paths.append(pdf_parser_root / "uploads" / f"{task_id}.pdf")
    for key in ("upload_path", "markdown_path"):
        value = str(task.get(key) or "").strip()
        if not value:
            continue
        path = Path(value)
        path = path if path.is_absolute() else REPO_ROOT / path
        if key == "markdown_path" and path.name:
            paths.append(path.parent)
        paths.append(path)
    unique: list[Path] = []
    seen: set[Path] = set()
    for path in paths:
        try:
            resolved = path.resolve()
        except OSError:
            resolved = path
        if resolved in seen:
            continue
        seen.add(resolved)
        unique.append(path)
    return unique


def remove_path(path: Path) -> bool:
    if path.is_dir():
        shutil.rmtree(path)
        return True
    if path.is_file() or path.is_symlink():
        path.unlink()
        return True
    return False


def backup_tasks_db(tasks_db: Path) -> Path:
    backup_dir = tasks_db.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_path = backup_dir / f"tasks.before-jp-non-yuho-cleanup.{stamp}.db"
    shutil.copy2(tasks_db, backup_path)
    return backup_path


def delete_task_rows(tasks_db: Path, task_ids: list[str]) -> int:
    if not task_ids:
        return 0
    con = sqlite3.connect(tasks_db)
    try:
        con.executemany("delete from tasks where task_id = ?", [(task_id,) for task_id in task_ids])
        deleted = con.total_changes
        con.commit()
        return deleted
    finally:
        con.close()


def contains_task_id(value: Any, task_ids: set[str]) -> bool:
    if isinstance(value, str):
        return value in task_ids
    if isinstance(value, dict):
        return any(contains_task_id(item, task_ids) for item in value.values())
    if isinstance(value, list):
        return any(contains_task_id(item, task_ids) for item in value)
    return False


def update_workflow_jobs(pdf_parser_root: Path, task_ids: list[str], *, apply: bool) -> dict[str, Any]:
    path = pdf_parser_root / "workflow_jobs.json"
    result = {"path": str(path), "removed_jobs": 0}
    if not path.is_file() or not task_ids:
        return result
    payload = load_json(path)
    jobs = payload.get("jobs") if isinstance(payload, dict) else None
    if not isinstance(jobs, list):
        return result
    task_id_set = set(task_ids)
    kept = [job for job in jobs if not contains_task_id(job, task_id_set)]
    removed = len(jobs) - len(kept)
    result["removed_jobs"] = removed
    if removed and apply:
        payload["jobs"] = kept
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def remove_empty_parents(paths: list[Path], stop_at: Path, *, apply: bool) -> list[str]:
    removed: list[str] = []
    stop = stop_at.resolve()
    for path in paths:
        current = path.parent
        while True:
            try:
                resolved = current.resolve()
            except OSError:
                resolved = current
            if resolved == stop or stop not in resolved.parents:
                break
            try:
                if any(current.iterdir()):
                    break
            except OSError:
                break
            removed.append(str(current))
            if apply:
                current.rmdir()
            current = current.parent
    return removed


def main() -> int:
    args = parse_args()
    jp_download_root = args.jp_download_root.resolve()
    pdf_parser_root = args.pdf_parser_root.resolve()
    tasks_db = args.tasks_db.resolve()
    manifest_path = args.manifest.resolve()

    delete_candidates = discover_delete_candidates(jp_download_root, args.year)
    task_matches = load_task_matches(tasks_db, delete_candidates)
    task_ids = sorted({str(task["task_id"]) for task in task_matches if task.get("task_id")})

    index_updates = update_download_indexes(jp_download_root, delete_candidates, apply=args.apply)
    workflow_update = update_workflow_jobs(pdf_parser_root, task_ids, apply=args.apply)

    deleted_files: list[str] = []
    missing_files: list[str] = []
    artifact_paths: list[Path] = []

    for candidate in delete_candidates:
        for path in (candidate.pdf_path, candidate.metadata_path):
            if path is None:
                continue
            if args.apply and remove_path(path):
                deleted_files.append(str(path))
            elif path.exists():
                deleted_files.append(str(path))
            else:
                missing_files.append(str(path))

    for task in task_matches:
        artifact_paths.extend(task_artifact_paths(pdf_parser_root, task))

    deleted_artifacts: list[str] = []
    for path in artifact_paths:
        if args.apply and remove_path(path):
            deleted_artifacts.append(str(path))
        elif path.exists():
            deleted_artifacts.append(str(path))

    db_backup: str | None = None
    db_deleted_rows = 0
    if args.apply and task_ids:
        db_backup = str(backup_tasks_db(tasks_db))
        db_deleted_rows = delete_task_rows(tasks_db, task_ids)

    empty_dirs_removed = remove_empty_parents(
        [candidate.pdf_path for candidate in delete_candidates],
        jp_download_root,
        apply=args.apply,
    )

    manifest = {
        "schema_version": 1,
        "mode": "apply" if args.apply else "dry_run",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "year": args.year,
        "jp_download_root": str(jp_download_root),
        "pdf_parser_root": str(pdf_parser_root),
        "delete_candidate_count": len(delete_candidates),
        "delete_candidates": [
            {
                "pdf_path": str(candidate.pdf_path),
                "metadata_path": str(candidate.metadata_path) if candidate.metadata_path else None,
                "source_id": candidate.source_id,
                "form": candidate.form,
                "ticker": candidate.ticker,
                "company_name": candidate.company_name,
                "report_end": candidate.report_end,
                "file_name": candidate.file_name,
                "content_sha256": candidate.content_sha256,
            }
            for candidate in delete_candidates
        ],
        "deleted_files": deleted_files,
        "missing_files": missing_files,
        "download_index_updates": index_updates,
        "parser_task_count": len(task_ids),
        "parser_tasks": task_matches,
        "deleted_parser_artifacts": deleted_artifacts,
        "workflow_jobs_update": workflow_update,
        "tasks_db_backup": db_backup,
        "tasks_db_deleted_rows": db_deleted_rows,
        "empty_dirs_removed": empty_dirs_removed,
    }
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(
        json.dumps(
            {
                "mode": manifest["mode"],
                "delete_candidate_count": manifest["delete_candidate_count"],
                "parser_task_count": manifest["parser_task_count"],
                "download_indexes_touched": len(index_updates),
                "workflow_jobs_removed": workflow_update["removed_jobs"],
                "tasks_db_deleted_rows": db_deleted_rows,
                "manifest": str(manifest_path),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
