"""macOS (BSD) ``sed -i ''`` takes an empty backup suffix as its own argument.

Observed 2026-10-03: ``sed -i '' 's/a/b/' tests/x.py`` was refused with
"targeting 's/a/b/'": the checker read ``''`` as the script and the real
script as a file, so a write to an allowed path was blocked.  The empty
token is the BSD suffix; GNU sed given an empty script is a no-op, so
skipping it cannot hide a write.
"""

import pytest

from app.config.write_policy import WritePolicyManager
from app.mcp_servers.shell_server import ShellServer
from app.mcp_servers.write_policy import ShellWriteChecker, _inplace_targets


def test_empty_suffix_is_not_the_script():
    assert _inplace_targets("sed", ["sed", "-i", "", "s/a/b/", "f.py"]) == ["f.py"]


def test_gnu_form_unchanged():
    assert _inplace_targets("sed", ["sed", "-i", "s/a/b/", "f.py"]) == ["f.py"]
    assert _inplace_targets("sed", ["sed", "-i", "-e", "s/a/b/", "f.py"]) == ["f.py"]


@pytest.fixture
def checker(tmp_path):
    root = str(tmp_path)
    (tmp_path / "tests").mkdir()
    pm = WritePolicyManager()
    pm.load_for_project("p", root)
    pm._policy["safe_write_paths"].append("tests/")
    c = ShellWriteChecker(pm)
    c.set_project_root(root)
    yield c
    c.clear_project_root()


def test_bsd_sed_into_allowed_path_passes_and_outside_still_blocked(checker):
    split = ShellServer()._split_by_shell_operators
    assert checker.check("sed -i '' 's/a/b/' tests/x.py", split)[0]
    ok, reason = checker.check("sed -i '' 's/a/b/' app/x.py", split)
    assert not ok and "app/x.py" in reason
