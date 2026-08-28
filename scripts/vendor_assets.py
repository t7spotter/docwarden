#!/usr/bin/env python3
"""Download the renderer bundle into src/apidocs_live/static/vendor/.

Run once after cloning; the result is committed so the wheel is self-contained
and the portal works with no network access.

    python scripts/vendor_assets.py
"""

from __future__ import annotations

import sys
import urllib.request
from pathlib import Path

SCALAR_VERSION = "1.67.0"
SCALAR_URL = (
    f"https://cdn.jsdelivr.net/npm/@scalar/api-reference@{SCALAR_VERSION}"
    "/dist/browser/standalone.js"
)

VENDOR_DIR = Path(__file__).resolve().parent.parent / "src" / "apidocs_live" / "static" / "vendor"


def main() -> int:
    VENDOR_DIR.mkdir(parents=True, exist_ok=True)
    target = VENDOR_DIR / "scalar.js"

    print(f"downloading {SCALAR_URL}")
    with urllib.request.urlopen(SCALAR_URL, timeout=60) as response:
        payload = response.read()

    if len(payload) < 100_000:
        print(f"error: unexpectedly small download ({len(payload)} bytes)", file=sys.stderr)
        return 1

    target.write_bytes(payload)
    (VENDOR_DIR / "VERSION").write_text(f"@scalar/api-reference@{SCALAR_VERSION}\n", encoding="utf-8")
    print(f"wrote {target} ({len(payload) / 1_048_576:.1f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
