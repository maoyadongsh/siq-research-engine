#!/usr/bin/env python3
"""Exercise one real-path, NOT_PRODUCTION siq_analysis business pilot run."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import stat
import tempfile
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

FIXED_BASE_URL = "http://127.0.0.1:28651"
PROFILE = "siq_analysis"
PILOT_SCHEMA = "siq.openshell.siq_analysis_wide_pilot_output.v1"
RESULT_SCHEMA = "siq.openshell.siq_analysis_wide_pilot_contract.v1"
TOOL_MARKER = "SIQ_WIDE_PILOT_WRITE_OK"
FINAL_MARKER = "SIQ_WIDE_PILOT_OK"
PILOT_ID_RE = re.compile(r"pilot-[0-9a-f]{12}\Z")
COMPANY_RE = re.compile(r"[^/\\]{1,128}\Z")
STOCK_CODE_RE = re.compile(r"[A-Za-z0-9._-]{1,32}\Z")
TERMINAL_EVENTS = {"run.cancelled", "run.completed", "run.failed"}
CONTRACT_RECEIPT_NAME = "contract.sanitized.json"
WIDE_PILOT_RUNS_RELATIVE = Path("var/openshell/poc/siq-analysis-wide/runs")
MARKET_ROOTS = {
    "cn": Path("data/wiki/companies"),
    "eu": Path("data/wiki/eu/companies"),
    "hk": Path("data/wiki/hk/companies"),
    "jp": Path("data/wiki/jp/companies"),
    "kr": Path("data/wiki/kr/companies"),
    "us": Path("data/wiki/us/companies"),
}


class PilotContractError(RuntimeError):
    """Stable pilot failure without including business content or credentials."""


@dataclass(frozen=True)
class PilotPaths:
    company_root: Path
    source: Path
    analysis_root: Path
    work_root: Path
    output_root: Path
    output: Path


def _safe_company(company: str) -> bool:
    return bool(
        COMPANY_RE.fullmatch(company)
        and company not in {".", ".."}
        and company[0].isalnum()
        and all(character.isalnum() or character in "-_.()（）" for character in company)
    )


def _assert_no_symlink_chain(root: Path, path: Path) -> None:
    try:
        relative = path.relative_to(root)
    except ValueError as exc:
        raise PilotContractError("pilot_path_outside_project") from exc
    current = root
    for part in relative.parts:
        current /= part
        try:
            mode = current.lstat().st_mode
        except FileNotFoundError:
            break
        if stat.S_ISLNK(mode):
            raise PilotContractError("pilot_path_symlinked")


def resolve_pilot_paths(project_root: Path, *, market: str, company: str, pilot_id: str) -> PilotPaths:
    if market not in MARKET_ROOTS or not _safe_company(company) or not PILOT_ID_RE.fullmatch(pilot_id):
        raise PilotContractError("pilot_identity_invalid")
    project_root = project_root.resolve(strict=True)
    company_root = project_root / MARKET_ROOTS[market] / company
    source = company_root / "company.json"
    analysis_root = company_root / "analysis"
    work_root = analysis_root / ".work"
    output_root = work_root / pilot_id
    output = output_root / "result.json"
    for path in (company_root, source, analysis_root, work_root, output_root, output):
        _assert_no_symlink_chain(project_root, path)
    if not company_root.is_dir() or not analysis_root.is_dir() or not work_root.is_dir():
        raise PilotContractError("pilot_company_layout_invalid")
    source_info = source.lstat()
    if not stat.S_ISREG(source_info.st_mode) or source_info.st_nlink != 1:
        raise PilotContractError("pilot_source_invalid")
    try:
        output_info = output_root.lstat()
    except FileNotFoundError as exc:
        raise PilotContractError("pilot_output_root_missing") from exc
    if (
        not stat.S_ISDIR(output_info.st_mode)
        or output_info.st_uid != os.geteuid()
        or stat.S_IMODE(output_info.st_mode) != 0o700
        or any(output_root.iterdir())
    ):
        raise PilotContractError("pilot_output_conflict")
    return PilotPaths(company_root, source, analysis_root, work_root, output_root, output)


def _stable_file(path: Path, *, max_bytes: int = 1024 * 1024) -> bytes:
    expected = path.lstat()
    if expected.st_size > max_bytes:
        raise PilotContractError("pilot_file_too_large")
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_nlink != 1
            or (opened.st_dev, opened.st_ino) != (expected.st_dev, expected.st_ino)
        ):
            raise PilotContractError("pilot_source_invalid")
        chunks: list[bytes] = []
        byte_count = 0
        while chunk := os.read(descriptor, min(64 * 1024, max_bytes + 1 - byte_count)):
            chunks.append(chunk)
            byte_count += len(chunk)
            if byte_count > max_bytes:
                raise PilotContractError("pilot_file_too_large")
        finished = os.fstat(descriptor)
        if (finished.st_size, finished.st_mtime_ns, finished.st_ino) != (
            opened.st_size,
            opened.st_mtime_ns,
            opened.st_ino,
        ):
            raise PilotContractError("pilot_source_changed")
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def source_contract(path: Path) -> tuple[bytes, str, str]:
    content = _stable_file(path)
    try:
        payload = json.loads(content)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PilotContractError("pilot_source_invalid") from exc
    stock_code = payload.get("stock_code") if isinstance(payload, dict) else None
    if stock_code is None and isinstance(payload, dict):
        stock_code = payload.get("ticker")
    if not isinstance(stock_code, str) or not STOCK_CODE_RE.fullmatch(stock_code):
        raise PilotContractError("pilot_source_contract_invalid")
    return content, hashlib.sha256(content).hexdigest(), stock_code


def build_prompt(paths: PilotPaths, *, pilot_id: str) -> str:
    source_literal = repr(paths.source.as_posix())
    output_literal = repr(paths.output.as_posix())
    pilot_literal = repr(pilot_id)
    schema_literal = repr(PILOT_SCHEMA)
    code = (
        "import hashlib,json,pathlib;"
        f"s=pathlib.Path({source_literal});"
        f"t=pathlib.Path({output_literal});"
        "b=s.read_bytes();p=json.loads(b);v=p.get('stock_code');"
        "v=p.get('ticker') if v is None else v;"
        "o={'schema_version':"
        f"{schema_literal},'pilot_id':{pilot_literal},"
        "'stock_code':str(v),'source_sha256':hashlib.sha256(b).hexdigest()};"
        "t.open('x',encoding='ascii').write(json.dumps(o,ensure_ascii=True,sort_keys=True)+'\\n');"
        f"print({TOOL_MARKER!r})"
    )
    command = f"python -c {shlex.quote(code)}"
    return (
        "这是一次 NOT_PRODUCTION 的 SIQ OpenShell 单任务路径验证。\n"
        "必须且只需调用一次 terminal 工具，原样执行下面的命令：\n"
        f"{command}\n"
        f"工具成功后只用一句中文确认，并原样包含 {FINAL_MARKER}。\n"
        "不要访问网络，不要读取其他文件，不要修改或删除任何既有文件。\n"
    )


def _request_json(
    base_url: str,
    method: str,
    path: str,
    api_key: str,
    payload: dict[str, Any] | None = None,
    *,
    timeout: float = 15,
) -> tuple[int, dict[str, Any]]:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {"Authorization": f"Bearer {api_key}"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(f"{base_url}{path}", data=body, method=method, headers=headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        value = json.load(response)
        if not isinstance(value, dict):
            raise PilotContractError("pilot_api_response_invalid")
        return response.status, value


def _start_run(base_url: str, api_key: str, prompt: str) -> str:
    status, payload = _request_json(base_url, "POST", "/v1/runs", api_key, {"input": prompt})
    run_id = payload.get("run_id")
    if status != 202 or payload.get("status") != "started" or not isinstance(run_id, str):
        raise PilotContractError("pilot_run_create_failed")
    if not run_id.startswith("run_"):
        raise PilotContractError("pilot_run_id_invalid")
    return run_id


def _collect_events(base_url: str, api_key: str, run_id: str, *, timeout: float = 180) -> list[dict[str, Any]]:
    request = urllib.request.Request(
        f"{base_url}/v1/runs/{run_id}/events",
        headers={"Authorization": f"Bearer {api_key}"},
    )
    deadline = time.monotonic() + timeout
    events: list[dict[str, Any]] = []
    with urllib.request.urlopen(request, timeout=min(timeout, 30)) as response:
        if response.status != 200 or response.headers.get_content_type() != "text/event-stream":
            raise PilotContractError("pilot_sse_contract_failed")
        for raw_line in response:
            if time.monotonic() >= deadline:
                raise PilotContractError("pilot_sse_timeout")
            line = raw_line.decode("utf-8").strip()
            if not line.startswith("data: "):
                continue
            event = json.loads(line[6:])
            if not isinstance(event, dict) or event.get("run_id") != run_id:
                raise PilotContractError("pilot_sse_event_invalid")
            events.append(event)
    return events


def _wait_completed(base_url: str, api_key: str, run_id: str) -> dict[str, Any]:
    deadline = time.monotonic() + 30
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        status, last = _request_json(base_url, "GET", f"/v1/runs/{run_id}", api_key)
        if status == 200 and last.get("status") in {"completed", "failed", "cancelled"}:
            return last
        time.sleep(0.1)
    raise PilotContractError("pilot_terminal_status_timeout")


def validate_run(events: list[dict[str, Any]], result: dict[str, Any]) -> None:
    for event_type in ("message.delta", "tool.started", "tool.completed", "run.completed"):
        if not any(event.get("event") == event_type for event in events):
            raise PilotContractError("pilot_required_event_missing")
    started = [event for event in events if event.get("event") == "tool.started"]
    completed = [event for event in events if event.get("event") == "tool.completed"]
    terminals = [event for event in events if event.get("event") in TERMINAL_EVENTS]
    if (
        len(started) != 1
        or len(completed) != 1
        or started[0].get("tool") != "terminal"
        or completed[0].get("tool") != "terminal"
        or len(terminals) != 1
        or terminals[0].get("event") != "run.completed"
        or result.get("status") != "completed"
    ):
        raise PilotContractError("pilot_tool_contract_failed")
    final_output = str(result.get("output") or terminals[0].get("output") or "")
    if FINAL_MARKER not in final_output:
        raise PilotContractError("pilot_final_marker_missing")


def validate_output(path: Path, *, pilot_id: str, stock_code: str, source_sha256: str) -> None:
    content = _stable_file(path)
    try:
        payload = json.loads(content)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PilotContractError("pilot_output_invalid") from exc
    if payload != {
        "pilot_id": pilot_id,
        "schema_version": PILOT_SCHEMA,
        "source_sha256": source_sha256,
        "stock_code": stock_code,
    }:
        raise PilotContractError("pilot_output_contract_mismatch")


def remove_exact_output(
    paths: PilotPaths,
    *,
    pilot_id: str,
    stock_code: str,
    source_sha256: str,
) -> None:
    _assert_no_symlink_chain(paths.company_root, paths.output_root)
    if not paths.output_root.exists():
        raise PilotContractError("pilot_output_cleanup_unsafe")
    info = paths.output_root.lstat()
    if not stat.S_ISDIR(info.st_mode):
        raise PilotContractError("pilot_output_cleanup_unsafe")
    entries = list(os.scandir(paths.output_root))
    if not entries:
        paths.output_root.rmdir()
        return
    if len(entries) != 1 or entries[0].name != paths.output.name or entries[0].is_symlink():
        raise PilotContractError("pilot_output_cleanup_unsafe")
    output_info = entries[0].stat(follow_symlinks=False)
    if not stat.S_ISREG(output_info.st_mode) or output_info.st_nlink != 1:
        raise PilotContractError("pilot_output_cleanup_unsafe")
    validate_output(
        paths.output,
        pilot_id=pilot_id,
        stock_code=stock_code,
        source_sha256=source_sha256,
    )
    paths.output.unlink()
    paths.output_root.rmdir()


def write_contract_receipt(project_root: Path, *, pilot_id: str, result: dict[str, Any]) -> Path:
    if not PILOT_ID_RE.fullmatch(pilot_id) or result.get("schema_version") != RESULT_SCHEMA:
        raise PilotContractError("pilot_contract_receipt_invalid")
    project_root = project_root.resolve(strict=True)
    run_dir = project_root / WIDE_PILOT_RUNS_RELATIVE / pilot_id
    _assert_no_symlink_chain(project_root, run_dir)
    try:
        run_info = run_dir.lstat()
    except OSError as exc:
        raise PilotContractError("pilot_contract_receipt_run_missing") from exc
    if (
        not stat.S_ISDIR(run_info.st_mode)
        or run_info.st_uid != os.geteuid()
        or stat.S_IMODE(run_info.st_mode) != 0o700
    ):
        raise PilotContractError("pilot_contract_receipt_run_unsafe")
    target = run_dir / CONTRACT_RECEIPT_NAME
    if target.exists() or target.is_symlink():
        raise PilotContractError("pilot_contract_receipt_exists")
    content = (json.dumps(result, ensure_ascii=True, sort_keys=True) + "\n").encode("ascii")
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{target.name}.", dir=run_dir)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            os.fchmod(handle.fileno(), 0o600)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, target, follow_symlinks=False)
    except FileExistsError as exc:
        raise PilotContractError("pilot_contract_receipt_exists") from exc
    finally:
        temporary.unlink(missing_ok=True)
    return target


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=FIXED_BASE_URL)
    parser.add_argument("--api-key-file", type=Path, required=True)
    parser.add_argument("--market", choices=sorted(MARKET_ROOTS), required=True)
    parser.add_argument("--company", required=True)
    parser.add_argument("--pilot-id", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.base_url != FIXED_BASE_URL:
        raise PilotContractError("pilot_endpoint_invalid")
    if args.api_key_file.is_symlink() or not args.api_key_file.is_file():
        raise PilotContractError("pilot_api_key_file_invalid")
    api_key = args.api_key_file.read_text(encoding="ascii").strip()
    if len(api_key) != 64 or any(character not in "0123456789abcdef" for character in api_key):
        raise PilotContractError("pilot_api_key_file_invalid")

    project_root = Path(__file__).resolve().parents[2]
    paths = resolve_pilot_paths(
        project_root,
        market=args.market,
        company=args.company,
        pilot_id=args.pilot_id,
    )
    source_before, source_sha256, stock_code = source_contract(paths.source)
    run_id = ""
    events: list[dict[str, Any]] = []
    try:
        run_id = _start_run(args.base_url, api_key, build_prompt(paths, pilot_id=args.pilot_id))
        events = _collect_events(args.base_url, api_key, run_id)
        result = _wait_completed(args.base_url, api_key, run_id)
        validate_run(events, result)
        validate_output(
            paths.output,
            pilot_id=args.pilot_id,
            stock_code=stock_code,
            source_sha256=source_sha256,
        )
        if _stable_file(paths.source) != source_before:
            raise PilotContractError("pilot_source_changed")
    finally:
        remove_exact_output(
            paths,
            pilot_id=args.pilot_id,
            stock_code=stock_code,
            source_sha256=source_sha256,
        )

    result = {
        "schema_version": RESULT_SCHEMA,
        "mode": "NOT_PRODUCTION_WIDE_PILOT",
        "readiness_effect": "none",
        "profile": PROFILE,
        "run_status": "completed",
        "event_count": len(events),
        "source_read": True,
        "analysis_write": True,
        "source_unchanged": True,
        "pilot_output_removed": True,
        "run_id_present": bool(run_id),
    }
    write_contract_receipt(project_root, pilot_id=args.pilot_id, result=result)
    print(json.dumps(result, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, PilotContractError, UnicodeError, ValueError) as exc:
        code = str(exc) if isinstance(exc, PilotContractError) else "pilot_contract_os_error"
        raise SystemExit(f"siq_analysis wide pilot contract failed: {code}") from exc
