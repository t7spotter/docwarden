"""MCP over HTTP, stateless.

Implements the JSON-RPC subset a tools-only server needs — initialize,
tools/list, tools/call, ping — directly, so the MCP endpoint is an ordinary
POST handler that runs unchanged inside a Django view and inside the CLI
server. Mounting the official SDK's ASGI app inside a Django URLconf is not
supported, and one code path for both adapters is worth more here than the
few dozen lines it saves.

`mcp_stdio.py` uses the official SDK for local stdio use.
"""

from __future__ import annotations

import json
from typing import Any

from . import agent as agent_payloads
from .config import Config
from .index import api_summaries, conventions, operation_detail, schema_detail, search_operations
from .loader import Registry

PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
SERVER_NAME = "apidocs-live"

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603

TOOLS: list[dict[str, Any]] = [
    {
        "name": "list_apis",
        "description": (
            "List every documented API in this doc set with its title, version and "
            "operation count. Start here to see what exists."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "search_operations",
        "description": (
            "Find API operations by free text. Searches operationId, URL path, summary, "
            "tags and description across every API. Returns compact entries; call "
            "get_operation for the full definition."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Free text: a verb, a noun, a URL fragment, or an operationId."},
                "app": {"type": "string", "description": "Restrict to one API, by its app name."},
                "method": {"type": "string", "description": "Restrict to one HTTP method."},
                "limit": {"type": "integer", "description": "Maximum results (default 20)."},
            },
            "required": ["query"],
        },
    },
    {
        "name": "get_operation",
        "description": (
            "The complete definition of one operation: description, parameters, request "
            "body, and every response with its schema and examples, with all local $refs "
            "already inlined. Accepts an operationId or 'METHOD /path'."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "operation_id": {
                    "type": "string",
                    "description": "An operationId, or a method and path such as 'POST /v1/widgets/'.",
                }
            },
            "required": ["operation_id"],
        },
    },
    {
        "name": "get_schema",
        "description": "Resolve one component schema by name, with nested $refs inlined.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "The component schema name, exactly as the spec declares it."},
                "app": {"type": "string", "description": "Which API to look in. Omit to search all."},
            },
            "required": ["name"],
        },
    },
    {
        "name": "get_conventions",
        "description": (
            "How one API works as a whole: its flow, authentication, conventions and "
            "security notes, plus the x-* blocks carrying rate limits, cache TTLs, token "
            "lifetimes and gateway details. This is the context that is not visible in "
            "any single operation — read it before integrating against an API."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"app": {"type": "string", "description": "The API name, as returned by list_apis."}},
            "required": ["app"],
        },
    },
    {
        "name": "list_changes",
        "description": (
            "What changed in the documentation since a baseline, classified as breaking "
            "or additive. Use it to answer 'will this break my client?' after a backend "
            "update. The baseline is a git revision of the spec directory (a tag, branch "
            "or commit) or a snapshot file written earlier; omit it for the previous "
            "commit that touched the specs."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "since": {
                    "type": "string",
                    "description": "A git revision, or the path to a snapshot .json. Optional.",
                }
            },
        },
    },
    {
        "name": "get_spec",
        "description": "The raw OpenAPI document for one API, as JSON or YAML. Large; prefer the other tools.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "app": {"type": "string"},
                "format": {"type": "string", "enum": ["json", "yaml"], "description": "Default json."},
            },
            "required": ["app"],
        },
    },
]


def handle_rpc(payload: Any, registry: Registry, config: Config) -> Any:
    """Handle one JSON-RPC message (or a list of them). None means no reply."""
    if isinstance(payload, list):
        replies = [reply for message in payload if (reply := handle_rpc(message, registry, config))]
        return replies or None

    if not isinstance(payload, dict):
        return _error(None, INVALID_REQUEST, "request must be a JSON object")

    method = payload.get("method")
    message_id = payload.get("id")
    params = payload.get("params") or {}

    # Notifications carry no id and get no reply.
    if message_id is None:
        return None

    if method == "initialize":
        requested = str(params.get("protocolVersion", ""))
        version = requested if requested in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0]
        return _result(
            message_id,
            {
                "protocolVersion": version,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": SERVER_NAME, "title": config.title, "version": _version()},
                "instructions": (
                    f"Live OpenAPI documentation for {config.title}. The specs are read from "
                    "disk on every call, so answers always reflect the current docs. Use "
                    "list_apis to see what exists, search_operations to find an endpoint, "
                    "get_operation for its full contract, and get_conventions for an API's "
                    "flow, auth rules and rate limits."
                ),
            },
        )

    if method == "ping":
        return _result(message_id, {})

    if method == "tools/list":
        return _result(message_id, {"tools": TOOLS})

    if method == "tools/call":
        name = params.get("name")
        arguments = params.get("arguments") or {}
        if not isinstance(arguments, dict):
            return _error(message_id, INVALID_PARAMS, "arguments must be an object")
        try:
            return _result(message_id, call_tool(name, arguments, registry, config))
        except Exception as exc:  # a bad argument must not take the endpoint down
            return _result(message_id, _tool_error(f"{type(exc).__name__}: {exc}"))

    return _error(message_id, METHOD_NOT_FOUND, f"unknown method {method!r}")


def call_tool(name: str, arguments: dict[str, Any], registry: Registry, config: Config) -> dict[str, Any]:
    """Run one tool. Returns an MCP tool result."""
    if name == "list_apis":
        return _tool_json({"revision": registry.revision, "apis": api_summaries(registry)})

    if name == "search_operations":
        query = str(arguments.get("query", ""))
        matches = search_operations(
            registry,
            query,
            app=arguments.get("app"),
            method=arguments.get("method"),
            limit=int(arguments.get("limit") or 20),
        )
        if not matches:
            return _tool_json(
                {
                    "query": query,
                    "results": [],
                    "hint": "No match. Call list_apis to see what exists, or search a URL fragment.",
                }
            )
        return _tool_json({"query": query, "count": len(matches), "results": matches})

    if name == "get_operation":
        operation_id = str(arguments.get("operation_id", ""))
        detail = operation_detail(registry, operation_id)
        if detail is None:
            return _tool_error(f"No operation {operation_id!r}. Use search_operations to find it.")
        return _tool_json(detail)

    if name == "get_schema":
        schema = schema_detail(registry, str(arguments.get("name", "")), arguments.get("app"))
        if schema is None:
            return _tool_error(f"No schema named {arguments.get('name')!r}.")
        return _tool_json(schema)

    if name == "get_conventions":
        app = str(arguments.get("app", ""))
        found = conventions(registry, app)
        if found is None:
            return _tool_error(f"No API named {app!r}. Known: {', '.join(registry.names())}.")
        return _tool_json(found)

    if name == "list_changes":
        from . import diff

        since = str(arguments.get("since") or "") or diff.default_since(registry) or ""
        if not since:
            return _tool_error(
                "No baseline to compare against. Pass `since` (a git revision or a "
                "snapshot file), or run this against a git checkout of the specs."
            )
        try:
            changes = diff.compare(diff.snapshot_at(registry, since), diff.snapshot(registry))
        except diff.DiffUnavailable as exc:
            return _tool_error(str(exc))
        return _tool_json(
            {
                "since": since,
                "counts": diff.summarize(changes),
                "changes": diff.as_dicts(changes),
            }
        )

    if name == "get_spec":
        app = str(arguments.get("app", ""))
        spec = registry.get(app)
        if spec is None or spec.error:
            return _tool_error(f"No API named {app!r}. Known: {', '.join(registry.names())}.")
        if str(arguments.get("format", "json")).lower() == "yaml":
            import yaml

            text = yaml.safe_dump(spec.data, allow_unicode=True, sort_keys=False)
        else:
            text = json.dumps(spec.data, ensure_ascii=False, indent=2)
        text, truncated = agent_payloads.truncate_spec(text)
        if truncated:
            text = "NOTE: truncated; use get_operation for specific endpoints.\n\n" + text
        return {"content": [{"type": "text", "text": text}]}

    return _tool_error(f"unknown tool {name!r}")


def _tool_json(payload: Any) -> dict[str, Any]:
    return {
        "content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False, indent=2)}],
        "structuredContent": payload if isinstance(payload, dict) else {"result": payload},
    }


def _tool_error(message: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": message}], "isError": True}


def _result(message_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": message_id, "result": result}


def _error(message_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": message_id, "error": {"code": code, "message": message}}


def _version() -> str:
    from . import __version__

    return __version__
