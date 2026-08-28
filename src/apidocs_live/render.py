"""HTML pages.

The shell owns cross-spec navigation, the operation browser, and the two panels
every OpenAPI renderer throws away: the info.description narrative and the
top-level x-* blocks. Scalar renders the polished per-spec reference inside an
iframe, so its styles never collide with ours.
"""

from __future__ import annotations

import html
import json
from typing import Any

from . import md
from .config import Config
from .index import api_summaries, build_index
from .loader import Registry, Spec

CDN_SCALAR = "https://cdn.jsdelivr.net/npm/@scalar/api-reference@1.67.0/dist/browser/standalone.js"

_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<link rel="stylesheet" href="{static}/shell.css">
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 16 16'><text y='13' font-size='14'>&#128218;</text></svg>">
</head>
<body>
<div class="layout">
<aside class="sidebar">{sidebar}</aside>
<main class="main">{main}</main>
</div>
<script>window.APIDOCS = {config};</script>
<script src="{static}/shell.js"></script>
</body>
</html>
"""


def _e(value: Any) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def page(config: Config, registry: Registry, title: str, main: str, active: str = "") -> str:
    payload = {
        "base": config.base_path,
        "watch": config.watch,
        "revision": registry.revision,
    }
    return _PAGE.format(
        title=_e(title),
        static=_e(config.url("_static")),
        sidebar=_sidebar(config, registry, active),
        main=main,
        config=json.dumps(payload),
    )


def _sidebar(config: Config, registry: Registry, active: str) -> str:
    rows = []
    for api in api_summaries(registry):
        classes = "active" if api["app"] == active else ""
        rows.append(
            f'<li><a class="{classes}" href="{_e(config.url(api["app"] + "/"))}">'
            f'<span>{_e(api["title"])}</span>'
            f'<span class="nav-count">{api["operations"]}</span></a></li>'
        )

    changes_url = _e(config.url("changes"))
    changes_active = "active" if active == "__changes__" else ""

    agent_links = "".join(
        f'<li><a href="{_e(config.url(path))}">{_e(label)}</a></li>'
        for label, path in (("index.json", "index.json"), ("llms.txt", "llms.txt"), ("openapi", "openapi/"))
    )

    return f"""
<h1 class="brand"><a href="{_e(config.url("/"))}">{_e(config.title)}</a></h1>
<p class="brand-sub">rev {_e(registry.revision)}</p>
<div class="search">
  <input id="search-input" type="search" placeholder="Search operations…" autocomplete="off" aria-label="Search operations">
  <ul id="search-results" class="search-results"></ul>
</div>
<p class="nav-label">APIs</p>
<ul class="nav-list">{"".join(rows)}</ul>
<p class="nav-label">Changes</p>
<ul class="nav-list"><li><a class="{changes_active}" href="{changes_url}"><span>What changed</span></a></li></ul>
<p class="nav-label">For agents</p>
<ul class="nav-list">{agent_links}</ul>
"""


# ---------------------------------------------------------------- landing


def landing(config: Config, registry: Registry) -> str:
    apis = api_summaries(registry)
    total = sum(api["operations"] for api in apis)

    cards = []
    for api in apis:
        spec = registry.specs[api["app"]]
        blurb = _e(md.strip(spec.description, 150)) if not spec.error else _e(spec.error)
        cards.append(
            f'<a class="card" href="{_e(config.url(api["app"] + "/"))}">'
            f'<h3>{_e(api["title"])}</h3><p>{blurb}</p>'
            f'<span class="pill">{api["operations"]} operations</span> '
            f'<span class="pill">v{_e(api["version"])}</span></a>'
        )

    errors = "".join(
        f'<div class="notice">{_e(message)}</div>' for message in registry.errors
    )
    broken = "".join(
        f'<div class="notice">{_e(api["app"])}: {_e(api["error"])}</div>'
        for api in apis
        if api["error"]
    )

    main = f"""
<div class="page-head">
  <h1>{_e(config.title)}</h1>
  <p>{len(apis)} APIs · {total} operations · always current, straight from the specs.</p>
</div>
{errors}{broken}
<div class="cards">{"".join(cards)}</div>
{_agent_box(config)}
"""
    return page(config, registry, config.title, main)


def _agent_box(config: Config) -> str:
    mcp_url = config.url("mcp")
    return f"""
<div class="agent-box">
  <h2>Connect an AI agent</h2>
  <p>Point an agent here once and it always reads current docs — nothing to re-share.</p>
  <pre><code>{{
  "mcpServers": {{
    "apidocs": {{ "type": "http", "url": "&lt;origin&gt;{_e(mcp_url)}" }}
  }}
}}</code></pre>
  <p>Or fetch it as plain data:</p>
  <ul class="agent-links">
    <li><a href="{_e(config.url("index.json"))}">index.json</a></li>
    <li><a href="{_e(config.url("llms.txt"))}">llms.txt</a></li>
    <li><a href="{_e(config.url("llms-full.txt"))}">llms-full.txt</a></li>
    <li><a href="{_e(config.url("revision.json"))}">revision.json</a></li>
  </ul>
</div>
"""


# ---------------------------------------------------------------- one API


def api_page(config: Config, registry: Registry, spec: Spec) -> str:
    if spec.error:
        main = f'<div class="page-head"><h1>{_e(spec.name)}</h1></div><div class="notice">{_e(spec.error)}</div>'
        return page(config, registry, spec.name, main, active=spec.name)

    entries = [entry for entry in build_index(registry) if entry["app"] == spec.name]
    servers = [s.get("url", "") for s in spec.data.get("servers", []) if isinstance(s, dict)]

    pills = [f'<span class="pill">v{_e(spec.version)}</span>', f'<span class="pill">{len(entries)} operations</span>']
    pills += [f'<span class="pill pill-accent">{_e(url)}</span>' for url in servers[:2]]

    reference_src = config.url(f"{spec.name}/reference")

    main = f"""
<div class="page-head">
  <h1>{_e(spec.title)}</h1>
  <p>{_e(md.strip(spec.description, 190))}</p>
  <div class="meta-row">{"".join(pills)}</div>
</div>
<div class="tabs" data-tabs role="tablist">
  <button data-tab="operations" role="tab">Operations</button>
  <button data-tab="reference" role="tab">Reference</button>
  <button data-tab="conventions" role="tab">Conventions</button>
  <button data-tab="limits" role="tab">Limits &amp; specs</button>
</div>
<section id="panel-operations" class="tab-panel">{_operations(entries)}</section>
<section id="panel-reference" class="tab-panel" hidden>
  <iframe class="frame" data-src="{_e(reference_src)}" title="{_e(spec.title)} reference"></iframe>
</section>
<section id="panel-conventions" class="tab-panel prose" hidden>{_conventions(spec)}</section>
<section id="panel-limits" class="tab-panel" hidden>{_extensions(spec)}</section>
"""
    return page(config, registry, f"{spec.title} · {config.title}", main, active=spec.name)


def _operations(entries: list[dict[str, Any]]) -> str:
    if not entries:
        return '<p class="empty">This spec declares no operations.</p>'

    rows = []
    for entry in entries:
        method = entry["method"].lower()
        statuses = "".join(
            f'<li class="status status-{str(code)[0]}">{_e(code)} <span style="opacity:.7">{_e(name)}</span></li>'
            for code, name in entry["responses"].items()
        )
        lock = '<span class="lock" title="Requires authentication">&#128274;</span>' if entry["auth"] != "public" else ""
        request = (
            f'<h4>Request body</h4><p><code>{_e(entry["request"])}</code></p>' if entry["request"] else ""
        )
        rows.append(
            f"""<details class="op" data-op="{_e(entry["id"])}" id="op-{_e(entry["id"])}">
<summary class="op-head">
  <span class="method method-{method}">{_e(entry["method"])}</span>
  <span><span class="op-path">{_e(entry["path"])}</span><span class="op-summary">{_e(entry["summary"])}</span></span>
  <span class="op-flags">{lock}<span class="pill">{_e(entry["id"])}</span></span>
</summary>
<div class="op-body">
  <h4>Responses</h4>
  <ul class="status-list">{statuses}</ul>
  {request}
  <h4>Full definition</h4>
  <pre><code data-detail>Loading…</code></pre>
</div>
</details>"""
        )
    return f'<div class="ops" id="operations">{"".join(rows)}</div>'


def _conventions(spec: Spec) -> str:
    parts = [md.render(spec.description)] if spec.description else []

    tags = [tag for tag in spec.data.get("tags", []) if isinstance(tag, dict)]
    if tags:
        parts.append("<h2>Tags</h2>")
        for tag in tags:
            parts.append(f'<h3>{_e(tag.get("name"))}</h3>{md.render(tag.get("description", ""))}')

    schemes = spec.data.get("components", {}).get("securitySchemes") or {}
    if schemes:
        rows = "".join(
            f"<tr><th>{_e(name)}</th><td>{_e(json.dumps(value, ensure_ascii=False))}</td></tr>"
            for name, value in schemes.items()
        )
        parts.append(f'<h2>Security schemes</h2><div class="ext-block"><table class="kv">{rows}</table></div>')

    return "".join(parts) or '<p class="empty">This spec carries no description.</p>'


def _extensions(spec: Spec) -> str:
    extensions = spec.extensions
    if not extensions:
        return '<p class="empty">This spec declares no x-* blocks.</p>'

    blocks = []
    for name, value in extensions.items():
        blocks.append(f'<div class="ext-block"><h3>{_e(name)}</h3>{_ext_table(value)}</div>')

    note = (
        '<p class="empty" style="margin-bottom:16px">Vendor extensions from the spec. '
        "Standard renderers drop these, so they live here.</p>"
    )
    return note + "".join(blocks)


def _ext_table(value: Any) -> str:
    if isinstance(value, dict):
        rows = "".join(
            f"<tr><th>{_e(key)}</th><td>{_e(_scalar(item))}</td></tr>" for key, item in value.items()
        )
        return f'<table class="kv">{rows}</table>'
    if isinstance(value, list):
        rows = "".join(f"<tr><td>{_e(_scalar(item))}</td></tr>" for item in value)
        return f'<table class="kv">{rows}</table>'
    return f'<table class="kv"><tr><td>{_e(_scalar(value))}</td></tr></table>'


def _scalar(value: Any) -> str:
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, indent=2)
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


# ---------------------------------------------------------------- reference frame


def reference_page(config: Config, spec: Spec, servers: list[str]) -> str:
    """A bare page hosting the renderer, loaded in an iframe by api_page."""
    script = CDN_SCALAR if config.renderer == "cdn" else config.url("_static/vendor/scalar.js")
    settings: dict[str, Any] = {
        "url": config.url(f"openapi/{spec.name}.json"),
        "hideDownloadButton": False,
        "darkMode": None,
    }
    if servers:
        settings["servers"] = [{"url": url} for url in servers]

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_e(spec.title)}</title>
<style>body {{ margin: 0; }}</style>
</head>
<body>
<script id="api-reference" data-configuration='{html.escape(json.dumps(settings), quote=True)}'></script>
<script src="{_e(script)}"></script>
</body>
</html>
"""


# ---------------------------------------------------------------- changes


_LEVEL_LABEL = {"breaking": "Breaking", "additive": "Additive", "info": "Note"}


def changes_page(config: Config, registry: Registry, since: str, changes, error: str | None) -> str:
    from .diff import summarize

    counts = summarize(changes)
    pills = "".join(
        f'<span class="pill pill-{level}">{count} {_LEVEL_LABEL[level].lower()}</span>'
        for level, count in counts.items()
        if count
    )

    form = f"""
<form class="since-form" method="get">
  <label for="since">Compare against</label>
  <input id="since" name="since" value="{_e(since)}" placeholder="a git revision, tag, or snapshot.json">
  <button type="submit">Show changes</button>
</form>
"""

    if error:
        body = f'<div class="notice">{_e(error)}</div>'
    elif not changes:
        body = '<p class="empty">Nothing changed against this baseline.</p>'
    else:
        body = _change_groups(config, changes)

    main = f"""
<div class="page-head">
  <h1>Changes</h1>
  <p>What moved between a baseline and the specs as they are right now.</p>
  <div class="meta-row">{pills}</div>
</div>
{form}
{body}
"""
    return page(config, registry, f"Changes · {config.title}", main, active="__changes__")


def _change_groups(config: Config, changes) -> str:
    grouped: dict[tuple[str, str], list] = {}
    for change in changes:
        grouped.setdefault((change.app, change.operation), []).append(change)

    blocks = []
    for (app, operation), items in grouped.items():
        worst = min(items, key=lambda c: ("breaking", "additive", "info").index(c.level)).level
        heading = _e(operation) if operation else "whole API"
        # The operation id is the anchor the API page opens on.
        link = f'<a href="{_e(config.url(app + "/"))}">{_e(app)}</a>'

        rows = "".join(
            f'<tr><td><span class="tag tag-{item.level}">{_LEVEL_LABEL[item.level]}</span></td>'
            f"<td><code>{_e(item.kind)}</code></td><td>{_e(item.detail)}</td></tr>"
            for item in items
        )
        blocks.append(
            f'<div class="ext-block change-{worst}">'
            f"<h3>{heading} <span class=\"change-app\">{link}</span></h3>"
            f'<table class="kv change-table">{rows}</table></div>'
        )
    return "".join(blocks)

