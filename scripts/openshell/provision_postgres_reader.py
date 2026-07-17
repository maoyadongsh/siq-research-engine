#!/usr/bin/env python3
"""Provision, verify, or remove SIQ's dedicated OpenShell PostgreSQL reader."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import secrets
import stat
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping, Protocol, Sequence

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ROLE_NAME = "siq_openshell_reader"
ROLE_MARKER = "managed-by=siq-openshell;component=read-only-data-broker;version=1"
SCHEMA_VERSION = "siq.openshell.postgres-reader-state.v1"
PROOF_SCHEMA_VERSION = "siq.openshell.service_security_proofs.v1"
DEFAULT_CONTAINER = "docker-postgres-1"
DEFAULT_ADMIN_USER = "postgres"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_HOST_PORT = 15_432
DEFAULT_CONTAINER_PORT = 5_432
DEFAULT_SECRET_PATH = PROJECT_ROOT / "var/openshell/secrets/postgres-reader.env"
DEFAULT_STATE_PATH = PROJECT_ROOT / "var/openshell/postgres-reader/state.json"
DEFAULT_PROOF_PATH = PROJECT_ROOT / "var/openshell/proofs/service-security.json"
DEFAULT_BACKUP_ROOT = PROJECT_ROOT / "var/openshell/backups/postgres-reader"
LOCK_PATH = PROJECT_ROOT / "var/openshell/locks/maintenance.lock"
MAX_FILE_BYTES = 64 * 1024

DATABASE_SCHEMAS = (
    ("siq", "pdf2md"),
    ("siq_hk", "pdf2md_hk"),
    ("siq_us", "sec_us"),
    ("siq_jp", "edinet_jp"),
    ("siq_kr", "dart_kr"),
    ("siq_eu", "eu_ifrs"),
)

IDENTIFIER_RE = re.compile(r"[a-z_][a-z0-9_]{0,62}\Z")
CONTAINER_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
PASSWORD_RE = re.compile(r"[A-Za-z0-9_-]{43,128}\Z")
SECRET_KEYS = {
    "SIQ_OPENSHELL_PG_RO_HOST",
    "SIQ_OPENSHELL_PG_RO_PORT",
    "SIQ_OPENSHELL_PG_RO_USER",
    "SIQ_OPENSHELL_PG_RO_PASSWORD",
    "SIQ_OPENSHELL_PG_RO_SSLMODE",
}


class ReaderProvisionError(RuntimeError):
    """A stable operator-facing failure that never contains credentials or SQL output."""


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""


class CommandRunner(Protocol):
    def run(self, command: Sequence[str], *, input_text: str) -> CommandResult: ...


class SubprocessRunner:
    def run(self, command: Sequence[str], *, input_text: str) -> CommandResult:
        try:
            completed = subprocess.run(
                list(command),
                cwd=PROJECT_ROOT,
                input=input_text,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=45,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ReaderProvisionError("postgres_admin_command_failed") from exc
        return CommandResult(completed.returncode, completed.stdout, completed.stderr)


def _sql_identifier(value: str) -> str:
    if not value or "\x00" in value or "\n" in value or "\r" in value or len(value.encode()) > 63:
        raise ReaderProvisionError("postgres_identifier_invalid")
    return '"' + value.replace('"', '""') + '"'


def _sql_literal(value: str) -> str:
    if "\x00" in value:
        raise ReaderProvisionError("postgres_literal_invalid")
    return "'" + value.replace("'", "''") + "'"


def _validate_fixed_identifier(value: str) -> str:
    if not IDENTIFIER_RE.fullmatch(value):
        raise ReaderProvisionError("fixed_postgres_identifier_invalid")
    return value


class DockerPostgres:
    """Run psql inside the reviewed Compose container without putting passwords in argv."""

    def __init__(
        self,
        *,
        container: str,
        admin_user: str,
        container_port: int,
        runner: CommandRunner | None = None,
    ) -> None:
        if not CONTAINER_RE.fullmatch(container):
            raise ReaderProvisionError("postgres_container_name_invalid")
        _validate_fixed_identifier(admin_user)
        if not 1 <= container_port <= 65_535:
            raise ReaderProvisionError("postgres_container_port_invalid")
        self.container = container
        self.admin_user = admin_user
        self.container_port = container_port
        self.runner = runner or SubprocessRunner()

    def _checked(self, command: Sequence[str], *, input_text: str, error_code: str) -> str:
        result = self.runner.run(command, input_text=input_text)
        if result.returncode != 0:
            raise ReaderProvisionError(error_code)
        if len(result.stdout.encode("utf-8", errors="replace")) > MAX_FILE_BYTES:
            raise ReaderProvisionError("postgres_response_too_large")
        return result.stdout

    def admin(self, database: str, sql: str) -> str:
        _validate_fixed_identifier(database)
        command = (
            "docker",
            "exec",
            "-i",
            self.container,
            "psql",
            "-X",
            "-A",
            "-t",
            "-v",
            "ON_ERROR_STOP=1",
            "-U",
            self.admin_user,
            "-d",
            database,
        )
        return self._checked(command, input_text=sql, error_code="postgres_admin_sql_failed")

    def reader(self, database: str, password: str, sql: str) -> str:
        _validate_fixed_identifier(database)
        if not PASSWORD_RE.fullmatch(password):
            raise ReaderProvisionError("postgres_reader_password_invalid")
        shell = (
            "IFS= read -r PGPASSWORD; export PGPASSWORD; "
            "exec psql -X -A -t -v ON_ERROR_STOP=1 "
            f'-h 127.0.0.1 -p {self.container_port} -U "$1" -d "$2"'
        )
        command = (
            "docker",
            "exec",
            "-i",
            self.container,
            "sh",
            "-ceu",
            shell,
            "siq-postgres-reader",
            ROLE_NAME,
            database,
        )
        return self._checked(
            command,
            input_text=f"{password}\n{sql}",
            error_code="postgres_reader_verification_failed",
        )


def _last_nonempty_line(output: str) -> str:
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    if not lines:
        raise ReaderProvisionError("postgres_response_empty")
    return lines[-1]


def _parse_json_line(output: str) -> Mapping[str, Any]:
    try:
        value = json.loads(_last_nonempty_line(output))
    except (json.JSONDecodeError, UnicodeError) as exc:
        raise ReaderProvisionError("postgres_response_invalid") from exc
    if not isinstance(value, dict):
        raise ReaderProvisionError("postgres_response_invalid")
    return value


def _role_marker(backend: DockerPostgres) -> str | None:
    role = _sql_literal(ROLE_NAME)
    output = backend.admin(
        "postgres",
        (f"SELECT COALESCE(shobj_description(oid, 'pg_authid'), '') FROM pg_roles WHERE rolname = {role};\n"),
    )
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    return lines[-1] if lines else None


def _database_inventory(backend: DockerPostgres) -> set[str]:
    output = backend.admin(
        "postgres",
        "SELECT datname FROM pg_database WHERE datallowconn AND NOT datistemplate ORDER BY datname;\n",
    )
    return {line.strip() for line in output.splitlines() if line.strip()}


def _schema_owners(backend: DockerPostgres, *, database: str, schema: str) -> tuple[str, ...]:
    schema_literal = _sql_literal(schema)
    output = backend.admin(
        database,
        (
            "SELECT DISTINCT pg_get_userbyid(owner_oid) FROM ("
            "SELECT n.nspowner AS owner_oid FROM pg_namespace n "
            f"WHERE n.nspname = {schema_literal} "
            "UNION ALL SELECT c.relowner FROM pg_class c "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            f"WHERE n.nspname = {schema_literal}"
            ") AS owners ORDER BY 1;\n"
        ),
    )
    owners = tuple(line.strip() for line in output.splitlines() if line.strip())
    if not owners:
        raise ReaderProvisionError("postgres_schema_missing")
    for owner in owners:
        _sql_identifier(owner)
    return owners


def _cluster_role_sql(password: str) -> str:
    if not PASSWORD_RE.fullmatch(password):
        raise ReaderProvisionError("postgres_reader_password_invalid")
    role = _sql_identifier(ROLE_NAME)
    role_literal = _sql_literal(ROLE_NAME)
    password_literal = _sql_literal(password)
    marker_literal = _sql_literal(ROLE_MARKER)
    return f"""
DO $siq$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = {role_literal}) THEN
        CREATE ROLE {role} LOGIN;
    END IF;
END
$siq$;
ALTER ROLE {role} WITH LOGIN PASSWORD {password_literal}
    NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS
    CONNECTION LIMIT 16 VALID UNTIL 'infinity';
ALTER ROLE {role} SET default_transaction_read_only = on;
ALTER ROLE {role} SET statement_timeout = '30s';
ALTER ROLE {role} SET idle_in_transaction_session_timeout = '15s';
ALTER ROLE {role} SET search_path = pg_catalog;
COMMENT ON ROLE {role} IS {marker_literal};
""".lstrip()


def _database_grant_sql(*, database: str, schema: str, owners: Sequence[str]) -> str:
    database_ident = _sql_identifier(database)
    schema_ident = _sql_identifier(schema)
    role = _sql_identifier(ROLE_NAME)
    statements = [
        "BEGIN;",
        f"GRANT CONNECT ON DATABASE {database_ident} TO {role};",
        f"REVOKE CREATE ON SCHEMA {schema_ident} FROM {role};",
        f"GRANT USAGE ON SCHEMA {schema_ident} TO {role};",
        f"REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA {schema_ident} FROM {role};",
        f"GRANT SELECT ON ALL TABLES IN SCHEMA {schema_ident} TO {role};",
        f"REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA {schema_ident} FROM {role};",
        f"REVOKE ALL PRIVILEGES ON ALL FUNCTIONS IN SCHEMA {schema_ident} FROM {role};",
    ]
    for owner in owners:
        statements.append(
            "ALTER DEFAULT PRIVILEGES FOR ROLE "
            f"{_sql_identifier(owner)} IN SCHEMA {schema_ident} "
            f"GRANT SELECT ON TABLES TO {role};"
        )
    statements.extend(("COMMIT;", ""))
    return "\n".join(statements)


def _database_revoke_sql(*, database: str, schema: str, owners: Sequence[str]) -> str:
    database_ident = _sql_identifier(database)
    schema_ident = _sql_identifier(schema)
    role = _sql_identifier(ROLE_NAME)
    statements = ["BEGIN;"]
    for owner in owners:
        statements.append(
            "ALTER DEFAULT PRIVILEGES FOR ROLE "
            f"{_sql_identifier(owner)} IN SCHEMA {schema_ident} "
            f"REVOKE SELECT ON TABLES FROM {role};"
        )
    statements.extend(
        (
            f"REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA {schema_ident} FROM {role};",
            f"REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA {schema_ident} FROM {role};",
            f"REVOKE ALL PRIVILEGES ON ALL FUNCTIONS IN SCHEMA {schema_ident} FROM {role};",
            f"REVOKE USAGE, CREATE ON SCHEMA {schema_ident} FROM {role};",
            f"REVOKE CONNECT ON DATABASE {database_ident} FROM {role};",
            "COMMIT;",
            "",
        )
    )
    return "\n".join(statements)


def _verification_sql(schema: str) -> str:
    schema_literal = _sql_literal(schema)
    role_literal = _sql_literal(ROLE_NAME)
    return f"""
SELECT json_build_object(
    'database', current_database(),
    'schema', {schema_literal},
    'role', current_user,
    'transaction_read_only', current_setting('transaction_read_only'),
    'role_flags_safe', (
        SELECT NOT (rolsuper OR rolcreatedb OR rolcreaterole OR rolreplication OR rolbypassrls)
        FROM pg_roles WHERE rolname = {role_literal}
    ),
    'schema_usage', has_schema_privilege(current_user, {schema_literal}, 'USAGE'),
    'schema_create', has_schema_privilege(current_user, {schema_literal}, 'CREATE'),
    'non_select_table_privileges', (
        SELECT count(*) FROM information_schema.role_table_grants
        WHERE grantee = current_user AND table_schema = {schema_literal}
          AND privilege_type <> 'SELECT'
    ),
    'missing_select_relations', (
        SELECT count(*) FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = {schema_literal}
          AND c.relkind IN ('r', 'p', 'v', 'm', 'f')
          AND NOT has_table_privilege(current_user, c.oid, 'SELECT')
    ),
    'sequence_privileges', (
        SELECT count(*) FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = {schema_literal} AND c.relkind = 'S'
          AND (
              has_sequence_privilege(current_user, c.oid, 'USAGE') OR
              has_sequence_privilege(current_user, c.oid, 'SELECT') OR
              has_sequence_privilege(current_user, c.oid, 'UPDATE')
          )
    )
)::text;
""".lstrip()


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _ensure_project_local(path: Path) -> Path:
    candidate = path if path.is_absolute() else PROJECT_ROOT / path
    try:
        root = PROJECT_ROOT.resolve(strict=True)
        normalized = Path(os.path.abspath(candidate))
        normalized.relative_to(root)
    except (OSError, ValueError) as exc:
        raise ReaderProvisionError("state_path_outside_project") from exc
    current = root
    relative = normalized.relative_to(root)
    for part in relative.parts:
        current = current / part
        if current.exists() and current.is_symlink():
            raise ReaderProvisionError("state_path_symlink_blocked")
    return normalized


def _secure_directory(path: Path) -> None:
    path = _ensure_project_local(path)
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    current = PROJECT_ROOT.resolve(strict=True)
    for part in path.relative_to(current).parts:
        current = current / part
        if current.is_symlink() or not current.is_dir():
            raise ReaderProvisionError("state_directory_unsafe")
    os.chmod(path, 0o700)


def _atomic_private_write(path: Path, content: str) -> None:
    path = _ensure_project_local(path)
    _secure_directory(path.parent)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    except Exception:
        try:
            os.close(descriptor)
        except OSError:
            pass
        temporary.unlink(missing_ok=True)
        raise


def _read_private_file(path: Path) -> str:
    path = _ensure_project_local(path)
    try:
        info = path.lstat()
    except OSError as exc:
        raise ReaderProvisionError("private_state_missing") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise ReaderProvisionError("private_state_unsafe")
    if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600 or info.st_size > MAX_FILE_BYTES:
        raise ReaderProvisionError("private_state_unsafe")
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise ReaderProvisionError("private_state_unreadable") from exc


def _secret_content(*, password: str, host: str, host_port: int) -> str:
    if not PASSWORD_RE.fullmatch(password):
        raise ReaderProvisionError("postgres_reader_password_invalid")
    if host != DEFAULT_HOST or not 1 <= host_port <= 65_535:
        raise ReaderProvisionError("postgres_reader_endpoint_invalid")
    return (
        f"SIQ_OPENSHELL_PG_RO_HOST={host}\n"
        f"SIQ_OPENSHELL_PG_RO_PORT={host_port}\n"
        f"SIQ_OPENSHELL_PG_RO_USER={ROLE_NAME}\n"
        f"SIQ_OPENSHELL_PG_RO_PASSWORD={password}\n"
        "SIQ_OPENSHELL_PG_RO_SSLMODE=prefer\n"
    )


def _load_secret(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in _read_private_file(path).splitlines():
        if not line or line.startswith("#") or "=" not in line:
            raise ReaderProvisionError("postgres_reader_secret_invalid")
        key, value = line.split("=", 1)
        if key in values or key not in SECRET_KEYS:
            raise ReaderProvisionError("postgres_reader_secret_invalid")
        values[key] = value
    if set(values) != SECRET_KEYS:
        raise ReaderProvisionError("postgres_reader_secret_invalid")
    if values["SIQ_OPENSHELL_PG_RO_HOST"] != DEFAULT_HOST:
        raise ReaderProvisionError("postgres_reader_secret_invalid")
    if values["SIQ_OPENSHELL_PG_RO_USER"] != ROLE_NAME:
        raise ReaderProvisionError("postgres_reader_secret_invalid")
    if values["SIQ_OPENSHELL_PG_RO_SSLMODE"] != "prefer":
        raise ReaderProvisionError("postgres_reader_secret_invalid")
    try:
        port = int(values["SIQ_OPENSHELL_PG_RO_PORT"])
    except ValueError as exc:
        raise ReaderProvisionError("postgres_reader_secret_invalid") from exc
    if not 1 <= port <= 65_535 or not PASSWORD_RE.fullmatch(values["SIQ_OPENSHELL_PG_RO_PASSWORD"]):
        raise ReaderProvisionError("postgres_reader_secret_invalid")
    return values


def _load_state(path: Path) -> Mapping[str, Any]:
    try:
        value = json.loads(_read_private_file(path))
    except json.JSONDecodeError as exc:
        raise ReaderProvisionError("postgres_reader_state_invalid") from exc
    if not isinstance(value, dict) or value.get("schema_version") != SCHEMA_VERSION:
        raise ReaderProvisionError("postgres_reader_state_invalid")
    if value.get("role") != ROLE_NAME or value.get("role_marker") != ROLE_MARKER:
        raise ReaderProvisionError("postgres_reader_state_invalid")
    mappings = value.get("database_schemas")
    if not isinstance(mappings, list) or len(mappings) != len(DATABASE_SCHEMAS):
        raise ReaderProvisionError("postgres_reader_state_invalid")
    expected_pairs = list(DATABASE_SCHEMAS)
    actual_pairs: list[tuple[str, str]] = []
    for item in mappings:
        if not isinstance(item, dict) or set(item) != {"database", "schema", "owners"}:
            raise ReaderProvisionError("postgres_reader_state_invalid")
        database, schema, owners = item["database"], item["schema"], item["owners"]
        if not isinstance(database, str) or not isinstance(schema, str) or not isinstance(owners, list) or not owners:
            raise ReaderProvisionError("postgres_reader_state_invalid")
        for owner in owners:
            if not isinstance(owner, str):
                raise ReaderProvisionError("postgres_reader_state_invalid")
            _sql_identifier(owner)
        actual_pairs.append((database, schema))
    if actual_pairs != expected_pairs:
        raise ReaderProvisionError("postgres_reader_state_invalid")
    return value


@contextmanager
def _maintenance_lock() -> Iterator[None]:
    lock_path = _ensure_project_local(LOCK_PATH)
    _secure_directory(lock_path.parent)
    descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        os.fchmod(descriptor, 0o600)
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _inventory(backend: DockerPostgres) -> list[dict[str, Any]]:
    available = _database_inventory(backend)
    missing = [database for database, _schema in DATABASE_SCHEMAS if database not in available]
    if missing:
        raise ReaderProvisionError("required_postgres_database_missing")
    return [
        {
            "database": database,
            "schema": schema,
            "owners": list(_schema_owners(backend, database=database, schema=schema)),
        }
        for database, schema in DATABASE_SCHEMAS
    ]


def _verify(backend: DockerPostgres, *, password: str) -> list[dict[str, Any]]:
    if _role_marker(backend) != ROLE_MARKER:
        raise ReaderProvisionError("postgres_reader_role_not_managed")
    checks: list[dict[str, Any]] = []
    for database, schema in DATABASE_SCHEMAS:
        payload = _parse_json_line(backend.reader(database, password, _verification_sql(schema)))
        expected = {
            "database": database,
            "schema": schema,
            "role": ROLE_NAME,
            "transaction_read_only": "on",
            "role_flags_safe": True,
            "schema_usage": True,
            "schema_create": False,
            "non_select_table_privileges": 0,
            "missing_select_relations": 0,
            "sequence_privileges": 0,
        }
        if payload != expected:
            raise ReaderProvisionError("postgres_reader_security_contract_failed")
        checks.append(dict(payload))
    return checks


def _proof_payload(*, postgres_proven: bool, existing: Mapping[str, Any] | None = None) -> dict[str, Any]:
    milvus_proven = False
    if existing and existing.get("schema_version") == PROOF_SCHEMA_VERSION:
        milvus_proven = existing.get("milvus_write_protection") is True
    return {
        "schema_version": PROOF_SCHEMA_VERSION,
        "postgres_readonly_identity": postgres_proven,
        "milvus_write_protection": milvus_proven,
    }


def _read_existing_proof(path: Path) -> Mapping[str, Any] | None:
    if not path.exists():
        return None
    try:
        value = json.loads(_read_private_file(path))
    except (json.JSONDecodeError, ReaderProvisionError):
        return None
    return value if isinstance(value, dict) else None


def _write_preflight_backup(
    root: Path,
    *,
    marker: str | None,
    inventory: Sequence[Mapping[str, Any]],
) -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_path = _ensure_project_local(root) / timestamp / "preflight.json"
    suffix = 0
    while backup_path.exists():
        suffix += 1
        backup_path = _ensure_project_local(root) / f"{timestamp}-{suffix}" / "preflight.json"
    payload = {
        "schema_version": "siq.openshell.postgres-reader-preflight.v1",
        "captured_at": _utc_now(),
        "role": ROLE_NAME,
        "role_present": marker is not None,
        "role_managed": marker == ROLE_MARKER,
        "database_schemas": [
            {
                "database": item["database"],
                "schema": item["schema"],
                "owners": list(item["owners"]),
            }
            for item in inventory
        ],
    }
    _atomic_private_write(backup_path, json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True) + "\n")
    return str(backup_path.relative_to(PROJECT_ROOT.resolve(strict=True)))


def apply_reader(
    backend: DockerPostgres,
    *,
    secret_path: Path,
    state_path: Path,
    proof_path: Path,
    backup_root: Path,
    host: str,
    host_port: int,
    rotate_password: bool,
) -> dict[str, Any]:
    marker = _role_marker(backend)
    if marker is not None and marker != ROLE_MARKER:
        raise ReaderProvisionError("postgres_reader_role_collision")
    inventory = _inventory(backend)
    backup_relative = _write_preflight_backup(backup_root, marker=marker, inventory=inventory)
    secret_exists = secret_path.exists()
    existing_password: str | None = None
    if secret_exists:
        existing_password = _load_secret(secret_path)["SIQ_OPENSHELL_PG_RO_PASSWORD"]
    if marker == ROLE_MARKER and existing_password is None and not rotate_password:
        raise ReaderProvisionError("postgres_reader_secret_missing_for_managed_role")
    password = secrets.token_urlsafe(48) if rotate_password or existing_password is None else existing_password
    role_was_absent = marker is None
    try:
        backend.admin("postgres", _cluster_role_sql(password))
        for item in inventory:
            backend.admin(
                item["database"],
                _database_grant_sql(
                    database=item["database"],
                    schema=item["schema"],
                    owners=item["owners"],
                ),
            )
        checks = _verify(backend, password=password)
    except Exception:
        if role_was_absent:
            for item in reversed(inventory):
                try:
                    backend.admin(
                        item["database"],
                        _database_revoke_sql(
                            database=item["database"],
                            schema=item["schema"],
                            owners=item["owners"],
                        ),
                    )
                except Exception:
                    pass
            try:
                backend.admin("postgres", f"DROP ROLE IF EXISTS {_sql_identifier(ROLE_NAME)};\n")
            except Exception:
                pass
        elif rotate_password and existing_password is not None:
            try:
                backend.admin("postgres", _cluster_role_sql(existing_password))
            except Exception:
                pass
        raise

    _atomic_private_write(secret_path, _secret_content(password=password, host=host, host_port=host_port))
    state = {
        "schema_version": SCHEMA_VERSION,
        "role": ROLE_NAME,
        "role_marker": ROLE_MARKER,
        "applied_at": _utc_now(),
        "baseline_backup": backup_relative,
        "database_schemas": inventory,
    }
    _atomic_private_write(state_path, json.dumps(state, ensure_ascii=True, indent=2, sort_keys=True) + "\n")
    proof = _proof_payload(postgres_proven=True, existing=_read_existing_proof(proof_path))
    _atomic_private_write(proof_path, json.dumps(proof, ensure_ascii=True, indent=2, sort_keys=True) + "\n")
    return {
        "action": "apply",
        "baseline_backup_created": True,
        "role": ROLE_NAME,
        "verified_mappings": len(checks),
        "status": "ready",
    }


def verify_reader(
    backend: DockerPostgres,
    *,
    secret_path: Path,
    proof_path: Path,
) -> dict[str, Any]:
    password = _load_secret(secret_path)["SIQ_OPENSHELL_PG_RO_PASSWORD"]
    checks = _verify(backend, password=password)
    proof = _proof_payload(postgres_proven=True, existing=_read_existing_proof(proof_path))
    _atomic_private_write(proof_path, json.dumps(proof, ensure_ascii=True, indent=2, sort_keys=True) + "\n")
    return {"action": "verify", "role": ROLE_NAME, "verified_mappings": len(checks), "status": "ready"}


def rollback_reader(
    backend: DockerPostgres,
    *,
    secret_path: Path,
    state_path: Path,
    proof_path: Path,
    delete_secret: bool,
) -> dict[str, Any]:
    marker = _role_marker(backend)
    if marker is None:
        state_path.unlink(missing_ok=True)
        proof_path.unlink(missing_ok=True)
        if delete_secret:
            secret_path.unlink(missing_ok=True)
        return {"action": "rollback", "role": ROLE_NAME, "status": "already_absent"}
    if marker != ROLE_MARKER:
        raise ReaderProvisionError("postgres_reader_role_not_managed")
    if state_path.exists():
        inventory = list(_load_state(state_path)["database_schemas"])
    else:
        inventory = _inventory(backend)
    for item in reversed(inventory):
        backend.admin(
            item["database"],
            _database_revoke_sql(
                database=item["database"],
                schema=item["schema"],
                owners=item["owners"],
            ),
        )
    role = _sql_identifier(ROLE_NAME)
    backend.admin("postgres", f"ALTER ROLE {role} RESET ALL;\nDROP ROLE {role};\n")
    if _role_marker(backend) is not None:
        raise ReaderProvisionError("postgres_reader_role_drop_failed")
    state_path.unlink(missing_ok=True)
    proof_path.unlink(missing_ok=True)
    if delete_secret:
        secret_path.unlink(missing_ok=True)
    return {"action": "rollback", "role": ROLE_NAME, "status": "removed"}


def _plan() -> dict[str, Any]:
    return {
        "action": "plan",
        "role": ROLE_NAME,
        "database_schemas": [{"database": database, "schema": schema} for database, schema in DATABASE_SCHEMAS],
        "security": {
            "default_transaction_read_only": True,
            "request_database_selection": False,
            "schema_create": False,
            "table_privileges": ["SELECT"],
            "sequence_privileges": [],
        },
        "mutated": False,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", nargs="?", choices=("plan", "apply", "verify", "rollback"), default="plan")
    parser.add_argument("--container", default=DEFAULT_CONTAINER)
    parser.add_argument("--admin-user", default=DEFAULT_ADMIN_USER)
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--host-port", type=int, default=DEFAULT_HOST_PORT)
    parser.add_argument("--container-port", type=int, default=DEFAULT_CONTAINER_PORT)
    parser.add_argument("--secret-file", type=Path, default=DEFAULT_SECRET_PATH)
    parser.add_argument("--state-file", type=Path, default=DEFAULT_STATE_PATH)
    parser.add_argument("--proof-file", type=Path, default=DEFAULT_PROOF_PATH)
    parser.add_argument("--backup-root", type=Path, default=DEFAULT_BACKUP_ROOT)
    parser.add_argument("--confirm-role", default="")
    parser.add_argument("--rotate-password", action="store_true")
    parser.add_argument("--delete-secret", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.action == "plan":
        print(json.dumps(_plan(), ensure_ascii=True, sort_keys=True))
        return 0
    if args.confirm_role != ROLE_NAME:
        print("PostgreSQL reader operation refused: exact --confirm-role is required.", file=sys.stderr)
        return 2
    if args.rotate_password and args.action != "apply":
        print("PostgreSQL reader operation refused: --rotate-password is apply-only.", file=sys.stderr)
        return 2
    if args.delete_secret and args.action != "rollback":
        print("PostgreSQL reader operation refused: --delete-secret is rollback-only.", file=sys.stderr)
        return 2

    try:
        secret_path = _ensure_project_local(args.secret_file)
        state_path = _ensure_project_local(args.state_file)
        proof_path = _ensure_project_local(args.proof_file)
        backup_root = _ensure_project_local(args.backup_root)
        backend = DockerPostgres(
            container=args.container,
            admin_user=args.admin_user,
            container_port=args.container_port,
        )
        with _maintenance_lock():
            if args.action == "apply":
                result = apply_reader(
                    backend,
                    secret_path=secret_path,
                    state_path=state_path,
                    proof_path=proof_path,
                    backup_root=backup_root,
                    host=args.host,
                    host_port=args.host_port,
                    rotate_password=args.rotate_password,
                )
            elif args.action == "verify":
                result = verify_reader(backend, secret_path=secret_path, proof_path=proof_path)
            else:
                result = rollback_reader(
                    backend,
                    secret_path=secret_path,
                    state_path=state_path,
                    proof_path=proof_path,
                    delete_secret=args.delete_secret,
                )
    except ReaderProvisionError as exc:
        print(f"PostgreSQL reader operation failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
