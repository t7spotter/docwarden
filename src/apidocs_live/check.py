"""Validate the doc set.

Mirrors the rules already configured in redocly.yaml (operation-operationId,
operation-summary, operation-description, no-unresolved-refs) and adds the one
that only matters once several specs are served together: operationIds must be
unique across the whole doc set, because they address operations in the index
and over MCP. No Node, no OpenAPI validator dependency — this is a lint pass,
not a full schema validation.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .loader import Registry, Spec, resolve_ref


@dataclass
class Problem:
    level: str  # "error" or "warning"
    app: str
    message: str

    def __str__(self) -> str:
        return f"{self.level:7} {self.app:12} {self.message}"


def check(registry: Registry) -> list[Problem]:
    problems = [Problem("error", "-", message) for message in registry.errors]
    seen_ids: dict[str, str] = {}

    for name in registry.names():
        spec = registry.specs[name]
        if spec.error:
            problems.append(Problem("error", name, spec.error))
            continue

        if not spec.data.get("openapi"):
            problems.append(Problem("error", name, "missing top-level `openapi` version"))
        if not spec.data.get("info", {}).get("title"):
            problems.append(Problem("warning", name, "missing info.title"))

        for url, method, operation, _shared in spec.operations():
            where = f"{method.upper()} {url}"

            operation_id = operation.get("operationId")
            if not operation_id:
                problems.append(Problem("error", name, f"{where}: no operationId"))
            elif operation_id in seen_ids:
                problems.append(
                    Problem("error", name, f"{where}: operationId {operation_id!r} also in {seen_ids[operation_id]}")
                )
            else:
                seen_ids[operation_id] = name

            if not operation.get("summary"):
                problems.append(Problem("error", name, f"{where}: no summary"))
            if not operation.get("description"):
                problems.append(Problem("warning", name, f"{where}: no description"))
            if not operation.get("responses"):
                problems.append(Problem("error", name, f"{where}: no responses"))

        for ref in sorted(_collect_refs(spec.data)):
            if not ref.startswith("#/"):
                problems.append(Problem("warning", name, f"external $ref not resolved: {ref}"))
                continue
            try:
                resolve_ref(spec.data, ref)
            except (KeyError, IndexError, ValueError):
                problems.append(Problem("error", name, f"unresolved $ref: {ref}"))

    return problems


def _collect_refs(node: Any, found: set[str] | None = None) -> set[str]:
    found = set() if found is None else found
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str):
            found.add(ref)
        for value in node.values():
            _collect_refs(value, found)
    elif isinstance(node, list):
        for item in node:
            _collect_refs(item, found)
    return found


def summarize(problems: list[Problem]) -> tuple[int, int]:
    errors = sum(1 for problem in problems if problem.level == "error")
    warnings = len(problems) - errors
    return errors, warnings
