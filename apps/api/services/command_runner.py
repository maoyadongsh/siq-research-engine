from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Mapping, Sequence


_SENSITIVE_OPTION_NAMES = {
    "api-key",
    "connection-string",
    "database-url",
    "database-uri",
    "db-url",
    "dsn",
    "password",
    "pgpassword",
    "postgres-url",
    "secret",
    "token",
}
_SENSITIVE_ENV_NAMES = {"DATABASE_URL", "PGPASSWORD", "POSTGRES_PASSWORD"}


def _normalized_option_name(value: str) -> str:
    return value.lstrip("-").replace("_", "-").lower()


def _is_sensitive_option(value: str) -> bool:
    return value.startswith("--") and _normalized_option_name(value) in _SENSITIVE_OPTION_NAMES


def _redacted_assignment(value: str) -> str | None:
    if "=" not in value:
        return None
    name, _raw_value = value.split("=", 1)
    if _is_sensitive_option(name):
        return f"{name}=***"
    if name.upper() in _SENSITIVE_ENV_NAMES:
        return f"{name}=***"
    return None


def format_command(args: Sequence[str]) -> str:
    redacted: list[str] = []
    hide_next = False
    for arg in args:
        if hide_next:
            redacted.append("***")
            hide_next = False
            continue
        assignment = _redacted_assignment(arg)
        if assignment is not None:
            redacted.append(assignment)
            continue
        redacted.append(arg)
        if _is_sensitive_option(arg):
            hide_next = True
    return " ".join(redacted)


def run_command(
    args: Sequence[str],
    *,
    cwd: str | Path | None = None,
    timeout: int | float | None = None,
    env: Mapping[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(args),
        cwd=str(cwd) if cwd is not None else None,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=dict(env) if env is not None else None,
        check=False,
    )
