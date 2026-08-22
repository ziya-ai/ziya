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


# Vars pytest itself owns and rewrites per test phase.  Snapshotting them is
# noise, and restoring them would fight pytest's own bookkeeping.
_ENV_GUARD_IGNORE_PREFIXES = ("PYTEST_",)


@pytest.fixture(autouse=True)
def _restore_environ():
    """Undo os.environ mutations a test leaves behind.

    Production setup code (app.config.environment.setup_environment,
    app.cli.setup_env) assigns os.environ DIRECTLY, which monkeypatch cannot
    undo for a var that was unset beforehand -- so a test using delenv is
    isolated from its predecessors but never from its successors.  Three
    separate suite-wide contaminations traced to exactly that:

      * a tmp_path CA bundle whose body was the literal text "fake", left in
        SSL_CERT_FILE / AWS_CA_BUNDLE / REQUESTS_CA_BUNDLE -- every later test
        that built an SSL context died with "ssl.SSLError: [X509] PEM lib";
      * AWS_PROFILE=my-profile -- later suites that ADOPT an already-set
        profile rather than defaulting then hit "ProfileNotFound";
      * ZIYA_MAX_OUTPUT_TOKENS=100 -- later config assertions read 100
        instead of the model's real default.

    Diff-based rather than clear()+update() so only what the test actually
    changed is touched.

    LIMITATION: this cannot undo MODULE-LEVEL writes.  Pytest imports every
    test module during collection, before the first test runs, so an
    import-time ``os.environ[...] = ...`` anywhere in the tree is already in
    effect when the first snapshot is taken.  Those need fixing at the source.
    """
    def _snapshot():
        return {k: v for k, v in os.environ.items()
                if not k.startswith(_ENV_GUARD_IGNORE_PREFIXES)}

    before = _snapshot()
    yield
    after = _snapshot()
    for key in after.keys() - before.keys():
        os.environ.pop(key, None)
    for key in before.keys() - after.keys():
        os.environ[key] = before[key]
    for key in before.keys() & after.keys():
        if before[key] != after[key]:
            os.environ[key] = before[key]


@pytest.fixture
def temp_config_dir(tmp_path):
    """Create a temporary config directory for testing."""
    config_dir = tmp_path / ".ziya"
    config_dir.mkdir()
    return config_dir


@pytest.fixture(autouse=True)
def _isolate_ziya_home(tmp_path, monkeypatch):
    """Redirect ~/.ziya to a per-test sandbox for the whole suite.

    The embedding fixture below isolates embeddings.npz, but NOTHING was
    isolating the stores themselves.  ``MemoryStorage`` and
    ``ProposalsStore`` are module-level singletons that resolve
    ``get_ziya_home() / "memory"`` in ``__init__``, so any test touching
    memory wrote to the developer's real ``~/.ziya/memory/``.  This is not
    hypothetical: a suite run overwrote a live ``proposals.json``
    (26,494 -> 447 bytes) and ``probationary.jsonl`` (1.7 MB -> 6 KB) with
    fixture rows ("A's proposal.", "Retract me once."), destroying real
    user data with no backup.  ``memories.json`` survived only because it
    was encrypted and the tests had no key — i.e. the one file that did
    not lose data was saved by accident, not by design.

    Redirecting via the ZIYA_HOME env var rather than monkeypatching
    ``get_ziya_home`` is deliberate: 30+ modules import that function, most
    calling it per-operation, and ``get_ziya_home`` reads ZIYA_HOME on every
    call.  One env var therefore covers every present and future call site,
    including paths that never go through the two storage singletons
    (activity_counter.json, organize_history.json, keyring.json).

    The singletons must ALSO be reset, before and after: one built by an
    earlier test caches ``self._dir`` and would keep writing to the real
    path despite the env var, and one built inside the sandbox would point
    at a deleted tmp_path once the test ends.

    Note the sandbox has no keyring.json, so ALE degrades to plaintext
    writes inside the sandbox.  That is correct for tests (it makes fixture
    data readable) and is why this fixture does not need to stub encryption.
    """
    sandbox_home = tmp_path / "ziya_home"
    sandbox_home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("ZIYA_HOME", str(sandbox_home))

    # Reset any singleton built before this fixture ran (it holds the REAL
    # path).  Restore the originals afterwards so a developer running the
    # suite in-process doesn't end up with stores pointing at a deleted dir.
    saved = {}
    for mod_name in ("app.storage.memory", "app.storage.proposals"):
        try:
            import importlib
            mod = importlib.import_module(mod_name)
        except Exception:
            continue
        saved[mod_name] = getattr(mod, "_instance", None)
        mod._instance = None

    # The encryptor caches the keyring it loaded from the real home; drop it
    # so it re-resolves against the sandbox.
    try:
        import app.utils.encryption as _enc
        saved_encryptor = getattr(_enc, "_encryptor", None)
        _enc._encryptor = None
    except Exception:
        _enc = None
        saved_encryptor = None

    try:
        yield sandbox_home
    finally:
        for mod_name, prior in saved.items():
            try:
                import importlib
                importlib.import_module(mod_name)._instance = prior
            except Exception:
                pass
        if _enc is not None:
            _enc._encryptor = saved_encryptor


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


@pytest.fixture(autouse=True)
def _disable_launch_preflight(monkeypatch):
    """Turn off the task-run launch preflight for the whole suite.

    The preflight makes a live STS call to check AWS credentials before a
    run starts, and mints a ``held`` run when they are invalid.  That is
    right in production and wrong here twice over:

      * Tests stub the executor, so no model call ever happens — holding
        the run reports an infrastructure problem that is irrelevant to
        what is being tested.  Eight tests across test_api_task_cards and
        test_api_task_bindings failed this way the first time a developer
        ran the suite with expired credentials, all asserting on run
        status and all getting ``held``.
      * It costs a network round-trip per launch (~10s cold), which is
        pure latency for a check whose answer nothing consumes.

    Set ZIYA_SKIP_LAUNCH_PREFLIGHT explicitly in a test that wants to
    exercise the preflight itself.
    """
    monkeypatch.setenv("ZIYA_SKIP_LAUNCH_PREFLIGHT", "1")


@pytest.fixture(autouse=True)
def _ensure_open_event_loop():
    """Guarantee every test starts with an OPEN event loop installed.

    ``asyncio.run()`` closes the loop it creates.  Several test files use it
    (e.g. test_mcp_manager_builtin_dispatch.py), while eight others reach for
    the older ``asyncio.get_event_loop().run_until_complete(...)`` idiom --
    which, after a prior test has closed the loop, dies with "There is no
    current event loop in thread 'MainThread'".

    The failure is therefore ORDER-DEPENDENT, and with pytest-randomly
    installed it surfaces only on some seeds: test_context_management_tools
    passes 25/25 alone but fails 24 when it happens to follow the closer.
    An intermittently-red suite is worse than a consistently-red one, since
    it teaches readers to disregard failures.

    Fixing it here rather than at the ~16 call sites keeps one behaviour in
    one place and protects files not yet written from inheriting the trap.
    This does NOT replace pytest-asyncio: it only repairs the loop that
    sync-style helpers rely on, and pytest-asyncio manages its own loops for
    ``async def`` tests independently.
    """
    import asyncio
    import warnings

    previous = None
    # The probe itself is the deprecated call this fixture exists to make
    # safe, so on 3.12 it emits "DeprecationWarning: There is no current
    # event loop" on the exact path it is repairing.  Suppressed only
    # around the probe: a fixture that adds a warning to every test in the
    # suite trains readers to ignore warnings, which is the same failure
    # mode as an intermittently-red suite.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        try:
            previous = asyncio.get_event_loop_policy().get_event_loop()
        except RuntimeError:
            # No loop bound to this thread — normal on 3.12+ after a close.
            previous = None

    if previous is None or previous.is_closed():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    else:
        loop = previous

    yield

    # Leave a usable loop installed for whatever runs next.  Deliberately
    # NOT closing the loop we created: closing it here would recreate the
    # exact condition this fixture exists to prevent for the next test.
    if loop.is_closed():
        asyncio.set_event_loop(asyncio.new_event_loop())


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
