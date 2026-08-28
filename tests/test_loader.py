from pathlib import Path

import yaml

from apidocs_live.loader import (
    discover_specs,
    load_registry,
    load_spec,
    reload_if_changed,
    resolve_deep,
    resolve_ref,
)


def test_loads_every_spec(registry):
    assert registry.errors == []
    assert registry.names()
    assert all(spec.error is None for spec in registry.specs.values())


def test_redocly_registry_sets_names_and_order(sample_root):
    declared = yaml.safe_load((sample_root / "redocly.yaml").read_text())["apis"]
    assert [name for name, _ in discover_specs(sample_root)] == list(declared)


def test_glob_fallback_names_specs_after_their_directory(spec_copy):
    (spec_copy / "redocly.yaml").unlink()
    found = dict(discover_specs(spec_copy))
    assert found
    for name, path in found.items():
        assert path.parent.name == name


def test_explicit_sources_win(sample_root):
    first = next(iter(yaml.safe_load((sample_root / "redocly.yaml").read_text())["apis"].values()))
    found = discover_specs(sample_root, {"only": first["root"]})
    assert [name for name, _ in found] == ["only"]


def test_revision_changes_only_when_content_changes(spec_copy):
    registry = load_registry(spec_copy)
    before = registry.revision

    target = next(iter(registry.specs.values())).path
    target.touch()
    assert reload_if_changed(registry) is False
    assert registry.revision == before

    target.write_text(target.read_text().replace("openapi:", "openapi:  ", 1))
    assert reload_if_changed(registry) is True
    assert registry.revision != before


def test_reload_picks_up_a_new_spec(spec_copy):
    registry = load_registry(spec_copy)
    before = set(registry.names())

    (spec_copy / "redocly.yaml").unlink()
    reload_if_changed(registry)  # re-discover without the registry file

    added = spec_copy / "apps" / "brandnew"
    added.mkdir(parents=True)
    (added / "openapi.yaml").write_text(
        "openapi: 3.0.3\ninfo: {title: New, version: '1'}\npaths: {}\n"
    )
    assert reload_if_changed(registry) is True
    assert "brandnew" in registry.names()
    assert before <= set(registry.names())


def test_a_broken_spec_does_not_break_the_doc_set(spec_copy):
    target = next(spec_copy.rglob("openapi.yaml"))
    target.write_text("openapi: 3.0.3\n  bad indentation: [\n")

    registry = load_registry(spec_copy)
    broken = [spec for spec in registry.specs.values() if spec.error]
    assert len(broken) == 1
    assert any(spec.error is None for spec in registry.specs.values())


def test_missing_root_is_reported_not_raised(tmp_path: Path):
    registry = load_registry(tmp_path / "nothing-here")
    assert registry.errors
    assert registry.specs == {}


def test_load_spec_reports_unreadable_files(tmp_path: Path):
    spec = load_spec("gone", tmp_path / "missing.yaml")
    assert spec.error and spec.data == {}


def test_resolve_ref_and_deep_inlining():
    document = {
        "components": {
            "schemas": {
                "Inner": {"type": "string"},
                "Outer": {"type": "object", "properties": {"a": {"$ref": "#/components/schemas/Inner"}}},
            }
        }
    }
    assert resolve_ref(document, "#/components/schemas/Inner") == {"type": "string"}
    resolved = resolve_deep(document, document["components"]["schemas"]["Outer"])
    assert resolved["properties"]["a"] == {"type": "string"}


def test_resolve_deep_survives_a_cycle():
    # A self-referential schema inlines one level, then stops and says so,
    # rather than recursing forever or refusing to resolve at all.
    document = {"components": {"schemas": {"Node": {"properties": {"next": {"$ref": "#/components/schemas/Node"}}}}}}
    resolved = resolve_deep(document, document["components"]["schemas"]["Node"])
    assert resolved["properties"]["next"]["properties"]["next"]["x-circular"] is True


def test_resolve_deep_marks_a_dangling_ref():
    resolved = resolve_deep({}, {"$ref": "#/components/schemas/Nope"})
    assert resolved["x-unresolved"] is True


def test_resolve_deep_keeps_sibling_keys():
    document = {"components": {"schemas": {"S": {"type": "string"}}}}
    resolved = resolve_deep(document, {"$ref": "#/components/schemas/S", "description": "kept"})
    assert resolved == {"type": "string", "description": "kept"}


def test_registry_names_are_url_safe(tmp_path: Path):
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "openapi.yaml").write_text("openapi: 3.0.3\ninfo: {title: A, version: '1'}\npaths: {}\n")
    (tmp_path / "redocly.yaml").write_text("apis:\n  My API v2:\n    root: a/openapi.yaml\n")

    name = discover_specs(tmp_path)[0][0]
    assert "/" not in name and " " not in name
