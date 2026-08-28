"""Payloads built for machine readers.

Shared by the HTTP routes and the MCP tools so both surfaces always agree.
"""

from __future__ import annotations

from typing import Any

from . import md
from .config import Config
from .index import api_summaries, build_index
from .loader import Registry

# Rough cap so a get_spec call cannot blow up an agent's context.
MAX_SPEC_CHARS = 400_000


def index_payload(registry: Registry, config: Config) -> dict[str, Any]:
    return {
        "title": config.title,
        "revision": registry.revision,
        "apis": api_summaries(registry),
        "operations": build_index(registry),
    }


def revision_payload(registry: Registry) -> dict[str, Any]:
    return {
        "revision": registry.revision,
        "specs": {
            name: {
                "sha256": spec.sha256,
                "version": spec.version,
                "error": spec.error,
            }
            for name, spec in registry.specs.items()
        },
    }


def llms_txt(registry: Registry, config: Config, origin: str = "") -> str:
    """The short index, in the llms.txt convention: what is here and where."""
    base = f"{origin}{config.base_path}" if origin else config.base_path
    apis = api_summaries(registry)
    total = sum(api["operations"] for api in apis)

    lines = [
        f"# {config.title}",
        "",
        f"> Live OpenAPI documentation for {len(apis)} APIs and {total} operations. "
        f"Generated from the specs on every request, so it is never stale. "
        f"Revision {registry.revision}.",
        "",
        "## APIs",
        "",
    ]
    for api in apis:
        spec = registry.specs[api["app"]]
        blurb = md.strip(spec.description, 160)
        lines.append(f"- [{api['title']}]({base}/openapi/{api['app']}.json): {blurb}")

    lines += [
        "",
        "## Machine endpoints",
        "",
        f"- [Operation index]({base}/index.json): every operation across every API, one compact JSON document.",
        f"- [Revision]({base}/revision.json): content hashes; poll this to tell whether anything changed.",
        f"- [Full documentation]({base}/llms-full.txt): every operation and every convention as plain text.",
        f"- MCP endpoint: POST {base}/mcp (tools: list_apis, search_operations, get_operation, get_schema, get_conventions, get_spec).",
        "",
        "## Operations",
        "",
    ]
    for entry in build_index(registry):
        auth = "" if entry["auth"] == "public" else f" [{entry['auth']}]"
        codes = ",".join(entry["responses"])
        lines.append(
            f"- `{entry['method']} {entry['path']}` — {entry['summary']} "
            f"(id `{entry['id']}`, app `{entry['app']}`{auth}, responses {codes})"
        )
    lines.append("")
    return "\n".join(lines)


def llms_full_txt(registry: Registry, config: Config) -> str:
    """Everything an agent could want, as plain text: conventions plus operations."""
    out = [f"# {config.title}", "", f"Revision: {registry.revision}", ""]

    for name in registry.names():
        spec = registry.specs[name]
        out += ["", "=" * 72, f"# {spec.title} (app: {name}, version {spec.version})", "=" * 72, ""]
        if spec.error:
            out += [f"ERROR: {spec.error}", ""]
            continue

        servers = [s.get("url") for s in spec.data.get("servers", []) if isinstance(s, dict)]
        if servers:
            out += ["Servers: " + ", ".join(str(url) for url in servers), ""]
        if spec.description:
            out += [spec.description.strip(), ""]

        for key, value in spec.extensions.items():
            out += [f"## {key}", _plain(value), ""]

        out += ["## Operations", ""]
        for entry in build_index(registry):
            if entry["app"] != name:
                continue
            operation = _operation(spec, entry)
            out += [
                f"### {entry['method']} {entry['path']}",
                f"operationId: {entry['id']}",
                f"auth: {entry['auth']}",
                f"summary: {entry['summary']}",
            ]
            description = str(operation.get("description", "")).strip()
            if description:
                out += ["", description]
            if entry["request"]:
                out.append(f"request body: {entry['request']}")
            out.append("responses:")
            for code, schema in entry["responses"].items():
                out.append(f"  {code}: {schema}")
            out.append("")

        schemas = spec.data.get("components", {}).get("schemas") or {}
        if schemas:
            out += ["## Schemas", ""]
            for schema_name, schema in schemas.items():
                out += [f"### {schema_name}", _plain(schema), ""]

    return "\n".join(out)


def _operation(spec, entry: dict[str, Any]) -> dict[str, Any]:
    item = (spec.data.get("paths") or {}).get(entry["path"]) or {}
    return item.get(entry["method"].lower()) or {}


def _plain(value: Any, indent: int = 0) -> str:
    pad = "  " * indent
    if isinstance(value, dict):
        return "\n".join(
            f"{pad}{key}:" + ("\n" + _plain(item, indent + 1) if isinstance(item, (dict, list)) else f" {item}")
            for key, item in value.items()
        )
    if isinstance(value, list):
        return "\n".join(
            f"{pad}-" + ("\n" + _plain(item, indent + 1) if isinstance(item, (dict, list)) else f" {item}")
            for item in value
        )
    return f"{pad}{value}"


def truncate_spec(text: str) -> tuple[str, bool]:
    if len(text) <= MAX_SPEC_CHARS:
        return text, False
    return text[:MAX_SPEC_CHARS] + "\n... truncated ...\n", True
