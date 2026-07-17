"""API-facing adapter for the company-scoped SIQ OpenShell runtime pool."""

from __future__ import annotations

import asyncio
import hashlib
import importlib
import os
import re
import stat
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any, Literal, Mapping

_CODE_ROOT = Path(__file__).resolve().parents[3]
_POOL_MODULE_NAMES = (
    "scripts.openshell.siq_analysis_pool_registry",
    "scripts.openshell.siq_analysis_pool_concurrency",
)
_POOL_MODULE_RELATIVES = (
    Path("scripts/openshell/siq_analysis_pool_registry.py"),
    Path("scripts/openshell/siq_analysis_pool_concurrency.py"),
)
_MODULE_LOCK = threading.Lock()
_MODULE_CACHE: tuple[ModuleType, ModuleType] | None = None
_MARKETS = {"cn", "eu", "hk", "jp", "kr", "us"}
_MARKET_ROOTS = {
    "cn": Path("data/wiki/companies"),
    "eu": Path("data/wiki/eu/companies"),
    "hk": Path("data/wiki/hk/companies"),
    "jp": Path("data/wiki/jp/companies"),
    "kr": Path("data/wiki/kr/companies"),
    "us": Path("data/wiki/us/companies"),
}
_SESSION_ID_RE = re.compile(r"[^\x00-\x1f]{1,512}\Z")


class OpenShellPoolAdapterError(RuntimeError):
    """A stable, secret-free API adapter failure."""

    def __init__(self, code: str, *, retryable: bool = False) -> None:
        if re.fullmatch(r"[a-z0-9][a-z0-9_.-]{0,95}", code) is None:
            code = "openshell_pool_adapter_internal_error"
        self.code = code
        self.retryable = retryable
        super().__init__(code)


@dataclass(frozen=True, repr=False)
class ResolvedPoolBinding:
    target: Literal["host", "openshell"]
    market: str = ""
    company: str = ""
    scope_id: str = ""
    run_id: str = ""
    base: str = ""
    api_key: str = ""
    session_namespace: str = ""
    analysis_relative_path: str = ""

    def __repr__(self) -> str:
        return (
            "ResolvedPoolBinding("
            f"target={self.target!r}, market={self.market!r}, company={self.company!r}, "
            f"scope_id={self.scope_id!r}, run_id={self.run_id!r}, base={self.base!r}, "
            f"session_namespace={self.session_namespace!r}, "
            f"analysis_relative_path={self.analysis_relative_path!r}, api_key='<redacted>')"
        )


@dataclass(frozen=True, repr=False)
class PoolAdapterAdmission:
    status: Literal["host", "active", "queued"]
    target: Literal["host", "openshell"]
    market: str = ""
    company: str = ""
    lease_id: str = ""
    owner_token: str = ""
    owner_generation: int = 0
    run_bound: bool = False
    queue_position: int = 0
    expires_at: int = 0
    base: str = ""
    api_key: str = ""
    run_id: str = ""
    session_namespace: str = ""
    write_relative_path: str = ""
    scope_id: str = ""
    analysis_relative_path: str = ""

    def __repr__(self) -> str:
        return (
            "PoolAdapterAdmission("
            f"status={self.status!r}, target={self.target!r}, market={self.market!r}, "
            f"company={self.company!r}, lease_id={self.lease_id!r}, "
            f"owner_token='<redacted>', owner_generation={self.owner_generation}, "
            f"run_bound={self.run_bound}, "
            f"queue_position={self.queue_position}, expires_at={self.expires_at}, "
            f"base={self.base!r}, run_id={self.run_id!r}, "
            f"session_namespace={self.session_namespace!r}, "
            f"write_relative_path={self.write_relative_path!r}, scope_id={self.scope_id!r}, "
            f"analysis_relative_path={self.analysis_relative_path!r}, api_key='<redacted>')"
        )


@dataclass(frozen=True, repr=False)
class PoolRecoveryTakeover:
    binding: ResolvedPoolBinding
    admission: PoolAdapterAdmission

    def __repr__(self) -> str:
        return (
            "PoolRecoveryTakeover("
            f"binding={self.binding!r}, admission={self.admission!r})"
        )


@dataclass(frozen=True)
class _LeaseIdentity:
    tenant_id: str
    user_id: str
    session_id: str


def _verified_code_root() -> Path:
    try:
        root = _CODE_ROOT.resolve(strict=True)
    except OSError as exc:
        raise OpenShellPoolAdapterError("openshell_pool_code_root_invalid") from exc
    if root != _CODE_ROOT.absolute() or root in {Path("/"), Path("/home"), Path("/tmp"), Path("/var")}:
        raise OpenShellPoolAdapterError("openshell_pool_code_root_invalid")
    return root


def _verified_module_file(root: Path, relative: Path) -> Path:
    path = root / relative
    try:
        info = path.lstat()
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise OpenShellPoolAdapterError("openshell_pool_module_invalid") from exc
    if (
        resolved != path
        or not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.geteuid()
        or info.st_nlink != 1
        or info.st_mode & 0o022
    ):
        raise OpenShellPoolAdapterError("openshell_pool_module_invalid")
    return path


def _load_pool_modules() -> tuple[ModuleType, ModuleType]:
    global _MODULE_CACHE
    if _MODULE_CACHE is not None:
        return _MODULE_CACHE
    with _MODULE_LOCK:
        if _MODULE_CACHE is not None:
            return _MODULE_CACHE
        root = _verified_code_root()
        expected = [
            _verified_module_file(root, relative)
            for relative in _POOL_MODULE_RELATIVES
        ]
        root_text = str(root)
        inserted = root_text not in sys.path
        if inserted:
            sys.path.insert(0, root_text)
        try:
            modules = tuple(importlib.import_module(name) for name in _POOL_MODULE_NAMES)
        except Exception as exc:
            raise OpenShellPoolAdapterError("openshell_pool_module_load_failed") from exc
        finally:
            if inserted:
                try:
                    sys.path.remove(root_text)
                except ValueError:
                    pass
        if any(
            module.__file__ is None or Path(module.__file__).resolve(strict=True) != path
            for module, path in zip(modules, expected, strict=True)
        ):
            raise OpenShellPoolAdapterError("openshell_pool_module_identity_mismatch")
        _MODULE_CACHE = modules  # type: ignore[assignment]
        return _MODULE_CACHE


def _mapping(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        dumped = model_dump(exclude_none=True)
        if isinstance(dumped, Mapping):
            return dumped
    return {}


def _market(value: Any) -> str:
    market = str(value or "").strip().lower()
    return market if market in _MARKETS else ""


def _one_hint(values: list[str], *, code: str) -> str:
    hints = {value for value in values if value}
    if len(hints) > 1:
        raise OpenShellPoolAdapterError(code)
    return next(iter(hints), "")


def _normalize_code(value: Any, market: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    prefix, separator, remainder = raw.partition(":")
    if separator and prefix.strip().lower() in _MARKETS:
        raw = remainder.strip()
    return raw


def _directory_scope(project_root: Path, raw_dir: str, market_hint: str) -> tuple[str, str] | None:
    if not raw_dir:
        return None
    candidate = Path(raw_dir)
    if ".." in candidate.parts or not candidate.parts:
        raise OpenShellPoolAdapterError("openshell_pool_context_directory_invalid")
    if candidate.is_absolute():
        normalized = Path(os.path.normpath(os.fspath(candidate)))
        try:
            relative = normalized.relative_to(project_root / "data/wiki")
        except ValueError as exc:
            raise OpenShellPoolAdapterError("openshell_pool_context_directory_invalid") from exc
    else:
        relative = candidate
        if relative.parts[:2] == ("data", "wiki"):
            relative = Path(*relative.parts[2:])
    parts = relative.parts
    if len(parts) == 1:
        if not market_hint:
            raise OpenShellPoolAdapterError("openshell_pool_context_market_required")
        return market_hint, parts[0]
    if len(parts) == 2 and parts[0] == "companies":
        return "cn", parts[1]
    if len(parts) == 3 and parts[0] in _MARKETS - {"cn"} and parts[1] == "companies":
        return parts[0], parts[2]
    raise OpenShellPoolAdapterError("openshell_pool_context_directory_invalid")


def _context_scope(
    context: Any,
    *,
    registry: Mapping[str, Any],
    project_root: Path,
) -> tuple[str, str] | None:
    raw = _mapping(context)
    company = _mapping(raw.get("company"))
    identity = _mapping(raw.get("research_identity"))
    market_hint = _one_hint(
        [
            _market(company.get("market")),
            _market(identity.get("market")),
            _market(raw.get("market")),
        ],
        code="openshell_pool_context_market_conflict",
    )
    raw_dir = str(company.get("dir") or "").strip()
    directory_scope = _directory_scope(project_root, raw_dir, market_hint)
    code_hint = _one_hint(
        [
            _normalize_code(company.get("code"), market_hint),
            _normalize_code(company.get("company_id"), market_hint),
            _normalize_code(identity.get("company_id"), market_hint),
        ],
        code="openshell_pool_context_company_conflict",
    )
    name_hint = str(company.get("name") or "").strip()
    if directory_scope is not None:
        market, canonical_company = directory_scope
        if market_hint and market != market_hint:
            raise OpenShellPoolAdapterError("openshell_pool_context_market_conflict")
        expected_code, separator, expected_name = canonical_company.partition("-")
        if code_hint and code_hint not in {expected_code, canonical_company}:
            raise OpenShellPoolAdapterError("openshell_pool_context_company_conflict")
        if name_hint and separator and name_hint != expected_name:
            raise OpenShellPoolAdapterError("openshell_pool_context_company_conflict")
        return market, canonical_company

    if not market_hint or (not code_hint and not name_hint):
        return None
    matches: list[tuple[str, str]] = []
    for entry in registry.get("bindings", []):
        if not isinstance(entry, Mapping) or entry.get("market") != market_hint:
            continue
        canonical_company = str(entry.get("company") or "")
        expected_code, separator, expected_name = canonical_company.partition("-")
        code_matches = not code_hint or code_hint in {expected_code, canonical_company}
        name_matches = not name_hint or (separator and name_hint == expected_name)
        if code_matches and name_matches:
            matches.append((market_hint, canonical_company))
    if len(matches) > 1:
        raise OpenShellPoolAdapterError("openshell_pool_context_ambiguous")
    return matches[0] if matches else (market_hint, code_hint if code_hint else name_hint)


class OpenShellPoolAdapter:
    """Resolve immutable bindings and manage durable, user-scoped pool leases."""

    def __init__(self, *, project_root: Path = _CODE_ROOT) -> None:
        try:
            root = project_root.resolve(strict=True)
        except OSError as exc:
            raise OpenShellPoolAdapterError("openshell_pool_project_root_invalid") from exc
        if root != project_root.absolute():
            raise OpenShellPoolAdapterError("openshell_pool_project_root_invalid")
        self.project_root = root
        self.registry, self.concurrency = _load_pool_modules()

    def _translate(self, exc: Exception) -> OpenShellPoolAdapterError:
        for module, class_name in (
            (self.registry, "PoolRegistryError"),
            (self.concurrency, "PoolConcurrencyError"),
        ):
            error_class = getattr(module, class_name, None)
            if isinstance(error_class, type) and isinstance(exc, error_class):
                code = str(getattr(exc, "code", "openshell_pool_adapter_internal_error"))
                retryable = bool(getattr(exc, "retryable", False))
                return OpenShellPoolAdapterError(code, retryable=retryable)
        return OpenShellPoolAdapterError("openshell_pool_adapter_internal_error")

    def resolve_binding(self, context: Any) -> ResolvedPoolBinding:
        try:
            registry = self.registry.load_registry(project_root=self.project_root)
            scope = _context_scope(context, registry=registry, project_root=self.project_root)
            if scope is None:
                return ResolvedPoolBinding(target="host")
            market, company = scope
            entries = [
                entry
                for entry in registry["bindings"]
                if entry["market"] == market and entry["company"] == company
            ]
            if len(entries) > 1:
                raise OpenShellPoolAdapterError("openshell_pool_context_ambiguous")
            if not entries:
                analysis = self.project_root / _MARKET_ROOTS[market] / company / "analysis"
                try:
                    company_info = analysis.parent.lstat()
                    analysis_info = analysis.lstat()
                except OSError:
                    return ResolvedPoolBinding(target="host")
                if (
                    not stat.S_ISDIR(company_info.st_mode)
                    or not stat.S_ISDIR(analysis_info.st_mode)
                    or analysis.parent.is_symlink()
                    or analysis.is_symlink()
                ):
                    return ResolvedPoolBinding(target="host")
                return ResolvedPoolBinding(target="host", market=market, company=company)
            entry = entries[0]
            route = self.registry.resolve(
                market=market,
                company=company,
                project_root=self.project_root,
            )
            if route.target != "openshell" or route.run_id != entry["run_id"]:
                raise OpenShellPoolAdapterError("openshell_pool_binding_changed", retryable=True)
            return ResolvedPoolBinding(
                target="openshell",
                market=market,
                company=company,
                scope_id=str(entry["scope_id"]),
                run_id=route.run_id,
                base=route.base,
                api_key=route.api_key,
                session_namespace=route.session_namespace,
                analysis_relative_path=route.analysis_relative_path,
            )
        except OpenShellPoolAdapterError:
            raise
        except Exception as exc:
            raise self._translate(exc) from None

    @staticmethod
    def _identity(
        *,
        session_id: str,
        tenant_id: str | None,
        user_id: str | None,
    ) -> _LeaseIdentity:
        session = str(session_id or "").strip()
        if _SESSION_ID_RE.fullmatch(session) is None:
            raise OpenShellPoolAdapterError("openshell_pool_session_id_invalid")
        tenant = str(tenant_id or "siq").strip()
        if not tenant:
            tenant = "siq"
        user = str(user_id or "").strip()
        if not user:
            user = "session-derived-" + hashlib.sha256(session.encode("utf-8")).hexdigest()[:32]
        return _LeaseIdentity(tenant_id=tenant, user_id=user, session_id=session)

    @staticmethod
    def _admission(value: Any) -> PoolAdapterAdmission:
        status = str(value.status)
        target: Literal["host", "openshell"] = "host" if status == "host" else "openshell"
        return PoolAdapterAdmission(
            status=status,
            target=target,
            market=str(value.market),
            company=str(value.company),
            lease_id=str(value.lease_id),
            owner_token=str(value.owner_token),
            owner_generation=int(value.owner_generation),
            run_bound=bool(value.run_bound),
            queue_position=int(value.queue_position),
            expires_at=int(value.expires_at),
            base=str(value.base),
            api_key=str(value.api_key),
            run_id=str(value.run_id),
            session_namespace=str(value.session_namespace),
            write_relative_path=str(value.write_relative_path),
            scope_id=str(value.scope_id),
            analysis_relative_path=str(value.analysis_relative_path),
        )

    def takeover_recovery(
        self,
        *,
        session_id: str,
        recovery_lock_fd: int,
        tenant_id: str | None = None,
        user_id: str | None = None,
        expected_lease_id: str | None = None,
        expected_scope_id: str | None = None,
        expected_run_id: str | None = None,
        expected_owner_generation: int | None = None,
        now: int | None = None,
    ) -> PoolRecoveryTakeover:
        """Take over one exact pre-restart lease without knowing its old token."""

        identity = self._identity(session_id=session_id, tenant_id=tenant_id, user_id=user_id)
        try:
            raw = self.concurrency.takeover_recovery(
                tenant_id=identity.tenant_id,
                user_id=identity.user_id,
                session_id=identity.session_id,
                expected_lease_id=expected_lease_id,
                expected_scope_id=expected_scope_id,
                expected_run_id=expected_run_id,
                expected_owner_generation=expected_owner_generation,
                recovery_lock_fd=recovery_lock_fd,
                now=now,
                project_root=self.project_root,
            )
            admission = self._admission(raw)
            if (
                admission.target != "openshell"
                or admission.status not in {"active", "queued"}
                or not admission.scope_id
                or not admission.run_id
                or not admission.analysis_relative_path
                or not admission.owner_token
                or admission.owner_generation < 1
            ):
                raise OpenShellPoolAdapterError("openshell_pool_recovery_admission_invalid")
            binding = ResolvedPoolBinding(
                target="openshell",
                market=admission.market,
                company=admission.company,
                scope_id=admission.scope_id,
                run_id=admission.run_id,
                base=admission.base,
                api_key=admission.api_key,
                session_namespace=admission.session_namespace,
                analysis_relative_path=admission.analysis_relative_path,
            )
            return PoolRecoveryTakeover(binding=binding, admission=admission)
        except OpenShellPoolAdapterError:
            raise
        except Exception as exc:
            raise self._translate(exc) from None

    def acquire(
        self,
        binding: ResolvedPoolBinding,
        *,
        session_id: str,
        tenant_id: str | None = None,
        user_id: str | None = None,
        now: int | None = None,
    ) -> PoolAdapterAdmission:
        identity = self._identity(session_id=session_id, tenant_id=tenant_id, user_id=user_id)
        if binding.target == "host":
            if not binding.market or not binding.company:
                return PoolAdapterAdmission(status="host", target="host")
        try:
            value = self.concurrency.acquire(
                market=binding.market,
                company=binding.company,
                tenant_id=identity.tenant_id,
                user_id=identity.user_id,
                session_id=identity.session_id,
                expected_run_id=binding.run_id or None,
                now=now,
                project_root=self.project_root,
            )
            admission = self._admission(value)
            if binding.target == "openshell" and (
                admission.target != "openshell" or admission.run_id != binding.run_id
            ):
                raise OpenShellPoolAdapterError("openshell_pool_binding_changed", retryable=True)
            return admission
        except OpenShellPoolAdapterError:
            raise
        except Exception as exc:
            raise self._translate(exc) from None

    def heartbeat(
        self,
        binding: ResolvedPoolBinding,
        *,
        session_id: str,
        tenant_id: str | None = None,
        user_id: str | None = None,
        owner_token: str,
        owner_generation: int,
        now: int | None = None,
    ) -> PoolAdapterAdmission:
        if binding.target != "openshell":
            return PoolAdapterAdmission(status="host", target="host")
        identity = self._identity(session_id=session_id, tenant_id=tenant_id, user_id=user_id)
        try:
            value = self.concurrency.heartbeat(
                market=binding.market,
                company=binding.company,
                tenant_id=identity.tenant_id,
                user_id=identity.user_id,
                session_id=identity.session_id,
                owner_token=owner_token,
                owner_generation=owner_generation,
                expected_run_id=binding.run_id,
                now=now,
                project_root=self.project_root,
            )
            return self._admission(value)
        except Exception as exc:
            raise self._translate(exc) from None

    def mark_run_bound(
        self,
        binding: ResolvedPoolBinding,
        *,
        session_id: str,
        owner_token: str,
        owner_generation: int,
        tenant_id: str | None = None,
        user_id: str | None = None,
        now: int | None = None,
    ) -> PoolAdapterAdmission:
        if binding.target != "openshell":
            return PoolAdapterAdmission(status="host", target="host")
        identity = self._identity(session_id=session_id, tenant_id=tenant_id, user_id=user_id)
        try:
            value = self.concurrency.mark_run_bound(
                market=binding.market,
                company=binding.company,
                tenant_id=identity.tenant_id,
                user_id=identity.user_id,
                session_id=identity.session_id,
                owner_token=owner_token,
                owner_generation=owner_generation,
                expected_run_id=binding.run_id,
                now=now,
                project_root=self.project_root,
            )
            return self._admission(value)
        except Exception as exc:
            raise self._translate(exc) from None

    def release(
        self,
        *,
        session_id: str,
        tenant_id: str | None = None,
        user_id: str | None = None,
        owner_token: str = "",
        owner_generation: int = 0,
        terminal_confirmed: bool = False,
        now: int | None = None,
    ) -> bool:
        identity = self._identity(session_id=session_id, tenant_id=tenant_id, user_id=user_id)
        try:
            result = self.concurrency.release(
                tenant_id=identity.tenant_id,
                user_id=identity.user_id,
                session_id=identity.session_id,
                owner_token=owner_token,
                owner_generation=owner_generation,
                terminal_confirmed=terminal_confirmed,
                now=now,
                project_root=self.project_root,
            )
            return result.get("released") is True
        except Exception as exc:
            raise self._translate(exc) from None

    def abandon(
        self,
        *,
        session_id: str,
        owner_token: str,
        owner_generation: int,
        tenant_id: str | None = None,
        user_id: str | None = None,
        now: int | None = None,
    ) -> Literal["missing", "removed", "orphaned"]:
        identity = self._identity(session_id=session_id, tenant_id=tenant_id, user_id=user_id)
        try:
            result = self.concurrency.abandon(
                tenant_id=identity.tenant_id,
                user_id=identity.user_id,
                session_id=identity.session_id,
                owner_token=owner_token,
                owner_generation=owner_generation,
                now=now,
                project_root=self.project_root,
            )
            state = str(result.get("state") or "missing")
            if state not in {"missing", "removed", "orphaned"}:
                raise OpenShellPoolAdapterError("openshell_pool_abandon_state_invalid")
            return state  # type: ignore[return-value]
        except OpenShellPoolAdapterError:
            raise
        except Exception as exc:
            raise self._translate(exc) from None

    def wait(
        self,
        binding: ResolvedPoolBinding,
        *,
        session_id: str,
        tenant_id: str | None = None,
        user_id: str | None = None,
        owner_token: str,
        owner_generation: int,
        timeout_seconds: float = 30.0,
        poll_interval: float = 0.25,
    ) -> PoolAdapterAdmission:
        if timeout_seconds <= 0 or not 0.01 <= poll_interval <= min(timeout_seconds, 5.0):
            raise OpenShellPoolAdapterError("openshell_pool_wait_parameters_invalid")
        deadline = time.monotonic() + timeout_seconds
        while True:
            admission = self.heartbeat(
                binding,
                session_id=session_id,
                tenant_id=tenant_id,
                user_id=user_id,
                owner_token=owner_token,
                owner_generation=owner_generation,
            )
            if admission.status != "queued":
                return admission
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise OpenShellPoolAdapterError("openshell_pool_wait_timeout", retryable=True)
            time.sleep(min(poll_interval, remaining))

    def acquire_wait(
        self,
        binding: ResolvedPoolBinding,
        *,
        session_id: str,
        tenant_id: str | None = None,
        user_id: str | None = None,
        timeout_seconds: float = 30.0,
        poll_interval: float = 0.25,
    ) -> PoolAdapterAdmission:
        admission = self.acquire(
            binding,
            session_id=session_id,
            tenant_id=tenant_id,
            user_id=user_id,
        )
        if admission.status != "queued":
            return admission
        try:
            return self.wait(
                binding,
                session_id=session_id,
                tenant_id=tenant_id,
                user_id=user_id,
                owner_token=admission.owner_token,
                owner_generation=admission.owner_generation,
                timeout_seconds=timeout_seconds,
                poll_interval=poll_interval,
            )
        except BaseException:
            try:
                self.abandon(
                    session_id=session_id,
                    tenant_id=tenant_id,
                    user_id=user_id,
                    owner_token=admission.owner_token,
                    owner_generation=admission.owner_generation,
                )
            except BaseException:
                pass
            raise

    async def acquire_async(self, *args: Any, **kwargs: Any) -> PoolAdapterAdmission:
        return await asyncio.to_thread(self.acquire, *args, **kwargs)

    async def takeover_recovery_async(self, **kwargs: Any) -> PoolRecoveryTakeover:
        return await asyncio.to_thread(self.takeover_recovery, **kwargs)

    async def heartbeat_async(self, *args: Any, **kwargs: Any) -> PoolAdapterAdmission:
        return await asyncio.to_thread(self.heartbeat, *args, **kwargs)

    async def mark_run_bound_async(self, *args: Any, **kwargs: Any) -> PoolAdapterAdmission:
        return await asyncio.to_thread(self.mark_run_bound, *args, **kwargs)

    async def release_async(self, **kwargs: Any) -> bool:
        return await asyncio.to_thread(self.release, **kwargs)

    async def abandon_async(self, **kwargs: Any) -> Literal["missing", "removed", "orphaned"]:
        return await asyncio.to_thread(self.abandon, **kwargs)

    async def wait_async(
        self,
        binding: ResolvedPoolBinding,
        *,
        session_id: str,
        tenant_id: str | None = None,
        user_id: str | None = None,
        owner_token: str,
        owner_generation: int,
        timeout_seconds: float = 30.0,
        poll_interval: float = 0.25,
    ) -> PoolAdapterAdmission:
        if timeout_seconds <= 0 or not 0.01 <= poll_interval <= min(timeout_seconds, 5.0):
            raise OpenShellPoolAdapterError("openshell_pool_wait_parameters_invalid")
        deadline = asyncio.get_running_loop().time() + timeout_seconds
        while True:
            admission = await self.heartbeat_async(
                binding,
                session_id=session_id,
                tenant_id=tenant_id,
                user_id=user_id,
                owner_token=owner_token,
                owner_generation=owner_generation,
            )
            if admission.status != "queued":
                return admission
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise OpenShellPoolAdapterError("openshell_pool_wait_timeout", retryable=True)
            await asyncio.sleep(min(poll_interval, remaining))

    async def acquire_wait_async(
        self,
        binding: ResolvedPoolBinding,
        *,
        session_id: str,
        tenant_id: str | None = None,
        user_id: str | None = None,
        timeout_seconds: float = 30.0,
        poll_interval: float = 0.25,
    ) -> PoolAdapterAdmission:
        acquire_task = asyncio.create_task(
            self.acquire_async(
                binding,
                session_id=session_id,
                tenant_id=tenant_id,
                user_id=user_id,
            )
        )
        try:
            admission = await asyncio.shield(acquire_task)
            if admission.status != "queued":
                return admission
            return await self.wait_async(
                binding,
                session_id=session_id,
                tenant_id=tenant_id,
                user_id=user_id,
                owner_token=admission.owner_token,
                owner_generation=admission.owner_generation,
                timeout_seconds=timeout_seconds,
                poll_interval=poll_interval,
            )
        except BaseException:
            if not acquire_task.done():
                try:
                    await asyncio.shield(acquire_task)
                except BaseException:
                    pass
            if acquire_task.done() and not acquire_task.cancelled():
                try:
                    admission = acquire_task.result()
                except Exception:
                    admission = None
                if admission is not None and admission.target == "openshell":
                    try:
                        await asyncio.shield(
                            self.abandon_async(
                                session_id=session_id,
                                tenant_id=tenant_id,
                                user_id=user_id,
                                owner_token=admission.owner_token,
                                owner_generation=admission.owner_generation,
                            )
                        )
                    except BaseException:
                        pass
            raise


_DEFAULT_ADAPTER: OpenShellPoolAdapter | None = None
_DEFAULT_ADAPTER_LOCK = threading.Lock()


def default_adapter() -> OpenShellPoolAdapter:
    global _DEFAULT_ADAPTER
    if _DEFAULT_ADAPTER is None:
        with _DEFAULT_ADAPTER_LOCK:
            if _DEFAULT_ADAPTER is None:
                _DEFAULT_ADAPTER = OpenShellPoolAdapter()
    return _DEFAULT_ADAPTER


def resolve_binding(context: Any) -> ResolvedPoolBinding:
    return default_adapter().resolve_binding(context)


def acquire(
    binding: ResolvedPoolBinding,
    *,
    session_id: str,
    tenant_id: str | None = None,
    user_id: str | None = None,
) -> PoolAdapterAdmission:
    return default_adapter().acquire(
        binding,
        session_id=session_id,
        tenant_id=tenant_id,
        user_id=user_id,
    )


def heartbeat(
    binding: ResolvedPoolBinding,
    *,
    session_id: str,
    tenant_id: str | None = None,
    user_id: str | None = None,
    owner_token: str,
    owner_generation: int,
) -> PoolAdapterAdmission:
    return default_adapter().heartbeat(
        binding,
        session_id=session_id,
        tenant_id=tenant_id,
        user_id=user_id,
        owner_token=owner_token,
        owner_generation=owner_generation,
    )


def mark_run_bound(
    binding: ResolvedPoolBinding,
    *,
    session_id: str,
    owner_token: str,
    owner_generation: int,
    tenant_id: str | None = None,
    user_id: str | None = None,
) -> PoolAdapterAdmission:
    return default_adapter().mark_run_bound(
        binding,
        session_id=session_id,
        owner_token=owner_token,
        owner_generation=owner_generation,
        tenant_id=tenant_id,
        user_id=user_id,
    )


def release(
    *,
    session_id: str,
    tenant_id: str | None = None,
    user_id: str | None = None,
    owner_token: str = "",
    owner_generation: int = 0,
    terminal_confirmed: bool = False,
) -> bool:
    return default_adapter().release(
        session_id=session_id,
        tenant_id=tenant_id,
        user_id=user_id,
        owner_token=owner_token,
        owner_generation=owner_generation,
        terminal_confirmed=terminal_confirmed,
    )


def abandon(
    *,
    session_id: str,
    owner_token: str,
    owner_generation: int,
    tenant_id: str | None = None,
    user_id: str | None = None,
) -> Literal["missing", "removed", "orphaned"]:
    return default_adapter().abandon(
        session_id=session_id,
        owner_token=owner_token,
        owner_generation=owner_generation,
        tenant_id=tenant_id,
        user_id=user_id,
    )


def wait(
    binding: ResolvedPoolBinding,
    *,
    session_id: str,
    tenant_id: str | None = None,
    user_id: str | None = None,
    owner_token: str,
    owner_generation: int,
    timeout_seconds: float = 30.0,
    poll_interval: float = 0.25,
) -> PoolAdapterAdmission:
    return default_adapter().wait(
        binding,
        session_id=session_id,
        tenant_id=tenant_id,
        user_id=user_id,
        owner_token=owner_token,
        owner_generation=owner_generation,
        timeout_seconds=timeout_seconds,
        poll_interval=poll_interval,
    )


def acquire_wait(
    binding: ResolvedPoolBinding,
    *,
    session_id: str,
    tenant_id: str | None = None,
    user_id: str | None = None,
    timeout_seconds: float = 30.0,
    poll_interval: float = 0.25,
) -> PoolAdapterAdmission:
    return default_adapter().acquire_wait(
        binding,
        session_id=session_id,
        tenant_id=tenant_id,
        user_id=user_id,
        timeout_seconds=timeout_seconds,
        poll_interval=poll_interval,
    )


async def acquire_async(*args: Any, **kwargs: Any) -> PoolAdapterAdmission:
    return await default_adapter().acquire_async(*args, **kwargs)


async def heartbeat_async(*args: Any, **kwargs: Any) -> PoolAdapterAdmission:
    return await default_adapter().heartbeat_async(*args, **kwargs)


async def mark_run_bound_async(*args: Any, **kwargs: Any) -> PoolAdapterAdmission:
    return await default_adapter().mark_run_bound_async(*args, **kwargs)


async def release_async(**kwargs: Any) -> bool:
    return await default_adapter().release_async(**kwargs)


async def abandon_async(**kwargs: Any) -> Literal["missing", "removed", "orphaned"]:
    return await default_adapter().abandon_async(**kwargs)


async def wait_async(*args: Any, **kwargs: Any) -> PoolAdapterAdmission:
    return await default_adapter().wait_async(*args, **kwargs)


async def acquire_wait_async(*args: Any, **kwargs: Any) -> PoolAdapterAdmission:
    return await default_adapter().acquire_wait_async(*args, **kwargs)
