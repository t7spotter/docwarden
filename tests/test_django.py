"""The Django plugin, exercised through a throwaway project.

Configured here rather than in a real project so the test proves the two-line
integration from the README is genuinely all that is required.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

django = pytest.importorskip("django")

SAMPLE_ROOT = Path(__file__).resolve().parent / "fixtures" / "sample-api"


@pytest.fixture(scope="module", autouse=True)
def django_project():
    from django.conf import settings

    if not settings.configured:
        settings.configure(
            DEBUG=False,
            SECRET_KEY="test-only",
            ROOT_URLCONF="tests.test_django",
            ALLOWED_HOSTS=["testserver"],
            INSTALLED_APPS=["apiwarden"],
            APIWARDEN={"root": SAMPLE_ROOT, "title": "Mounted portal"},
            DATABASES={},
            USE_TZ=True,
        )
    django.setup()
    yield

    from apiwarden.views import reset_portal

    reset_portal()


# ROOT_URLCONF points here: the mount is exactly what the README documents.
from django.urls import include, path  # noqa: E402

urlpatterns = [path("api-docs/", include("apiwarden.urls"))]


@pytest.fixture
def client():
    from django.test import Client

    return Client()


def test_landing_page_is_mounted(client):
    response = client.get("/api-docs/")
    assert response.status_code == 200
    assert b"Mounted portal" in response.content


def test_links_are_written_under_the_mount_point(client):
    markup = client.get("/api-docs/").content.decode()
    assert '"/api-docs/index.json"' in markup.replace("'", '"')
    assert 'href="/index.json"' not in markup


def test_machine_endpoints_work_when_mounted(client):
    payload = json.loads(client.get("/api-docs/index.json").content)
    assert payload["operations"]

    first = payload["apis"][0]["app"]
    assert client.get(f"/api-docs/openapi/{first}.json").status_code == 200
    assert client.get(f"/api-docs/{first}/").status_code == 200
    assert client.get("/api-docs/llms.txt").status_code == 200


def test_static_assets_are_served_without_collectstatic(client):
    response = client.get("/api-docs/_static/shell.css")
    assert response.status_code == 200
    assert response["Content-Type"].startswith("text/css")


def test_mcp_post_is_csrf_exempt(client):
    from django.test import Client

    enforcing = Client(enforce_csrf_checks=True)
    response = enforcing.post(
        "/api-docs/mcp",
        data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}),
        content_type="application/json",
    )
    assert response.status_code == 200
    assert json.loads(response.content)["result"]["tools"]


def test_unknown_path_under_the_mount_is_404(client):
    assert client.get("/api-docs/no/such/page").status_code == 404


def test_watch_defaults_to_debug():
    from django.conf import settings
    from django.test import override_settings

    from apiwarden.views import load_config

    assert load_config().watch is False
    with override_settings(DEBUG=True, APIWARDEN={"root": SAMPLE_ROOT}):
        assert load_config().watch is True
    assert settings.DEBUG is False


def test_token_setting_gates_the_mounted_portal(client):
    from django.test import override_settings

    from apiwarden.views import reset_portal

    with override_settings(APIWARDEN={"root": SAMPLE_ROOT, "token": "hunter2"}):
        reset_portal()
        assert client.get("/api-docs/index.json").status_code == 401
        assert client.get("/api-docs/index.json?token=hunter2").status_code == 200
    reset_portal()


def test_unknown_setting_is_rejected_loudly():
    from django.test import override_settings

    from apiwarden.views import load_config

    with override_settings(APIWARDEN={"root": SAMPLE_ROOT, "colour": "blue"}):
        with pytest.raises(ValueError, match="colour"):
            load_config()
