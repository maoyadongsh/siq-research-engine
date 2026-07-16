from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

SOURCE = Path(__file__).resolve().parents[1] / "check_health_flag.py"


def _module():
    spec = importlib.util.spec_from_file_location("siq_check_health_flag_under_test", SOURCE)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({"queue_worker_ready": True, "mineru": False, "submit_ready": False}, True),
        ({"queue_worker_ready": False, "mineru": True}, False),
        ({"queue_worker_ready": 1}, False),
        ({"queue_worker_ready": "true"}, False),
        ({"nested": {"queue_worker_ready": True}}, False),
        ([{"queue_worker_ready": True}], False),
    ],
)
def test_health_flag_requires_exact_top_level_boolean(payload, expected: bool) -> None:
    module = _module()

    assert module.health_flag_is_true(payload, "queue_worker_ready") is expected


@pytest.mark.parametrize(
    ("body", "expected_code"),
    [
        (b'{"worker_ready": true, "pdf_parser_ready": false}', 0),
        (b'{"worker_ready": false, "pdf_parser_ready": true}', 1),
        (b'{"worker_ready":', 1),
        (b"[]", 1),
    ],
)
def test_cli_health_behavior_matrix(body: bytes, expected_code: int) -> None:
    result = subprocess.run(
        [sys.executable, str(SOURCE), "worker_ready"],
        input=body,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert result.returncode == expected_code
    assert result.stdout == b""


def test_cli_rejects_oversized_or_invalid_field() -> None:
    oversized = json.dumps({"worker_ready": True, "padding": "x" * (1024 * 1024)}).encode()
    too_large = subprocess.run(
        [sys.executable, str(SOURCE), "worker_ready"],
        input=oversized,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    invalid_field = subprocess.run(
        [sys.executable, str(SOURCE), "../../secret"],
        input=b'{"../../secret": true}',
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert too_large.returncode == 1
    assert invalid_field.returncode == 2
