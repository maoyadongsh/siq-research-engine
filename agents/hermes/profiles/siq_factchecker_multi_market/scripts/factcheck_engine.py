#!/usr/bin/env python3
"""Compatibility command that preserves the target-only fact-check boundary."""

from factcheck_cli import main


if __name__ == "__main__":
    raise SystemExit(main())
