"""server_only_paths: directories only the server may write.

The evidence ledger, job runner and data-source connectors keep records
that a tool must not be able to forge (design/research-harness.md,
"Conflicts and resolutions" 1).  The write policy was allow-only and
``.ziya/`` is a safe path, so ``.ziya/ledger/`` was writable by any tool.

Asserted at every sink a write can take, since a deny that lives only in
WritePolicyManager is skipped by the YOLO and task-scope short-circuits in
``fileio._check_write_allowed`` and by the shell's task-scope fallback.
Every refusal is paired with a sibling path that is still allowed, so a
check that refused everything would fail here.
"""

import os

import pytest

from app.config.write_policy import WritePolicyManager, SERVER_ONLY_PATHS
from app.mcp.tools.fileio import _check_write_allowed
from app.mcp_servers.shell_server import ShellServer
from app.mcp_servers.write_policy import ShellWriteChecker
from app.utils import write_grants


@pytest.fixture
def root(tmp_path):
    proj = tmp_path / "proj"
    (proj / ".ziya").mkdir(parents=True)
    return str(proj)


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / "ziyahome"
    h.mkdir()
    monkeypatch.setenv("ZIYA_HOME", str(h))
    return str(h)


@pytest.fixture
def pm(root):
    m = WritePolicyManager()
    m._project_root = root
    return m


DENIED = [".ziya/ledger/obs.jsonl", ".ziya/jobs/j1/manifest.json", ".ziya/ledger"]


def test_listed_entries():
    assert set(SERVER_ONLY_PATHS) == {".ziya/ledger/", ".ziya/jobs/", "~/.ziya/datasources/"}


class TestManager:
    @pytest.mark.parametrize("rel", DENIED)
    def test_project_entries_refused(self, pm, root, rel):
        assert WritePolicyManager.is_server_only_path(rel, root)
        assert not pm.is_write_allowed(rel, root)
        ok, reason = pm.check_write(rel, root)
        assert not ok and "server" in reason.lower()

    def test_sibling_in_ziya_still_allowed(self, pm, root):
        assert pm.is_write_allowed(".ziya/ledger_notes.md", root)
        assert pm.is_write_allowed(".ziya/notes/ledger/x.md", root)

    def test_user_level_datasources_refused(self, pm, root, home):
        target = os.path.join(home, "datasources", "src.json")
        assert WritePolicyManager.is_server_only_path(target, root)
        # Widening the policy to the whole home must not reopen it.
        pm._policy["safe_write_paths"].append(home + "/")
        assert pm.is_write_allowed(os.path.join(home, "other.json"), root)
        assert not pm.is_write_allowed(target, root)

    def test_traversal_and_case_variants_refused(self, pm, root):
        assert not pm.is_write_allowed(".ziya/x/../ledger/a", root)
        assert not pm.is_write_allowed(".ZIYA/Ledger/a", root)

    def test_symlink_into_ledger_refused(self, pm, root):
        os.makedirs(os.path.join(root, ".ziya", "ledger"))
        os.symlink(os.path.join(root, ".ziya", "ledger"), os.path.join(root, ".ziya", "alias"))
        assert pm.is_write_allowed(".ziya/other.md", root)
        assert not pm.is_write_allowed(".ziya/alias/obs.jsonl", root)

    def test_direct_write_modes_cannot_reopen(self, pm, root):
        pm._policy["direct_write_mode"] = "all_files"
        ok, _ = pm.is_direct_write_allowed(".ziya/ledger/a", root, file_exists=False)
        assert not ok
        ok, _ = pm.is_direct_write_allowed("src/a.py", root, file_exists=False)
        assert ok


class TestFileWriteSink:
    def test_refused_even_in_yolo(self, root, monkeypatch):
        monkeypatch.setenv("ZIYA_YOLO_MODE", "1")
        assert _check_write_allowed(".ziya/ledger/a.jsonl", root) != ""
        assert _check_write_allowed(".ziya/scratch.md", root) == ""

    def test_task_grant_cannot_reopen(self, root):
        assert "server" in write_grants.floor_reason(".ziya/jobs/j/x", root).lower()
        assert write_grants.floor_reason(".ziya/scratch.md", root) == ""


class TestShellSink:
    @pytest.fixture
    def checker(self, root):
        pm = WritePolicyManager()
        pm.load_for_project("test-project", root)
        c = ShellWriteChecker(pm)
        c.set_project_root(root)
        yield c
        c.clear_project_root()
        c.clear_task_scope()

    @pytest.fixture
    def split(self):
        return ShellServer()._split_by_shell_operators

    def test_redirect_and_destructive_refused(self, checker, split):
        assert checker.check("echo hi > .ziya/notes.md", split)[0]
        assert not checker.check("echo hi > .ziya/ledger/obs.jsonl", split)[0]
        assert not checker.check("rm .ziya/jobs/j1/manifest.json", split)[0]

    def test_task_scope_cannot_reopen(self, checker, split, root):
        checker.set_task_scope({"project_root": root,
                                "writable": [{"path": ".ziya/ledger", "is_dir": True}]})
        assert not checker.check("echo hi > .ziya/ledger/obs.jsonl", split)[0]
