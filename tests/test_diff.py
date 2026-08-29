"""The change view.

Baselines are built by mutating a copy of the sample rather than by asserting
on its subject matter, so the tests hold whatever the sample doc set contains.
"""

from __future__ import annotations

import copy
import json
import subprocess

import pytest

from apiwarden import diff
from apiwarden.config import Config
from apiwarden.http import Request
from apiwarden.loader import load_registry
from apiwarden.mcp_http import handle_rpc
from apiwarden.router import Portal, handle


@pytest.fixture
def before(registry):
    return diff.snapshot(registry)


def first_operation(snapshot):
    app = next(iter(snapshot["apis"]))
    key = next(iter(snapshot["apis"][app]["operations"]))
    return app, key


def mutate(snapshot, change):
    after = copy.deepcopy(snapshot)
    app, key = first_operation(after)
    change(after["apis"][app]["operations"][key], after["apis"][app]["operations"])
    return after, app, key


def kinds(changes, level=None):
    return {c.kind for c in changes if level is None or c.level == level}


def test_identical_snapshots_produce_nothing(before):
    assert diff.compare(before, before) == []


def test_removing_an_operation_is_breaking(before):
    after = copy.deepcopy(before)
    app, key = first_operation(after)
    after["apis"][app]["operations"].pop(key)

    assert "operation-removed" in kinds(diff.compare(before, after), diff.BREAKING)


def test_adding_an_operation_is_additive(before):
    after = copy.deepcopy(before)
    app = next(iter(after["apis"]))
    template = next(iter(after["apis"][app]["operations"].values()))
    after["apis"][app]["operations"]["GET /brand/new/"] = {**copy.deepcopy(template), "id": "brandNew"}

    assert "operation-added" in kinds(diff.compare(before, after), diff.ADDITIVE)


def test_removing_an_api_is_breaking_and_adding_one_is_not(before):
    fewer = copy.deepcopy(before)
    fewer["apis"].pop(next(iter(fewer["apis"])))
    assert "api-removed" in kinds(diff.compare(before, fewer), diff.BREAKING)
    assert "api-added" in kinds(diff.compare(fewer, before), diff.ADDITIVE)


def test_changing_an_operation_id_is_breaking(before):
    after, _, _ = mutate(before, lambda op, ops: op.update(id="renamed"))
    assert "operation-id-changed" in kinds(diff.compare(before, after), diff.BREAKING)


def test_requiring_auth_is_breaking_and_dropping_it_is_additive(before):
    public, _, _ = mutate(before, lambda op, ops: op.update(auth="public"))
    guarded, _, _ = mutate(before, lambda op, ops: op.update(auth="bearer"))

    assert "auth-required" in kinds(diff.compare(public, guarded), diff.BREAKING)
    assert "auth-changed" in kinds(diff.compare(guarded, public), diff.ADDITIVE)


def test_a_new_required_request_field_is_breaking(before):
    after, _, _ = mutate(
        before,
        lambda op, ops: op["request"].setdefault("fields", {}).update(
            {"newField": {"type": "string", "required": True, "enum": None}}
        ),
    )
    changes = diff.compare(before, after)
    assert "field-added" in kinds(changes, diff.BREAKING)


def test_a_new_optional_request_field_is_additive(before):
    after, _, _ = mutate(
        before,
        lambda op, ops: op["request"].setdefault("fields", {}).update(
            {"newField": {"type": "string", "required": False, "enum": None}}
        ),
    )
    assert "field-added" in kinds(diff.compare(before, after), diff.ADDITIVE)


def test_removing_a_response_field_is_breaking(before):
    after = copy.deepcopy(before)
    for api in after["apis"].values():
        for operation in api["operations"].values():
            for response in operation["responses"].values():
                if response["fields"]:
                    response["fields"].pop(next(iter(response["fields"])))
                    assert "field-removed" in kinds(diff.compare(before, after), diff.BREAKING)
                    return
    pytest.skip("no response in the sample declares fields")


def test_changing_a_field_type_is_breaking(before):
    after = copy.deepcopy(before)
    for api in after["apis"].values():
        for operation in api["operations"].values():
            for response in operation["responses"].values():
                for field in response["fields"].values():
                    field["type"] = "definitely-not-the-old-type"
                    assert "field-type-changed" in kinds(diff.compare(before, after), diff.BREAKING)
                    return
    pytest.skip("no typed response fields in the sample")


def test_enum_values_removed_break_and_added_do_not(before):
    narrow = copy.deepcopy(before)
    wide = copy.deepcopy(before)
    app, key = first_operation(before)
    for source, values in ((narrow, ["a"]), (wide, ["a", "b"])):
        response = next(iter(source["apis"][app]["operations"][key]["responses"].values()))
        response["fields"]["status"] = {"type": "string", "required": False, "enum": values}

    assert "enum-value-added" in kinds(diff.compare(narrow, wide), diff.ADDITIVE)
    assert "enum-value-removed" in kinds(diff.compare(wide, narrow), diff.BREAKING)


def test_a_new_required_parameter_is_breaking(before):
    after, _, _ = mutate(
        before, lambda op, ops: op["parameters"].update({"query:page": {"required": True, "type": "integer"}})
    )
    assert "parameter-added" in kinds(diff.compare(before, after), diff.BREAKING)


def test_summary_and_deprecation_are_informational(before):
    after, _, _ = mutate(before, lambda op, ops: op.update(summary="reworded", deprecated=True))
    changes = diff.compare(before, after)
    assert kinds(changes, diff.INFO) == {"summary-changed", "deprecated"}
    assert not kinds(changes, diff.BREAKING)


def test_breaking_changes_are_listed_first(before):
    after, _, _ = mutate(before, lambda op, ops: op.update(id="renamed", summary="reworded"))
    changes = diff.compare(before, after)
    assert [c.level for c in changes] == sorted(
        (c.level for c in changes), key=[diff.BREAKING, diff.ADDITIVE, diff.INFO].index
    )


def test_summarize_counts_by_level(before):
    after, _, _ = mutate(before, lambda op, ops: op.update(id="renamed", summary="reworded"))
    counts = diff.summarize(diff.compare(before, after))
    assert counts[diff.BREAKING] == 1 and counts[diff.INFO] == 1


def test_flatten_walks_nested_objects_and_arrays():
    flat = diff._flatten(
        {
            "type": "object",
            "required": ["id"],
            "properties": {
                "id": {"type": "string"},
                "items": {"type": "array", "items": {"type": "object", "properties": {"n": {"type": "integer"}}}},
            },
        }
    )
    assert flat["id"]["required"] is True
    assert "items[].n" in flat


def test_flatten_stops_at_max_depth():
    schema = current = {"type": "object", "properties": {}}
    for _ in range(diff.MAX_DEPTH + 4):
        child = {"type": "object", "properties": {}}
        current["properties"]["deeper"] = child
        current = child
    assert len(diff._flatten(schema)) <= diff.MAX_DEPTH + 2


# ---------------------------------------------------------------- baselines


def test_snapshot_file_round_trips(registry, tmp_path):
    path = tmp_path / "baseline.json"
    path.write_text(json.dumps(diff.snapshot(registry)))
    assert diff.compare(diff.snapshot_at(registry, str(path)), diff.snapshot(registry)) == []


def test_a_corrupt_snapshot_file_is_reported(registry, tmp_path):
    path = tmp_path / "broken.json"
    path.write_text("{not json")
    with pytest.raises(diff.DiffUnavailable):
        diff.snapshot_at(registry, str(path))


def test_diff_against_a_git_revision(spec_copy):
    run = lambda *args: subprocess.run(["git", "-C", str(spec_copy), *args], capture_output=True, check=True)
    run("init", "-q")
    run("config", "user.email", "t@example.com")
    run("config", "user.name", "T")
    run("add", "-A")
    run("commit", "-qm", "baseline")

    registry = load_registry(spec_copy)
    assert diff.compare(diff.snapshot_at(registry, "HEAD"), diff.snapshot(registry)) == []

    # Remove one operation, commit nothing, and diff the working tree against HEAD.
    target = next(spec_copy.rglob("openapi.yaml"))
    document = target.read_text()
    registry = load_registry(spec_copy)
    spec = next(s for s in registry.specs.values() if s.path == target)
    url = next(iter(spec.data["paths"]))
    trimmed = [line for line in document.splitlines() if not line.startswith(f"  {url}:")]
    target.write_text("\n".join(trimmed) + "\n")

    registry = load_registry(spec_copy)
    changes = diff.compare(diff.snapshot_at(registry, "HEAD"), diff.snapshot(registry))
    assert any(c.level == diff.BREAKING for c in changes)

    assert diff.default_since(registry) is None or isinstance(diff.default_since(registry), str)


def test_an_unknown_revision_is_reported(registry):
    with pytest.raises(diff.DiffUnavailable):
        diff.snapshot_at(registry, "no-such-revision-abcdef")


# ---------------------------------------------------------------- surfaces


def test_changes_route_without_a_baseline_explains_itself(registry, sample_root, tmp_path):
    import shutil

    plain = tmp_path / "no-git"
    shutil.copytree(sample_root, plain)
    portal = Portal(Config(root=plain), load_registry(plain))

    page = handle(Request("GET", "/changes"), portal)
    assert page.status == 200
    assert b"snapshot.json" in page.body

    payload = json.loads(handle(Request("GET", "/changes.json"), portal).body)
    assert payload["error"]


def test_changes_json_reports_a_known_baseline(registry, sample_root, tmp_path):
    path = tmp_path / "baseline.json"
    path.write_text(json.dumps(diff.snapshot(registry)))

    portal = Portal(Config(root=sample_root), registry)
    response = handle(Request("GET", "/changes.json", query={"since": str(path)}), portal)
    payload = json.loads(response.body)

    assert response.status == 200
    assert payload["changes"] == []
    assert payload["counts"] == {"breaking": 0, "additive": 0, "info": 0}


def test_changes_page_renders_grouped_changes(registry, sample_root, tmp_path):
    stale = diff.snapshot(registry)
    app = next(iter(stale["apis"]))
    stale["apis"][app]["operations"].pop(next(iter(stale["apis"][app]["operations"])))

    path = tmp_path / "baseline.json"
    path.write_text(json.dumps(stale))

    portal = Portal(Config(root=sample_root), registry)
    markup = handle(Request("GET", "/changes", query={"since": str(path)}), portal).body.decode()
    assert "operation-added" in markup
    assert "Additive" in markup


def test_sidebar_links_to_the_changes_page(portal):
    assert 'href="/changes"' in handle(Request("GET", "/"), portal).body.decode()


def test_list_changes_tool(registry, sample_root, tmp_path):
    path = tmp_path / "baseline.json"
    path.write_text(json.dumps(diff.snapshot(registry)))
    portal = Portal(Config(root=sample_root), registry)

    reply = handle_rpc(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
         "params": {"name": "list_changes", "arguments": {"since": str(path)}}},
        portal.registry, portal.config,
    )
    assert json.loads(reply["result"]["content"][0]["text"])["counts"]["breaking"] == 0


def test_list_changes_reports_a_bad_baseline(portal):
    reply = handle_rpc(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
         "params": {"name": "list_changes", "arguments": {"since": "no-such-rev-xyz"}}},
        portal.registry, portal.config,
    )
    assert reply["result"]["isError"] is True


def test_list_changes_is_declared(portal):
    tools = handle_rpc({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, portal.registry, portal.config)
    assert "list_changes" in [t["name"] for t in tools["result"]["tools"]]


def test_flatten_reports_a_required_name_with_no_property():
    # Invalid OpenAPI, but a spec in the wild can carry it, and adding such a
    # name still breaks callers.
    flat = diff._flatten({"type": "object", "required": ["ghost"], "properties": {}})
    assert flat["ghost"] == {"type": None, "required": True, "enum": None}


def test_a_properly_declared_new_required_field_is_breaking(registry):
    import copy as copy_module

    before = diff.snapshot(registry)
    after = copy_module.deepcopy(before)
    app, key = first_operation(after)
    after["apis"][app]["operations"][key]["request"].setdefault("fields", {})["device_id"] = {
        "type": "string",
        "required": True,
        "enum": None,
    }

    changes = diff.compare(before, after)
    assert any(c.kind == "field-added" and "required" in c.detail for c in changes if c.level == diff.BREAKING)
