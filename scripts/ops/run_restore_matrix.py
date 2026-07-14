#!/usr/bin/env python3
"""Run the required restore smoke for every SIQ business database backup."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.parse import parse_qsl, unquote, urlsplit

REPO_ROOT = Path(__file__).resolve().parents[2]
RESTORE_SCRIPT = REPO_ROOT / "scripts" / "ops" / "restore_smoke.sh"
SCHEMA_VERSION = "siq_restore_matrix_v2"
SCHEMA_CONTRACT_VERSION = "siq_postgres_schema_contract_v1"
EMPTY_TOMBSTONE_HEAD_HMAC = "0" * 64
VOICEPRINT_EXPECTED_COUNT_ENV = "SIQ_MEETING_VOICEPRINT_TOMBSTONE_EXPECTED_COUNT"
VOICEPRINT_EXPECTED_HEAD_ENV = "SIQ_MEETING_VOICEPRINT_TOMBSTONE_EXPECTED_HEAD_HMAC"
REQUIRED_FILE_ARCHIVES: tuple[str, ...] = (
    "backend-data.tar.gz",
    "pdf-parser-data.tar.gz",
    "wiki.tar.gz",
    "report-downloads.tar.gz",
    "hermes-home.tar.gz",
)
DATABASE_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{1,62}$")
OBJECT_RECORD_RE = re.compile(
    r"^object=(?P<object>\S+) status=(?P<status>\S+) size=(?P<size>[0-9]+)(?:\s+.*)?$"
)
CHECKSUM_RECORD_RE = re.compile(r"^(?P<digest>[0-9a-fA-F]{64}) (?P<mode>[ *])(?P<path>.+)$")
LOCAL_PATH_RE = re.compile(r"(?:/(?:home|Users|tmp)/[^\s`\"'|,;]+|[A-Za-z]:\\+Users\\+[^\s`\"'|,;]+)")
LIBPQ_QUERY_ENV = {
    "application_name": "PGAPPNAME",
    "connect_timeout": "PGCONNECT_TIMEOUT",
    "options": "PGOPTIONS",
    "sslcert": "PGSSLCERT",
    "sslcrl": "PGSSLCRL",
    "sslkey": "PGSSLKEY",
    "sslmode": "PGSSLMODE",
    "sslrootcert": "PGSSLROOTCERT",
    "target_session_attrs": "PGTARGETSESSIONATTRS",
}


@dataclass(frozen=True)
class RestoreTarget:
    database: str
    expected_relations: tuple[str, ...]
    probe_relation: str
    require_nonempty: bool


@dataclass(frozen=True)
class VoiceprintCheckpointExpectation:
    entry_count: int
    head_hmac: str
    checkpoint_sha256: str


TARGETS: tuple[RestoreTarget, ...] = (
    RestoreTarget("siq_app", ("public.users", "public.user_artifacts"), "public.users", False),
    RestoreTarget(
        "siq_document_parser",
        ("document_parser.documents", "document_parser.parse_runs"),
        "document_parser.documents",
        False,
    ),
    RestoreTarget("siq_us", ("sec_us.filings", "sec_us.financial_statement_items"), "sec_us.v_agent_financial_facts", True),
    RestoreTarget("siq_hk", ("pdf2md_hk.filings", "pdf2md_hk.financial_statement_items"), "pdf2md_hk.v_agent_financial_facts", True),
    RestoreTarget("siq_jp", ("edinet_jp.filings", "edinet_jp.financial_statement_items"), "edinet_jp.v_agent_financial_facts", True),
    RestoreTarget("siq_kr", ("dart_kr.filings", "dart_kr.financial_statement_items"), "dart_kr.v_agent_financial_facts", True),
    RestoreTarget("siq_eu", ("eu_ifrs.filings", "eu_ifrs.financial_statement_items"), "eu_ifrs.v_agent_financial_facts", True),
)

SCHEMA_AUTHORITIES: dict[str, tuple[str, ...]] = {
    "siq_app": ("apps/api/migrations/*.sql",),
    "siq_document_parser": ("db/ddl/060_create_document_parser_schema.sql",),
    "siq_us": ("db/ddl/010_create_sec_us_schema.sql",),
    "siq_hk": ("db/ddl/020_create_pdf2md_hk_schema.sql",),
    "siq_jp": ("db/ddl/030_create_edinet_jp_schema.sql",),
    "siq_kr": ("db/ddl/040_create_dart_kr_schema.sql",),
    "siq_eu": ("db/ddl/050_create_eu_ifrs_schema.sql",),
}


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _redact(value: str, *, limit: int = 240, keep_tail: bool = False) -> str:
    text = str(value or "").replace("\r", " ").replace("\n", " ")
    text = re.sub(r"(?i)(postgres(?:ql)?(?:\+[^:]+)?://)[^\s]+", r"\1[redacted]", text)
    text = LOCAL_PATH_RE.sub("[local-path]", text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= limit:
        return text
    return ("..." + text[-(limit - 3) :]) if keep_tail else text[:limit]


def _restore_phase_status(
    output: str,
    phase: str,
    *,
    returncode: int,
    requested: bool = True,
) -> str:
    """Read machine-stable phase markers emitted by restore_smoke.sh."""
    if not requested:
        return "not_requested"
    statuses = re.findall(
        rf"restore_phase={re.escape(phase)} status=(started|passed|failed)",
        output,
    )
    if "failed" in statuses:
        return "failed"
    if "passed" in statuses or returncode == 0:
        return "passed"
    if "started" in statuses:
        return "failed"
    return "not_run"


def _failure_summary(output: str) -> str:
    """Prefer a structured restore error over trailing cleanup diagnostics."""
    for line in reversed(output.splitlines()):
        candidate = line.strip()
        if not candidate.startswith("{") or '"error_code"' not in candidate:
            continue
        try:
            payload = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and payload.get("error_code"):
            return _redact(candidate)
    return _redact(output, keep_tail=True)


def _voiceprint_checkpoint_evidence(
    output: str,
    expected: VoiceprintCheckpointExpectation,
) -> dict[str, Any] | None:
    for line in reversed(output.splitlines()):
        candidate = line.strip()
        if not candidate.startswith("{"):
            continue
        try:
            payload = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        if payload.get("schema_version") != "siq.meeting.voiceprint_tombstone_reconcile.v1":
            continue
        if (
            payload.get("status") != "passed"
            or payload.get("ledger_checkpoint_verified") is not True
            or type(payload.get("ledger_entry_count")) is not int
            or payload.get("ledger_entry_count") != expected.entry_count
            or payload.get("ledger_head_hmac") != expected.head_hmac
        ):
            return None
        return {
            "actual_entry_count": payload["ledger_entry_count"],
            "actual_head_hmac": payload["ledger_head_hmac"],
            "checkpoint_verified": True,
        }
    return None


def schema_authority_files(database: str, *, repo_root: Path = REPO_ROOT) -> tuple[Path, ...]:
    patterns = SCHEMA_AUTHORITIES.get(database)
    if not patterns:
        raise ValueError(f"schema_authority_not_defined:{database}")
    paths: list[Path] = []
    for pattern in patterns:
        paths.extend(sorted(repo_root.glob(pattern)))
    paths = sorted(set(paths), key=lambda path: path.relative_to(repo_root).as_posix())
    if not paths or any(not path.is_file() or path.stat().st_size <= 0 for path in paths):
        raise ValueError(f"schema_authority_missing:{database}")
    return tuple(paths)


def schema_authority_sha256(database: str, *, repo_root: Path = REPO_ROOT) -> str:
    paths = schema_authority_files(database, repo_root=repo_root)
    digest = hashlib.sha256()
    for path in paths:
        digest.update(path.relative_to(repo_root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).hexdigest().encode("ascii"))
        digest.update(b"\0")
    return digest.hexdigest()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _required_voiceprint_checkpoint() -> VoiceprintCheckpointExpectation:
    validation_required = os.getenv(
        "SIQ_RESTORE_MATRIX_VOICEPRINT_TOMBSTONE_REQUIRED", "1"
    ).strip().lower()
    if validation_required not in {"1", "true", "yes", "on"}:
        raise ValueError("voiceprint_tombstone_validation_must_be_required")
    raw_count = os.getenv(VOICEPRINT_EXPECTED_COUNT_ENV, "").strip()
    raw_head = os.getenv(VOICEPRINT_EXPECTED_HEAD_ENV, "").strip()
    if not raw_count:
        raise ValueError("voiceprint_tombstone_expected_count_missing")
    if not raw_head:
        raise ValueError("voiceprint_tombstone_expected_head_hmac_missing")
    if not re.fullmatch(r"0|[1-9][0-9]*", raw_count):
        raise ValueError("voiceprint_tombstone_expected_count_invalid")
    if not re.fullmatch(r"[0-9a-fA-F]{64}", raw_head):
        raise ValueError("voiceprint_tombstone_expected_head_hmac_invalid")
    entry_count = int(raw_count)
    head_hmac = raw_head.lower()
    if entry_count == 0 and head_hmac != EMPTY_TOMBSTONE_HEAD_HMAC:
        raise ValueError("voiceprint_tombstone_empty_checkpoint_head_mismatch")
    payload = json.dumps(
        {"entry_count": entry_count, "head_hmac": head_hmac},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")
    return VoiceprintCheckpointExpectation(
        entry_count=entry_count,
        head_hmac=head_hmac,
        checkpoint_sha256=hashlib.sha256(payload).hexdigest(),
    )


def compatibility_migrations(database: str, *, repo_root: Path = REPO_ROOT) -> tuple[Path, ...]:
    """Return the complete ordered authority chain for compatibility validation."""
    return schema_authority_files(database, repo_root=repo_root)


def _validate_backup_manifest(
    backup_dir: Path,
) -> tuple[
    Path,
    dict[str, str],
    dict[str, dict[str, str]],
    dict[str, dict[str, Any]],
]:
    manifest_path = backup_dir / "manifest.txt"
    checksum_path = backup_dir / "checksums.sha256"
    if not manifest_path.is_file() or not checksum_path.is_file():
        raise ValueError("backup_manifest_or_checksum_missing")
    values: dict[str, str] = {}
    object_records: dict[str, list[dict[str, str | int]]] = {}
    for line in manifest_path.read_text(encoding="utf-8").splitlines():
        if line.startswith("object="):
            match = OBJECT_RECORD_RE.fullmatch(line)
            if match:
                object_records.setdefault(match.group("object"), []).append(
                    {
                        "status": match.group("status"),
                        "size": int(match.group("size")),
                    }
                )
            else:
                remainder = line.removeprefix("object=")
                object_name = remainder.split(maxsplit=1)[0] if remainder else ""
                object_records.setdefault(object_name, []).append(
                    {"status": "invalid", "size": -1}
                )
            continue
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    checksum_entries: dict[str, list[str]] = {}
    for line in checksum_path.read_text(encoding="utf-8").splitlines():
        match = CHECKSUM_RECORD_RE.fullmatch(line)
        if match:
            checksum_entries.setdefault(match.group("path"), []).append(
                match.group("digest").lower()
            )
    databases = tuple(item.strip() for item in values.get("postgres_databases", "").split(",") if item.strip())
    expected = tuple(target.database for target in TARGETS)
    if databases != expected:
        raise ValueError("backup_database_set_or_order_mismatch")
    backup_mode = values.get("backup_mode", "").strip().lower()
    if backup_mode not in {"required", "release"}:
        raise ValueError("backup_mode_not_release_grade")
    if values.get("skip_large", "").strip() != "0":
        raise ValueError("backup_skip_large_not_release_grade")
    if values.get("schema_contract_version") != SCHEMA_CONTRACT_VERSION:
        raise ValueError("backup_schema_contract_version_mismatch")
    archive_evidence: dict[str, dict[str, Any]] = {}
    for archive in REQUIRED_FILE_ARCHIVES:
        records = object_records.get(archive, [])
        if len(records) != 1:
            raise ValueError(f"backup_required_archive_manifest_record_mismatch:{archive}")
        record = records[0]
        if record["status"] != "ok":
            raise ValueError(f"backup_required_archive_not_ok:{archive}")
        path = backup_dir / archive
        if path.is_symlink() or not path.is_file() or path.stat().st_size <= 0:
            raise ValueError(f"backup_required_archive_missing_or_empty:{archive}")
        actual_size = path.stat().st_size
        if record["size"] != actual_size:
            raise ValueError(f"backup_required_archive_size_mismatch:{archive}")
        digests = checksum_entries.get(archive, [])
        if len(digests) != 1:
            raise ValueError(f"backup_required_archive_checksum_entry_mismatch:{archive}")
        actual_digest = _file_sha256(path)
        if digests[0] != actual_digest:
            raise ValueError(f"backup_required_archive_checksum_mismatch:{archive}")
        archive_evidence[archive] = {
            "size": actual_size,
            "sha256": actual_digest,
        }
    schema_evidence: dict[str, dict[str, str]] = {}
    for database in databases:
        if not DATABASE_NAME_RE.fullmatch(database):
            raise ValueError("invalid_database_name_in_manifest")
        if not (backup_dir / "postgres" / f"{database}.sql.gz").is_file():
            raise ValueError(f"backup_dump_missing:{database}")
        authority_key = f"schema_authority_sha256_{database}"
        observed_authority = values.get(authority_key, "")
        expected_authority = schema_authority_sha256(database)
        if not re.fullmatch(r"[0-9a-f]{64}", observed_authority):
            raise ValueError(f"backup_schema_authority_missing:{database}")
        if observed_authority != expected_authority:
            raise ValueError(f"backup_schema_authority_mismatch:{database}")
        snapshot_key = f"schema_snapshot_{database}"
        expected_snapshot = f"postgres/{database}.schema.sql.gz"
        if values.get(snapshot_key) != expected_snapshot:
            raise ValueError(f"backup_schema_snapshot_manifest_mismatch:{database}")
        snapshot_path = backup_dir / expected_snapshot
        if not snapshot_path.is_file() or snapshot_path.stat().st_size <= 0:
            raise ValueError(f"backup_schema_snapshot_missing:{database}")
        schema_evidence[database] = {
            "authority_sha256": observed_authority,
            "snapshot": expected_snapshot,
            "snapshot_sha256": hashlib.sha256(snapshot_path.read_bytes()).hexdigest(),
        }
    return checksum_path, values, schema_evidence, archive_evidence


def _run_target(
    target: RestoreTarget,
    *,
    backup_dir: Path,
    checksum_path: Path,
    schema_snapshot: Path,
    migrations: tuple[Path, ...],
    voiceprint_checkpoint: VoiceprintCheckpointExpectation,
    admin_url: str,
    restore_script: Path,
    timeout: float,
    runner: Any = subprocess.run,
) -> dict[str, Any]:
    env = {
        **os.environ,
        "SIQ_RESTORE_SMOKE": "1",
        "SIQ_RESTORE_SMOKE_MODE": "required",
        "SIQ_RESTORE_SMOKE_SOURCE": str(backup_dir / "postgres" / f"{target.database}.sql.gz"),
        "SIQ_RESTORE_SMOKE_ADMIN_URL": admin_url,
        "SIQ_RESTORE_SMOKE_CHECKSUM_MANIFEST": str(checksum_path),
        "SIQ_RESTORE_SMOKE_EXPECTED_SCHEMA_SNAPSHOT": str(schema_snapshot),
        "SIQ_RESTORE_SMOKE_COMPATIBILITY_MIGRATIONS": "\n".join(
            str(migration) for migration in migrations
        ),
        "SIQ_RESTORE_SMOKE_EXPECTED_RELATIONS": ",".join(target.expected_relations),
        "SIQ_RESTORE_SMOKE_AGENT_VIEW": target.probe_relation,
        "SIQ_RESTORE_SMOKE_REQUIRE_AGENT_VIEW": "1" if target.require_nonempty else "0",
        "SIQ_RESTORE_SMOKE_NONEMPTY_RELATIONS": target.probe_relation if target.require_nonempty else "",
        "SIQ_RESTORE_SMOKE_DATABASE_NAME": target.database,
        VOICEPRINT_EXPECTED_COUNT_ENV: str(voiceprint_checkpoint.entry_count),
        VOICEPRINT_EXPECTED_HEAD_ENV: voiceprint_checkpoint.head_hmac,
        "SIQ_RESTORE_SMOKE_VOICEPRINT_TOMBSTONE_REQUIRED": (
            os.getenv("SIQ_RESTORE_MATRIX_VOICEPRINT_TOMBSTONE_REQUIRED", "1")
            if target.database == "siq_app"
            else "0"
        ),
    }
    voiceprint_requested = str(env["SIQ_RESTORE_SMOKE_VOICEPRINT_TOMBSTONE_REQUIRED"]).lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    voiceprint_evidence: dict[str, Any] | None = None
    started = time.monotonic()
    try:
        completed = runner(
            [str(restore_script)],
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        returncode = int(completed.returncode)
        diagnostic_source = "\n".join(part for part in (completed.stdout, completed.stderr) if part)
        diagnostic = _failure_summary(diagnostic_source) if returncode else None
        if returncode == 0 and voiceprint_requested:
            voiceprint_evidence = _voiceprint_checkpoint_evidence(
                diagnostic_source,
                voiceprint_checkpoint,
            )
            if voiceprint_evidence is None:
                returncode = 1
                diagnostic = "voiceprint_checkpoint_evidence_missing_or_mismatch"
    except subprocess.TimeoutExpired:
        returncode = 124
        diagnostic = "restore_timeout"
        diagnostic_source = ""
    except OSError as exc:
        returncode = 1
        diagnostic = f"runner_error:{type(exc).__name__}"
        diagnostic_source = ""
    schema_snapshot_status = _restore_phase_status(
        diagnostic_source,
        "schema_snapshot",
        returncode=returncode,
    )
    migration_status = _restore_phase_status(
        diagnostic_source,
        "migration_compatibility",
        returncode=returncode,
    )
    voiceprint_status = _restore_phase_status(
        diagnostic_source,
        "voiceprint_tombstone",
        returncode=returncode,
        requested=voiceprint_requested,
    )
    voiceprint_validation: dict[str, Any] = {
        "status": voiceprint_status,
        "required": voiceprint_requested,
    }
    if voiceprint_requested:
        voiceprint_validation.update(
            {
                "expected_entry_count": voiceprint_checkpoint.entry_count,
                "expected_head_hmac": voiceprint_checkpoint.head_hmac,
                "checkpoint_sha256": voiceprint_checkpoint.checkpoint_sha256,
                **(voiceprint_evidence or {}),
            }
        )
        if voiceprint_evidence is None:
            voiceprint_validation["status"] = "failed"
    result = {
        "database": target.database,
        "status": "passed" if returncode == 0 else "failed",
        "passed": returncode == 0,
        "duration_seconds": round(time.monotonic() - started, 3),
        "expected_relation_count": len(target.expected_relations),
        "probe_kind": "nonempty" if target.require_nonempty else "queryable",
        "schema_snapshot_validation": {
            "status": schema_snapshot_status,
            "snapshot": schema_snapshot.name,
        },
        "migration_compatibility": {
            "status": migration_status,
            "authority": [migration.name for migration in migrations],
            "authority_count": len(migrations),
        },
        "voiceprint_tombstone_validation": voiceprint_validation,
    }
    if diagnostic:
        result["failure_summary"] = diagnostic
    return result


def _audit_temporary_database_cleanup(
    *,
    admin_url: str,
    timeout: float,
    runner: Any = subprocess.run,
) -> dict[str, Any]:
    """Fail closed unless every disposable restore database was removed."""
    parsed = urlsplit(admin_url)
    env = {**os.environ, "PGDATABASE": unquote(parsed.path.lstrip("/")) or "postgres"}
    if parsed.hostname:
        env["PGHOST"] = unquote(parsed.hostname)
    if parsed.port:
        env["PGPORT"] = str(parsed.port)
    if parsed.username:
        env["PGUSER"] = unquote(parsed.username)
    if parsed.password:
        env["PGPASSWORD"] = unquote(parsed.password)
    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        env_name = LIBPQ_QUERY_ENV.get(key)
        if env_name:
            env[env_name] = value
    try:
        completed = runner(
            [
                "psql",
                "-v",
                "ON_ERROR_STOP=1",
                "-Atqc",
                "select count(*), current_setting('server_version_num') "
                "from pg_database where datname like 'siq_restore_smoke_%'",
            ],
            env=env,
            capture_output=True,
            text=True,
            timeout=min(timeout, 30.0),
            check=False,
        )
        output = str(completed.stdout or "").strip()
        if int(completed.returncode) != 0:
            diagnostic_source = "\n".join(part for part in (completed.stdout, completed.stderr) if part)
            return {
                "status": "failed",
                "passed": False,
                "temporary_database_prefix": "siq_restore_smoke_",
                "failure_summary": _redact(diagnostic_source, keep_tail=True) or "cleanup_audit_failed",
            }
        parts = output.split("|")
        if len(parts) != 2 or not all(part.isdigit() for part in parts):
            return {
                "status": "failed",
                "passed": False,
                "temporary_database_prefix": "siq_restore_smoke_",
                "failure_summary": "cleanup_audit_invalid_count",
            }
        residual_count = int(parts[0])
        server_version_num = int(parts[1])
        return {
            "status": "passed" if residual_count == 0 else "failed",
            "passed": residual_count == 0,
            "temporary_database_prefix": "siq_restore_smoke_",
            "residual_database_count": residual_count,
            "server_version_num": server_version_num,
            "postgres_major": server_version_num // 10000,
        }
    except subprocess.TimeoutExpired:
        return {
            "status": "failed",
            "passed": False,
            "temporary_database_prefix": "siq_restore_smoke_",
            "failure_summary": "cleanup_audit_timeout",
        }
    except (OSError, ValueError) as exc:
        return {
            "status": "failed",
            "passed": False,
            "temporary_database_prefix": "siq_restore_smoke_",
            "failure_summary": f"cleanup_audit_error:{type(exc).__name__}",
        }


def run_matrix(
    *,
    backup_dir: Path,
    admin_url: str,
    restore_script: Path = RESTORE_SCRIPT,
    timeout: float = 900.0,
    runner: Any = subprocess.run,
) -> dict[str, Any]:
    voiceprint_checkpoint = _required_voiceprint_checkpoint()
    parsed_scheme = admin_url.split(":", 1)[0].lower()
    if parsed_scheme not in {"postgres", "postgresql", "postgresql+psycopg", "postgresql+psycopg2"}:
        raise ValueError("invalid_restore_admin_url")
    checksum_path, manifest, schema_evidence, archive_evidence = _validate_backup_manifest(
        backup_dir.resolve()
    )
    checksum_bytes = checksum_path.read_bytes()
    checksum_entry_count = sum(1 for line in checksum_bytes.splitlines() if line.strip())
    results = [
        _run_target(
            target,
            backup_dir=backup_dir.resolve(),
            checksum_path=checksum_path,
            schema_snapshot=backup_dir.resolve() / schema_evidence[target.database]["snapshot"],
            migrations=compatibility_migrations(target.database),
            voiceprint_checkpoint=voiceprint_checkpoint,
            admin_url=admin_url,
            restore_script=restore_script,
            timeout=timeout,
            runner=runner,
        )
        for target in TARGETS
    ]
    cleanup = _audit_temporary_database_cleanup(admin_url=admin_url, timeout=timeout, runner=runner)
    passed_count = sum(1 for item in results if item["passed"])
    schema_snapshot_passed_count = sum(
        1 for item in results if item["schema_snapshot_validation"]["status"] == "passed"
    )
    migration_passed_count = sum(
        1 for item in results if item["migration_compatibility"]["status"] == "passed"
    )
    schema_compatibility_passed = (
        schema_snapshot_passed_count == len(results)
        and migration_passed_count == len(results)
    )
    passed = passed_count == len(results) and cleanup["passed"]
    backup_id = manifest.get("timestamp") or "unknown"
    voiceprint_validation = results[0]["voiceprint_tombstone_validation"]
    checkpoint_binding_payload = json.dumps(
        {
            "backup_id": backup_id,
            "checkpoint_sha256": voiceprint_checkpoint.checkpoint_sha256,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": now_iso(),
        "status": "passed" if passed else "failed",
        "passed": passed,
        "backup_id": backup_id,
        "backup_evidence": {
            "manifest_id": manifest.get("timestamp") or "unknown",
            "backup_mode": manifest["backup_mode"].strip().lower(),
            "skip_large": manifest["skip_large"].strip(),
            "database_dump_count": len(TARGETS),
            "checksum_entry_count": checksum_entry_count,
            "checksum_manifest_sha256": hashlib.sha256(checksum_bytes).hexdigest(),
            "shared_checksum_manifest": True,
            "checksum_verification_mode": "per_database_restore",
            "schema_contract_version": SCHEMA_CONTRACT_VERSION,
            "schema_authority_verified": True,
            "schema_snapshot_count": len(schema_evidence),
            "schema_snapshot_verification_mode": "restored_catalog_redump",
            "migration_compatibility_mode": "authority_chain_schema_convergence",
            "required_file_archive_count": len(archive_evidence),
            "required_file_archives_verified": True,
            "required_file_archives": list(archive_evidence),
        },
        "schema_compatibility": {
            "passed": schema_compatibility_passed,
            "contract_version": SCHEMA_CONTRACT_VERSION,
            "authority_digest_count": len(schema_evidence),
            "restored_snapshot_count": schema_snapshot_passed_count,
            "migration_dry_run_count": migration_passed_count,
            "databases": [target.database for target in TARGETS],
        },
        "voiceprint_tombstone_checkpoint": {
            "backup_id": backup_id,
            "expected_entry_count": voiceprint_checkpoint.entry_count,
            "expected_head_hmac": voiceprint_checkpoint.head_hmac,
            "checkpoint_sha256": voiceprint_checkpoint.checkpoint_sha256,
            "backup_binding_sha256": hashlib.sha256(
                checkpoint_binding_payload
            ).hexdigest(),
            "verified": voiceprint_validation.get("checkpoint_verified") is True,
            "actual_entry_count": voiceprint_validation.get("actual_entry_count"),
            "actual_head_hmac": voiceprint_validation.get("actual_head_hmac"),
        },
        "summary": {
            "databases": len(results),
            "passed_databases": passed_count,
            "failed_databases": len(results) - passed_count,
            "residual_databases": cleanup.get("residual_database_count"),
        },
        "results": results,
        "cleanup": cleanup,
    }


def render_markdown(report: Mapping[str, Any]) -> str:
    summary = report.get("summary") if isinstance(report.get("summary"), Mapping) else {}
    backup_evidence = report.get("backup_evidence") if isinstance(report.get("backup_evidence"), Mapping) else {}
    checkpoint = (
        report.get("voiceprint_tombstone_checkpoint")
        if isinstance(report.get("voiceprint_tombstone_checkpoint"), Mapping)
        else {}
    )
    lines = [
        "# SIQ PostgreSQL Restore Matrix",
        "",
        f"- Status: `{report.get('status', 'unknown')}`",
        f"- Backup ID: `{report.get('backup_id', 'unknown')}`",
        f"- Backup mode: `{backup_evidence.get('backup_mode', 'unknown')}`",
        f"- Large data skipped: `{backup_evidence.get('skip_large', 'unknown')}`",
        f"- Shared checksum manifest: `{backup_evidence.get('shared_checksum_manifest', False)}`",
        f"- Checksum entries: `{backup_evidence.get('checksum_entry_count', 0)}`",
        f"- Checksum manifest SHA-256: `{backup_evidence.get('checksum_manifest_sha256', 'unknown')}`",
        f"- Schema contract: `{backup_evidence.get('schema_contract_version', 'unknown')}`",
        f"- Schema authority verified: `{backup_evidence.get('schema_authority_verified', False)}`",
        f"- Restored schema snapshots: `{backup_evidence.get('schema_snapshot_count', 0)}`",
        f"- Required file archives: `{backup_evidence.get('required_file_archive_count', 0)}`",
        f"- Voiceprint ledger entries: `{checkpoint.get('expected_entry_count', 'unknown')}`",
        f"- Voiceprint checkpoint verified: `{checkpoint.get('verified', False)}`",
        f"- Voiceprint checkpoint SHA-256: `{checkpoint.get('checkpoint_sha256', 'unknown')}`",
        f"- Backup/checkpoint binding SHA-256: `{checkpoint.get('backup_binding_sha256', 'unknown')}`",
        f"- Databases: `{summary.get('passed_databases', 0)}/{summary.get('databases', 0)}`",
        f"- Temporary database residue: `{summary.get('residual_databases', 'unknown')}`",
        f"- PostgreSQL major: `{(report.get('cleanup') or {}).get('postgres_major', 'unknown')}`",
        "",
        "| Database | Status | Schema | Migration | Voiceprint | Probe | Relations | Duration (s) | Failure |",
        "| --- | --- | --- | --- | --- | --- | ---: | ---: | --- |",
    ]
    for item in report.get("results") or []:
        if not isinstance(item, Mapping):
            continue
        failure = str(item.get("failure_summary") or "").replace("|", "\\|")
        schema_status = (item.get("schema_snapshot_validation") or {}).get("status", "unknown")
        migration_status = (item.get("migration_compatibility") or {}).get("status", "unknown")
        voiceprint_status = (item.get("voiceprint_tombstone_validation") or {}).get("status", "unknown")
        lines.append(
            f"| `{item.get('database')}` | `{item.get('status')}` | `{schema_status}` | "
            f"`{migration_status}` | `{voiceprint_status}` | `{item.get('probe_kind')}` | "
            f"{item.get('expected_relation_count', 0)} | {item.get('duration_seconds', 0)} | {failure} |"
        )
    return "\n".join(lines) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backup-dir", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=900.0)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--markdown", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        report = run_matrix(
            backup_dir=args.backup_dir,
            admin_url=str(os.getenv("SIQ_RESTORE_MATRIX_ADMIN_URL") or "").strip(),
            timeout=args.timeout,
        )
    except ValueError as exc:
        report = {
            "schema_version": SCHEMA_VERSION,
            "generated_at": now_iso(),
            "status": "blocked",
            "passed": False,
            "reason": _redact(str(exc)),
            "summary": {"databases": len(TARGETS), "passed_databases": 0, "failed_databases": len(TARGETS)},
            "results": [],
        }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.markdown.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args.markdown.write_text(render_markdown(report), encoding="utf-8")
    print(json.dumps({"status": report["status"], **report["summary"]}, ensure_ascii=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
