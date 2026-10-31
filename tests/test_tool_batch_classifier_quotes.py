"""Read-only shell classification must survive quoted operators and wrappers.

Observed 2026-10-16 (audit log, one conversation): every ``grep "a\\|b"`` the
model issued was classified as a BARRIER because the segment splitter cut the
command on the ``|`` INSIDE the quotes, leaving an unbalanced segment that
shlex refused.  Three independent greps that should take ~0.4 s ran serially
in ~5 s.  These tests pin the classifier to shell-aware tokenisation and the
wrappers / tools the model actually reaches for (``rg``, ``jq``, ``xargs``,
``timeout``).  Every "is read-only" case here was a barrier before the fix.
"""

import pytest

from app.tool_batch import is_read_only_call, plan_groups, shell_command_is_read_only
from types import SimpleNamespace


@pytest.mark.parametrize("cmd", [
    # The defect: alternation inside quotes.
    r'grep -n "inter_tool_delay\|asyncio.sleep" app/tool_execution.py | head -40',
    "grep -E 'a|b' x",
    'grep -n "a|b" x',
    'ls tests | grep -i "batch\\|parallel"; grep -rn "x" tests --include=*.py -l | head',
    # Quoted control operators are arguments, not separators.
    'grep -n "&&" app/foo.py',
    "grep ';' x",       # ``x`` is not a command, so this cannot pass by accident
    "grep '&&' x || grep '||' y",
    # fd-dups and /dev/null are not writes.
    "wc -l a.py b.py 2>/dev/null",
    "ls nope 2>&1 | head",
    "grep -rn foo app 2> /dev/null",
    # Tools and wrappers the model uses for research.
    "rg -n 'foo' app | head",
    "rg --files app",
    "jq .version package.json",
    "find app -name '*.py' | xargs grep -l foo",
    "find . -name '*.py' -print0 | xargs -0 -n 50 grep -c def",
    "timeout 10 grep -r foo .",
    "timeout -k 2 30s rg foo",
    "time grep -r foo .",
])
def test_read_only(cmd):
    assert shell_command_is_read_only(cmd) is True


@pytest.mark.parametrize("cmd", [
    "echo hi > out.txt",
    "cat a >> b",
    "grep foo x 2> err.log",
    "ls | xargs rm",
    "find . | xargs -I{} mv {} /tmp",
    "timeout 10 rm -rf build",
    "timeout grep foo x",              # no duration: not the shape we understand
    "time python3 -c 'open(\"x\",\"w\")'",
    "rg --pre ./script foo",
    "rg --pre=./script foo",
    "python3 -m pytest tests/ -q",     # may write caches; still a barrier
    "grep 'unterminated x",            # unbalanced: don't guess
])
def test_not_read_only(cmd):
    assert shell_command_is_read_only(cmd) is False


def test_quoted_alternation_greps_form_one_concurrent_group():
    """The seam: three alternation greps in one turn are ONE group, not
    three barriers."""
    def ctx(i, cmd):
        return SimpleNamespace(tool_id=f"t{i}", actual_tool_name="run_shell_command",
                               args={"command": cmd})
    groups = plan_groups([
        ctx(1, r'grep -n "a\|b" x'),
        ctx(2, r'grep -rn "c\|d" app | head'),
        ctx(3, "rg -n e app"),
    ])
    assert [[c.tool_id for c in g] for g in groups] == [["t1", "t2", "t3"]]


@pytest.mark.parametrize("name", [
    "render_diagram", "analyze", "dataset_describe", "job_status", "job_read",
    "task_run_read", "ledger_query", "view_image", "recall_image", "shadow_wait",
    "handoff_read", "bead_status", "mcp_server_status", "nova_web_search",
])
def test_observing_builtins_overlap(name):
    assert is_read_only_call(name, {}) is True


@pytest.mark.parametrize("name", ["file_write", "job_start", "job_kill", "ledger_record",
                                  "store_output", "shadow_send", "mcp_some_external_tool"])
def test_mutating_and_external_tools_stay_serial(name):
    assert is_read_only_call(name, {}) is False
