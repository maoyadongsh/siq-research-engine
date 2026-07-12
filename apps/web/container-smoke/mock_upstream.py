from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class SmokeHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:
        self._respond()

    def do_POST(self) -> None:
        self._respond(set_session_cookie=True)

    def _respond(self, *, set_session_cookie: bool = False) -> None:
        content_length = int(self.headers.get("Content-Length", "0"))
        request_body = self.rfile.read(content_length).decode("utf-8")
        body = json.dumps(
            {
                "method": self.command,
                "path": self.path,
                "authorization": self.headers.get("Authorization"),
                "cookie": self.headers.get("Cookie"),
                "csrf_token": self.headers.get("X-CSRF-Token"),
                "forwarded_proto": self.headers.get("X-Forwarded-Proto"),
                "body": request_body,
            },
            separators=(",", ":"),
        ).encode("utf-8")

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-SIQ-Smoke-Upstream", os.environ.get("SIQ_SMOKE_UPSTREAM_NAME", "mock-api"))
        if set_session_cookie:
            self.send_header(
                "Set-Cookie",
                "siq_access_token=rotated-session; Path=/; HttpOnly; SameSite=Lax",
            )
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return


if __name__ == "__main__":
    port = int(os.environ.get("SIQ_SMOKE_UPSTREAM_PORT", "18081"))
    ThreadingHTTPServer(("0.0.0.0", port), SmokeHandler).serve_forever()
