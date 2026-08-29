"""Write the portal out as static files.

For teams that would rather publish from CI than run a process. Everything the
browser needs is emitted, including the machine endpoints, so an agent can read
a published build the same way it reads a live one. The only thing a static
build cannot offer is the MCP endpoint, which needs to answer POSTs.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import yaml

from . import agent, render
from .config import Config
from .index import build_index, conventions, operation_detail, schema_detail
from .loader import Registry, load_registry
from .router import STATIC_DIR


def build_static(config: Config, output: Path) -> int:
    output = Path(output)
    registry = load_registry(config.root, config.sources or None)

    # A static build has no watcher to talk to; make sure the pages know it.
    config.watch = False

    if output.exists():
        shutil.rmtree(output)
    output.mkdir(parents=True)

    written = 0
    written += _write(output / "index.html", render.landing(config, registry))
    written += _write(output / "index.json", _json(agent.index_payload(registry, config)))
    written += _write(output / "revision.json", _json(agent.revision_payload(registry)))
    written += _write(output / "llms.txt", agent.llms_txt(registry, config))
    written += _write(output / "llms-full.txt", agent.llms_full_txt(registry, config))
    written += _write(output / "health.json", _json({"status": "ok", "revision": registry.revision}))

    for name in registry.names():
        spec = registry.specs[name]
        written += _write(output / name / "index.html", render.api_page(config, registry, spec))
        if spec.error:
            continue
        written += _write(output / "display" / f"{name}.json", _json(render.display_spec(config, spec)))
        written += _write(output / "openapi" / f"{name}.json", _json(spec.data))
        written += _write(
            output / "openapi" / f"{name}.yaml",
            yaml.safe_dump(spec.data, allow_unicode=True, sort_keys=False),
        )
        written += _write(output / "conventions" / f"{name}.json", _json(conventions(registry, name)))

    written += _write_details(output, registry)
    written += _copy_static(output / "_static")
    return written


def _write_details(output: Path, registry: Registry) -> int:
    """One JSON per operation and per schema — what the pages fetch on demand."""
    written = 0
    for entry in build_index(registry):
        detail = operation_detail(registry, entry["id"])
        if detail:
            written += _write(output / "operation" / f"{entry['id']}.json", _json(detail))

    for name in registry.names():
        spec = registry.specs[name]
        if spec.error:
            continue
        for schema_name in (spec.data.get("components", {}).get("schemas") or {}):
            found = schema_detail(registry, schema_name, name)
            if found:
                written += _write(output / "schema" / f"{schema_name}.json", _json(found))
    return written


def _copy_static(target: Path) -> int:
    shutil.copytree(STATIC_DIR, target)
    return sum(1 for path in target.rglob("*") if path.is_file())


def _write(path: Path, content: str) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return 1


def _json(payload) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2)
