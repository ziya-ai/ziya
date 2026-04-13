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


# Safety-net per-test timeout.  Prevents any single test from blocking
# the entire suite when pytest-timeout is not installed.
_TEST_TIMEOUT_SECS = 60


@pytest.fixture(autouse=True)
def _enforce_test_timeout():
    """Kill the current test if it exceeds _TEST_TIMEOUT_SECS.

    Uses signal.alarm (Unix only) as a hard backstop.  Only active when
    pytest-timeout is NOT installed (to avoid double-timeout conflicts).
    """
    try:
        import pytest_timeout  # noqa: F401
        yield  # pytest-timeout handles timeouts via pytest.ini
        return
    except ImportError:
        pass

    import signal

    def _timeout_handler(signum, frame):
        raise TimeoutError(f"Test exceeded {_TEST_TIMEOUT_SECS}s safety timeout")

    # signal.alarm is Unix-only and main-thread-only
    try:
        old_handler = signal.signal(signal.SIGALRM, _timeout_handler)
        signal.alarm(_TEST_TIMEOUT_SECS)
    except (OSError, ValueError, AttributeError):
        # Windows or not-main-thread — skip
        yield
        return

    try:
        yield
    finally:
        signal.alarm(0)  # cancel pending alarm
        signal.signal(signal.SIGALRM, old_handler)
