from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Sequence


def format_command(args: Sequence[str]) -> str:
    redacted: list[str] = []
    hide_next = False
    for arg in args:
        if hide_next:
            redacted.append("***")
            hide_next = False
            continue
        redacted.append(arg)
        if arg == "--database-url":
            hide_next = True
    return " ".join(redacted)


def run_command(
    args: Sequence[str],
    *,
    cwd: str | Path | None = None,
    timeout: int | float | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(args),
        cwd=str(cwd) if cwd is not None else None,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
