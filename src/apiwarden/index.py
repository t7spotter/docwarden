"""Flatten the doc set into a compact operation index, and search it.

The index is the cheap entry point for an agent: every operation across every
spec in one small document, with the detail available on demand.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from .loader import Registry, Spec, ref_name, resolve_deep, resolve_ref

_WORD = re.compile(r"[a-z0-9]+")


def operation_auth(spec: Spec, operation: dict[str, Any]) -> str:
    """How the operation authenticates: a scheme name, or "public"."""
    security = operation.get("security", spec.data.get("security"))
    if not security:
        return "public"

    schemes = spec.data.get("components", {}).get("securitySchemes", {})
    names = [name for entry in security if isinstance(entry, dict) for name in entry]
    if not names:
        return "public"

    scheme = schemes.get(names[0], {})
    if scheme.get("type") == "http":
        return str(scheme.get("scheme", "http")).lower()
    return str(scheme.get("type", names[0]))


def _body_schema_name(operation: dict[str, Any]) -> str | None:
    content = (operation.get("requestBody") or {}).get("content") or {}
    for media in content.values():
        name = ref_name(media.get("schema"))
        if name:
            return name
        schema = media.get("schema")
        if isinstance(schema, dict) and schema.get("type"):
            return str(schema["type"])
    return None


def _response_names(spec: Spec, operation: dict[str, Any]) -> dict[str, str]:
    """Map status code -> schema name, or the response description as a fallback."""
    out: dict[str, str] = {}
    for code, response in (operation.get("responses") or {}).items():
        if not isinstance(response, dict):
            continue
        # Shared responses such as '#/components/responses/Unauthorized'.
        if isinstance(response.get("$ref"), str):
            try:
                response = resolve_ref(spec.data, response["$ref"])
            except (KeyError, IndexError, ValueError):
                out[str(code)] = ref_name({"$ref": response["$ref"]}) or "?"
                continue

        name = None
        for media in (response.get("content") or {}).values():
            name = ref_name(media.get("schema"))
            if name:
                break
            schema = media.get("schema")
            if isinstance(schema, dict):
                if schema.get("type") == "array":
                    inner = ref_name(schema.get("items")) or "object"
                    name = f"{inner}[]"
                elif schema.get("type"):
                    name = str(schema["type"])
            if name:
                break
        out[str(code)] = name or str(response.get("description", "")).strip().split("\n")[0]
    return out


def operation_hash(operation: dict[str, Any]) -> str:
    payload = json.dumps(operation, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


def _fallback_id(method: str, url: str) -> str:
    parts = [p for p in re.split(r"[/{}]", url) if p]
    return method.lower() + "".join(p.title().replace("-", "").replace("_", "") for p in parts)


def build_index(registry: Registry) -> list[dict[str, Any]]:
    """One compact entry per operation, across every spec."""
    entries: list[dict[str, Any]] = []
    for name in registry.names():
        spec = registry.specs[name]
        if spec.error:
            continue
        for url, method, operation, _shared in spec.operations():
            entry = {
                "id": operation.get("operationId") or _fallback_id(method, url),
                "app": name,
                "method": method.upper(),
                "path": url,
                "summary": str(operation.get("summary", "")).strip(),
                "tags": list(operation.get("tags", [])),
                "auth": operation_auth(spec, operation),
                "request": _body_schema_name(operation),
                "responses": _response_names(spec, operation),
                "hash": operation_hash(operation),
            }
            if operation.get("deprecated"):
                entry["deprecated"] = True
            entries.append(entry)
    return entries


def find_operation(registry: Registry, operation_id: str) -> tuple[Spec, str, str, dict] | None:
    """Locate an operation by operationId, or by "METHOD /path"."""
    wanted = operation_id.strip()
    method_path = None
    if " " in wanted:
        method, _, url = wanted.partition(" ")
        method_path = (method.strip().lower(), url.strip())

    for name in registry.names():
        spec = registry.specs[name]
        if spec.error:
            continue
        for url, method, operation, shared in spec.operations():
            if operation.get("operationId") == wanted:
                return spec, url, method, _merged(operation, shared)
            if method_path and (method, url) == method_path:
                return spec, url, method, _merged(operation, shared)
            if _fallback_id(method, url) == wanted:
                return spec, url, method, _merged(operation, shared)
    return None


def _merged(operation: dict[str, Any], shared: list) -> dict[str, Any]:
    """Fold path-level parameters into the operation."""
    if not shared:
        return operation
    return {**operation, "parameters": [*shared, *operation.get("parameters", [])]}


def operation_detail(registry: Registry, operation_id: str) -> dict[str, Any] | None:
    """Everything about one operation, with local $refs inlined."""
    found = find_operation(registry, operation_id)
    if not found:
        return None
    spec, url, method, operation = found

    return {
        "id": operation.get("operationId") or _fallback_id(method, url),
        "app": spec.name,
        "api": spec.title,
        "method": method.upper(),
        "path": url,
        "auth": operation_auth(spec, operation),
        "servers": [s.get("url") for s in spec.data.get("servers", []) if isinstance(s, dict)],
        "summary": operation.get("summary", ""),
        "description": operation.get("description", ""),
        "tags": operation.get("tags", []),
        "parameters": resolve_deep(spec.data, operation.get("parameters", [])),
        "requestBody": resolve_deep(spec.data, operation.get("requestBody")) if operation.get("requestBody") else None,
        "responses": resolve_deep(spec.data, operation.get("responses", {})),
    }


def schema_detail(registry: Registry, name: str, app: str | None = None) -> dict[str, Any] | None:
    """Resolve one component schema. Searches every spec when app is omitted."""
    names = [app] if app else registry.names()
    for spec_name in names:
        spec = registry.specs.get(spec_name)
        if not spec or spec.error:
            continue
        schema = (spec.data.get("components", {}).get("schemas") or {}).get(name)
        if schema is not None:
            return {
                "name": name,
                "app": spec.name,
                "schema": resolve_deep(spec.data, schema),
            }
    return None


def conventions(registry: Registry, app: str) -> dict[str, Any] | None:
    """The narrative and the x-* blocks — the part renderers throw away.

    info.description is where an API explains itself as a whole, and top-level
    x-* blocks are where a team records what does not fit the OpenAPI schema.
    Neither shows up per-operation, so both are easy to lose.
    """
    spec = registry.get(app)
    if not spec or spec.error:
        return None
    return {
        "app": spec.name,
        "api": spec.title,
        "version": spec.version,
        "description": spec.description,
        "tags": spec.data.get("tags", []),
        "servers": spec.data.get("servers", []),
        "securitySchemes": spec.data.get("components", {}).get("securitySchemes", {}),
        "extensions": spec.extensions,
    }


def _tokens(value: str) -> list[str]:
    return _WORD.findall(value.lower())


def search_operations(
    registry: Registry,
    query: str,
    app: str | None = None,
    method: str | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Rank operations against a free-text query.

    Scoring is deliberately simple: exact id/path hits first, then field-weighted
    token overlap. A few dozen operations do not need a search index.
    """
    entries = build_index(registry)
    if app:
        entries = [e for e in entries if e["app"] == app]
    if method:
        entries = [e for e in entries if e["method"] == method.upper()]

    terms = _tokens(query)
    if not terms:
        return entries[:limit]

    lowered = query.strip().lower()
    scored = []
    for entry in entries:
        spec = registry.specs[entry["app"]]
        operation = _operation_body(spec, entry)
        haystacks = (
            (entry["id"].lower(), 6),
            (entry["path"].lower(), 5),
            (entry["summary"].lower(), 4),
            (" ".join(entry["tags"]).lower(), 3),
            (str(operation.get("description", "")).lower(), 1),
        )

        score = 0
        if lowered == entry["id"].lower() or lowered == entry["path"].lower():
            score += 100
        for text, weight in haystacks:
            hits = sum(1 for term in terms if term in text)
            score += hits * weight
            if hits == len(terms):
                score += weight  # every term present in one field
        if score:
            scored.append((score, entry))

    scored.sort(key=lambda pair: (-pair[0], pair[1]["app"], pair[1]["path"]))
    return [dict(entry, score=score) for score, entry in scored[:limit]]


def _operation_body(spec: Spec, entry: dict[str, Any]) -> dict[str, Any]:
    item = (spec.data.get("paths") or {}).get(entry["path"]) or {}
    return item.get(entry["method"].lower()) or {}


def api_summaries(registry: Registry) -> list[dict[str, Any]]:
    """One row per spec, for the sidebar and for list_apis."""
    counts: dict[str, int] = {}
    for entry in build_index(registry):
        counts[entry["app"]] = counts.get(entry["app"], 0) + 1

    rows = []
    for name in registry.names():
        spec = registry.specs[name]
        rows.append(
            {
                "app": name,
                "title": spec.title,
                "version": spec.version,
                "operations": counts.get(name, 0),
                "tags": [t.get("name") for t in spec.data.get("tags", []) if isinstance(t, dict)],
                "extensions": list(spec.extensions),
                "error": spec.error,
            }
        )
    return rows
