"""MCP over stdio, for an agent running on the same machine.

    docwarden mcp ./api-docs

The stdio transport is newline-delimited JSON-RPC, so this is framing around
the same handle_rpc() the HTTP endpoint uses: one definition of what an agent
can ask for, no second implementation to keep in step, and no dependency on an
SDK whose API changes between major versions.
"""

from __future__ import annotations

import json
import sys

from .config import Config
from .loader import reload_if_changed
from .mcp_http import PARSE_ERROR, handle_rpc
from .router import build_portal


def run(config: Config, stdin=None, stdout=None) -> None:
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout

    # No watcher thread: reload before each message instead, so a long-lived
    # agent session never answers from a spec that has since changed.
    portal = build_portal(config, watch=False)

    for line in stdin:
        line = line.strip()
        if not line:
            continue

        try:
            payload = json.loads(line)
        except ValueError as exc:
            _write(stdout, {"jsonrpc": "2.0", "id": None, "error": {"code": PARSE_ERROR, "message": str(exc)}})
            continue

        reload_if_changed(portal.registry, portal.config.sources or None)

        reply = handle_rpc(payload, portal.registry, portal.config)
        if reply is not None:
            _write(stdout, reply)


def _write(stdout, message) -> None:
    # One message per line: the framing forbids embedded newlines.
    stdout.write(json.dumps(message, ensure_ascii=False) + "\n")
    stdout.flush()
