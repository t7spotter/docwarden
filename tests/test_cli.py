import json
import threading
import time
import urllib.request
from pathlib import Path

import pytest

from apidocs_live.cli import main
from apidocs_live.config import Config, find_toml, from_dict, from_toml
from apidocs_live.loader import load_registry
from apidocs_live.watcher import Watcher


def test_check_passes_on_the_sample(sample_root, capsys):
    assert main(["check", str(sample_root)]) == 0
    assert "0 errors" in capsys.readouterr().out


def test_check_fails_on_a_broken_spec(spec_copy, capsys):
    next(spec_copy.rglob("openapi.yaml")).write_text("openapi: 3.0.3\n bad: [\n")
    assert main(["check", str(spec_copy)]) == 1
    capsys.readouterr()


def test_check_strict_turns_warnings_into_failure(tmp_path, capsys):
    # An operation with no description is a warning, not an error: clean by
    # default, a failure under --strict.
    spec = tmp_path / "apps" / "thing" / "openapi.yaml"
    spec.parent.mkdir(parents=True)
    spec.write_text(
        "openapi: 3.0.3\n"
        "info: {title: Thing, version: '1'}\n"
        "paths:\n"
        "  /things/:\n"
        "    get:\n"
        "      operationId: listThings\n"
        "      summary: List things.\n"
        "      responses:\n"
        "        '200': {description: ok}\n"
    )

    assert main(["check", str(tmp_path)]) == 0
    assert main(["check", str(tmp_path), "--strict"]) == 1
    assert "1 warnings" in capsys.readouterr().out


def test_build_writes_a_usable_static_copy(sample_root, tmp_path, capsys):
    output = tmp_path / "dist"
    assert main(["build", str(sample_root), "-o", str(output)]) == 0
    capsys.readouterr()

    assert (output / "index.html").exists()
    assert (output / "_static" / "shell.css").exists()

    payload = json.loads((output / "index.json").read_text())
    assert payload["operations"]
    for api in payload["apis"]:
        assert (output / api["app"] / "index.html").exists()
        assert (output / "openapi" / f"{api['app']}.json").exists()
    for entry in payload["operations"]:
        assert (output / "operation" / f"{entry['id']}.json").exists()

    # A static build has no server to reload it, so it must not advertise SSE.
    assert '"watch": false' in (output / "index.html").read_text().lower()


def test_build_replaces_a_previous_output(sample_root, tmp_path, capsys):
    output = tmp_path / "dist"
    main(["build", str(sample_root), "-o", str(output)])
    stale = output / "stale.txt"
    stale.write_text("old")
    main(["build", str(sample_root), "-o", str(output)])
    capsys.readouterr()
    assert not stale.exists()


def test_serve_answers_over_http(sample_root, capsys):
    from apidocs_live.router import build_portal
    from apidocs_live.server import serve

    config = Config(root=sample_root, title="Served", watch=False)
    portal = build_portal(config)

    thread = threading.Thread(target=serve, args=(portal, "127.0.0.1", 8479), daemon=True)
    thread.start()
    time.sleep(0.4)

    base = "http://127.0.0.1:8479"
    with urllib.request.urlopen(f"{base}/health") as response:
        assert json.loads(response.read())["revision"] == portal.registry.revision
    with urllib.request.urlopen(f"{base}/") as response:
        assert response.status == 200

    request = urllib.request.Request(
        f"{base}/mcp",
        data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request) as response:
        assert json.loads(response.read())["result"]["tools"]

    with pytest.raises(urllib.error.HTTPError) as raised:
        urllib.request.urlopen(f"{base}/nope")
    assert raised.value.code == 404
    capsys.readouterr()


def test_watcher_notices_an_edit(spec_copy):
    registry = load_registry(spec_copy)
    seen = []
    watcher = Watcher(registry, interval=0.05, on_change=seen.append)
    watcher.start()
    try:
        target = next(spec_copy.rglob("openapi.yaml"))
        target.write_text(target.read_text().replace("openapi:", "openapi:  ", 1))

        deadline = time.time() + 5
        while not seen and time.time() < deadline:
            time.sleep(0.05)
    finally:
        watcher.stop()

    assert seen and seen[-1] == registry.revision


def test_watcher_survives_an_unparsable_moment(spec_copy):
    registry = load_registry(spec_copy)
    watcher = Watcher(registry, interval=0.05)
    watcher.start()
    try:
        target = next(spec_copy.rglob("openapi.yaml"))
        good = target.read_text()
        target.write_text("openapi: 3.0.3\n bad: [\n")  # mid-save state
        time.sleep(0.3)
        target.write_text(good)
        time.sleep(0.3)
    finally:
        watcher.stop()

    assert any(spec.error is None for spec in registry.specs.values())


def test_config_rejects_unknown_keys():
    with pytest.raises(ValueError, match="nonsense"):
        from_dict({"nonsense": 1})


def test_config_rejects_an_unknown_renderer():
    with pytest.raises(ValueError, match="renderer"):
        Config(renderer="crayons")


def test_config_url_honours_base_path():
    assert Config(base_path="/docs/").url("index.json") == "/docs/index.json"
    assert Config().url("index.json") == "/index.json"
    assert Config(base_path="docs").url("/") == "/docs/"


def test_toml_config_is_read(tmp_path: Path):
    (tmp_path / "apidocs.toml").write_text('[apidocs]\ntitle = "From toml"\nrenderer = "cdn"\n')
    config = from_toml(tmp_path / "apidocs.toml")
    assert config.title == "From toml" and config.renderer == "cdn"


def test_missing_toml_gives_defaults(tmp_path: Path):
    assert from_toml(tmp_path / "absent.toml").title == Config().title


def test_find_toml_looks_upwards(tmp_path: Path):
    (tmp_path / "apidocs.toml").write_text("[apidocs]\n")
    nested = tmp_path / "a" / "b"
    nested.mkdir(parents=True)
    assert find_toml(nested) == tmp_path / "apidocs.toml"


def test_serve_prints_a_summary_and_any_errors(spec_copy, capsys, monkeypatch):
    next(spec_copy.rglob("openapi.yaml")).write_text("openapi: 3.0.3\n bad: [\n")
    started = {}
    monkeypatch.setattr("apidocs_live.server.serve", lambda portal, host, port: started.update(host=host, port=port))

    assert main(["serve", str(spec_copy), "--port", "9999", "--no-watch"]) == 0

    printed = capsys.readouterr().out
    assert "specs from" in printed
    assert "error" in printed
    assert started == {"host": "127.0.0.1", "port": 9999}


def test_serve_reports_watching(sample_root, capsys, monkeypatch):
    monkeypatch.setattr("apidocs_live.server.serve", lambda *a, **k: None)
    main(["serve", str(sample_root)])
    assert "watching for changes" in capsys.readouterr().out


def test_title_defaults_to_the_project_directory(sample_root, capsys, monkeypatch):
    monkeypatch.setattr("apidocs_live.server.serve", lambda *a, **k: None)
    main(["serve", str(sample_root)])
    assert sample_root.resolve().parent.name in capsys.readouterr().out


def test_mcp_subcommand_speaks_jsonrpc_over_stdio(sample_root):
    import subprocess
    import sys

    messages = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
    ]
    completed = subprocess.run(
        [sys.executable, "-m", "apidocs_live.cli", "mcp", str(sample_root)],
        input="\n".join(json.dumps(m) for m in messages),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert completed.returncode == 0, completed.stderr

    replies = [json.loads(line) for line in completed.stdout.splitlines() if line.strip()]
    assert [r["id"] for r in replies] == [1, 2]  # the notification got no reply
    assert replies[1]["result"]["tools"]


def test_stdio_reports_malformed_json_without_dying(sample_root):
    import io

    from apidocs_live.mcp_stdio import run

    out = io.StringIO()
    run(Config(root=sample_root), stdin=io.StringIO('{bad\n\n{"jsonrpc":"2.0","id":1,"method":"ping"}\n'), stdout=out)

    replies = [json.loads(line) for line in out.getvalue().splitlines() if line.strip()]
    assert replies[0]["error"]["code"] == -32700
    assert replies[1]["result"] == {}


def _spawn(config, port):
    from apidocs_live.router import build_portal
    from apidocs_live.server import serve

    portal = build_portal(config)
    thread = threading.Thread(target=serve, args=(portal, "127.0.0.1", port), daemon=True)
    thread.start()
    time.sleep(0.4)
    return portal


def test_server_streams_sse_and_answers_head(sample_root):
    import http.client

    _spawn(Config(root=sample_root, title="Streamed", watch=True), 8481)

    connection = http.client.HTTPConnection("127.0.0.1", 8481, timeout=5)
    connection.request("HEAD", "/health")
    head = connection.getresponse()
    assert head.status == 200
    assert head.read() == b""
    connection.close()

    connection = http.client.HTTPConnection("127.0.0.1", 8481, timeout=5)
    connection.request("GET", "/events")
    stream = connection.getresponse()
    assert stream.status == 200
    assert b"event: revision" in stream.read(64)
    connection.close()


def test_server_answers_500_when_a_handler_raises(sample_root, monkeypatch):
    import urllib.error

    monkeypatch.setattr("apidocs_live.server.handle", _boom)
    _spawn(Config(root=sample_root, watch=False), 8482)

    with pytest.raises(urllib.error.HTTPError) as raised:
        urllib.request.urlopen("http://127.0.0.1:8482/")
    assert raised.value.code == 500


def _boom(request, portal):
    raise RuntimeError("deliberate")


def test_binding_a_busy_port_fails_clearly(sample_root):
    import socket

    from apidocs_live.router import build_portal
    from apidocs_live.server import serve

    holder = socket.socket()
    holder.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    holder.bind(("127.0.0.1", 8483))
    holder.listen(1)
    try:
        with pytest.raises(SystemExit, match="cannot bind"):
            serve(build_portal(Config(root=sample_root, watch=False)), "127.0.0.1", 8483)
    finally:
        holder.close()


def test_snapshot_then_changes_round_trip(spec_copy, tmp_path, capsys):
    baseline = tmp_path / "baseline.json"
    assert main(["snapshot", str(spec_copy), "-o", str(baseline)]) == 0
    assert "wrote" in capsys.readouterr().out

    assert main(["changes", str(spec_copy), "--since", str(baseline)]) == 0
    assert "0 breaking" in capsys.readouterr().out


def test_changes_fails_on_breaking_when_asked(spec_copy, tmp_path, capsys):
    baseline = tmp_path / "baseline.json"
    main(["snapshot", str(spec_copy), "-o", str(baseline)])
    capsys.readouterr()

    # Delete a whole spec: every operation in it disappears.
    next(spec_copy.rglob("openapi.yaml")).unlink()

    assert main(["changes", str(spec_copy), "--since", str(baseline)]) == 0
    assert main(["changes", str(spec_copy), "--since", str(baseline), "--fail-on-breaking"]) == 1
    assert "breaking" in capsys.readouterr().out


def test_changes_without_a_baseline_explains_itself(spec_copy, capsys):
    assert main(["changes", str(spec_copy)]) == 1
    assert "--since" in capsys.readouterr().err


def test_changes_with_an_unreadable_baseline(spec_copy, capsys):
    assert main(["changes", str(spec_copy), "--since", "no-such-revision-xyz"]) == 1
    assert "cannot read the baseline" in capsys.readouterr().err
