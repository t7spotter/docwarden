"""End-to-end checks in a real browser.

The renderer is a web component that fetches its own spec, so the things most
likely to break — does it actually render, does a live edit reach the open
page, does the portal navigation work — are invisible to a server-side test.
One of these caught a name collision between two `load` functions that made
the renderer silently never load.

Skipped unless playwright and its browser are installed:

    pip install -e ".[browser]" && playwright install chromium
"""

from __future__ import annotations

import shutil
import socket
import threading
import time
from pathlib import Path

import pytest

sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright

from apiwarden.config import Config
from apiwarden.router import build_portal
from apiwarden.server import serve

LAUNCH = ["--no-sandbox", "--disable-dev-shm-usage"]


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as playwright:
        try:
            launched = playwright.chromium.launch(args=LAUNCH)
        except Exception as exc:  # the browser binary is not installed
            pytest.skip(f"chromium unavailable: {exc}")
        yield launched
        launched.close()


@pytest.fixture(scope="module")
def live(tmp_path_factory):
    """A server over a writable copy, so a test may edit the specs."""
    root = tmp_path_factory.mktemp("live") / "sample-api"
    shutil.copytree(Path(__file__).resolve().parent / "fixtures" / "sample-api", root)

    port = _free_port()
    portal = build_portal(Config(root=root, title="Browser test", watch=True))
    threading.Thread(target=serve, args=(portal, "127.0.0.1", port), daemon=True).start()
    time.sleep(0.5)

    yield f"http://127.0.0.1:{port}", root, portal
    if portal.watcher:
        portal.watcher.stop()


@pytest.fixture
def page(browser, live):
    created = browser.new_page(viewport={"width": 1400, "height": 900})
    problems: list[str] = []
    created.on("pageerror", lambda error: problems.append(str(error)))
    yield created
    created.close()
    assert not problems, f"javascript errors on the page: {problems}"


def _shadow_text(page) -> str:
    return page.evaluate("document.getElementById('docs').shadowRoot.textContent") or ""


def _first_app(portal) -> str:
    return portal.registry.names()[0]


def test_the_renderer_actually_renders(page, live):
    base, _, portal = live
    page.goto(f"{base}/{_first_app(portal)}/", wait_until="load")
    page.wait_for_timeout(2500)

    state = page.evaluate("""() => {
      const el = document.getElementById('docs');
      return {
        upgraded: typeof el.loadSpec === 'function',
        failed: el.loadFailed,
        tags: el.resolvedSpec && el.resolvedSpec.tags ? el.resolvedSpec.tags.length : 0,
        rendered: el.shadowRoot.innerHTML.length,
      };
    }""")
    assert state["upgraded"]
    assert state["failed"] is False
    assert state["tags"] > 0
    assert state["rendered"] > 5000


def test_portal_navigation_is_present_and_styled(page, live):
    base, _, portal = live
    page.goto(f"{base}/{_first_app(portal)}/", wait_until="load")
    page.wait_for_timeout(2000)

    assert page.is_visible("#api-switch")
    assert page.is_visible("#search-input")
    # Slotted content is styled by the page's own stylesheet, not the shadow
    # root; an unstyled control means shell.css did not reach this page.
    padding = page.evaluate("getComputedStyle(document.querySelector('.portal-nav')).gap")
    assert padding not in ("", "normal")


def test_cross_spec_search_finds_other_apis(page, live):
    base, _, portal = live
    names = portal.registry.names()
    if len(names) < 2:
        pytest.skip("the sample doc set has only one API")

    page.goto(f"{base}/{names[0]}/", wait_until="load")
    page.wait_for_timeout(2000)

    from apiwarden.index import build_index

    target = next(e for e in build_index(portal.registry) if e["app"] != names[0])
    page.fill("#search-input", target["id"])
    page.wait_for_timeout(700)

    rows = page.eval_on_selector_all("#search-results li a", "els => els.map(e => e.textContent)")
    assert rows, "no search results"
    assert target["path"] in rows[0]


def test_deep_link_scrolls_to_an_operation(page, live):
    base, _, portal = live
    from apiwarden.index import build_index

    app = _first_app(portal)
    entry = next(e for e in build_index(portal.registry) if e["app"] == app)

    page.goto(f"{base}/{app}/?op={entry['method']}%20{entry['path']}", wait_until="load")
    page.wait_for_timeout(3000)

    element_id = f"{entry['method'].lower()}-{entry['path']}"
    found = page.evaluate(
        "id => !!document.getElementById('docs').shadowRoot.getElementById(id)", element_id
    )
    assert found, f"no element {element_id!r}; RapiDoc's id format may have changed"


def test_an_edit_reaches_the_open_page_without_navigating(page, live):
    base, root, portal = live
    app = _first_app(portal)
    spec = portal.registry.specs[app].path
    original = spec.read_text(encoding="utf-8")
    marker = "EDITED-WHILE-OPEN"

    page.goto(f"{base}/{app}/", wait_until="load")
    page.wait_for_timeout(2500)
    assert marker not in _shadow_text(page)

    navigations: list[str] = []
    page.on("framenavigated", lambda frame: navigations.append(frame.url))

    try:
        title = portal.registry.specs[app].title
        spec.write_text(original.replace(f"title: {title}", f"title: {marker}", 1), encoding="utf-8")

        deadline = time.time() + 15
        while marker not in _shadow_text(page) and time.time() < deadline:
            page.wait_for_timeout(400)

        assert marker in _shadow_text(page), "the live edit never reached the page"
        assert not navigations, "the page reloaded instead of swapping the spec in place"
    finally:
        spec.write_text(original, encoding="utf-8")


def test_right_to_left_text_lays_itself_out(page, live):
    base, _, portal = live
    page.goto(f"{base}/{_first_app(portal)}/", wait_until="load")
    page.wait_for_timeout(2500)

    counts = page.evaluate("""() => {
      const root = document.getElementById('docs').shadowRoot;
      const els = Array.from(root.querySelectorAll('.m-markdown p, .m-markdown li'));
      return {
        total: els.length,
        plaintext: els.filter(e => getComputedStyle(e).unicodeBidi === 'plaintext').length,
      };
    }""")
    assert counts["total"] > 0
    # rapidoc-extra.css is injected into the shadow root via the css-file
    # attribute; if that stopped working, none of these would be plaintext.
    assert counts["plaintext"] == counts["total"]
