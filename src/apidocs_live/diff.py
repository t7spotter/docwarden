"""What changed between two versions of the doc set.

The point is not a text diff — it is answering "does this break my client?".
So each spec is reduced to a snapshot of the things a caller actually depends
on (which operations exist, which fields are required, what a response
contains) and two snapshots are compared field by field.

The "before" side comes either from a git revision of the same directory or
from a snapshot file written earlier, so a team can diff against a release tag
as easily as against yesterday.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .index import build_index, operation_auth
from .loader import Registry, parse_spec, resolve_deep

BREAKING = "breaking"
ADDITIVE = "additive"
INFO = "info"

# How deep to walk nested schemas when flattening. Deep enough for real
# payloads, shallow enough that a recursive schema cannot produce a huge diff.
MAX_DEPTH = 6


@dataclass
class Change:
    level: str
    app: str
    operation: str
    kind: str
    detail: str


# ---------------------------------------------------------------- snapshots


def snapshot(registry: Registry) -> dict[str, Any]:
    """Reduce the doc set to the parts a client contract depends on."""
    apis: dict[str, Any] = {}

    for entry in build_index(registry):
        spec = registry.specs[entry["app"]]
        api = apis.setdefault(entry["app"], {"version": spec.version, "operations": {}})
        item = (spec.data.get("paths") or {}).get(entry["path"]) or {}
        operation = item.get(entry["method"].lower()) or {}

        api["operations"][f"{entry['method']} {entry['path']}"] = {
            "id": entry["id"],
            "summary": entry["summary"],
            "auth": operation_auth(spec, operation),
            "deprecated": bool(operation.get("deprecated")),
            "parameters": _parameters(spec, item, operation),
            "request": _request(spec, operation),
            "responses": _responses(spec, operation),
        }

    return {"revision": registry.revision, "apis": apis}


def _parameters(spec, item: dict, operation: dict) -> dict[str, Any]:
    merged = [*item.get("parameters", []), *operation.get("parameters", [])]
    out = {}
    for parameter in resolve_deep(spec.data, merged):
        if isinstance(parameter, dict) and parameter.get("name"):
            out[f"{parameter.get('in', '?')}:{parameter['name']}"] = {
                "required": bool(parameter.get("required")),
                "type": (parameter.get("schema") or {}).get("type"),
            }
    return out


def _request(spec, operation: dict) -> dict[str, Any]:
    body = operation.get("requestBody")
    if not body:
        return {}
    resolved = resolve_deep(spec.data, body)
    for media in (resolved.get("content") or {}).values():
        return {"required": bool(resolved.get("required")), "fields": _flatten(media.get("schema") or {})}
    return {"required": bool(resolved.get("required")), "fields": {}}


def _responses(spec, operation: dict) -> dict[str, Any]:
    out = {}
    for code, response in resolve_deep(spec.data, operation.get("responses") or {}).items():
        if not isinstance(response, dict):
            continue
        fields: dict[str, Any] = {}
        for media in (response.get("content") or {}).values():
            fields = _flatten(media.get("schema") or {})
            break
        out[str(code)] = {"fields": fields}
    return out


def _flatten(schema: Any, prefix: str = "", depth: int = 0) -> dict[str, Any]:
    """Schema to {dotted.field: {type, required, enum}}."""
    if not isinstance(schema, dict) or depth > MAX_DEPTH:
        return {}

    if schema.get("type") == "array" or "items" in schema:
        return _flatten(schema.get("items") or {}, f"{prefix}[]", depth + 1)

    for combinator in ("allOf", "oneOf", "anyOf"):
        if isinstance(schema.get(combinator), list):
            merged: dict[str, Any] = {}
            for option in schema[combinator]:
                merged.update(_flatten(option, prefix, depth + 1))
            return merged

    properties = schema.get("properties")
    required = set(schema.get("required") or [])
    if not isinstance(properties, dict):
        properties = {}
    if not properties and not required:
        return {}

    out: dict[str, Any] = {}
    # A name listed under `required` with no matching property is a spec bug,
    # but adding one still breaks callers, so it must not go unreported.
    for name in required - set(properties):
        key = f"{prefix}.{name}" if prefix else name
        out[key] = {"type": None, "required": True, "enum": None}

    for name, child in properties.items():
        if not isinstance(child, dict):
            continue
        key = f"{prefix}.{name}" if prefix else name
        out[key] = {
            "type": child.get("type"),
            "required": name in required,
            "enum": sorted(str(value) for value in child["enum"]) if isinstance(child.get("enum"), list) else None,
        }
        out.update(_flatten(child, key, depth + 1))
    return out


# ---------------------------------------------------------------- comparison


def compare(before: dict[str, Any], after: dict[str, Any]) -> list[Change]:
    changes: list[Change] = []
    old_apis, new_apis = before.get("apis", {}), after.get("apis", {})

    for app in sorted(set(old_apis) | set(new_apis)):
        if app not in new_apis:
            changes.append(Change(BREAKING, app, "", "api-removed", "the whole API is gone"))
            continue
        if app not in old_apis:
            count = len(new_apis[app]["operations"])
            changes.append(Change(ADDITIVE, app, "", "api-added", f"new API with {count} operations"))
            continue
        changes += _compare_operations(app, old_apis[app]["operations"], new_apis[app]["operations"])

    order = {BREAKING: 0, ADDITIVE: 1, INFO: 2}
    changes.sort(key=lambda change: (order[change.level], change.app, change.operation))
    return changes


def _compare_operations(app: str, before: dict, after: dict) -> list[Change]:
    changes: list[Change] = []

    for key in sorted(set(before) | set(after)):
        if key not in after:
            changes.append(Change(BREAKING, app, key, "operation-removed", "no longer documented"))
            continue
        if key not in before:
            changes.append(Change(ADDITIVE, app, key, "operation-added", after[key]["summary"] or "new operation"))
            continue

        old, new = before[key], after[key]

        if old["id"] != new["id"]:
            changes.append(
                Change(BREAKING, app, key, "operation-id-changed", f"{old['id']} -> {new['id']}")
            )
        if old["auth"] == "public" and new["auth"] != "public":
            changes.append(Change(BREAKING, app, key, "auth-required", f"now requires {new['auth']}"))
        elif old["auth"] != new["auth"]:
            level = ADDITIVE if new["auth"] == "public" else BREAKING
            changes.append(Change(level, app, key, "auth-changed", f"{old['auth']} -> {new['auth']}"))
        if new["deprecated"] and not old["deprecated"]:
            changes.append(Change(INFO, app, key, "deprecated", "marked deprecated"))
        if old["summary"] != new["summary"]:
            changes.append(Change(INFO, app, key, "summary-changed", new["summary"] or "(none)"))

        changes += _compare_parameters(app, key, old["parameters"], new["parameters"])
        changes += _compare_fields(app, key, "request", old["request"].get("fields", {}), new["request"].get("fields", {}))
        changes += _compare_responses(app, key, old["responses"], new["responses"])

    return changes


def _compare_parameters(app: str, key: str, before: dict, after: dict) -> list[Change]:
    changes = []
    for name in sorted(set(before) | set(after)):
        if name not in after:
            changes.append(Change(BREAKING, app, key, "parameter-removed", name))
        elif name not in before:
            level = BREAKING if after[name]["required"] else ADDITIVE
            suffix = " (required)" if after[name]["required"] else ""
            changes.append(Change(level, app, key, "parameter-added", name + suffix))
        elif not before[name]["required"] and after[name]["required"]:
            changes.append(Change(BREAKING, app, key, "parameter-now-required", name))
        elif before[name]["type"] != after[name]["type"]:
            changes.append(
                Change(BREAKING, app, key, "parameter-type-changed",
                       f"{name}: {before[name]['type']} -> {after[name]['type']}")
            )
    return changes


def _compare_responses(app: str, key: str, before: dict, after: dict) -> list[Change]:
    changes = []
    for code in sorted(set(before) | set(after)):
        if code not in after:
            changes.append(Change(BREAKING, app, key, "response-removed", f"{code} no longer documented"))
        elif code not in before:
            changes.append(Change(ADDITIVE, app, key, "response-added", code))
        else:
            changes += _compare_fields(
                app, key, f"response {code}", before[code]["fields"], after[code]["fields"]
            )
    return changes


def _compare_fields(app: str, key: str, where: str, before: dict, after: dict) -> list[Change]:
    """Field-level comparison.

    Direction matters: a field vanishing from a response breaks readers, while
    a field vanishing from a request breaks writers, so both count as breaking.
    A new *optional* request field is safe; a new required one is not.
    """
    changes = []
    for name in sorted(set(before) | set(after)):
        label = f"{where}.{name}"

        if name not in after:
            changes.append(Change(BREAKING, app, key, "field-removed", label))
            continue
        if name not in before:
            required = after[name]["required"] and where == "request"
            changes.append(
                Change(BREAKING if required else ADDITIVE, app, key, "field-added",
                       label + (" (required)" if required else ""))
            )
            continue

        old, new = before[name], after[name]
        if old["type"] != new["type"]:
            changes.append(
                Change(BREAKING, app, key, "field-type-changed", f"{label}: {old['type']} -> {new['type']}")
            )
        if not old["required"] and new["required"] and where == "request":
            changes.append(Change(BREAKING, app, key, "field-now-required", label))
        elif old["required"] and not new["required"] and where == "request":
            changes.append(Change(ADDITIVE, app, key, "field-now-optional", label))

        old_enum, new_enum = old.get("enum"), new.get("enum")
        if old_enum and new_enum and old_enum != new_enum:
            gone = sorted(set(old_enum) - set(new_enum))
            added = sorted(set(new_enum) - set(old_enum))
            if gone:
                changes.append(Change(BREAKING, app, key, "enum-value-removed", f"{label}: {', '.join(gone)}"))
            if added:
                changes.append(Change(ADDITIVE, app, key, "enum-value-added", f"{label}: {', '.join(added)}"))
    return changes


# ---------------------------------------------------------------- "before" sources


class DiffUnavailable(Exception):
    """The requested baseline could not be read."""


def snapshot_at(registry: Registry, since: str) -> dict[str, Any]:
    """Load the baseline named by `since`: a snapshot file, or a git revision."""
    path = Path(since)
    if path.suffix == ".json" and path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except ValueError as exc:
            raise DiffUnavailable(f"{since} is not a readable snapshot: {exc}") from exc

    return snapshot(_registry_at_revision(registry, since))


def _registry_at_revision(registry: Registry, revision: str) -> Registry:
    """Rebuild the doc set as it was at a git revision."""
    top = _git(registry.root, "rev-parse", "--show-toplevel")
    past = Registry(root=registry.root)

    for name, spec in registry.specs.items():
        try:
            relative = spec.path.relative_to(Path(top))
        except ValueError:
            continue
        try:
            raw = _git_bytes(registry.root, "show", f"{revision}:{relative.as_posix()}")
        except DiffUnavailable:
            continue  # the spec did not exist yet at that revision
        past.specs[name] = parse_spec(name, spec.path, raw)

    if not past.specs:
        raise DiffUnavailable(f"no specs found at revision {revision!r}")
    return past


def default_since(registry: Registry) -> str | None:
    """The previous commit that touched the specs, when this is a git checkout."""
    try:
        return _git(registry.root, "log", "-2", "--format=%H", "--", ".").splitlines()[1]
    except (DiffUnavailable, IndexError):
        return None


def _git(root: Path, *args: str) -> str:
    return _git_bytes(root, *args).decode("utf-8", "replace").strip()


def _git_bytes(root: Path, *args: str) -> bytes:
    try:
        completed = subprocess.run(
            ["git", "-C", str(root), *args], capture_output=True, timeout=15, check=False
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise DiffUnavailable(f"git is not usable here: {exc}") from exc

    if completed.returncode != 0:
        message = completed.stderr.decode("utf-8", "replace").strip()
        raise DiffUnavailable(message or f"git {' '.join(args)} failed")
    return completed.stdout


def summarize(changes: list[Change]) -> dict[str, int]:
    counts = {BREAKING: 0, ADDITIVE: 0, INFO: 0}
    for change in changes:
        counts[change.level] += 1
    return counts


def as_dicts(changes: list[Change]) -> list[dict[str, str]]:
    return [asdict(change) for change in changes]
