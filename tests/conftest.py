"""Shared fixtures.

The sample doc set in api-docs/ is the fixture: exercising the real thing keeps
the tests honest about what a hand-written multi-spec doc set looks like. Tests
assert on structure, never on the sample's subject matter, so replacing the
sample does not break them.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from apiwarden.config import Config
from apiwarden.loader import load_registry
from apiwarden.router import Portal

SAMPLE_ROOT = Path(__file__).resolve().parent.parent / "api-docs"


@pytest.fixture
def sample_root() -> Path:
    return SAMPLE_ROOT


@pytest.fixture
def registry():
    return load_registry(SAMPLE_ROOT)


@pytest.fixture
def config() -> Config:
    return Config(root=SAMPLE_ROOT, title="Test portal")


@pytest.fixture
def portal(config, registry) -> Portal:
    return Portal(config, registry)


@pytest.fixture
def spec_copy(tmp_path: Path) -> Path:
    """A writable copy of the sample, for tests that edit specs."""
    import shutil

    target = tmp_path / "api-docs"
    shutil.copytree(SAMPLE_ROOT, target)
    return target
