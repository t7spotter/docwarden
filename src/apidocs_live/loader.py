"""Discover, parse and reload OpenAPI specs from a directory."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

SPEC_FILENAMES = ("openapi.yaml", "openapi.yml", "openapi.json")
HTTP_METHODS = ("get", "put", "post", "delete", "options", "head", "patch", "trace")


@dataclass
class Spec:
    """One parsed OpenAPI document."""

    name: str
    path: Path
    data: dict[str, Any]
    sha256: str
    mtime: float
    # Parse error, if the file could not be read. data is {} when set.
    error: str | None = None

    @property
    def title(self) -> str:
        return self.data.get("info", {}).get("title", self.name)

    @property
    def version(self) -> str:
        return str(self.data.get("info", {}).get("version", ""))

    @property
    def description(self) -> str:
        return self.data.get("info", {}).get("description", "")

    @property
    def extensions(self) -> dict[str, Any]:
        """Top-level x-* blocks. Renderers drop these; we surface them."""
        return {k: v for k, v in self.data.items() if k.startswith("x-")}

    def operations(self):
        """Yield (path, method, operation) for every operation in the spec."""
        for url, item in (self.data.get("paths") or {}).items():
            if not isinstance(item, dict):
                continue
            shared = item.get("parameters", [])
            for method in HTTP_METHODS:
                operation = item.get(method)
                if isinstance(operation, dict):
                    yield url, method, operation, shared


@dataclass
class Registry:
    """The loaded doc set, reloaded from disk when files change."""

    root: Path
    specs: dict[str, Spec] = field(default_factory=dict)
    # Errors from discovery itself (missing root, unreadable registry file).
    errors: list[str] = field(default_factory=list)

    @property
    def revision(self) -> str:
        """Hash over every spec's content; changes iff any spec changes."""
        joined = "".join(f"{name}:{self.specs[name].sha256}" for name in sorted(self.specs))
        return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:12]

    def get(self, name: str) -> Spec | None:
        return self.specs.get(name)

    def names(self) -> list[str]:
        return list(self.specs)


def discover_specs(root: Path, sources: dict[str, str] | None = None) -> list[tuple[str, Path]]:
    """Find the spec files under root.

    Explicit `sources` wins. Otherwise a redocly.yaml `apis:` map is used when
    present, so an existing Redocly doc set keeps its names and ordering. With
    neither, every openapi.{yaml,yml,json} below root is picked up and named
    after its parent directory.
    """
    root = Path(root)
    if sources:
        return [(name, (root / path).resolve()) for name, path in sources.items()]

    registry_file = root / "redocly.yaml"
    if registry_file.exists():
        found = _read_redocly(registry_file, root)
        if found:
            return found

    return _glob_specs(root)


def _read_redocly(registry_file: Path, root: Path) -> list[tuple[str, Path]]:
    try:
        data = yaml.safe_load(registry_file.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return []

    found = []
    for name, entry in (data.get("apis") or {}).items():
        target = entry.get("root") if isinstance(entry, dict) else entry
        if isinstance(target, str):
            # These become URL segments, so they need the same slug rules as
            # discovered names. Ordinary names pass through unchanged.
            found.append((_slugify(str(name)), (root / target).resolve()))
    return found


def _glob_specs(root: Path) -> list[tuple[str, Path]]:
    seen: dict[Path, None] = {}
    for filename in SPEC_FILENAMES:
        for path in sorted(root.rglob(filename)):
            seen.setdefault(path.resolve(), None)

    found = []
    used: set[str] = set()
    for path in seen:
        name = path.parent.name if path.parent != root else path.stem
        name = _slugify(name)
        # Two apps could share a directory name under different parents.
        candidate, counter = name, 2
        while candidate in used:
            candidate, counter = f"{name}-{counter}", counter + 1
        used.add(candidate)
        found.append((candidate, path))
    return found


def _slugify(value: str) -> str:
    cleaned = "".join(char if char.isalnum() or char in "-_" else "-" for char in value.lower())
    return cleaned.strip("-") or "api"


def load_spec(name: str, path: Path) -> Spec:
    """Parse one spec file. Parse failures become a Spec carrying `error`."""
    try:
        raw = path.read_bytes()
    except OSError as exc:
        return Spec(name, path, {}, "", 0.0, error=f"cannot read {path}: {exc}")

    try:
        mtime = path.stat().st_mtime
    except OSError:
        mtime = 0.0

    return parse_spec(name, path, raw, mtime)


def parse_spec(name: str, path: Path, raw: bytes, mtime: float = 0.0) -> Spec:
    """Parse spec bytes. Used for files on disk and for past git revisions."""
    digest = hashlib.sha256(raw).hexdigest()[:12]

    try:
        text = raw.decode("utf-8")
        data = json.loads(text) if path.suffix == ".json" else yaml.safe_load(text)
    except (UnicodeDecodeError, ValueError, yaml.YAMLError) as exc:
        return Spec(name, path, {}, digest, mtime, error=f"{path.name}: {exc}")

    if not isinstance(data, dict):
        return Spec(name, path, {}, digest, mtime, error=f"{path.name}: not a mapping")

    return Spec(name, path, data, digest, mtime)


def load_registry(root: Path, sources: dict[str, str] | None = None) -> Registry:
    """Load every spec under root."""
    root = Path(root)
    registry = Registry(root=root)

    if not root.exists():
        registry.errors.append(f"spec directory not found: {root}")
        return registry

    found = discover_specs(root, sources)
    if not found:
        registry.errors.append(f"no OpenAPI specs found under {root}")
        return registry

    for name, path in found:
        registry.specs[name] = load_spec(name, path)
    return registry


def reload_if_changed(registry: Registry, sources: dict[str, str] | None = None) -> bool:
    """Re-read specs whose file changed. Returns True when anything changed.

    Cheap enough to call on every request: it stats the known files, and only
    re-globs when a file has appeared or disappeared.
    """
    found = discover_specs(registry.root, sources)
    if [name for name, _ in found] != registry.names():
        fresh = load_registry(registry.root, sources)
        registry.specs = fresh.specs
        registry.errors = fresh.errors
        return True

    changed = False
    for name, path in found:
        current = registry.specs.get(name)
        try:
            mtime = path.stat().st_mtime
        except OSError:
            mtime = 0.0
        if current is None or current.mtime != mtime:
            reloaded = load_spec(name, path)
            # mtime moves whenever the file is touched; only report a change
            # when the bytes actually differ.
            if current is None or reloaded.sha256 != current.sha256:
                changed = True
            registry.specs[name] = reloaded
    return changed


def resolve_ref(spec_data: dict[str, Any], ref: str) -> Any:
    """Resolve a local JSON pointer such as '#/components/schemas/Profile'."""
    if not ref.startswith("#/"):
        raise KeyError(f"only local $refs are supported, got {ref!r}")

    node: Any = spec_data
    for part in ref[2:].split("/"):
        part = part.replace("~1", "/").replace("~0", "~")
        if isinstance(node, list):
            node = node[int(part)]
        else:
            node = node[part]
    return node


def resolve_deep(spec_data: dict[str, Any], node: Any, _seen: frozenset[str] = frozenset()) -> Any:
    """Inline every local $ref in `node`, guarding against reference cycles.

    A cycle is left as {"$ref": ...} rather than recursed into, so a
    self-referential schema still returns something an agent can read.
    """
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str):
            if ref in _seen:
                return {"$ref": ref, "x-circular": True}
            try:
                target = resolve_ref(spec_data, ref)
            except (KeyError, IndexError, ValueError):
                return {"$ref": ref, "x-unresolved": True}
            resolved = resolve_deep(spec_data, target, _seen | {ref})
            # Keep sibling keys (description, example) alongside the target.
            siblings = {k: v for k, v in node.items() if k != "$ref"}
            if siblings and isinstance(resolved, dict):
                return {**resolved, **resolve_deep(spec_data, siblings, _seen | {ref})}
            return resolved
        return {key: resolve_deep(spec_data, value, _seen) for key, value in node.items()}

    if isinstance(node, list):
        return [resolve_deep(spec_data, item, _seen) for item in node]

    return node


def ref_name(node: Any) -> str | None:
    """Short component name for a $ref, for the compact operation index."""
    if isinstance(node, dict) and isinstance(node.get("$ref"), str):
        return node["$ref"].rsplit("/", 1)[-1]
    return None
