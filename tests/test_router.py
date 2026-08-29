import json

import pytest

from apiwarden.config import Config
from apiwarden.http import Request
from apiwarden.index import build_index
from apiwarden.router import Portal, handle


def get(portal, path, **query):
    return handle(Request("GET", path, query=query), portal)


def body(response):
    return json.loads(response.body.decode())


def test_landing_page_lists_every_api(portal, registry):
    response = get(portal, "/")
    assert response.status == 200
    markup = response.body.decode()
    for name in registry.names():
        assert f'href="/{name}/"' in markup


def test_api_page_hosts_the_renderer_with_portal_nav(portal, registry):
    name = registry.names()[0]
    markup = get(portal, f"/{name}/").body.decode()

    assert "<rapi-doc" in markup
    assert 'render-style="read"' in markup
    assert 'show-method-in-nav-bar="as-colored-block"' in markup
    # Our switcher and cross-spec search live in the renderer's own nav slot.
    assert 'slot="nav-logo"' in markup
    assert 'id="api-switch"' in markup
    assert 'id="search-input"' in markup
    assert f'"spec": "/display/{name}.json"' in markup


def test_display_spec_carries_the_vendor_extensions(portal, registry):
    name = next((n for n, s in registry.specs.items() if s.extensions), None)
    if name is None:
        pytest.skip("the sample doc set declares no x-* blocks")

    response = get(portal, f"/display/{name}.json")
    assert response.status == 200
    description = body(response)["info"]["description"]
    for key in registry.specs[name].extensions:
        assert key in description


def test_display_spec_leaves_the_raw_spec_untouched(portal, registry):
    name = next((n for n, s in registry.specs.items() if s.extensions), None)
    if name is None:
        pytest.skip("the sample doc set declares no x-* blocks")

    raw = body(get(portal, f"/openapi/{name}.json"))
    assert raw == registry.specs[name].data
    assert raw["info"]["description"] == registry.specs[name].description


def test_display_spec_applies_configured_servers(registry, sample_root):
    portal = Portal(Config(root=sample_root, servers=["https://api.example.test"]), registry)
    document = body(get(portal, f"/display/{registry.names()[0]}.json"))
    assert document["servers"] == [{"url": "https://api.example.test"}]


def test_display_spec_of_an_unknown_api_is_404(portal):
    assert get(portal, "/display/nope.json").status == 404


def test_the_reference_route_is_gone(portal, registry):
    assert get(portal, f"/{registry.names()[0]}/reference").status == 404


def test_index_json_matches_the_index(portal, registry):
    payload = body(get(portal, "/index.json"))
    assert payload["revision"] == registry.revision
    assert len(payload["operations"]) == len(build_index(registry))


def test_revision_and_health(portal, registry):
    assert body(get(portal, "/health"))["revision"] == registry.revision
    payload = body(get(portal, "/revision.json"))
    assert set(payload["specs"]) == set(registry.names())


def test_llms_endpoints_are_text(portal):
    for path in ("/llms.txt", "/llms-full.txt"):
        response = get(portal, path)
        assert response.status == 200
        assert response.content_type.startswith("text/markdown")
        assert response.body


def test_openapi_json_and_yaml(portal, registry):
    name = registry.names()[0]
    as_json = get(portal, f"/openapi/{name}.json")
    assert as_json.status == 200
    assert body(as_json)["openapi"]
    assert as_json.headers["ETag"].strip('"') == registry.specs[name].sha256

    as_yaml = get(portal, f"/openapi/{name}.yaml")
    assert as_yaml.status == 200
    assert as_yaml.content_type.startswith("application/yaml")


def test_openapi_index_lists_download_urls(portal, registry):
    payload = body(get(portal, "/openapi/"))
    assert set(payload) == set(registry.names())


def test_operation_and_schema_json(portal, registry):
    entry = build_index(registry)[0]
    assert body(get(portal, f"/operation/{entry['id']}.json"))["id"] == entry["id"]
    assert get(portal, "/operation/nope.json").status == 404
    assert get(portal, "/schema/NoSuchSchema.json").status == 404


def test_search_json(portal, registry):
    entry = build_index(registry)[0]
    payload = body(get(portal, "/search.json", q=entry["id"]))
    assert payload["results"][0]["id"] == entry["id"]


def test_static_assets_are_served(portal):
    response = get(portal, "/_static/shell.css")
    assert response.status == 200
    assert response.content_type.startswith("text/css")


def test_static_path_traversal_is_refused(portal):
    assert get(portal, "/_static/../../../../etc/passwd").status == 404


def test_unknown_route_is_404(portal):
    assert get(portal, "/no/such/thing").status == 404


def test_events_is_off_when_not_watching(portal):
    assert get(portal, "/events").status == 404


def test_base_path_is_stripped(registry, sample_root):
    config = Config(root=sample_root, base_path="api-docs")
    portal = Portal(config, registry)
    assert get(portal, "/api-docs/health").status == 200
    assert '"/api-docs/index.json"' in get(portal, "/api-docs/").body.decode().replace("'", '"')


def test_token_gate_blocks_then_allows(registry, sample_root):
    config = Config(root=sample_root, token="s3cret")
    portal = Portal(config, registry)

    denied = get(portal, "/index.json")
    assert denied.status == 401
    assert "Bearer" in denied.headers["WWW-Authenticate"]

    assert get(portal, "/index.json", token="s3cret").status == 200
    assert handle(
        Request("GET", "/index.json", headers={"authorization": "Bearer s3cret"}), portal
    ).status == 200
    assert get(portal, "/index.json", token="wrong").status == 401


def test_only_get_head_and_post_are_accepted(portal):
    assert handle(Request("DELETE", "/index.json"), portal).status == 405
    assert handle(Request("POST", "/index.json"), portal).status == 405


def test_a_broken_spec_still_renders_a_page(spec_copy):
    from apiwarden.loader import load_registry

    target = next(spec_copy.rglob("openapi.yaml"))
    target.write_text("openapi: 3.0.3\n bad: [\n")
    registry = load_registry(spec_copy)
    portal = Portal(Config(root=spec_copy), registry)

    assert get(portal, "/").status == 200
    broken = next(name for name, spec in registry.specs.items() if spec.error)
    assert get(portal, f"/{broken}/").status == 200


def test_sse_stream_emits_the_current_revision(registry, sample_root):
    config = Config(root=sample_root, watch=True)
    portal = Portal(config, registry)

    response = get(portal, "/events")
    assert response.status == 200
    assert response.content_type.startswith("text/event-stream")

    first = next(response.stream)
    assert first.decode() == f"event: revision\ndata: {registry.revision}\n\n"


def test_head_and_cdn_renderer(registry, sample_root):
    from apiwarden.render import CDN_RAPIDOC

    portal = Portal(Config(root=sample_root, renderer="cdn"), registry)
    markup = get(portal, f"/{registry.names()[0]}/").body.decode()
    assert CDN_RAPIDOC in markup
    assert "/_static/vendor/rapidoc.js" not in markup
    assert handle(Request("HEAD", "/health"), portal).status == 200


def test_vendored_renderer_is_served(portal, registry):
    assert "/_static/vendor/rapidoc.js" in get(portal, f"/{registry.names()[0]}/").body.decode()
    assert get(portal, "/_static/vendor/rapidoc.js").status == 200
    assert get(portal, "/_static/rapidoc-extra.css").status == 200


def test_theme_is_pinned_when_configured(registry, sample_root):
    portal = Portal(Config(root=sample_root, theme="dark"), registry)
    markup = get(portal, f"/{registry.names()[0]}/").body.decode()
    assert 'theme="dark"' in markup
    assert "#1e1e1e" in markup


def test_build_portal_starts_and_stops_a_watcher(sample_root):
    from apiwarden.router import build_portal

    portal = build_portal(Config(root=sample_root, watch=True))
    try:
        assert portal.watcher is not None
        assert portal.watcher.revision == portal.registry.revision
    finally:
        portal.watcher.stop()


def test_base_path_does_not_swallow_a_similar_prefix(registry, sample_root):
    portal = Portal(Config(root=sample_root, base_path="api-docs"), registry)
    assert get(portal, "/api-docs/health").status == 200
    assert get(portal, "/api-docs-internal/health").status == 404


def test_display_spec_escapes_pipes_in_extension_tables(registry, sample_root):
    # Extension values become markdown table cells; an unescaped pipe would
    # end the cell early and mangle the table.
    from apiwarden.render import display_spec

    spec = registry.specs[registry.names()[0]]
    spec.data["x-test-block"] = {"pattern": "a|b|c", "multi": "one\ntwo"}
    try:
        description = display_spec(Config(root=sample_root), spec)["info"]["description"]
        row = next(line for line in description.splitlines() if "`pattern`" in line)
        assert row == r"| `pattern` | a\|b\|c |"
        # A newline inside a value would break the row onto two lines.
        assert "one two" in description
    finally:
        spec.data.pop("x-test-block")


def test_display_spec_without_extensions_keeps_the_description(registry, sample_root):
    from apiwarden.render import display_spec

    spec = registry.specs[registry.names()[0]]
    saved = {key: spec.data.pop(key) for key in list(spec.data) if key.startswith("x-")}
    try:
        document = display_spec(Config(root=sample_root), spec)
        assert document["info"]["description"] == spec.description
        assert "Limits & specifications" not in document["info"]["description"]
    finally:
        spec.data.update(saved)


def test_static_assets_revalidate_rather_than_being_pinned(portal):
    # A long max-age here means an open browser keeps running old JavaScript
    # after the package is updated, which is how a live docs portal goes stale.
    response = get(portal, "/_static/shell.js")
    assert response.status == 200
    assert response.headers["Cache-Control"] == "no-cache"
    assert response.headers["ETag"]


def test_static_assets_answer_304_to_a_matching_etag(portal):
    first = get(portal, "/_static/shell.js")
    again = handle(
        Request("GET", "/_static/shell.js", headers={"if-none-match": first.headers["ETag"]}), portal
    )
    assert again.status == 304
    assert again.body == b""


def test_static_assets_ignore_a_cache_busting_query(portal):
    assert get(portal, "/_static/shell.js", v="9.9.9").status == 200


def test_pages_are_not_cached(portal, registry):
    for path in ("/", f"/{registry.names()[0]}/", "/changes"):
        assert get(portal, path).headers["Cache-Control"] == "no-cache"


def test_asset_urls_carry_the_version(portal, registry):
    from apiwarden import __version__

    for path in ("/", f"/{registry.names()[0]}/"):
        markup = get(portal, path).body.decode()
        assert f"shell.js?v={__version__}" in markup
        assert f"shell.css?v={__version__}" in markup
