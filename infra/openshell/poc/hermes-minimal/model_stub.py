#!/usr/bin/env python3
"""Deterministic loopback-only OpenAI chat-completions stub for the SIQ PoC."""

from __future__ import annotations

import argparse
import json
import time
import uuid
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

MODEL = "siq-poc-model"
MAX_REQUEST_BYTES = 2 * 1024 * 1024
TOOL_MARKER = "SIQ_POC_TOOL_EXECUTED"
ALLOWED_TOOL_NAMES = {
    "execute_code",
    "patch",
    "process",
    "read_file",
    "search_files",
    "terminal",
    "write_file",
}


def _chunk(completion_id: str, delta: dict[str, Any], finish_reason: str | None = None) -> dict[str, Any]:
    return {
        "id": completion_id,
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": MODEL,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
    }


def _visible_text(messages: list[Any]) -> str:
    parts: list[str] = []
    for message in messages:
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        if isinstance(content, str):
            parts.append(content)
        elif isinstance(content, list):
            for item in content:
                if isinstance(item, dict) and isinstance(item.get("text"), str):
                    parts.append(item["text"])
    return "\n".join(parts)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, _format: str, *_args: Any) -> None:
        return

    def _json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/health":
            self._json(HTTPStatus.OK, {"status": "ok"})
            return
        if self.path == "/v1/models":
            self._json(
                HTTPStatus.OK,
                {"object": "list", "data": [{"id": MODEL, "object": "model", "owned_by": "siq"}]},
            )
            return
        self._json(HTTPStatus.NOT_FOUND, {"error": {"message": "not found"}})

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/v1/chat/completions":
            self._json(HTTPStatus.NOT_FOUND, {"error": {"message": "not found"}})
            return

        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = -1
        if length < 0 or length > MAX_REQUEST_BYTES:
            self._json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": {"message": "request too large"}})
            return
        try:
            request = json.loads(self.rfile.read(length) or b"{}")
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._json(HTTPStatus.BAD_REQUEST, {"error": {"message": "invalid JSON"}})
            return

        if not isinstance(request, dict):
            self._json(HTTPStatus.BAD_REQUEST, {"error": {"message": "request must be an object"}})
            return
        if (
            request.get("model") != MODEL
            or request.get("stream") is not True
            or request.get("stream_options") != {"include_usage": True}
            or self.headers.get("Authorization") != "Bearer no-key-required"
        ):
            self._json(HTTPStatus.BAD_REQUEST, {"error": {"message": "provider request contract mismatch"}})
            return

        messages = request.get("messages")
        if not isinstance(messages, list):
            self._json(HTTPStatus.BAD_REQUEST, {"error": {"message": "messages must be an array"}})
            return

        tools = request.get("tools")
        if not isinstance(tools, list):
            self._json(HTTPStatus.BAD_REQUEST, {"error": {"message": "tools must be an array"}})
            return
        tool_names: set[str] = set()
        for tool in tools:
            function = tool.get("function") if isinstance(tool, dict) else None
            name = function.get("name") if isinstance(function, dict) else None
            if not isinstance(name, str):
                self._json(HTTPStatus.BAD_REQUEST, {"error": {"message": "invalid tool schema"}})
                return
            tool_names.add(name)
        if "terminal" not in tool_names or not tool_names.issubset(ALLOWED_TOOL_NAMES):
            self._json(HTTPStatus.BAD_REQUEST, {"error": {"message": "unexpected PoC tool surface"}})
            return

        text = _visible_text(messages)
        has_tool_result = any(isinstance(item, dict) and item.get("role") == "tool" for item in messages)
        completion_id = f"chatcmpl-siq-{uuid.uuid4().hex}"
        stream = bool(request.get("stream"))

        if "SIQ_POC_TOOL" in text and not has_tool_result:
            arguments = json.dumps(
                {
                    "command": (
                        "set -eu; printf 'shell-ok\\n' > /workspace/hermes-shell-proof.txt; "
                        "python3 -c \"from pathlib import Path; "
                        "Path('/workspace/hermes-python-proof.txt').write_text('python-ok\\\\n', encoding='utf-8')\"; "
                        f"printf '{TOOL_MARKER}\\n'"
                    ),
                    "timeout": 30,
                },
                separators=(",", ":"),
            )
            tool_call = {
                "index": 0,
                "id": "call_siq_poc_terminal",
                "type": "function",
                "function": {"name": "terminal", "arguments": arguments},
            }
            self._respond(completion_id, stream, tool_calls=[tool_call], finish_reason="tool_calls")
            return

        if "SIQ_POC_TOOL" in text and has_tool_result:
            tool_text = _visible_text([item for item in messages if isinstance(item, dict) and item.get("role") == "tool"])
            content = "SIQ_POC_TOOL_OK" if TOOL_MARKER in tool_text else "SIQ_POC_TOOL_RESULT_MISSING"
            self._respond(completion_id, stream, content=content)
            return

        if "SIQ_POC_SLOW" in text:
            self._respond_slow(completion_id, stream)
            return

        self._respond(completion_id, stream, content="SIQ OpenShell Hermes PoC completed.")

    def _respond(
        self,
        completion_id: str,
        stream: bool,
        *,
        content: str | None = None,
        tool_calls: list[dict[str, Any]] | None = None,
        finish_reason: str = "stop",
    ) -> None:
        if not stream:
            message: dict[str, Any] = {"role": "assistant", "content": content}
            if tool_calls is not None:
                message["tool_calls"] = [{key: value for key, value in item.items() if key != "index"} for item in tool_calls]
            self._json(
                HTTPStatus.OK,
                {
                    "id": completion_id,
                    "object": "chat.completion",
                    "created": int(time.time()),
                    "model": MODEL,
                    "choices": [{"index": 0, "message": message, "finish_reason": finish_reason}],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                },
            )
            return

        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        try:
            if tool_calls is not None:
                tool_call = tool_calls[0]
                arguments = tool_call["function"]["arguments"]
                midpoint = max(1, len(arguments) // 2)
                first_call = {
                    **tool_call,
                    "function": {**tool_call["function"], "arguments": arguments[:midpoint]},
                }
                continuation = {
                    "index": tool_call["index"],
                    "function": {"arguments": arguments[midpoint:]},
                }
                self._event(_chunk(completion_id, {"role": "assistant", "tool_calls": [first_call]}))
                self._event(_chunk(completion_id, {"tool_calls": [continuation]}))
            else:
                midpoint = max(1, len(content or "") // 2)
                self._event(_chunk(completion_id, {"role": "assistant", "content": (content or "")[:midpoint]}))
                self._event(_chunk(completion_id, {"content": (content or "")[midpoint:]}))
            self._event(_chunk(completion_id, {}, finish_reason))
            self._event(
                {
                    "id": completion_id,
                    "object": "chat.completion.chunk",
                    "created": int(time.time()),
                    "model": MODEL,
                    "choices": [],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                }
            )
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            return

    def _respond_slow(self, completion_id: str, stream: bool) -> None:
        if not stream:
            time.sleep(15)
            self._respond(completion_id, False, content="SIQ_POC_SLOW_FINISHED")
            return
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        try:
            self._event(_chunk(completion_id, {"role": "assistant", "content": ""}))
            for _ in range(150):
                self._event(_chunk(completion_id, {"content": "."}))
                time.sleep(0.1)
            self._event(_chunk(completion_id, {}, "stop"))
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            return

    def _event(self, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.wfile.write(b"data: " + body + b"\n\n")
        self.wfile.flush()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=19000)
    args = parser.parse_args()
    if args.host not in {"127.0.0.1", "::1", "localhost"}:
        parser.error("the PoC model stub must remain loopback-only")
    if not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    server.daemon_threads = True
    server.serve_forever()


if __name__ == "__main__":
    main()
