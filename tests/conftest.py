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


@pytest.fixture(autouse=True)
def _isolate_embedding_singletons(tmp_path, monkeypatch):
    """Force every test to use an isolated embedding cache and Noop provider.

    Without this, tests that exercise MemoryStorage.save() walk through
    embed_and_cache() -> get_embedding_cache() -> the real
    ~/.ziya/memory/embeddings.npz singleton.  Even tests that look harmless
    (e.g. building an in-memory store with a few demo memories) write
    embeddings into the user's real cache.  Earlier audit found 7 of 15
    memory test files leaking.

    Behaviour:
      - Provider singleton is reset and ZIYA_EMBEDDING_PROVIDER=none is set
        so embed_and_cache short-circuits before hitting Bedrock.
      - Cache singleton is reset and pointed at a per-test tmp_path so
        any code path that builds a fresh cache (or that bypasses the
        provider check) writes to a sandbox instead of the real npz.
      - Singletons are restored after the test.

    Tests that need a real embedding cache (the embedding-integration
    file, for example) replace `_provider`/`_cache` directly and don't
    rely on this fixture.
    """
    monkeypatch.setenv("ZIYA_EMBEDDING_PROVIDER", "none")
    try:
        import app.services.embedding_service as _es
    except Exception:
        # embedding service not importable in this environment — nothing to do
        yield
        return

    saved_provider = getattr(_es, "_provider", None)
    saved_cache = getattr(_es, "_cache", None)
    _es._provider = None  # Force NoopProvider on next get_embedding_provider()

    # Build a sandbox cache pointing at tmp_path.  Lazy import to avoid
    # circular issues if EmbeddingCache isn't importable here.
    try:
        sandbox_dir = tmp_path / "embed_sandbox"
        sandbox_dir.mkdir(parents=True, exist_ok=True)
        _es._cache = _es.EmbeddingCache(sandbox_dir)
    except Exception:
        _es._cache = None
    try:
        yield
    finally:
        _es._provider = saved_provider
        _es._cache = saved_cache


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
