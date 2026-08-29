"""Standalone server for `apiwarden serve`.

A threading HTTP server from the standard library — enough for a docs portal a
team reads, with no framework dependency. Django hosts the same handler in
production.
"""

from __future__ import annotations

import errno
import socket
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from .http import Request
from .router import Portal, handle

MAX_BODY = 4 * 1024 * 1024


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    portal: Portal

    def do_GET(self) -> None:  # noqa: N802
        self._serve("GET")

    def do_HEAD(self) -> None:  # noqa: N802
        self._serve("HEAD")

    def do_POST(self) -> None:  # noqa: N802
        self._serve("POST")

    def _serve(self, method: str) -> None:
        parts = urlsplit(self.path)
        query = {key: values[0] for key, values in parse_qs(parts.query).items()}
        headers = {key.lower(): value for key, value in self.headers.items()}

        length = min(int(headers.get("content-length") or 0), MAX_BODY)
        body = self.rfile.read(length) if length else b""

        host = headers.get("host", "")
        request = Request(
            method=method,
            path=parts.path,
            query=query,
            headers=headers,
            body=body,
            origin=f"http://{host}" if host else "",
        )

        try:
            response = handle(request, self.portal)
        except Exception as exc:  # keep the server up; the browser sees the error
            self.log_error("handler failed: %s", exc)
            self._send(500, {"Content-Type": "text/plain; charset=utf-8"}, b"internal error")
            return

        if response.stream is not None:
            self._send_stream(response)
            return

        self._send(response.status, response.headers, b"" if method == "HEAD" else response.body,
                   content_length=len(response.body))

    def _send(self, status, headers, body, content_length=None):
        self.send_response(status)
        for key, value in headers.items():
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(body) if content_length is None else content_length))
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _send_stream(self, response) -> None:
        self.send_response(response.status)
        for key, value in response.headers.items():
            self.send_header(key, value)
        self.send_header("Connection", "close")
        self.end_headers()
        try:
            for chunk in response.stream:
                self.wfile.write(chunk)
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass  # the browser navigated away
        self.close_connection = True

    def log_message(self, format: str, *args) -> None:
        sys.stderr.write(f"  {self.address_string()} {format % args}\n")


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def serve(portal: Portal, host: str = "127.0.0.1", port: int = 8080) -> None:
    handler = type("Handler", (_Handler,), {"portal": portal})
    try:
        server = _Server((host, port), handler)
    except OSError as exc:
        hint = ""
        if getattr(exc, "errno", None) in (errno.EADDRINUSE, errno.EACCES):
            hint = f"\n  something else is on that port — try: apiwarden serve {port + 1}"
        raise SystemExit(f"cannot bind {host}:{port} — {exc}{hint}") from exc

    shown = host if host not in ("0.0.0.0", "::") else socket.gethostname()
    print(f"  http://{shown}:{port}{portal.config.base_path}/", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  stopped")
    finally:
        server.server_close()
        if portal.watcher:
            portal.watcher.stop()
