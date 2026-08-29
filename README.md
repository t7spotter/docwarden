# docwarden

Point it at a directory of OpenAPI specs and it serves them as live
documentation — a browsable site for people, and an MCP server plus plain JSON
for AI agents. Nothing to export, nothing to re-share: the specs are read from
disk on every request, so whatever is on the branch is what the docs say.

Runs standalone, or mounts into an existing Django project in two lines.

```
pip install docwarden
docwarden serve ./api-docs
```

## Why

API documentation that lives in files has to be sent to whoever needs it, again
after every change. A frontend team ends up working from whichever copy they
were last given, and no one can tell what moved. Serving the specs instead of
sending them makes that whole problem go away: one URL, always current, for
people and for the agents they work with.

## The two audiences

**People** get a portal built on [RapiDoc](https://github.com/rapi-doc/RapiDoc):
a nav of colour-coded methods and URL paths, the read layout, server selection
and try-it. On top of that it adds an API switcher and search across every
spec, and it rescues the two things OpenAPI renderers normally drop — the
`info.description` narrative, which becomes navigable headings, and the
top-level `x-*` blocks where teams record rate limits, TTLs and everything else
that does not fit the schema, which become tables in the overview.

An edit to a spec reaches an open page in about a second, swapped in through
the renderer rather than by reloading, so nobody loses their place.

**Agents** get the same content as data:

| Endpoint | What it is |
|---|---|
| `POST /mcp` | MCP server — `list_apis`, `search_operations`, `get_operation`, `get_schema`, `get_conventions`, `get_spec` |
| `GET /index.json` | Every operation across every spec, one compact document |
| `GET /llms.txt`, `/llms-full.txt` | The doc set as plain text |
| `GET /openapi/<name>.json`, `.yaml` | The raw specs, byte-faithful |
| `GET /display/<name>.json` | The renderer's copy: `x-*` folded into the overview |
| `GET /operation/<id>.json` | One operation, `$ref`s inlined |
| `GET /revision.json` | Content hashes — poll to tell whether anything changed |
| `GET /changes?since=…` | What moved since a baseline, breaking changes called out |

Point an agent at the MCP endpoint once and it never reads a stale spec again:

```json
{
  "mcpServers": {
    "docwarden": { "type": "http", "url": "https://your-host/api-docs/mcp" }
  }
}
```

Locally, over stdio instead:

```
docwarden mcp ./api-docs
```

## Standalone

```
docwarden serve ./api-docs              # http://127.0.0.1:8080, reloads as you edit
docwarden check ./api-docs              # lint: operationIds, summaries, unresolved $refs
docwarden build ./api-docs -o dist/     # self-contained static copy, for CI publishing
docwarden changes ./api-docs --since v1.4.0
docwarden snapshot ./api-docs -o baseline.json
```

`serve` watches the spec files and pushes a reload to open browsers, so editing
a spec updates the page without a restart.

## What changed

Being always current is only half the problem — the other half is knowing what
moved. `changes` compares the specs against a baseline and sorts the result by
what it does to a caller:

```
$ docwarden changes ./api-docs --since v1.4.0
breaking  accounts POST /v1/accounts/otp/     field-added: request.device_id (required)
breaking  accounts POST /v1/accounts/otp/     response-removed: 429 no longer documented
info      accounts POST /v1/accounts/otp/     summary-changed: Send a one-time login code.
since v1.4.0: 2 breaking, 0 additive, 1 informational
```

**Breaking** is an operation or field disappearing, a new required field or
parameter, a type change, an enum value being removed, or authentication being
added. **Additive** is anything a current caller can ignore. The baseline is a
git revision of the spec directory, or a snapshot file written earlier with
`docwarden snapshot`. `--fail-on-breaking` exits non-zero, so CI can gate on it.

The same comparison is on the `/changes` page and the `list_changes` MCP tool,
so an agent can answer "will this break my client?" directly.

## In a Django project

```python
# settings.py
INSTALLED_APPS += ["docwarden"]

DOCWARDEN = {
    "root": BASE_DIR / "api-docs",
    "title": "Platform API",
    "servers": ["https://api.example.com"],   # what try-it should call
    "watch": DEBUG,
    "token": os.environ.get("DOCWARDEN_TOKEN"), # omit for a public portal
}

# urls.py
urlpatterns += [path("api-docs/", include("docwarden.urls"))]
```

That is the whole integration. The portal serves its own assets, so there is no
`collectstatic` step, and it adds no dependency beyond PyYAML. It coexists with
whatever documentation the project already has — it reads spec files and does
not touch your URLs, views, or schema generation.

Two production notes:

- `watch` defaults to `DEBUG`. Live reload holds an SSE connection open, which
  pins a sync worker; in production, agents poll `revision.json` instead.
- "Try it" calls the API host from the browser, so that host needs to allow the
  docs origin in its CORS configuration.

## Configuration

Settings are the same for both, via `DOCWARDEN`, an `docwarden.toml` beside the
specs, or CLI flags.

| Key | Default | Meaning |
|---|---|---|
| `root` | `api-docs` | Directory holding the specs |
| `title` | derived | Portal title |
| `servers` | spec's own | Base URLs offered for try-it |
| `renderer` | `vendor` | `vendor` serves the bundled RapiDoc, `cdn` loads it remotely |
| `theme` | `auto` | `auto` follows the reader's OS setting; `light`/`dark` pin it |
| `watch` | `False` | Reload when the spec files change |
| `token` | `None` | Require a shared token on every request (also `DOCWARDEN_TOKEN`) |
| `sources` | discovered | Explicit `{name: path}` map |

## How specs are discovered

1. An explicit `sources` map, if you set one.
2. Otherwise a `redocly.yaml` next to the specs — its `apis:` entries are used
   as-is, keeping the names and ordering an existing doc set already has.
3. Otherwise every `openapi.yaml` / `.yml` / `.json` below `root`, each named
   after its parent directory.

Specs are expected to be self-contained, using local `#/components/...` `$ref`s.

## Development

```
python scripts/vendor_assets.py   # download the renderer bundle
pip install -e ".[dev]"
pytest

# The browser tests are opt-in; they catch things a server-side test cannot,
# such as the renderer silently failing to load.
pip install -e ".[dev,browser]" && playwright install chromium
pytest tests/test_browser.py
```

`api-docs/` in this repository is a sample doc set used by the tests and by
`docwarden serve` when you try things out.

## License

MIT
