from apiwarden.check import check, summarize
from apiwarden.index import (
    api_summaries,
    build_index,
    conventions,
    find_operation,
    operation_detail,
    schema_detail,
    search_operations,
)
from apiwarden.loader import load_registry

REQUIRED_KEYS = {"id", "app", "method", "path", "summary", "tags", "auth", "request", "responses", "hash"}


def test_index_covers_every_operation(registry):
    expected = sum(len(list(spec.operations())) for spec in registry.specs.values())
    entries = build_index(registry)
    assert len(entries) == expected
    assert entries


def test_index_entries_have_a_stable_shape(registry):
    for entry in build_index(registry):
        assert REQUIRED_KEYS <= set(entry)
        assert entry["method"].isupper()
        assert entry["path"].startswith("/")
        assert entry["app"] in registry.names()
        assert isinstance(entry["responses"], dict)


def test_operation_ids_are_unique_across_the_doc_set(registry):
    ids = [entry["id"] for entry in build_index(registry)]
    assert len(ids) == len(set(ids))


def test_hash_tracks_the_operation_body(spec_copy):
    registry = load_registry(spec_copy)
    entry = build_index(registry)[0]

    spec = registry.specs[entry["app"]]
    spec.data["paths"][entry["path"]][entry["method"].lower()]["summary"] = "changed"
    assert build_index(registry)[0]["hash"] != entry["hash"]


def test_shared_response_refs_are_resolved_to_a_name(registry):
    # $ref'd responses under components/responses must not leak as raw pointers.
    for entry in build_index(registry):
        for name in entry["responses"].values():
            assert "#/" not in str(name)


def test_auth_is_public_or_a_scheme(registry):
    values = {entry["auth"] for entry in build_index(registry)}
    assert values
    assert all(isinstance(value, str) and value for value in values)


def test_find_operation_by_id_and_by_method_path(registry):
    entry = build_index(registry)[0]
    assert find_operation(registry, entry["id"]) is not None
    assert find_operation(registry, f"{entry['method']} {entry['path']}") is not None
    assert find_operation(registry, "does-not-exist") is None


def test_operation_detail_inlines_refs(registry):
    entry = next(e for e in build_index(registry) if e["responses"])
    detail = operation_detail(registry, entry["id"])

    assert detail["path"] == entry["path"]
    assert set(detail["responses"]) == set(entry["responses"])
    assert "$ref" not in str(detail["responses"])


def test_operation_detail_folds_in_path_level_parameters(registry):
    # A parameter declared once on the path item must appear on each operation.
    for name, spec in registry.specs.items():
        for url, item in (spec.data.get("paths") or {}).items():
            if not item.get("parameters"):
                continue
            method = next(m for m in ("get", "post", "patch", "put", "delete") if m in item)
            operation_id = item[method]["operationId"]
            assert operation_detail(registry, operation_id)["parameters"]
            return


def test_schema_detail_finds_schemas_with_and_without_an_app(registry):
    for name, spec in registry.specs.items():
        for schema_name in (spec.data.get("components", {}).get("schemas") or {}):
            assert schema_detail(registry, schema_name, name)["app"] == name
            assert schema_detail(registry, schema_name) is not None
            return


def test_conventions_carry_the_description_and_extensions(registry):
    for name, spec in registry.specs.items():
        if not spec.extensions:
            continue
        found = conventions(registry, name)
        assert found["extensions"] == spec.extensions
        assert found["description"] == spec.description
        return


def test_conventions_of_an_unknown_api_is_none(registry):
    assert conventions(registry, "no-such-api") is None


def test_search_ranks_an_exact_id_first(registry):
    entry = build_index(registry)[0]
    assert search_operations(registry, entry["id"])[0]["id"] == entry["id"]


def test_search_filters_by_app_and_method(registry):
    entry = build_index(registry)[0]
    results = search_operations(registry, entry["path"], app=entry["app"], method=entry["method"])
    assert results
    assert all(r["app"] == entry["app"] and r["method"] == entry["method"] for r in results)


def test_search_with_no_query_returns_everything(registry):
    assert len(search_operations(registry, "  ", limit=500)) == len(build_index(registry))


def test_search_for_nonsense_finds_nothing(registry):
    assert search_operations(registry, "zzzqqxnotathing") == []


def test_api_summaries_count_operations_per_spec(registry):
    entries = build_index(registry)
    for api in api_summaries(registry):
        assert api["operations"] == sum(1 for e in entries if e["app"] == api["app"])


def test_sample_doc_set_passes_its_own_lint(registry):
    errors, _ = summarize(check(registry))
    assert errors == 0


def test_lint_catches_a_duplicate_operation_id(spec_copy):
    registry = load_registry(spec_copy)
    names = registry.names()
    first, second = registry.specs[names[0]], registry.specs[names[1]]

    borrowed = next(iter(first.operations()))[2]["operationId"]
    url, method, operation, _ = next(iter(second.operations()))
    operation["operationId"] = borrowed

    messages = [p.message for p in check(registry) if p.level == "error"]
    assert any("also in" in message for message in messages)


def test_lint_catches_an_unresolved_ref(spec_copy):
    registry = load_registry(spec_copy)
    spec = registry.specs[registry.names()[0]]
    next(iter(spec.operations()))[2]["requestBody"] = {"$ref": "#/components/schemas/Missing"}

    assert any("unresolved $ref" in p.message for p in check(registry))
