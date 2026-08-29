"""Shared fixtures.

tests/fixtures/sample-api/ is a small, generic doc set that this repo owns
outright — two specs, deliberately unrelated to any real project, built to
exercise every shape the tests need (a shared path-level parameter, a vendor
extension block, $ref'd shared responses, typed response fields). Tests
assert on structure, never on its subject matter, so replacing it later does
not break them.

This is NOT the same thing as a real project's api-docs/ directory, which is
someone's own data and must never be committed here — see the
sample-specs-are-not-the-product memory for why that distinction matters.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from apiwarden.config import Config
from apiwarden.loader import load_registry
from apiwarden.router import Portal

SAMPLE_ROOT = Path(__file__).resolve().parent / "fixtures" / "sample-api"


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
