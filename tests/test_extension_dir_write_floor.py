"""
Regression coverage for PenPal #169 (CWE-434): a ``.py`` written into the
install extension directory (``app/extensions/prompt_extensions/``) is executed
at the next startup by ``PromptExtensionManager.load_extensions_from_directory``
(``spec.loader.exec_module``), so a write there is *persistent* code execution.

The fix makes that directory a HARD write floor: refused regardless of write
policy, ``direct_write_mode``, or a task-scope grant, and — on the ``file_write``
tool path — even under in-process YOLO. Two enforcement sites:

  * ``WritePolicyManager.is_install_extension_path`` + guards in
    ``is_write_allowed`` / ``is_direct_write_allowed`` (Site A). This covers the
    shell path too, since a shell redirect/``cp`` flows through
    ``is_write_allowed``.
  * ``fileio._check_write_allowed`` consults the floor BEFORE its YOLO-env and
    task-scope short-circuits (Site B), which otherwise return "" (allowed)
    without reaching the manager.

Deliberately NOT covered, and asserted here as the documented boundary:
shell-under-YOLO. YOLO's purpose is unrestricted in-process writes
(``python3 -c "open('app/extensions/x.py','w')"``), so no shell-side guard can
make #169 complete there without pretending to a completeness it cannot deliver.
That path is left to YOLO's own "you turned off the guard" contract.

Every negative (refused) assertion is paired with the positive control that the
same sink still ALLOWS a legitimate write, so a test cannot pass by refusing
everything.
"""
import os
import pytest

from app.config.write_policy import WritePolicyManager, DEFAULT_WRITE_POLICY

# The real directory the loader executes from — the guard must match THIS,
# not a hardcoded string, or a future relocation silently unguards it.
import app.config.write_policy as _wp
_EXT_DIR = _wp._INSTALL_EXTENSIONS_DIR
_APP_ROOT = _wp._INSTALL_APP_ROOT


def _mgr(mode="none", extra_safe=None, patterns=None):
    """A manager whose in-memory policy we control directly, so tests do not
    depend on any on-disk project.json."""
    m = WritePolicyManager()
    pol = dict(DEFAULT_WRITE_POLICY)
    pol["direct_write_mode"] = mode
    pol["safe_write_paths"] = list(DEFAULT_WRITE_POLICY["safe_write_paths"]) + list(extra_safe or [])
    pol["allowed_write_patterns"] = list(patterns or [])
    m._policy = pol
    # Pretend the root resolved so _ensure_loaded_for_root is a no-op.
    m._unresolved_roots.add(_APP_ROOT)
    return m


_EVIL = os.path.join(_EXT_DIR, "prompt_extensions", "evil.py")
_EVIL_REL = "app/extensions/prompt_extensions/evil.py"


# ---------------------------------------------------------------------------
# Site A — WritePolicyManager, the shared floor
# ---------------------------------------------------------------------------
class TestManagerFloor:
    def test_helper_flags_the_extension_dir(self):
        assert WritePolicyManager.is_install_extension_path(_EVIL, _APP_ROOT) is True

    def test_helper_flags_the_dir_itself(self):
        assert WritePolicyManager.is_install_extension_path(_EXT_DIR, _APP_ROOT) is True

    def test_helper_ignores_a_sibling_prefix_dir(self):
        # app/extensions-backup/ must NOT match app/extensions/ (CWE-22 prefix trap).
        sib = _APP_ROOT + os.sep + "extensions-backup" + os.sep + "x.py"
        assert WritePolicyManager.is_install_extension_path(sib, _APP_ROOT) is False

    def test_helper_ignores_an_ordinary_path(self):
        assert WritePolicyManager.is_install_extension_path(
            os.path.join(_APP_ROOT, "server.py"), _APP_ROOT) is False

    @pytest.mark.parametrize("mode", ["none", "new_files", "all_files"])
    def test_is_direct_write_allowed_refuses_under_every_mode(self, mode):
        allowed, reason = _mgr(mode=mode).is_direct_write_allowed(_EVIL, _APP_ROOT, file_exists=False)
        assert allowed is False
        assert "extension" in reason.lower()

    def test_all_files_still_allows_an_ordinary_project_file(self):
        # Positive control: the same all_files mode that must refuse the
        # extension dir must still permit a normal in-project write.
        allowed, _ = _mgr(mode="all_files").is_direct_write_allowed(
            os.path.join(_APP_ROOT, "server.py"), _APP_ROOT, file_exists=True)
        assert allowed is True

    def test_is_write_allowed_refuses_even_with_a_widening_pattern(self):
        # A user pattern of "*.py" must not re-open the extension dir.
        m = _mgr(patterns=["*.py"])
        assert m.is_write_allowed(_EVIL, _APP_ROOT) is False
        # ...but the pattern still works for a normal file (positive control).
        assert m.is_write_allowed(os.path.join(_APP_ROOT, "misc.py"), _APP_ROOT) is True


# ---------------------------------------------------------------------------
# Site B — file_write, ahead of its YOLO-env and task-scope short-circuits
# ---------------------------------------------------------------------------
class TestFileWriteFloor:
    def _check(self, monkeypatch, *, yolo=False):
        # The Site B guard is a staticmethod on the real WritePolicyManager,
        # checked BEFORE any manager is fetched, so refusal needs no patched
        # manager — it fires on the real class against the real _EVIL path.
        from app.mcp.tools import fileio
        if yolo:
            monkeypatch.setenv("ZIYA_YOLO_MODE", "1")
        else:
            monkeypatch.delenv("ZIYA_YOLO_MODE", raising=False)
        return fileio._check_write_allowed(_EVIL, _APP_ROOT, file_exists=False)

    def test_refused_normally(self, monkeypatch):
        assert self._check(monkeypatch) != ""

    def test_refused_under_yolo_env(self, monkeypatch):
        # The sharp one: YOLO-env returns "" (allowed) before the manager is
        # ever consulted, so the floor must sit ahead of it.
        assert self._check(monkeypatch, yolo=True) != ""

    def test_refused_under_task_scope_grant(self, monkeypatch):
        from app.mcp.tools import fileio
        monkeypatch.setattr(fileio, "_check_task_scope_write", lambda *a, **k: True)
        monkeypatch.delenv("ZIYA_YOLO_MODE", raising=False)
        assert fileio._check_write_allowed(_EVIL, _APP_ROOT, file_exists=False) != ""

    def test_allows_a_normal_project_write(self, monkeypatch):
        # Positive control — the floor must not break ordinary file_write.
        # This DOES need a permissive manager, patched at its real import
        # source (the function imports get_write_policy_manager locally from
        # app.config.write_policy).
        import app.config.write_policy as wp
        from app.mcp.tools import fileio
        monkeypatch.setattr(wp, "get_write_policy_manager", lambda: _mgr(mode="all_files"))
        monkeypatch.delenv("ZIYA_YOLO_MODE", raising=False)
        assert fileio._check_write_allowed(
            os.path.join(_APP_ROOT, "server.py"), _APP_ROOT, file_exists=True) == ""


# ---------------------------------------------------------------------------
# Documented boundary — shell-under-YOLO is intentionally NOT covered
# ---------------------------------------------------------------------------
class TestYoloShellBoundary:
    def test_the_guard_dir_is_the_loader_dir(self):
        # The floor is only meaningful if it guards the exact directory the
        # loader executes from. If the loader ever moves, this fails loudly.
        from app.extensions import __file__ as ext_init
        loader_dir = os.path.realpath(
            os.path.join(os.path.dirname(ext_init), "prompt_extensions"))
        guarded = os.path.realpath(os.path.join(_EXT_DIR, "prompt_extensions"))
        assert loader_dir == guarded
