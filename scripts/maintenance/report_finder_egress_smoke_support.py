#!/usr/bin/env python3
"""Container-side fixtures for the report-finder egress smoke."""

from __future__ import annotations

import argparse
import json
import socket
import socketserver
import struct
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import import_module
from pathlib import Path


def _append_event(output: Path, name: str, payload: dict[str, object]) -> None:
    output.mkdir(parents=True, exist_ok=True)
    with (output / f"{name}.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(payload, sort_keys=True) + "\n")


class _HTTPHandler(BaseHTTPRequestHandler):
    server_version = "SIQEgressSmoke/1"

    def do_GET(self) -> None:  # noqa: N802 - required by BaseHTTPRequestHandler
        name = str(self.server.siq_name)  # type: ignore[attr-defined]
        output = Path(self.server.siq_output)  # type: ignore[attr-defined]
        _append_event(
            output,
            name,
            {"client": self.client_address[0], "host": self.headers.get("Host"), "path": self.path},
        )
        if self.path == "/redirect-metadata":
            self.send_response(302)
            self.send_header("Location", "http://169.254.169.254:18081/latest/meta-data")
            self.end_headers()
            return
        body = b"<html><body>controlled official filing</body></html>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *_args: object) -> None:
        return


def serve_http(*, name: str, port: int, output: Path) -> None:
    server = ThreadingHTTPServer(("0.0.0.0", port), _HTTPHandler)
    server.siq_name = name  # type: ignore[attr-defined]
    server.siq_output = str(output)  # type: ignore[attr-defined]
    output.mkdir(parents=True, exist_ok=True)
    (output / f"{name}.ready").write_text("ready\n", encoding="utf-8")
    server.serve_forever()


DNS_RECORDS = {
    "private.sec.gov": "10.77.0.10",
    "linklocal.sec.gov": "169.254.240.10",
    "metadata.sec.gov": "169.254.169.254",
    "loopback.sec.gov": "127.0.0.1",
}


def _dns_name(packet: bytes, offset: int = 12) -> tuple[str, int]:
    labels: list[str] = []
    while True:
        length = packet[offset]
        offset += 1
        if length == 0:
            break
        labels.append(packet[offset : offset + length].decode("ascii"))
        offset += length
    return ".".join(labels).lower(), offset


class _DNSHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        packet, sock = self.request
        name, offset = _dns_name(packet)
        qtype, _qclass = struct.unpack("!HH", packet[offset : offset + 4])
        server = self.server
        counts = server.siq_counts  # type: ignore[attr-defined]
        if qtype == 1:
            counts[name] = counts.get(name, 0) + 1
        address = DNS_RECORDS.get(name)
        if name == "rebind.sec.gov" and qtype == 1:
            address = "93.184.216.10" if counts[name] == 1 else "127.0.0.1"
        _append_event(
            Path(server.siq_output),  # type: ignore[attr-defined]
            "dns",
            {"address": address, "name": name, "qtype": qtype, "sequence": counts.get(name, 0)},
        )
        question = packet[12 : offset + 4]
        if qtype == 1 and address:
            header = struct.pack("!HHHHHH", struct.unpack("!H", packet[:2])[0], 0x8180, 1, 1, 0, 0)
            answer = b"\xc0\x0c" + struct.pack("!HHIH", 1, 1, 0, 4) + socket.inet_aton(address)
        else:
            header = struct.pack("!HHHHHH", struct.unpack("!H", packet[:2])[0], 0x8180, 1, 0, 0, 0)
            answer = b""
        sock.sendto(header + question + answer, self.client_address)


class _ThreadingUDPServer(socketserver.ThreadingMixIn, socketserver.UDPServer):
    daemon_threads = True


def serve_dns(*, output: Path) -> None:
    server = _ThreadingUDPServer(("0.0.0.0", 53), _DNSHandler)
    server.siq_counts = {}  # type: ignore[attr-defined]
    server.siq_output = str(output)  # type: ignore[attr-defined]
    output.mkdir(parents=True, exist_ok=True)
    (output / "dns.ready").write_text("ready\n", encoding="utf-8")
    server.serve_forever()


def _event_count(output: Path, name: str) -> int:
    path = output / f"{name}.jsonl"
    if not path.exists():
        return 0
    return len(path.read_text(encoding="utf-8").splitlines())


def _events(output: Path, name: str) -> list[dict[str, object]]:
    path = output / f"{name}.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _blocked_call(call, pattern: str) -> str:
    try:
        call()
    except Exception as exc:  # the exact public API error is asserted below
        message = str(exc)
        if pattern not in message.lower():
            raise AssertionError(f"expected {pattern!r} in blocked error, got: {message}") from exc
        return message
    raise AssertionError("unsafe request unexpectedly reached a response")


def run_driver(output: Path) -> dict[str, object]:
    import httpx
    from market_report_finder_service.models.schemas import Market
    from market_report_finder_service.services.downloader import ReportDownloader, _PinnedReportHTTPTransport

    downloader_module = import_module("market_report_finder_service.services.downloader")

    original_connection = _PinnedReportHTTPTransport._connection
    connection_attempts: list[dict[str, object]] = []

    def traced_connection(scheme, connect_host, port, server_hostname, timeout):
        connection_attempts.append(
            {
                "connect_host": connect_host,
                "port": port,
                "scheme": scheme,
                "server_hostname": server_hostname,
            }
        )
        return original_connection(scheme, connect_host, port, server_hostname, timeout)

    _PinnedReportHTTPTransport._connection = staticmethod(traced_connection)
    loopback_hits = 0

    class LoopbackHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            nonlocal loopback_hits
            loopback_hits += 1
            self.send_response(200)
            self.end_headers()

        def log_message(self, _format: str, *_args: object) -> None:
            return

    loopback_server = ThreadingHTTPServer(("127.0.0.1", 18082), LoopbackHandler)
    loopback_thread = threading.Thread(target=loopback_server.serve_forever, daemon=True)
    loopback_thread.start()
    report: dict[str, object] = {}
    try:
        official_url = "http://www.sec.gov:18080/official"
        candidate = type(
            "Candidate",
            (),
            {"document_url": official_url, "market": Market.us, "metadata": {}, "source_id": "sec"},
        )()
        ReportDownloader()._validate_fetch_url(candidate, official_url)
        allowed_before = len(connection_attempts)
        with httpx.Client(transport=_PinnedReportHTTPTransport(), timeout=3.0) as client:
            response = client.get(official_url)
        official_events = _events(output, "official")
        report["official_allowlist"] = {
            "body_verified": response.content.startswith(b"<html>"),
            "connect_attempts": len(connection_attempts) - allowed_before,
            "connected_ip": connection_attempts[-1]["connect_host"],
            "host_header": official_events[-1].get("host") if official_events else None,
            "policy_validated": True,
            "status_code": response.status_code,
        }

        blocked_cases = {
            "private": "http://private.sec.gov:18083/secret",
            "link_local": "http://linklocal.sec.gov:18084/secret",
            "metadata": "http://metadata.sec.gov:18081/latest/meta-data",
            "loopback": "http://loopback.sec.gov:18082/secret",
        }
        blocked_reports: dict[str, object] = {}
        trap_names = {
            "private": "private-trap",
            "link_local": "linklocal-trap",
            "metadata": "metadata-trap",
        }
        for name, url in blocked_cases.items():
            before_connections = len(connection_attempts)
            before_trap = loopback_hits if name == "loopback" else _event_count(output, trap_names[name])
            error = _blocked_call(
                lambda url=url: _PinnedReportHTTPTransport().handle_request(httpx.Request("GET", url)),
                "private" if name == "private" else ("link-local" if name == "link_local" else ("metadata" if name == "metadata" else "loopback")),
            )
            time.sleep(0.05)
            after_trap = loopback_hits if name == "loopback" else _event_count(output, trap_names[name])
            blocked_reports[name] = {
                "blocked_before_connect": len(connection_attempts) == before_connections,
                "error": error,
                "trap_hits": after_trap - before_trap,
            }
        report["blocked_destinations"] = blocked_reports

        before_connections = len(connection_attempts)
        before_metadata = _event_count(output, "metadata-trap")
        redirect_url = "http://www.sec.gov:18080/redirect-metadata"
        redirect_candidate = type(
            "Candidate",
            (),
            {"document_url": redirect_url, "market": Market.us, "metadata": {}, "source_id": "sec"},
        )()
        with httpx.Client(transport=_PinnedReportHTTPTransport(), timeout=3.0, follow_redirects=False) as client:
            error = _blocked_call(
                lambda: ReportDownloader()._stream_get_with_redirects(
                    client,
                    redirect_url,
                    Path("/tmp/redirect-body"),
                    redirect_candidate,
                ),
                "non-public",
            )
        official_events = _events(output, "official")
        report["redirect_to_metadata"] = {
            "blocked_before_second_connect": len(connection_attempts) == before_connections + 1,
            "error": error,
            "initial_redirect_observed": any(event.get("path") == "/redirect-metadata" for event in official_events),
            "metadata_trap_hits": _event_count(output, "metadata-trap") - before_metadata,
        }

        rebind_url = "http://rebind.sec.gov:18080/rebind"
        ReportDownloader._validate_resolved_host("rebind.sec.gov", rebind_url)
        before_connections = len(connection_attempts)
        before_official = _event_count(output, "official")
        error = _blocked_call(
            lambda: _PinnedReportHTTPTransport().handle_request(httpx.Request("GET", rebind_url)),
            "loopback",
        )
        report["dns_rebind"] = {
            "blocked_before_connect": len(connection_attempts) == before_connections,
            "error": error,
            "official_stub_hits": _event_count(output, "official") - before_official,
        }
        report["dns_observations"] = [event for event in _events(output, "dns") if event.get("qtype") == 1]
    finally:
        loopback_server.shutdown()
        loopback_server.server_close()
        downloader_module._PinnedReportHTTPTransport._connection = staticmethod(original_connection)

    (output / "driver-report.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("dns", "driver", "http"))
    parser.add_argument("--name", default="stub")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--port", type=int, default=18080)
    args = parser.parse_args()
    if args.mode == "dns":
        serve_dns(output=args.output)
    elif args.mode == "http":
        serve_http(name=args.name, port=args.port, output=args.output)
    else:
        run_driver(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
