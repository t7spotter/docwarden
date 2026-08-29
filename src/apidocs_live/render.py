"""HTML pages.

The shell owns cross-spec navigation, the operation browser, and the two panels
every OpenAPI renderer throws away: the info.description narrative and the
top-level x-* blocks. Scalar renders the polished per-spec reference inside an
iframe, so its styles never collide with ours.
"""

from __future__ import annotations

import copy
import html
import json
from typing import Any

from . import md
from .config import Config
from .index import api_summaries, build_index
from .loader import Registry, Spec

CDN_RAPIDOC = "https://cdn.jsdelivr.net/npm/rapidoc@9.3.8/dist/rapidoc-min.js"

# The editor greys, so the portal reads like the panel it grew out of. RapiDoc
# takes its palette as attributes rather than CSS variables, so the same values
# live here and in shell.css.
THEMES = {
    "light": {
        "bg": "#ffffff",
        "text": "#1a1d21",
        "nav_bg": "#f3f3f3",
        "nav_text": "#3b4048",
        "nav_hover": "#e4e6e9",
        "accent": "#0066b8",
    },
    "dark": {
        "bg": "#1e1e1e",
        "text": "#d4d4d4",
        "nav_bg": "#252526",
        "nav_text": "#bbbbbb",
        "nav_hover": "#37373d",
        "accent": "#4daafc",
    },
}

# Tahoma and Noto Sans are here for their broad right-to-left coverage.
FONT_STACK = (
    '-apple-system, BlinkMacSystemFont, "Segoe UI", "Noto Sans", Tahoma, '
    'Roboto, "Helvetica Neue", Arial, sans-serif'
)

_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<link rel="stylesheet" href="{shell_css}">
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 16 16'><text y='13' font-size='14'>&#128218;</text></svg>">
</head>
<body>
<header class="topbar">{topbar}</header>
<main class="main">{main}</main>
<script>window.APIDOCS = {config};</script>
<script src="{shell_js}"></script>
</body>
</html>
"""


def _e(value: Any) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def page(config: Config, registry: Registry, title: str, main: str, active: str = "") -> str:
    """The frame for pages RapiDoc does not render: the landing and changes."""
    payload = {
        "base": config.base_path,
        "watch": config.watch,
        "revision": registry.revision,
        "theme": config.theme,
    }
    return _PAGE.format(
        title=_e(title),
        shell_css=_e(config.asset("shell.css")),
        shell_js=_e(config.asset("shell.js")),
        topbar=_topbar(config, registry, active),
        main=main,
        config=json.dumps(payload),
    )


def _topbar(config: Config, registry: Registry, active: str) -> str:
    options = "".join(
        f'<option value="{_e(config.url(api["app"] + "/"))}"'
        f'{" selected" if api["app"] == active else ""}>{_e(api["title"])}</option>'
        for api in api_summaries(registry)
    )
    changes = "active" if active == "__changes__" else ""

    return f"""
<a class="portal-title" href="{_e(config.url("/"))}">{_e(config.title)}</a>
<select class="portal-switch" id="api-switch" aria-label="Choose an API">
  <option value="">Choose an API…</option>{options}
</select>
<div class="topbar-search">
  <input class="portal-search" id="search-input" type="search" placeholder="Search all APIs…"
         autocomplete="off" aria-label="Search every API">
  <ul class="portal-results" id="search-results"></ul>
</div>
<nav class="topbar-links">
  <a class="{changes}" href="{_e(config.url("changes"))}">Changes</a>
  <a href="{_e(config.url("index.json"))}">index.json</a>
  <a href="{_e(config.url("llms.txt"))}">llms.txt</a>
</nav>
<span class="topbar-rev">rev {_e(registry.revision)}</span>
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


# RapiDoc, configured the way the VS Code OpenAPI viewer configures it: the
# read layout, a nav of colour-coded methods showing URL paths, server
# selection and try-it enabled. Everything below that is ours.
_RAPIDOC = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<!-- shell.css styles the nav-logo slot: slotted content stays in the light DOM
     and is styled by this document, not by the shadow root. -->
<link rel="stylesheet" href="{shell_css}">
<link rel="stylesheet" href="{extra_css}">
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 16 16'><text y='13' font-size='14'>&#128218;</text></svg>">
<style>
  html, body {{ margin: 0; height: 100%; background: {bg}; }}
  rapi-doc {{ width: 100%; height: 100%; }}
</style>
</head>
<body>
<rapi-doc
  id="docs"
  css-file="rapidoc-extra.css"
  theme="{theme}"
  bg-color="{bg}"
  text-color="{text}"
  nav-bg-color="{nav_bg}"
  nav-text-color="{nav_text}"
  nav-hover-bg-color="{nav_hover}"
  nav-accent-color="{accent}"
  primary-color="{accent}"
  render-style="read"
  show-header="false"
  show-info="true"
  show-components="true"
  allow-authentication="true"
  allow-try="true"
  allow-search="true"
  allow-advanced-search="true"
  allow-server-selection="true"
  allow-spec-url-load="false"
  allow-spec-file-load="false"
  allow-api-list-style-selection="true"
  show-method-in-nav-bar="as-colored-block"
  use-path-in-nav-bar="true"
  info-description-headings-in-navbar="true"
  nav-item-spacing="relaxed"
  schema-style="table"
  default-schema-tab="schema"
  regular-font="{font}"
  update-route="false"
>
  <div slot="nav-logo" class="portal-nav">{nav}</div>
</rapi-doc>
<script>window.APIDOCS = {config};</script>
<script src="{script}"></script>
<script src="{shell_js}"></script>
</body>
</html>
"""


def api_page(config: Config, registry: Registry, spec: Spec) -> str:
    """One API, rendered by RapiDoc, with our portal navigation in its nav."""
    if spec.error:
        main = f'<div class="page-head"><h1>{_e(spec.name)}</h1></div><div class="notice">{_e(spec.error)}</div>'
        return page(config, registry, spec.name, main, active=spec.name)

    # "auto" renders light and lets shell.js repaint from prefers-color-scheme
    # on load, so there is no flash for a reader who pinned a theme.
    theme_name = "dark" if config.theme == "dark" else "light"
    palette = dict(THEMES[theme_name], theme=theme_name)
    script = CDN_RAPIDOC if config.renderer == "cdn" else config.asset("vendor/rapidoc.js")

    payload = {
        "base": config.base_path,
        "watch": config.watch,
        "revision": registry.revision,
        "app": spec.name,
        "spec": config.url(f"display/{spec.name}.json"),
        "theme": config.theme,
    }

    return _RAPIDOC.format(
        title=_e(f"{spec.title} · {config.title}"),
        shell_css=_e(config.asset("shell.css")),
        shell_js=_e(config.asset("shell.js")),
        extra_css=_e(config.asset("rapidoc-extra.css")),
        script=_e(script),
        nav=_portal_nav(config, registry, spec.name),
        config=json.dumps(payload),
        font=_e(FONT_STACK),
        **{key: _e(value) for key, value in palette.items()},
    )


def _portal_nav(config: Config, registry: Registry, active: str) -> str:
    """What goes in RapiDoc's nav-logo slot: which API, and search across all."""
    options = "".join(
        f'<option value="{_e(config.url(api["app"] + "/"))}"'
        f'{" selected" if api["app"] == active else ""}>{_e(api["title"])}</option>'
        for api in api_summaries(registry)
    )

    return f"""
<a class="portal-title" href="{_e(config.url("/"))}">{_e(config.title)}</a>
<select class="portal-switch" id="api-switch" aria-label="Choose an API">{options}</select>
<input class="portal-search" id="search-input" type="search" placeholder="Search all APIs…"
       autocomplete="off" aria-label="Search every API">
<ul class="portal-results" id="search-results"></ul>
<div class="portal-links">
  <a href="{_e(config.url("changes"))}">Changes</a>
  <a href="{_e(config.url("index.json"))}">index.json</a>
  <a href="{_e(config.url("llms.txt"))}">llms.txt</a>
</div>
"""


# ---------------------------------------------------------------- display spec


def display_spec(config: Config, spec: Spec) -> dict[str, Any]:
    """The copy RapiDoc renders.

    Two departures from the file on disk, both presentational: the top-level
    x-* blocks are appended to info.description so they are visible (and, with
    info-description-headings-in-navbar, navigable) instead of being dropped,
    and the configured servers win over the ones in the file. The raw spec
    stays untouched at /openapi/<name>.json for downloads and agents.
    """
    document = copy.deepcopy(spec.data)

    extensions = spec.extensions
    if extensions:
        info = document.setdefault("info", {})
        info["description"] = (info.get("description") or "").rstrip() + _extensions_markdown(extensions)

    if config.servers:
        document["servers"] = [{"url": url} for url in config.servers]

    return document


def _extensions_markdown(extensions: dict[str, Any]) -> str:
    lines = ["", "", "## Limits & specifications", ""]
    for name, value in extensions.items():
        lines.append(f"### {name}")
        lines.append("")
        if isinstance(value, dict):
            lines += ["| | |", "|---|---|"]
            for key, item in value.items():
                lines.append(f"| `{key}` | {_md_cell(item)} |")
        elif isinstance(value, list):
            lines += [f"- {_md_cell(item)}" for item in value]
        else:
            lines.append(_md_cell(value))
        lines.append("")
    return "\n".join(lines)


def _md_cell(value: Any) -> str:
    if isinstance(value, (dict, list)):
        text = json.dumps(value, ensure_ascii=False)
    elif isinstance(value, bool):
        text = "true" if value else "false"
    else:
        text = str(value)
    # Table cells are one line, and a pipe would end the cell early.
    return text.replace("|", "\\|").replace("\n", " ").strip()


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

