"""
Tests for the first-run MCP config seed (live config + example sibling).

The properties that matter, in order of how badly they break the user:

1. The live config must be valid JSON.  json.load is what MCPManager uses; a
   file with // or # comments would be reported as config_error, which is a
   broken config out of the box.
2. The live config must be SHORT.  It is the first file a new user opens, and
   the previous 24-line _README array is the defect this design replaces.
3. The example file must never be loadable as config, or its entries present as
   failed servers.
4. A user-modified config must never be overwritten.
5. Failure must be silent and non-fatal.
"""

import json
import os
import re
import stat
from pathlib import Path
from unittest.mock import patch

import pytest

from app.utils.mcp_config_seed import (
    CONFIG_SEED_DOCUMENT,
    EXAMPLE_DOCUMENT,
    MCP_CONFIG_FILENAME,
    MCP_EXAMPLE_FILENAME,
    ensure_mcp_config_seed,
    mcp_config_file,
    mcp_example_file,
)


@pytest.fixture
def ziya_home(tmp_path, monkeypatch):
    """Point get_ziya_home() at a temp dir via ZIYA_HOME."""
    home = tmp_path / "dot-ziya"
    monkeypatch.setenv("ZIYA_HOME", str(home))
    return home


# ---------------------------------------------------------------------------
# Creation
# ---------------------------------------------------------------------------

def test_creates_both_files_when_absent(ziya_home):
    created = ensure_mcp_config_seed()
    assert created == ziya_home / MCP_CONFIG_FILENAME
    assert created.exists()
    assert (ziya_home / MCP_EXAMPLE_FILENAME).exists()


def test_both_files_are_valid_json(ziya_home):
    """json.load must succeed on the config — it is the parser MCPManager uses.

    The example file is held to the same bar even though Ziya never loads it:
    an entry copied out of it must not carry a comment into the live config.
    """
    ensure_mcp_config_seed()
    for path in (mcp_config_file(), mcp_example_file()):
        with open(path) as f:
            assert isinstance(json.load(f), dict), path


@pytest.mark.parametrize("filename", [MCP_CONFIG_FILENAME, MCP_EXAMPLE_FILENAME])
def test_no_json_comment_syntax(ziya_home, filename):
    """No comment tokens outside string literals, in either file.

    Checked structurally rather than by a bare substring scan: '//' occurs
    legitimately inside string VALUES (a URL, and the advice text warning
    against comments), and those are fine — json only chokes on a comment token
    in a syntactic position.  String literals are stripped first so this fails
    on a real commented-out example and passes on prose that mentions one.

    '#' is included because it is the form the obvious fix reaches for, and it
    is rejected by json.load exactly as '//' is.
    """
    ensure_mcp_config_seed()
    raw = (ziya_home / filename).read_text()
    stripped = re.sub(r'"(?:[^"\\]|\\.)*"', '""', raw)
    assert "//" not in stripped
    assert "/*" not in stripped
    assert "#" not in stripped


def test_live_config_is_short(ziya_home):
    """The readability property, pinned as a number.

    The rejected design put a 24-entry _README array in the file a new user
    opens first.  A ceiling here is crude but it is the only thing that
    actually fails when documentation creeps back into the live config.
    """
    path = ensure_mcp_config_seed()
    lines = path.read_text().strip().splitlines()
    assert len(lines) <= 8, (
        f"live config grew to {len(lines)} lines; documentation belongs in "
        f"{MCP_EXAMPLE_FILENAME}, not the file the user edits"
    )


def test_live_config_has_only_mcpservers_and_one_help_string(ziya_home):
    """Structural form of the same property: no nested documentation blocks."""
    data = json.loads(ensure_mcp_config_seed().read_text())
    assert set(data.keys()) == {"_help", "mcpServers"}
    assert isinstance(data["_help"], str), (
        "_help must be a single string; an array of lines is what made the "
        "previous seed unreadable"
    )


def test_live_config_mcpservers_is_present_and_empty(ziya_home):
    """The key the loader reads must exist so its location is discoverable,
    and be empty so nothing is launched."""
    data = json.loads(ensure_mcp_config_seed().read_text())
    assert data["mcpServers"] == {}


def test_help_points_at_the_example_file_that_exists(ziya_home):
    """A pointer to a missing file is worse than no pointer."""
    data = json.loads(ensure_mcp_config_seed().read_text())
    assert MCP_EXAMPLE_FILENAME in data["_help"]
    assert (ziya_home / MCP_EXAMPLE_FILENAME).exists()


# ---------------------------------------------------------------------------
# The example file cannot become live config
# ---------------------------------------------------------------------------

def test_example_filename_is_not_the_config_filename(ziya_home):
    """_find_config_file matches the literal name 'mcp_config.json'.

    Nothing in the codebase globs ~/.ziya/*.json, so a distinct filename is
    what makes the example inert.  Pinned because a rename to something like
    'mcp_config.json.example' would still be safe, but shortening it to
    'mcp_config.json' would silently make the examples live.
    """
    assert MCP_EXAMPLE_FILENAME != MCP_CONFIG_FILENAME
    assert mcp_example_file() != mcp_config_file()


def test_example_entries_are_disabled(ziya_home):
    """Defense in depth: the example is never loaded, but a user copying an
    entry wholesale must not get a server that fails to connect."""
    ensure_mcp_config_seed()
    data = json.loads(mcp_example_file().read_text())
    for name, cfg in data["mcpServers"].items():
        assert cfg.get("enabled") is False, f"{name} should ship disabled"


def test_example_entries_would_be_launchable(ziya_home):
    """Confirms the premise of the isolation tests rather than assuming it.

    MCPManager accepts any mcpServers entry carrying command/url/
    installation_path.  If these examples did NOT satisfy that, the isolation
    above would be vacuously true and the examples would also be useless as
    things to copy.
    """
    ensure_mcp_config_seed()
    data = json.loads(mcp_example_file().read_text())
    assert data["mcpServers"], "example block must not be empty"
    for name, cfg in data["mcpServers"].items():
        assert ("command" in cfg) or ("url" in cfg), name


def test_example_entries_do_not_leak_into_live_config(ziya_home):
    """The two files must not share server names in the live document."""
    ensure_mcp_config_seed()
    live = json.loads(mcp_config_file().read_text())
    example = json.loads(mcp_example_file().read_text())
    assert set(live["mcpServers"]) & set(example["mcpServers"]) == set()


def test_example_entries_pass_config_validation(ziya_home):
    """An entry copied out of the example must not immediately produce
    findings in the MCP status panel.

    This is the defect the previous seed shipped: its examples carried a
    '_comment' key that the validator reported as unknown, so following the
    README's own instructions produced an error.
    """
    from app.mcp.config_validation import validate_config

    ensure_mcp_config_seed()
    raw = mcp_example_file().read_text()
    findings = validate_config(json.loads(raw), raw)
    assert findings == [], f"example entries produce findings: {findings}"


def test_live_config_passes_config_validation(ziya_home):
    from app.mcp.config_validation import validate_config

    path = ensure_mcp_config_seed()
    raw = path.read_text()
    assert validate_config(json.loads(raw), raw) == []


# ---------------------------------------------------------------------------
# No-clobber, and the one deliberate exception
# ---------------------------------------------------------------------------

def test_user_modified_config_is_not_overwritten(ziya_home):
    """A hand-edited config must survive an upgrade."""
    ziya_home.mkdir(parents=True, exist_ok=True)
    path = ziya_home / MCP_CONFIG_FILENAME
    original = json.dumps({"mcpServers": {"mine": {"command": "echo"}}})
    path.write_text(original)

    assert ensure_mcp_config_seed() is None
    assert path.read_text() == original


def test_idempotent(ziya_home):
    """Second call is a no-op, not a rewrite."""
    first = ensure_mcp_config_seed()
    body = first.read_text()
    example_body = mcp_example_file().read_text()

    assert ensure_mcp_config_seed() is None
    assert first.read_text() == body
    assert mcp_example_file().read_text() == example_body


def test_pristine_legacy_seed_is_migrated(ziya_home):
    """An untouched copy of the old verbose seed is replaced.

    No-clobber would otherwise protect documentation Ziya wrote itself,
    leaving every existing install with the unreadable file forever.
    """
    ziya_home.mkdir(parents=True, exist_ok=True)
    path = ziya_home / MCP_CONFIG_FILENAME
    path.write_text(json.dumps({
        "_README": ["line one", "line two"],
        "_example_mcpServers": {"example-stdio-server": {"command": "npx"}},
        "mcpServers": {},
    }, indent=2))

    result = ensure_mcp_config_seed()

    assert result == path
    data = json.loads(path.read_text())
    assert set(data.keys()) == {"_help", "mcpServers"}
    assert "_README" not in data


def test_legacy_seed_with_user_servers_is_not_migrated(ziya_home):
    """The discriminator that keeps migration safe.

    A user who added a server to the old seed has content worth keeping, so
    the file must be left exactly as-is even though its shape still looks
    legacy.  This is the test that fails on the naive "has _README, replace
    it" implementation.
    """
    ziya_home.mkdir(parents=True, exist_ok=True)
    path = ziya_home / MCP_CONFIG_FILENAME
    original = json.dumps({
        "_README": ["line one"],
        "_example_mcpServers": {"example-stdio-server": {"command": "npx"}},
        "mcpServers": {"mine": {"command": "echo", "args": ["hi"]}},
    }, indent=2)
    path.write_text(original)

    assert ensure_mcp_config_seed() is None
    assert path.read_text() == original


def test_legacy_migration_ignores_files_with_extra_keys(ziya_home):
    """Key-set equality, not a subset test: an unrecognised sibling key means
    something else wrote this file, so leave it alone."""
    ziya_home.mkdir(parents=True, exist_ok=True)
    path = ziya_home / MCP_CONFIG_FILENAME
    original = json.dumps({
        "_README": ["line one"],
        "_example_mcpServers": {},
        "mcpServers": {},
        "tools": {"taskmanager": {"command": "npx"}},
    }, indent=2)
    path.write_text(original)

    assert ensure_mcp_config_seed() is None
    assert path.read_text() == original


def test_unparseable_config_is_not_migrated(ziya_home):
    """A syntax error is the user's to fix; silently replacing the file would
    destroy whatever they were mid-edit on."""
    ziya_home.mkdir(parents=True, exist_ok=True)
    path = ziya_home / MCP_CONFIG_FILENAME
    original = '{ "mcpServers": { BROKEN } }'
    path.write_text(original)

    assert ensure_mcp_config_seed() is None
    assert path.read_text() == original


def test_missing_example_is_recreated_beside_existing_config(ziya_home):
    """An install predating the example file gains one without its config
    being touched."""
    ziya_home.mkdir(parents=True, exist_ok=True)
    config = ziya_home / MCP_CONFIG_FILENAME
    original = json.dumps({"mcpServers": {"mine": {"command": "echo"}}})
    config.write_text(original)

    assert ensure_mcp_config_seed() is None
    assert (ziya_home / MCP_EXAMPLE_FILENAME).exists()
    assert config.read_text() == original


def test_user_edited_example_is_not_overwritten(ziya_home):
    ensure_mcp_config_seed()
    path = mcp_example_file()
    path.write_text('{"mcpServers": {"mine": {"command": "echo"}}}')
    body = path.read_text()

    ensure_mcp_config_seed()
    assert path.read_text() == body


# ---------------------------------------------------------------------------
# Failure handling
# ---------------------------------------------------------------------------

def test_file_mode_is_owner_only(ziya_home):
    """Users do put inline tokens in the config; neither file should be
    group/world readable regardless of the directory's mode."""
    ensure_mcp_config_seed()
    for path in (mcp_config_file(), mcp_example_file()):
        mode = stat.S_IMODE(os.stat(path).st_mode)
        assert mode & (stat.S_IRGRP | stat.S_IROTH | stat.S_IWGRP | stat.S_IWOTH) == 0, path


def test_unwritable_home_is_non_fatal(ziya_home):
    """A failure to seed must not raise — startup cannot depend on this."""
    with patch("app.utils.mcp_config_seed._write_json", side_effect=OSError("read-only fs")):
        assert ensure_mcp_config_seed() is None


def test_no_partial_file_left_on_write_failure(ziya_home):
    """Temp-and-rename: an interrupted write must not leave a truncated
    document that the manager would report as a syntax error."""
    real_dump = json.dump

    def exploding_dump(obj, fh, **kwargs):
        real_dump({"partial": True}, fh, **kwargs)
        raise OSError("disk full")

    with patch("app.utils.mcp_config_seed.json.dump", side_effect=exploding_dump):
        assert ensure_mcp_config_seed() is None

    assert not (ziya_home / MCP_CONFIG_FILENAME).exists()
    leftovers = list(ziya_home.glob("*.tmp"))
    assert leftovers == [], f"temp file not cleaned up: {leftovers}"


# ---------------------------------------------------------------------------
# Drift and integration
# ---------------------------------------------------------------------------

def test_documents_match_written_files(ziya_home):
    """The in-memory constants and the on-disk artifacts must not drift."""
    ensure_mcp_config_seed()
    assert json.loads(mcp_config_file().read_text()) == CONFIG_SEED_DOCUMENT
    assert json.loads(mcp_example_file().read_text()) == EXAMPLE_DOCUMENT


def test_paths_follow_ziya_home(ziya_home):
    assert mcp_config_file() == ziya_home / MCP_CONFIG_FILENAME
    assert mcp_example_file() == ziya_home / MCP_EXAMPLE_FILENAME


def test_manager_finds_the_seeded_config_under_ziya_home(ziya_home, monkeypatch, tmp_path):
    """_find_config_file must resolve to the file the seed wrote.

    This is the regression that made the previous round unverifiable: the seed
    used get_ziya_home() while the manager hardcoded ~/.ziya, so a ZIYA_HOME
    run seeded one file and read another.  cwd and the project root are moved
    out of the way because they take precedence in the search order.
    """
    from app.mcp.manager import MCPManager

    seeded = ensure_mcp_config_seed()
    empty_cwd = tmp_path / "elsewhere"
    empty_cwd.mkdir()
    monkeypatch.chdir(empty_cwd)

    with patch("pathlib.Path.exists", autospec=True) as mock_exists:
        # Only let the seeded path report existence, so a stray project-root
        # config on the developer's machine cannot win the search.
        mock_exists.side_effect = lambda self: str(self) == str(seeded)
        found = MCPManager(config_path=None)._find_config_file()

    assert found == str(seeded)


def test_seeded_config_loads_through_mcp_manager_without_error(ziya_home):
    """End-to-end: the manager must parse the seed cleanly and derive no user
    servers from it."""
    from app.mcp.manager import MCPManager

    path = ensure_mcp_config_seed()
    manager = MCPManager(config_path=str(path))

    with open(manager.config_path) as f:
        data = json.load(f)

    assert data.get("mcpServers") == {}
    assert manager.config_error is None
