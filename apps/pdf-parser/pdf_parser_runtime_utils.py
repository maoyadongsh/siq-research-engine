"""Runtime utility helpers for the PDF parser app."""

from __future__ import annotations

import json
import os
import threading
from collections import OrderedDict
from datetime import datetime, timezone

from artifact_manager import safe_remove as artifact_safe_remove
from artifact_manager import safe_unlink as artifact_safe_unlink
from task_store import is_terminal_status


def _now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _utc_now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _parse_task_datetime(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed.replace(tzinfo=None)


def _task_elapsed_seconds(task, now_factory=None):
    start = _parse_task_datetime(task.get("started_at"))
    if start is None:
        return None
    end = None
    if is_terminal_status(task.get("status")):
        end = _parse_task_datetime(task.get("completed_at"))
    if end is None:
        end = (now_factory or _utc_now)()
    return max(0, int((end - start).total_seconds()))


def _safe_unlink(path):
    artifact_safe_unlink(path)


def _safe_remove(path):
    artifact_safe_remove(path)


def _safe_header_value(value):
    return str(value or "").replace("\\", "\\\\").replace('"', '\\"').replace("\r", "_").replace("\n", "_")


def _looks_like_pdf(path):
    try:
        with open(path, "rb") as infile:
            return infile.read(5) == b"%PDF-"
    except OSError:
        return False


class FileCache:
    def __init__(self, max_items=32):
        self.max_items = max(1, int(max_items))
        self._items = OrderedDict()
        self._lock = threading.Lock()

    def clear(self):
        with self._lock:
            self._items.clear()

    def get(self, path, loader):
        if not path or not os.path.exists(path):
            return None
        stat = os.stat(path)
        cache_key = (os.path.abspath(path), stat.st_mtime_ns, stat.st_size)
        with self._lock:
            cached = self._items.get(cache_key)
            if cached is not None:
                self._items.move_to_end(cache_key)
                return cached
        value = loader(path)
        with self._lock:
            stale_keys = [key for key in self._items if key[0] == cache_key[0] and key != cache_key]
            for key in stale_keys:
                self._items.pop(key, None)
            self._items[cache_key] = value
            self._items.move_to_end(cache_key)
            while len(self._items) > self.max_items:
                self._items.popitem(last=False)
        return value


def _read_text_cached(path, cache):
    def loader(filename):
        with open(filename, "r", encoding="utf-8") as infile:
            return infile.read()

    return cache.get(path, loader)


def _read_json_cached(path, cache):
    def loader(filename):
        with open(filename, "r", encoding="utf-8") as infile:
            return json.load(infile)

    return cache.get(path, loader)
