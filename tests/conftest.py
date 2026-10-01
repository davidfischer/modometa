from pathlib import Path

import pytest


@pytest.fixture
def test_rules_dir() -> Path:
    """Path to isolated fixture archetype rules directory."""
    return Path(__file__).parent / "fixtures" / "rules"
