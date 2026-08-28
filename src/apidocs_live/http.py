"""Framework-agnostic request/response objects.

The router speaks only these, so the same handle() serves both the stdlib CLI
server and Django views.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Iterator


@dataclass
class Request:
    method: str
    path: str
    query: dict[str, str] = field(default_factory=dict)
    headers: dict[str, str] = field(default_factory=dict)
    body: bytes = b""
    # Origin of the request ("https://api.example.ir"), used to offer the current
    # host as a server in the try-it panel. Empty when it cannot be determined.
    origin: str = ""

    def header(self, name: str, default: str = "") -> str:
        return self.headers.get(name.lower(), default)

    def json(self) -> Any:
        if not self.body:
            return None
        return json.loads(self.body.decode("utf-8"))


@dataclass
class Response:
    status: int = 200
    headers: dict[str, str] = field(default_factory=dict)
    body: bytes = b""
    # Set instead of body for streaming responses (server-sent events).
    stream: Iterator[bytes] | None = None

    @property
    def content_type(self) -> str:
        return self.headers.get("Content-Type", "")


def html(markup: str, status: int = 200) -> Response:
    return Response(
        status=status,
        headers={"Content-Type": "text/html; charset=utf-8"},
        body=markup.encode("utf-8"),
    )


def text(content: str, status: int = 200, content_type: str = "text/plain") -> Response:
    return Response(
        status=status,
        headers={"Content-Type": f"{content_type}; charset=utf-8"},
        body=content.encode("utf-8"),
    )


def json_response(payload: Any, status: int = 200) -> Response:
    body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    return Response(
        status=status,
        headers={"Content-Type": "application/json; charset=utf-8"},
        body=body,
    )


def not_found(message: str = "Not found") -> Response:
    return json_response({"error": message}, status=404)
