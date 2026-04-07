"""Shared pytest fixtures for the mycat_meme test suite."""
from pathlib import Path

import pytest


@pytest.fixture
def fixtures_dir() -> Path:
    """Return the path to the test fixtures directory."""
    return Path(__file__).parent / "fixtures"


@pytest.fixture
def sample_meme(fixtures_dir: Path) -> Path:
    """Return the path to a sample 4:3 meme image fixture."""
    return fixtures_dir / "sample-meme.png"


@pytest.fixture
def sample_cat(fixtures_dir: Path) -> Path:
    """Return the path to a sample user cat photo fixture."""
    return fixtures_dir / "sample-cat.jpg"
