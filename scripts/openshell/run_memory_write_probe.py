#!/usr/bin/env python3
"""Run the host-owned, zero-residual PostgreSQL and Milvus memory probe.

The probe is deliberately outside the OpenShell sandbox. It exercises the same
FastAPI memory modules and runtime configuration as the live API, writes only
uniquely named synthetic records, and publishes owner-only receipts only after
both backends prove cleanup with a residual count of zero.
"""

from __future__ import annotations

import argparse
import asyncio
import fcntl
import hashlib
import json
import os
import re
import secrets
import stat
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence

REPO_ROOT = Path(__file__).resolve().parents[2]
API_ROOT = REPO_ROOT / "apps/api"
PROOF_ROOT_RELATIVE = Path("var/openshell/proofs")
LOCK_RELATIVE = Path("var/openshell/locks/memory-write-probe.lock")
POSTGRES_RECEIPT_RELATIVE = PROOF_ROOT_RELATIVE / "memory-postgresql-write-receipt.json"
MILVUS_RECEIPT_RELATIVE = PROOF_ROOT_RELATIVE / "memory-milvus-write-receipt.json"

POSTGRES_RECEIPT_SCHEMA = "siq.openshell.postgresql-memory-write-probe-receipt.v1"
MILVUS_RECEIPT_SCHEMA = "siq.openshell.milvus-memory-write-probe-receipt.v1"
EXECUTOR = "host_fastapi_memory_service_only"
LOGICAL_ALIAS = "siq_agent_memory_active"
REQUIRED_COLLECTION_SCHEMA = "siq_agent_memory_milvus_v2"
AGENT_GROUPS = ("primary_market", "secondary_market")
PROBE_TENANT = "__siq_openshell_memory_probe__"
PROBE_SOURCE_KIND = "openshell_memory_probe"
PHYSICAL_COLLECTION_RE = re.compile(r"siq_agent_memory__v[1-9][0-9]*\Z")
MAX_PROC_ENV_BYTES = 1024 * 1024
MAX_PROBE_SECONDS = 15 * 60

RUNTIME_ENV_NAMES = frozenset(
    {
        "DATABASE_URL",
        "SIQ_APP_DATABASE_URL",
        "SIQ_AGENT_MEMORY_EMBEDDING_DIM",
        "SIQ_AGENT_MEMORY_ENABLED",
        "SIQ_AGENT_MEMORY_MILVUS_COLLECTION",
        "SIQ_AGENT_MEMORY_MILVUS_EF",
        "SIQ_AGENT_MEMORY_MILVUS_INDEX_TYPE",
        "SIQ_AGENT_MEMORY_MILVUS_METRIC_TYPE",
        "SIQ_AGENT_MEMORY_MILVUS_TIMEOUT",
        "SIQ_AGENT_MEMORY_SCHEMA",
        "SIQ_AGENT_MEMORY_VECTOR_BACKEND",
        "SIQ_AGENT_MEMORY_WRITE_ENABLED",
        "SIQ_MILVUS_DB_NAME",
        "SIQ_MILVUS_HOST",
        "SIQ_MILVUS_PASSWORD",
        "SIQ_MILVUS_PORT",
        "SIQ_MILVUS_TOKEN",
        "SIQ_MILVUS_USER",
        "MILVUS_DB_NAME",
        "MILVUS_HOST",
        "MILVUS_PASSWORD",
        "MILVUS_PORT",
        "MILVUS_TOKEN",
        "MILVUS_USER",
    }
)


class MemoryProbeError(RuntimeError):
    """Stable failure code which never includes database or record content."""


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode("ascii")


def _probe_digest(*, nonce: bytes, backend: str, outcomes: Mapping[str, Any]) -> str:
    return hashlib.sha256(nonce + b"\0" + backend.encode("ascii") + b"\0" + _canonical(outcomes)).hexdigest()


def _read_api_environment(pid: int, *, project_root: Path) -> dict[str, str]:
    if pid <= 1:
        raise MemoryProbeError("api_pid_invalid")
    proc_root = Path("/proc") / str(pid)
    try:
        status = proc_root.stat()
        cwd = (proc_root / "cwd").resolve(strict=True)
        command = (proc_root / "cmdline").read_bytes()
        environ_path = proc_root / "environ"
        environ_info = environ_path.stat()
        raw = environ_path.read_bytes()
    except (FileNotFoundError, PermissionError, OSError) as exc:
        raise MemoryProbeError("api_process_unavailable") from exc
    if status.st_uid != os.geteuid() or environ_info.st_uid != os.geteuid():
        raise MemoryProbeError("api_process_owner_invalid")
    expected_cwd = (project_root / "apps/api").resolve(strict=True)
    if cwd != expected_cwd or b"uvicorn\0main:app\0" not in command:
        raise MemoryProbeError("api_process_identity_invalid")
    if len(raw) > MAX_PROC_ENV_BYTES:
        raise MemoryProbeError("api_process_environment_too_large")

    selected: dict[str, str] = {}
    for item in raw.split(b"\0"):
        if not item or b"=" not in item:
            continue
        key_bytes, value_bytes = item.split(b"=", 1)
        try:
            key = key_bytes.decode("ascii")
            value = value_bytes.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise MemoryProbeError("api_process_environment_invalid") from exc
        if key in RUNTIME_ENV_NAMES:
            selected[key] = value
    return selected


def _apply_runtime_environment(pid: int | None, *, project_root: Path) -> None:
    if pid is not None:
        os.environ.update(_read_api_environment(pid, project_root=project_root))
    database_url = os.getenv("SIQ_APP_DATABASE_URL") or os.getenv("DATABASE_URL") or ""
    if not database_url.startswith("postgresql"):
        raise MemoryProbeError("postgres_runtime_not_configured")
    if os.getenv("SIQ_AGENT_MEMORY_ENABLED", "auto").strip().lower() in {"0", "false", "no", "off"}:
        raise MemoryProbeError("memory_runtime_disabled")
    if os.getenv("SIQ_AGENT_MEMORY_WRITE_ENABLED", "auto").strip().lower() in {"0", "false", "no", "off"}:
        raise MemoryProbeError("memory_write_disabled")
    if os.getenv("SIQ_AGENT_MEMORY_VECTOR_BACKEND", "milvus").strip().lower() != "milvus":
        raise MemoryProbeError("milvus_runtime_not_selected")
    collection = os.getenv("SIQ_AGENT_MEMORY_MILVUS_COLLECTION", LOGICAL_ALIAS).strip() or LOGICAL_ALIAS
    if collection != LOGICAL_ALIAS:
        raise MemoryProbeError("milvus_alias_not_allowlisted")


def _check_private_parent(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    info = path.lstat()
    if (
        not stat.S_ISDIR(info.st_mode)
        or stat.S_ISLNK(info.st_mode)
        or info.st_uid != os.geteuid()
        or stat.S_IMODE(info.st_mode) & 0o077
    ):
        raise MemoryProbeError("private_state_directory_unsafe")


@contextmanager
def _probe_lock(project_root: Path) -> Iterator[None]:
    lock_path = project_root / LOCK_RELATIVE
    _check_private_parent(lock_path.parent)
    descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o600:
            raise MemoryProbeError("probe_lock_unsafe")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise MemoryProbeError("memory_probe_already_running") from exc
        yield
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def _check_existing_output(path: Path) -> None:
    if not path.exists() and not path.is_symlink():
        return
    info = path.lstat()
    if (
        stat.S_ISLNK(info.st_mode)
        or not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.geteuid()
        or info.st_nlink != 1
    ):
        raise MemoryProbeError("receipt_output_unsafe")


def _write_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    _check_private_parent(path.parent)
    _check_existing_output(path)
    content = json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True).encode("ascii") + b"\n"
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        if stat.S_IMODE(path.stat().st_mode) != 0o600:
            raise MemoryProbeError("receipt_output_mode_invalid")
    except MemoryProbeError:
        raise
    except OSError as exc:
        raise MemoryProbeError("receipt_output_failed") from exc
    finally:
        temporary.unlink(missing_ok=True)


def _postgres_outcome() -> dict[str, Any]:
    return {
        "insert": False,
        "readback": False,
        "rollback": False,
        "post_rollback_verify": False,
        "residual_count": 0,
    }


def _milvus_outcome() -> dict[str, Any]:
    return {
        "upsert": False,
        "get": False,
        "search": False,
        "delete": False,
        "post_delete_verify": False,
        "residual_count": 0,
    }


async def _postgres_probe(*, probe_token: str) -> tuple[dict[str, dict[str, Any]], int]:
    import database
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import AsyncSession

    from services import agent_memory_service

    if database.async_engine.dialect.name != "postgresql":
        raise MemoryProbeError("postgres_runtime_not_configured")
    schema = agent_memory_service._schema_name()
    table = f"{schema}.sessions"
    identifiers = {
        "primary_market": f"openshell-memory-probe-primary-{probe_token}",
        "secondary_market": f"openshell-memory-probe-secondary-{probe_token}",
    }
    outcomes = {group: _postgres_outcome() for group in AGENT_GROUPS}

    async def residual_count() -> int:
        async with database.async_engine.connect() as connection:
            result = await connection.execute(
                text(f"SELECT COUNT(*) FROM {table} WHERE tenant_id = :tenant_id AND session_id IN (:one, :two)"),
                {
                    "tenant_id": PROBE_TENANT,
                    "one": identifiers["primary_market"],
                    "two": identifiers["secondary_market"],
                },
            )
            return int(result.scalar_one())

    try:
        for group in AGENT_GROUPS:
            async with AsyncSession(database.async_engine, expire_on_commit=False) as session:
                transaction = await session.begin()
                try:
                    context = agent_memory_service.MemoryRequestContext(
                        tenant_id=PROBE_TENANT,
                        user_id=1,
                        profile=("siq_ic_master_coordinator" if group == "primary_market" else "siq_analysis"),
                        agent_group=group,
                        session_id=identifiers[group],
                        deal_id=(f"probe-deal-{probe_token}" if group == "primary_market" else None),
                        project_id=None,
                        visibility=("project_shared" if group == "primary_market" else "user_private"),
                    )
                    await agent_memory_service.record_session(
                        session,
                        context,
                        title="OpenShell synthetic memory probe",
                        metadata={"synthetic_probe": True},
                        commit=False,
                    )
                    outcomes[group]["insert"] = True
                    result = await session.execute(
                        text(f"SELECT COUNT(*) FROM {table} WHERE tenant_id = :tenant_id AND session_id = :session_id"),
                        {"tenant_id": PROBE_TENANT, "session_id": identifiers[group]},
                    )
                    outcomes[group]["readback"] = int(result.scalar_one()) == 1
                    await transaction.rollback()
                    outcomes[group]["rollback"] = True
                except Exception:
                    if transaction.is_active:
                        await transaction.rollback()
                    raise
            async with database.async_engine.connect() as verification:
                result = await verification.execute(
                    text(f"SELECT COUNT(*) FROM {table} WHERE tenant_id = :tenant_id AND session_id = :session_id"),
                    {"tenant_id": PROBE_TENANT, "session_id": identifiers[group]},
                )
                count = int(result.scalar_one())
            outcomes[group]["residual_count"] = count
            outcomes[group]["post_rollback_verify"] = count == 0
        residual = await residual_count()
        if residual != 0 or any(not all(outcomes[group][key] for key in ("insert", "readback", "rollback", "post_rollback_verify")) for group in AGENT_GROUPS):
            raise MemoryProbeError("postgres_probe_contract_failed")
        return outcomes, residual
    except Exception as exc:
        # Cleanup is limited to the reserved synthetic tenant and the two random
        # session names. It is a last resort if a driver violates rollback.
        try:
            async with database.async_engine.begin() as cleanup:
                await cleanup.execute(
                    text(f"DELETE FROM {table} WHERE tenant_id = :tenant_id AND session_id IN (:one, :two)"),
                    {
                        "tenant_id": PROBE_TENANT,
                        "one": identifiers["primary_market"],
                        "two": identifiers["secondary_market"],
                    },
                )
        except Exception as cleanup_exc:
            raise MemoryProbeError("postgres_probe_cleanup_failed") from cleanup_exc
        if isinstance(exc, MemoryProbeError):
            raise
        raise MemoryProbeError("postgres_probe_failed") from exc
    finally:
        await database.async_engine.dispose()


def _physical_collection(client: Any, alias: str) -> str:
    try:
        description = client.describe_alias(alias=alias)
    except Exception as exc:
        raise MemoryProbeError("milvus_alias_resolution_failed") from exc
    if not isinstance(description, dict):
        raise MemoryProbeError("milvus_alias_resolution_failed")
    physical = str(description.get("collection_name") or description.get("collection") or "")
    if not PHYSICAL_COLLECTION_RE.fullmatch(physical):
        raise MemoryProbeError("milvus_physical_collection_invalid")
    return physical


def _milvus_probe(*, probe_token: str) -> tuple[dict[str, dict[str, Any]], int, str]:
    from services import agent_memory_milvus

    if not agent_memory_milvus.milvus_enabled() or agent_memory_milvus.collection_name() != LOGICAL_ALIAS:
        raise MemoryProbeError("milvus_runtime_not_selected")
    client = agent_memory_milvus._client()
    preflight = agent_memory_milvus.collection_schema_preflight(client=client, name=LOGICAL_ALIAS)
    if (
        preflight.get("schema_version") != REQUIRED_COLLECTION_SCHEMA
        or preflight.get("exists") is not True
        or preflight.get("compatible") is not True
        or preflight.get("missing_fields") != []
    ):
        raise MemoryProbeError("milvus_schema_preflight_failed")
    physical = _physical_collection(client, LOGICAL_ALIAS)
    dimension = agent_memory_milvus.vector_dim()
    if dimension < 2:
        raise MemoryProbeError("milvus_vector_dimension_invalid")

    ids = {
        "primary_market": f"openshell-memory-probe-primary-{probe_token}",
        "secondary_market": f"openshell-memory-probe-secondary-{probe_token}",
    }
    outcomes = {group: _milvus_outcome() for group in AGENT_GROUPS}
    cleanup_filter = (
        f"tenant_id == {json.dumps(PROBE_TENANT)} and "
        f"source_kind == {json.dumps(PROBE_SOURCE_KIND)}"
    )

    def cleanup() -> int:
        try:
            client.delete(collection_name=LOGICAL_ALIAS, filter=cleanup_filter)
            client.flush(LOGICAL_ALIAS)
            remaining = client.query(
                collection_name=LOGICAL_ALIAS,
                filter=cleanup_filter,
                output_fields=["id"],
                limit=10,
            )
        except Exception as exc:
            raise MemoryProbeError("milvus_probe_cleanup_failed") from exc
        return len(remaining)

    # A previous SIGKILL may leave only records carrying both reserved markers.
    if cleanup() != 0:
        raise MemoryProbeError("milvus_stale_probe_cleanup_failed")
    try:
        for index, group in enumerate(AGENT_GROUPS):
            vector = [0.0] * dimension
            vector[index] = 1.0
            primary = group == "primary_market"
            record = agent_memory_milvus.AgentMemoryVectorRecord(
                id=ids[group],
                vector=vector,
                tenant_id=PROBE_TENANT,
                visibility=("project_shared" if primary else "user_private"),
                owner_user_id="1",
                profile=("siq_ic_master_coordinator" if primary else "siq_analysis"),
                agent_group=group,
                deal_id=(f"probe-deal-{probe_token}" if primary else ""),
                memory_type="synthetic_probe",
                source_kind=PROBE_SOURCE_KIND,
                source_id=ids[group],
                content_hash=hashlib.sha256(ids[group].encode("ascii")).hexdigest(),
                title="OpenShell synthetic memory probe",
                content="Synthetic memory write/read/delete verification record.",
                metadata_json="{\"synthetic_probe\":true}",
                updated_at_ts=int(time.time()),
            )
            if agent_memory_milvus.upsert_records([record], flush=True) != 1:
                raise MemoryProbeError("milvus_upsert_failed")
            outcomes[group]["upsert"] = True

            rows = client.get(collection_name=LOGICAL_ALIAS, ids=[ids[group]], output_fields=["id"])
            outcomes[group]["get"] = len(rows) == 1 and str(rows[0].get("id")) == ids[group]

            expr = agent_memory_milvus.acl_expr(
                tenant_id=PROBE_TENANT,
                user_id=1,
                deal_id=(f"probe-deal-{probe_token}" if primary else None),
                profile=record.profile,
                agent_group=group,
            )
            hits = agent_memory_milvus.search_records(vector=vector, expr=expr, limit=10)
            outcomes[group]["search"] = any(str(hit.get("id")) == ids[group] for hit in hits)

            client.delete(collection_name=LOGICAL_ALIAS, ids=[ids[group]])
            client.flush(LOGICAL_ALIAS)
            outcomes[group]["delete"] = True
            remaining = client.get(collection_name=LOGICAL_ALIAS, ids=[ids[group]], output_fields=["id"])
            outcomes[group]["residual_count"] = len(remaining)
            outcomes[group]["post_delete_verify"] = len(remaining) == 0

        if any(not all(outcomes[group][key] for key in ("upsert", "get", "search", "delete", "post_delete_verify")) for group in AGENT_GROUPS):
            raise MemoryProbeError("milvus_probe_contract_failed")
    except MemoryProbeError:
        raise
    except Exception as exc:
        raise MemoryProbeError("milvus_probe_failed") from exc
    finally:
        residual = cleanup()
    if residual != 0:
        raise MemoryProbeError("milvus_probe_residual_detected")
    return outcomes, residual, physical


def _receipts(
    *,
    started: int,
    completed: int,
    nonce: bytes,
    postgres_outcomes: Mapping[str, Any],
    postgres_residual: int,
    milvus_outcomes: Mapping[str, Any],
    milvus_residual: int,
    physical_collection: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if completed < started or completed - started > MAX_PROBE_SECONDS:
        raise MemoryProbeError("probe_window_invalid")
    postgres = {
        "schema_version": POSTGRES_RECEIPT_SCHEMA,
        "backend": "postgresql",
        "executor": EXECUTOR,
        "captured_at_unix": started,
        "completed_at_unix": completed,
        "probe_sha256": _probe_digest(nonce=nonce, backend="postgresql", outcomes=postgres_outcomes),
        "agent_groups": dict(postgres_outcomes),
        "residual_count": postgres_residual,
    }
    milvus = {
        "schema_version": MILVUS_RECEIPT_SCHEMA,
        "backend": "milvus",
        "executor": EXECUTOR,
        "logical_alias": LOGICAL_ALIAS,
        "physical_collection": physical_collection,
        "required_schema_version": REQUIRED_COLLECTION_SCHEMA,
        "schema_preflight_passed": True,
        "captured_at_unix": started,
        "completed_at_unix": completed,
        "probe_sha256": _probe_digest(nonce=nonce, backend="milvus", outcomes=milvus_outcomes),
        "agent_groups": dict(milvus_outcomes),
        "residual_count": milvus_residual,
    }
    return postgres, milvus


def run_probe(*, project_root: Path, api_pid: int | None) -> tuple[Path, Path]:
    root = project_root.expanduser().resolve(strict=True)
    _apply_runtime_environment(api_pid, project_root=root)
    if str(API_ROOT) not in sys.path:
        sys.path.insert(0, str(API_ROOT))
    with _probe_lock(root):
        started = int(time.time())
        nonce = secrets.token_bytes(32)
        probe_token = secrets.token_hex(16)
        postgres_outcomes, postgres_residual = asyncio.run(_postgres_probe(probe_token=probe_token))
        milvus_outcomes, milvus_residual, physical = _milvus_probe(probe_token=probe_token)
        completed = int(time.time())
        postgres, milvus = _receipts(
            started=started,
            completed=completed,
            nonce=nonce,
            postgres_outcomes=postgres_outcomes,
            postgres_residual=postgres_residual,
            milvus_outcomes=milvus_outcomes,
            milvus_residual=milvus_residual,
            physical_collection=physical,
        )
        postgres_path = root / POSTGRES_RECEIPT_RELATIVE
        milvus_path = root / MILVUS_RECEIPT_RELATIVE
        try:
            _write_atomic(postgres_path, postgres)
            _write_atomic(milvus_path, milvus)
        except Exception:
            postgres_path.unlink(missing_ok=True)
            milvus_path.unlink(missing_ok=True)
            raise
    return postgres_path, milvus_path


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=REPO_ROOT)
    parser.add_argument(
        "--api-pid",
        type=int,
        help="PID of the live project FastAPI process whose allowlisted runtime configuration is reused.",
    )
    return parser


def _reexec_api_interpreter() -> bool:
    """Enter the project API venv before importing its runtime dependencies."""

    expected_prefix = API_ROOT / ".venv"
    if Path(sys.prefix).resolve() == expected_prefix.resolve():
        return True
    interpreter = expected_prefix / "bin/python"
    if not interpreter.exists() or not os.access(interpreter, os.X_OK):
        return False
    try:
        os.execve(
            interpreter.as_posix(),
            [interpreter.as_posix(), Path(__file__).resolve().as_posix(), *sys.argv[1:]],
            dict(os.environ),
        )
    except OSError:
        return False
    return False


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        run_probe(project_root=args.project_root, api_pid=args.api_pid)
    except MemoryProbeError as exc:
        print(json.dumps({"ok": False, "decision": "NO_GO", "error_code": str(exc)}, sort_keys=True))
        return 1
    except Exception:
        print(json.dumps({"ok": False, "decision": "NO_GO", "error_code": "memory_write_probe_failed"}, sort_keys=True))
        return 1
    print(
        json.dumps(
            {
                "ok": True,
                "decision": "GO",
                "postgresql_receipt": POSTGRES_RECEIPT_RELATIVE.as_posix(),
                "milvus_receipt": MILVUS_RECEIPT_RELATIVE.as_posix(),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    if not _reexec_api_interpreter():
        print(
            json.dumps(
                {"ok": False, "decision": "NO_GO", "error_code": "api_python_interpreter_unavailable"},
                sort_keys=True,
            )
        )
        raise SystemExit(1)
    raise SystemExit(main())
