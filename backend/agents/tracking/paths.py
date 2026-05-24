"""Path helpers for the legacy rule-based tracking module."""

from __future__ import annotations

import os
from pathlib import Path


def wiki_root() -> Path:
    return Path(os.environ.get("WIKI_ROOT", "/home/maoyd/wiki")).expanduser()


def tracking_base_path() -> Path:
    return wiki_root() / "companies"


def company_tracking_dir(base_path: str | os.PathLike[str], stock_code: str, company_name: str) -> Path:
    return Path(base_path).expanduser() / f"{stock_code}-{company_name}" / "tracking"
