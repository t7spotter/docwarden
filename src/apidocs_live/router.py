"""The one request handler.

Both adapters — the stdlib CLI server and the Django views — build a Request,
call handle(), and write the Response back. Every route lives here, so the two
surfaces can never drift apart.
"""

from __future__ import annotations

import hmac
import json
import mimetypes
import time
from pathlib import Path
from typing import Any, Iterator

import yaml

from . import agent, diff, mcp_http, render
from .config import Config
from .http import Request, Response, html, json_response, not_found, text
from .index import conventions, operation_detail, schema_detail, search_operations
from .loader import Registry, reload_if_changed
from .watcher import Watcher

STATIC_DIR = Path(__file__).resolve().parent / "static"


class Portal:
    """Holds the loaded doc set for one mounted portal.

    Created once by each adapter. Cheap to build; reload is incremental.
    """

    def __init__(self, config: Config, registry: Registry, watcher: Watcher | None = None) -> None:
        self.config = config
        self.registry = registry
        self.watcher = watcher

    def refresh(self) -> None:
        """Re-read changed specs when no watcher thread is doing it for us."""
        if self.watcher is None and self.config.watch:
            reload_if_changed(self.registry, self.config.sources or None)


def handle(request: Request, portal: Portal) -> Response:
    config, registry = portal.config, portal.registry

    denied = _check_token(request, config)
    if denied:
        return denied

    path = _relative(request.path, config.base_path)

    if path.startswith("_static/"):
        return _static(path[len("_static/") :])

    portal.refresh()

    if request.method == "POST":
        if path == "mcp":
            return _mcp(request, portal)
        return json_response({"error": "method not allowed"}, status=405)

    if request.method not in ("GET", "HEAD"):
        return json_response({"error": "method not allowed"}, status=405)

    if path in ("", "index.html"):
        return html(render.landing(config, registry))

    if path == "health":
        return json_response({"status": "ok", "revision": registry.revision, "specs": len(registry.specs)})

    if path == "revision.json":
        return json_response(agent.revision_payload(registry))

    if path == "index.json":
        return json_response(agent.index_payload(registry, config))

    if path == "llms.txt":
        return text(agent.llms_txt(registry, config, request.origin), content_type="text/markdown")

    if path == "llms-full.txt":
        return text(agent.llms_full_txt(registry, config), content_type="text/markdown")

    if path == "search.json":
        query = request.query.get("q", "")
        results = search_operations(
            registry,
            query,
            app=request.query.get("app"),
            method=request.query.get("method"),
            limit=int(request.query.get("limit") or 20),
        )
        return json_response({"query": query, "count": len(results), "results": results})

    if path == "events":
        return _events(portal)

    if path in ("changes", "changes.json"):
        return _changes(request, portal, as_json=path.endswith(".json"))

    if path in ("openapi", "openapi/"):
        return json_response(
            {
                name: {
                    "json": config.url(f"openapi/{name}.json"),
                    "yaml": config.url(f"openapi/{name}.yaml"),
                }
                for name in registry.names()
            }
        )

    if path.startswith("openapi/"):
        return _spec_file(registry, path[len("openapi/") :])

    if path.startswith("display/") and path.endswith(".json"):
        # The renderer's copy: x-* blocks folded into the overview, configured
        # servers applied. /openapi/<name>.json stays byte-faithful.
        name = path[len("display/") : -len(".json")]
        spec = registry.get(name)
        if spec is None or spec.error:
            return not_found(f"no API {name!r}")
        return json_response(render.display_spec(config, spec))

    if path.startswith("operation/") and path.endswith(".json"):
        operation_id = path[len("operation/") : -len(".json")]
        detail = operation_detail(registry, _unquote(operation_id))
        return json_response(detail) if detail else not_found(f"no operation {operation_id!r}")

    if path.startswith("schema/") and path.endswith(".json"):
        name = _unquote(path[len("schema/") : -len(".json")])
        found = schema_detail(registry, name, request.query.get("app"))
        return json_response(found) if found else not_found(f"no schema {name!r}")

    if path.startswith("conventions/") and path.endswith(".json"):
        app = path[len("conventions/") : -len(".json")]
        found = conventions(registry, app)
        return json_response(found) if found else not_found(f"no API {app!r}")

    head, _, tail = path.partition("/")
    spec = registry.get(head)
    if spec is not None and tail in ("", "index.html"):
        return html(render.api_page(config, registry, spec))

    return not_found(f"no route for /{path}")


# ---------------------------------------------------------------- pieces


def _relative(path: str, base_path: str) -> str:
    # Only strip on a segment boundary, so a mount at /api-docs does not claim
    # the prefix of an unrelated /api-docs-internal.
    if base_path and (path == base_path or path.startswith(base_path + "/")):
        path = path[len(base_path) :]
    return path.strip("/")


def _unquote(value: str) -> str:
    from urllib.parse import unquote

    return unquote(value)


def _check_token(request: Request, config: Config) -> Response | None:
    if not config.token:
        return None

    header = request.header("authorization")
    supplied = header[len("Bearer ") :] if header.startswith("Bearer ") else request.query.get("token", "")
    if supplied and hmac.compare_digest(supplied, config.token):
        return None

    return Response(
        status=401,
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "WWW-Authenticate": 'Bearer realm="api-docs"',
        },
        body=json.dumps({"error": "a token is required"}).encode("utf-8"),
    )


def _static(relative: str) -> Response:
    target = (STATIC_DIR / relative).resolve()
    try:
        target.relative_to(STATIC_DIR)
    except ValueError:
        return not_found("bad static path")
    if not target.is_file():
        return not_found(f"no asset {relative!r}")

    content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
    if content_type.startswith("text/") or content_type in ("application/javascript", "application/json"):
        content_type += "; charset=utf-8"

    return Response(
        status=200,
        headers={"Content-Type": content_type, "Cache-Control": "public, max-age=3600"},
        body=target.read_bytes(),
    )


def _spec_file(registry: Registry, filename: str) -> Response:
    name, _, extension = filename.rpartition(".")
    spec = registry.get(name)
    if spec is None or spec.error:
        return not_found(f"no API {name!r}")

    if extension == "yaml":
        body = yaml.safe_dump(spec.data, allow_unicode=True, sort_keys=False)
        return Response(
            status=200,
            headers={"Content-Type": "application/yaml; charset=utf-8", "ETag": f'"{spec.sha256}"'},
            body=body.encode("utf-8"),
        )
    if extension == "json":
        body = json.dumps(spec.data, ensure_ascii=False, indent=2)
        return Response(
            status=200,
            headers={
                "Content-Type": "application/json; charset=utf-8",
                "ETag": f'"{spec.sha256}"',
                # The renderer runs in an iframe on this same origin; try-it
                # calls go to the API host, which is what CORS governs there.
                "Access-Control-Allow-Origin": "*",
            },
            body=body.encode("utf-8"),
        )
    return not_found("ask for .json or .yaml")


def _mcp(request: Request, portal: Portal) -> Response:
    try:
        payload = request.json()
    except (ValueError, UnicodeDecodeError) as exc:
        return json_response(
            {"jsonrpc": "2.0", "id": None, "error": {"code": mcp_http.PARSE_ERROR, "message": str(exc)}},
            status=400,
        )

    reply = mcp_http.handle_rpc(payload, portal.registry, portal.config)
    if reply is None:
        # A notification: accepted, nothing to say back.
        return Response(status=202, headers={"Content-Type": "application/json"}, body=b"")
    return json_response(reply)


def _changes(request: Request, portal: Portal, as_json: bool) -> Response:
    registry, config = portal.registry, portal.config
    since = request.query.get("since") or diff.default_since(registry) or ""

    changes: list[diff.Change] = []
    error = None
    if since:
        try:
            changes = diff.compare(diff.snapshot_at(registry, since), diff.snapshot(registry))
        except diff.DiffUnavailable as exc:
            error = str(exc)
    else:
        error = "Pass ?since=<git revision or snapshot.json> to compare against a baseline."

    if as_json:
        return json_response(
            {
                "since": since,
                "revision": registry.revision,
                "error": error,
                "counts": diff.summarize(changes),
                "changes": diff.as_dicts(changes),
            },
            status=200 if not error else 400,
        )
    return html(render.changes_page(config, registry, since, changes, error))


def _events(portal: Portal) -> Response:
    """Server-sent events carrying the current revision.

    Only used in development. In production `watch` is off and agents poll
    revision.json instead, because a held-open SSE connection pins a worker.
    """
    if not portal.config.watch:
        return not_found("live reload is disabled")

    def stream() -> Iterator[bytes]:
        last = ""
        deadline = time.monotonic() + 3600
        while time.monotonic() < deadline:
            revision = portal.watcher.revision if portal.watcher else portal.registry.revision
            if revision != last:
                last = revision
                yield f"event: revision\ndata: {revision}\n\n".encode("utf-8")
            else:
                yield b": keepalive\n\n"
            time.sleep(1.0)

    return Response(
        status=200,
        headers={
            "Content-Type": "text/event-stream; charset=utf-8",
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
        stream=stream(),
    )


def build_portal(config: Config, watch: bool | None = None) -> Portal:
    """Load the doc set and, when watching, start the poll thread."""
    from .loader import load_registry

    registry = load_registry(config.root, config.sources or None)
    watcher = None
    if watch if watch is not None else config.watch:
        watcher = Watcher(registry, config.sources or None)
        watcher.start()
    return Portal(config, registry, watcher)
