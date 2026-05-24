"""Runtime artifact helpers for uploads, results, and MinerU output."""

from __future__ import annotations

import os
import shutil
import time


def safe_unlink(path):
    if path and os.path.exists(path):
        try:
            os.unlink(path)
        except OSError:
            pass


def safe_remove(path):
    if not path or not os.path.exists(path):
        return
    try:
        if os.path.isdir(path):
            shutil.rmtree(path)
        else:
            os.unlink(path)
    except OSError:
        pass


def cleanup_unreferenced_children(folder, referenced_paths, cutoff_ts, remove=safe_remove):
    if not os.path.isdir(folder):
        return 0
    removed = 0
    folder_abs = os.path.abspath(folder)
    referenced = {os.path.abspath(path) for path in referenced_paths or []}
    for name in os.listdir(folder_abs):
        path_abs = os.path.abspath(os.path.join(folder_abs, name))
        if path_abs in referenced:
            continue
        try:
            if os.path.getmtime(path_abs) >= cutoff_ts:
                continue
        except OSError:
            continue
        remove(path_abs)
        removed += 1
    return removed


def cleanup_old_output_dirs(output_folder, retention_hours=24, now_ts=None, remove=safe_remove):
    if not os.path.isdir(output_folder):
        return 0
    try:
        retention = float(retention_hours)
    except (TypeError, ValueError):
        retention = 24.0
    if retention < 0:
        return 0
    now_ts = time.time() if now_ts is None else float(now_ts)
    cutoff_ts = now_ts - retention * 3600
    removed = 0
    for name in os.listdir(output_folder):
        path = os.path.join(output_folder, name)
        try:
            if os.path.getmtime(path) >= cutoff_ts:
                continue
        except OSError:
            continue
        remove(path)
        removed += 1
    return removed
