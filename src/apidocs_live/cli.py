"""`apidocs` — serve, check, build, and the stdio MCP server."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__
from .check import check as run_check
from .check import summarize
from .config import Config, env_token, find_toml, from_toml
from .loader import load_registry


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="apidocs",
        description="Serve a directory of OpenAPI specs as live documentation for humans and AI agents.",
    )
    parser.add_argument("--version", action="version", version=f"apidocs-live {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    serve = sub.add_parser("serve", help="run the live docs portal")
    _common(serve)
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8080)
    serve.add_argument("--no-watch", action="store_true", help="do not reload when the specs change")
    serve.add_argument("--token", default=None, help="require this shared token on every request")

    check = sub.add_parser("check", help="validate the doc set")
    _common(check)
    check.add_argument("--strict", action="store_true", help="treat warnings as failures")

    build = sub.add_parser("build", help="write a self-contained static copy")
    _common(build)
    build.add_argument("-o", "--output", default="dist", help="output directory (default: dist)")
    build.add_argument("--base-path", default="", help="URL prefix the site will be served under")

    mcp = sub.add_parser("mcp", help="run the MCP server on stdio")
    _common(mcp)

    snapshot = sub.add_parser("snapshot", help="write a baseline to diff against later")
    _common(snapshot)
    snapshot.add_argument("-o", "--output", default="apidocs-snapshot.json")

    changes = sub.add_parser("changes", help="show what changed since a baseline")
    _common(changes)
    changes.add_argument("--since", default=None, help="a git revision, or a snapshot file")
    changes.add_argument("--fail-on-breaking", action="store_true",
                         help="exit non-zero when a breaking change is found")

    args = parser.parse_args(argv)
    root = Path(args.root)

    config = _config(root, args)

    if args.command == "serve":
        return _serve(config, args)
    if args.command == "check":
        return _check(config, args)
    if args.command == "build":
        return _build(config, args)
    if args.command == "mcp":
        return _mcp(config)
    if args.command == "snapshot":
        return _snapshot(config, args)
    if args.command == "changes":
        return _changes(config, args)
    return 1


def _common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("root", nargs="?", default="api-docs", help="directory holding the specs")
    parser.add_argument("--title", default=None, help="portal title")
    parser.add_argument("--server", action="append", dest="servers", default=None,
                        help="override the API base URL offered in try-it (repeatable)")
    parser.add_argument("--renderer", choices=["vendor", "cdn"], default=None)


def _config(root: Path, args) -> Config:
    toml = find_toml(root if root.is_dir() else root.parent)
    config = from_toml(toml) if toml else Config()

    config.root = root
    if getattr(args, "title", None):
        config.title = args.title
    elif config.title == Config().title:
        config.title = f"{root.resolve().parent.name} API docs"
    if getattr(args, "servers", None):
        config.servers = list(args.servers)
    if getattr(args, "renderer", None):
        config.renderer = args.renderer
    if getattr(args, "base_path", None):
        config.base_path = args.base_path
    config.token = getattr(args, "token", None) or config.token or env_token()
    config.watch = not getattr(args, "no_watch", False)
    return config


def _serve(config: Config, args) -> int:
    from .router import build_portal
    from .server import serve

    portal = build_portal(config)
    problems = run_check(portal.registry)
    errors, warnings = summarize(problems)

    print(f"\n  {config.title}")
    print(f"  {len(portal.registry.specs)} specs from {config.root}"
          f" · rev {portal.registry.revision}"
          f" · {errors} errors, {warnings} warnings")
    for problem in problems:
        if problem.level == "error":
            print(f"    {problem}")
    if config.watch:
        print("  watching for changes")
    print()

    serve(portal, host=args.host, port=args.port)
    return 0


def _check(config: Config, args) -> int:
    registry = load_registry(config.root, config.sources or None)
    problems = run_check(registry)
    errors, warnings = summarize(problems)

    operations = 0
    for spec in registry.specs.values():
        operations += sum(1 for _ in spec.operations())

    for problem in problems:
        print(problem, file=sys.stderr if problem.level == "error" else sys.stdout)

    print(f"{len(registry.specs)} specs, {operations} operations, {errors} errors, {warnings} warnings")
    if errors:
        return 1
    return 1 if (args.strict and warnings) else 0


def _build(config: Config, args) -> int:
    from .build import build_static

    written = build_static(config, Path(args.output))
    print(f"wrote {written} files to {args.output}")
    return 0


def _snapshot(config: Config, args) -> int:
    import json

    from .diff import snapshot

    registry = load_registry(config.root, config.sources or None)
    output = Path(args.output)
    output.write_text(json.dumps(snapshot(registry), indent=2), encoding="utf-8")
    print(f"wrote {output} at revision {registry.revision}")
    return 0


def _changes(config: Config, args) -> int:
    from .diff import BREAKING, DiffUnavailable, compare, default_since, snapshot, snapshot_at, summarize

    registry = load_registry(config.root, config.sources or None)
    since = args.since or default_since(registry)
    if not since:
        print("nothing to compare against: pass --since <git revision or snapshot.json>", file=sys.stderr)
        return 1

    try:
        changes = compare(snapshot_at(registry, since), snapshot(registry))
    except DiffUnavailable as exc:
        print(f"cannot read the baseline: {exc}", file=sys.stderr)
        return 1

    for change in changes:
        where = f"{change.app} {change.operation}".strip()
        print(f"{change.level:9} {where:52} {change.kind}: {change.detail}")

    counts = summarize(changes)
    print(f"since {since}: {counts[BREAKING]} breaking, "
          f"{counts['additive']} additive, {counts['info']} informational")
    return 1 if (args.fail_on_breaking and counts[BREAKING]) else 0


def _mcp(config: Config) -> int:
    from .mcp_stdio import run

    config.watch = False
    run(config)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
