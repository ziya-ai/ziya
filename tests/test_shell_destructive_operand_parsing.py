"""
Write-target identification for destructive commands (cp/mv/ln/rm/...).

``ShellWriteChecker._destructive`` used to take "every token not starting
with ``-``" as the operands and, for cp/mv, the last of those as the only
write target.  That misreads several forms that write into the project:

  * GNU ``-t DIR`` / ``-tDIR`` / ``-rt DIR`` / ``--target-directory[=]DIR``
    (and its abbreviations): the destination comes FIRST, the last operand
    is a read-only source.
  * GNU value options (``-S SUF``, ``--suffix SUF``) under argument
    permutation: in ``cp /tmp/a src/x -S /tmp/z`` the destination is
    ``src/x``, not the suffix ``/tmp/z``.
  * ``--``: operands after it that start with ``-`` were dropped, so
    ``cp -- /tmp/a -dst`` checked nothing and wrote ``./-dst``.
  * BSD/macOS getopt does not permute: after the first operand, ``-v`` is
    a file name, so ``cp /tmp/a -v`` writes ``./-v`` (verified on macOS
    /bin/cp).
  * One-operand ``ln -s PATH`` creates ``./basename(PATH)``.
  * ``mv`` removes its sources, so ``mv src/x /tmp/y`` deletes a project
    file exactly as ``rm src/x`` does; only the destination was checked.

Each blocked case below was admitted by the old parser.  The allowed cases
pin that the fix does not over-block ordinary usage, including GNU ``-t``
copying OUT of the project (BSD refuses a leading ``-t``/long option
outright, so it cannot write there).
"""

import pytest

from app.config.write_policy import WritePolicyManager
from app.mcp_servers.write_policy import ShellWriteChecker


@pytest.fixture
def checker(tmp_path):
    proj = tmp_path / "project"
    (proj / ".ziya").mkdir(parents=True)
    (proj / "src").mkdir()
    pm = WritePolicyManager()
    pm.load_for_project("operand-parsing", str(proj))
    c = ShellWriteChecker(pm)
    c.set_project_root(str(proj))
    yield c
    c.clear_project_root()


def _one_segment(cmd):
    return [(None, cmd)]


BLOCKED = [
    # GNU target-directory forms
    "cp -t src/ /tmp/x",
    "cp -rt src/ /tmp/x",
    "cp -tsrc/ /tmp/x",
    "cp --target-directory=src/ /tmp/x",
    "cp --target-directory src/ /tmp/x",
    "cp --target src/ /tmp/x",
    "cp --t=src/ /tmp/x",
    "mv -t src/ /tmp/x",
    "mv --target-directory=src/ /tmp/x",
    "ln -tsrc/ /tmp/x",
    # GNU permutation past a value option
    "cp /tmp/a src/evil.py -S /tmp/z",
    "cp /tmp/a src/evil.py --suffix /tmp/z",
    "mv /tmp/a src/evil.py -S /tmp/z",
    # end-of-options marker
    "cp -- /tmp/a -dst",
    "rm -- /tmp/x -evil",
    # BSD non-permuting getopt: a trailing dash-word is an operand
    "cp /tmp/a -v",
    "rm /tmp/x -evil",
    # GNU -t AFTER the first operand is an operand to BSD
    "cp /tmp/a -t /tmp/ src/evil.py",
    # one-operand ln creates ./basename
    "ln -s /tmp/x",
    # mv removes its sources: moving a project file out deletes it
    "mv src/a.py /tmp/b",
    "mv src/a.py src/b.py /tmp/",
    "mv -t /tmp/ src/a.py",
    "mv --target-directory=/tmp/ src/a.py",
    "mv -- src/a.py /tmp/b",
]

ALLOWED = [
    "cp src/a.py /tmp/b",
    "cp -r src /tmp/d",
    "cp -t /tmp/ src/a.py",
    "cp --target-directory=/tmp/ src/a.py",
    "mv /tmp/a /tmp/b",
    "mv -t /tmp/ /tmp/a",
    "rm -rf /tmp/x",
    "rm -- /tmp/-x",
    "ln -s /tmp/a /tmp/link",
    "mkdir -p /tmp/new/dir",
]


@pytest.mark.parametrize("cmd", BLOCKED)
def test_project_write_is_blocked(checker, cmd):
    ok, reason = checker.check(cmd, _one_segment)
    assert not ok, f"{cmd!r} writes into the project but was allowed"
    assert "blocked" in reason


@pytest.mark.parametrize("cmd", ALLOWED)
def test_safe_write_is_allowed(checker, cmd):
    ok, reason = checker.check(cmd, _one_segment)
    assert ok, f"{cmd!r} writes only to safe paths but was refused: {reason}"


def test_mv_source_reason_names_the_removed_file(checker):
    ok, reason = checker.check("mv src/a.py /tmp/b", _one_segment)
    assert not ok
    assert "'mv src/a.py'" in reason


def test_cp_source_is_still_read_only(checker):
    # cp leaves its source in place, so a project source is not a write.
    ok, reason = checker.check("cp src/a.py /tmp/b", _one_segment)
    assert ok, reason


def test_blocked_reason_names_the_real_destination(checker):
    ok, reason = checker.check("cp -t src/ /tmp/x", _one_segment)
    assert not ok
    assert "'cp src/'" in reason
