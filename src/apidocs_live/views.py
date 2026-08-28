"""Django views.

One view for everything: build a Request, call the shared handler, write the
Response back. Django-specific concerns are csrf exemption on the MCP POST and
turning a streaming Response into a StreamingHttpResponse.
"""

from __future__ import annotations

import threading
from dataclasses import replace

from django.conf import settings
from django.http import HttpRequest, HttpResponse, StreamingHttpResponse
from django.views.decorators.csrf import csrf_exempt

from .config import Config, env_token, from_dict
from .http import Request
from .router import Portal, build_portal, handle

_portal: Portal | None = None
_lock = threading.Lock()


def get_portal() -> Portal:
    """Build the portal once per process."""
    global _portal
    if _portal is None:
        with _lock:
            if _portal is None:
                _portal = build_portal(load_config())
    return _portal


def reset_portal() -> None:
    """Drop the cached portal. Used by tests, and after a settings change."""
    global _portal
    with _lock:
        if _portal and _portal.watcher:
            _portal.watcher.stop()
        _portal = None


def load_config() -> Config:
    """Read settings.APIDOCS_LIVE, filling in Django-aware defaults."""
    values = dict(getattr(settings, "APIDOCS_LIVE", {}) or {})
    values.setdefault("root", _default_root())
    values.setdefault("title", "API Documentation")
    # Live reload holds an SSE connection open, which pins a sync worker; only
    # worth it in development.
    values.setdefault("watch", bool(getattr(settings, "DEBUG", False)))
    config = from_dict(values)
    config.token = config.token or env_token()
    return config


def _default_root():
    base = getattr(settings, "BASE_DIR", None)
    return (base / "api-docs") if base else "api-docs"


@csrf_exempt
def docs(django_request: HttpRequest, path: str = "") -> HttpResponse:
    shared = get_portal()
    # The URLconf chose where this is mounted, so take the prefix from the
    # resolved request rather than making it a second thing to configure. It
    # goes on a per-request Portal so two mounts in one project cannot race
    # over one another's prefix.
    mounted = Portal(
        replace(shared.config, base_path=_mount_point(django_request)),
        shared.registry,
        shared.watcher,
    )
    response = handle(_to_request(django_request), mounted)

    if response.stream is not None:
        streaming = StreamingHttpResponse(response.stream, status=response.status)
        for key, value in response.headers.items():
            streaming[key] = value
        return streaming

    http_response = HttpResponse(response.body, status=response.status)
    for key, value in response.headers.items():
        http_response[key] = value
    return http_response


def _mount_point(django_request: HttpRequest) -> str:
    captured = _captured(django_request)
    return django_request.path[: len(django_request.path) - len(captured)].rstrip("/")


def _to_request(django_request: HttpRequest) -> Request:
    return Request(
        method=django_request.method or "GET",
        path=django_request.path,
        query={key: django_request.GET[key] for key in django_request.GET},
        headers={key.lower(): value for key, value in django_request.headers.items()},
        body=django_request.body if django_request.method == "POST" else b"",
        origin=f"{django_request.scheme}://{django_request.get_host()}",
    )


def _captured(django_request: HttpRequest) -> str:
    match = getattr(django_request, "resolver_match", None)
    captured = (match.kwargs.get("path") if match else None) or ""
    return captured
