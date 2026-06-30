"""Compatibility facade for the Hermes chat runtime."""

from __future__ import annotations

import sys

from . import agent_chat_runtime_impl as _impl


sys.modules[__name__] = _impl
