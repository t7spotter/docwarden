import json

from docwarden.http import Request
from docwarden.index import build_index
from docwarden.mcp_http import METHOD_NOT_FOUND, TOOLS, handle_rpc
from docwarden.router import handle


def rpc(portal, method, params=None, message_id=1):
    return handle_rpc(
        {"jsonrpc": "2.0", "id": message_id, "method": method, "params": params or {}},
        portal.registry,
        portal.config,
    )


def call(portal, name, **arguments):
    reply = rpc(portal, "tools/call", {"name": name, "arguments": arguments})
    return reply["result"]


def payload(result):
    return json.loads(result["content"][0]["text"])


def test_initialize_echoes_a_supported_protocol(portal):
    result = rpc(portal, "initialize", {"protocolVersion": "2025-06-18"})["result"]
    assert result["protocolVersion"] == "2025-06-18"
    assert result["capabilities"]["tools"] is not None
    assert result["instructions"]


def test_initialize_falls_back_for_an_unknown_protocol(portal):
    result = rpc(portal, "initialize", {"protocolVersion": "1999-01-01"})["result"]
    assert result["protocolVersion"] in ("2025-06-18", "2025-03-26", "2024-11-05")


def test_notifications_get_no_reply(portal):
    assert handle_rpc({"jsonrpc": "2.0", "method": "notifications/initialized"}, portal.registry, portal.config) is None


def test_unknown_method_is_a_jsonrpc_error(portal):
    assert rpc(portal, "tools/nope")["error"]["code"] == METHOD_NOT_FOUND


def test_tools_list_declares_valid_schemas(portal):
    tools = rpc(portal, "tools/list")["result"]["tools"]
    assert [t["name"] for t in tools] == [t["name"] for t in TOOLS]
    for tool in tools:
        assert tool["description"]
        assert tool["inputSchema"]["type"] == "object"
        for required in tool["inputSchema"].get("required", []):
            assert required in tool["inputSchema"]["properties"]


def test_list_apis(portal, registry):
    assert [a["app"] for a in payload(call(portal, "list_apis"))["apis"]] == registry.names()


def test_search_then_get_operation(portal, registry):
    entry = build_index(registry)[0]
    results = payload(call(portal, "search_operations", query=entry["id"]))["results"]
    assert results[0]["id"] == entry["id"]

    detail = payload(call(portal, "get_operation", operation_id=entry["id"]))
    assert detail["path"] == entry["path"]
    assert "$ref" not in json.dumps(detail["responses"])


def test_get_operation_accepts_method_and_path(portal, registry):
    entry = build_index(registry)[0]
    detail = payload(call(portal, "get_operation", operation_id=f"{entry['method']} {entry['path']}"))
    assert detail["id"] == entry["id"]


def test_get_conventions_returns_narrative_and_extensions(portal, registry):
    name = registry.names()[0]
    found = payload(call(portal, "get_conventions", app=name))
    assert found["app"] == name
    assert "extensions" in found and "description" in found


def test_get_spec_in_both_formats(portal, registry):
    name = registry.names()[0]
    assert '"openapi"' in call(portal, "get_spec", app=name)["content"][0]["text"]
    assert "openapi:" in call(portal, "get_spec", app=name, format="yaml")["content"][0]["text"]


def test_unknown_targets_are_tool_errors_not_crashes(portal):
    for tool, arguments in (
        ("get_operation", {"operation_id": "nope"}),
        ("get_schema", {"name": "nope"}),
        ("get_conventions", {"app": "nope"}),
        ("get_spec", {"app": "nope"}),
        ("no_such_tool", {}),
    ):
        result = rpc(portal, "tools/call", {"name": tool, "arguments": arguments})["result"]
        assert result["isError"] is True


def test_search_with_no_results_still_succeeds(portal):
    result = call(portal, "search_operations", query="zzzqqxnotathing")
    assert not result.get("isError")
    assert payload(result)["results"] == []


def test_a_bad_argument_type_becomes_a_tool_error(portal):
    result = call(portal, "search_operations", query="x", limit="not-a-number")
    assert result["isError"] is True


def test_http_endpoint_returns_json_and_202_for_notifications(portal):
    request = Request(
        "POST",
        "/mcp",
        body=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}).encode(),
    )
    response = handle(request, portal)
    assert response.status == 200
    assert json.loads(response.body)["result"]["tools"]

    notification = Request(
        "POST", "/mcp", body=json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}).encode()
    )
    assert handle(notification, portal).status == 202


def test_http_endpoint_reports_malformed_json(portal):
    response = handle(Request("POST", "/mcp", body=b"{not json"), portal)
    assert response.status == 400
    assert json.loads(response.body)["error"]["code"] == -32700
