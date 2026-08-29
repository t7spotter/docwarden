"""Configuration, shared by the CLI and the Django plugin."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Keys accepted from settings.APIDOCS_LIVE, apidocs.toml, or CLI flags.
_KEYS = (
    "root",
    "title",
    "servers",
    "renderer",
    "theme",
    "watch",
    "token",
    "base_path",
    "sources",
)


@dataclass
class Config:
    # Directory holding the specs (the api-docs/ folder).
    root: Path = Path("api-docs")
    # Portal title shown in the shell.
    title: str = "API Documentation"
    # Server URLs offered in the try-it panel. Empty means use each spec's own
    # servers block.
    servers: list[str] = field(default_factory=list)
    # "vendor" serves the bundled renderer; "cdn" loads it from jsdelivr.
    renderer: str = "vendor"
    # "auto" follows the reader's OS setting; "light"/"dark" pin it.
    theme: str = "auto"
    # Poll the spec files for changes and push live reload over SSE.
    watch: bool = False
    # When set, every route requires this shared token.
    token: str | None = None
    # URL prefix the portal is mounted under, e.g. "/api-docs".
    base_path: str = ""
    # Explicit {name: path} overriding discovery.
    sources: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.root = Path(self.root).expanduser()
        self.base_path = "/" + self.base_path.strip("/") if self.base_path.strip("/") else ""
        if self.renderer not in ("vendor", "cdn"):
            raise ValueError(f"renderer must be 'vendor' or 'cdn', got {self.renderer!r}")
        if self.theme not in ("auto", "light", "dark"):
            raise ValueError(f"theme must be 'auto', 'light' or 'dark', got {self.theme!r}")

    def url(self, path: str) -> str:
        """Absolute URL path for a route, honouring base_path."""
        return f"{self.base_path}/{path.lstrip('/')}" if path.strip("/") else f"{self.base_path}/"


def from_dict(values: dict[str, Any]) -> Config:
    unknown = set(values) - set(_KEYS)
    if unknown:
        raise ValueError(f"unknown apidocs-live settings: {', '.join(sorted(unknown))}")
    return Config(**values)


def from_toml(path: Path) -> Config:
    """Read an apidocs.toml. Falls back to defaults when the file is absent."""
    if not path.exists():
        return Config()
    import tomllib

    data = tomllib.loads(path.read_text(encoding="utf-8"))
    section = data.get("apidocs", data)
    return from_dict({k: v for k, v in section.items() if k in _KEYS})


def find_toml(start: Path) -> Path | None:
    """Look for apidocs.toml beside the spec root, then in its parents."""
    for directory in (start, *start.parents):
        candidate = directory / "apidocs.toml"
        if candidate.exists():
            return candidate
        if (directory / ".git").exists():
            break
    return None


def env_token() -> str | None:
    return os.environ.get("APIDOCS_TOKEN") or None
