import json

import pytest

from apidocs_live.config import Config
from apidocs_live.http import Request
from apidocs_live.index import build_index
from apidocs_live.router import Portal, handle


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


def test_api_page_and_reference_frame(portal, registry):
    name = registry.names()[0]
    assert get(portal, f"/{name}/").status == 200
    reference = get(portal, f"/{name}/reference")
    assert reference.status == 200
    assert "api-reference" in reference.body.decode()


def test_api_page_surfaces_vendor_extensions(portal, registry):
    name = next((n for n, s in registry.specs.items() if s.extensions), None)
    if name is None:
        pytest.skip("the sample doc set declares no x-* blocks")
    markup = get(portal, f"/{name}/").body.decode()
    for key in registry.specs[name].extensions:
        assert key in markup


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
    from apidocs_live.loader import load_registry

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
    from apidocs_live.render import CDN_SCALAR

    portal = Portal(Config(root=sample_root, renderer="cdn"), registry)
    name = registry.names()[0]
    assert CDN_SCALAR in get(portal, f"/{name}/reference").body.decode()
    assert handle(Request("HEAD", "/health"), portal).status == 200


def test_build_portal_starts_and_stops_a_watcher(sample_root):
    from apidocs_live.router import build_portal

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
