"""
Pytest configuration and shared fixtures.
"""

import pytest
import pytest_asyncio
import sys
import os
from pathlib import Path

# Set asyncio mode
pytest_plugins = ('pytest_asyncio',)

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))


def pytest_configure(config):
    """Configure pytest with custom markers."""
    config.addinivalue_line(
        "markers", 
        "integration: mark test as integration test (requires network)"
    )
    config.option.asyncio_mode = "auto"


@pytest.fixture
def temp_config_dir(tmp_path):
    """Create a temporary config directory for testing."""
    config_dir = tmp_path / ".ziya"
    config_dir.mkdir()
    return config_dir
