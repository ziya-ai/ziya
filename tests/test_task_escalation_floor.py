"""Regression tests for task-scope escalation floor subtraction.

task_escalation_block previously flagged EVERY writable path and EVERY shell
command as an escalation, with no floor subtraction — so a card requesting only
``.ziya/audit`` (inside the safe-write floor) plus ``grep``/``ls`` (in the base
command allowlist) was reported as "unsigned privilege escalation" and demanded
``sudo ziya-approve`` for grants that grant nothing beyond the default floor.

These tests pin that floor-covered grants produce an EMPTY escalation block
(no signing required), while genuine escalations still register — including the
security-critical cases where a destructive/interpreter command is in the base
allowlist but a shell_commands grant would bypass its write-policy gate, so it
MUST remain an escalation.
"""

from app.config import scope_canonical as sc
from app.models.task_card import TaskScope, ScopeEntry


# ── Writable paths ────────────────────────────────────────────────────────────

def test_writable_inside_ziya_floor_is_not_escalation():
    scope = TaskScope(paths=[ScopeEntry(path=".ziya/audit", write=True, is_dir=True)])
    assert sc.task_escalation_block(scope) == {}
    assert sc.task_scope_hash(scope) == ""


def test_writable_bare_ziya_floor_is_not_escalation():
    scope = TaskScope(paths=[ScopeEntry(path=".ziya", write=True, is_dir=True)])
    assert sc.task_escalation_block(scope) == {}


def test_writable_inside_tmp_floor_is_not_escalation():
    scope = TaskScope(paths=[ScopeEntry(path="/tmp/scratch", write=True)])
    assert sc.task_escalation_block(scope) == {}


def test_writable_outside_floor_is_escalation():
    scope = TaskScope(paths=[ScopeEntry(path="src/main.py", write=True)])
    esc = sc.task_escalation_block(scope)
    assert esc == {"writable_paths": ["src/main.py"]}
    assert sc.task_scope_hash(scope) != ""


def test_sibling_prefix_of_floor_is_still_escalation():
    # ".ziya-backup" must NOT be treated as inside ".ziya/" (os.sep boundary).
    scope = TaskScope(paths=[ScopeEntry(path=".ziya-backup/x", write=True)])
    assert sc.task_escalation_block(scope) == {"writable_paths": [".ziya-backup/x"]}


# ── Shell commands ────────────────────────────────────────────────────────────

def test_allowlisted_commands_are_not_escalation():
    # grep, ls, cat, echo, find are all in the base allowlist and carry no
    # further runtime gate.
    scope = TaskScope(shell_commands=["grep", "ls", "cat", "echo", "find"])
    assert sc.task_escalation_block(scope) == {}


def test_non_allowlisted_command_is_escalation():
    # rg (ripgrep) is NOT in the base allowlist — distinct from grep.
    scope = TaskScope(shell_commands=["grep", "rg"])
    assert sc.task_escalation_block(scope) == {"shell_commands": ["rg"]}


def test_destructive_command_stays_escalation_even_though_allowlisted():
    # rm is in the allowlist but a shell_commands grant bypasses its
    # write-policy destructive gate — dropping it would under-report.
    scope = TaskScope(shell_commands=["rm"])
    assert sc.task_escalation_block(scope) == {"shell_commands": ["rm"]}


def test_interpreter_stays_escalation_even_though_allowlisted():
    scope = TaskScope(shell_commands=["python3"])
    assert sc.task_escalation_block(scope) == {"shell_commands": ["python3"]}


def test_inplace_edit_tools_stay_escalation():
    # sed/awk are allowlisted but a grant bypasses in-place-edit blocking.
    scope = TaskScope(shell_commands=["sed", "awk"])
    assert sc.task_escalation_block(scope) == {"shell_commands": ["awk", "sed"]}


def test_regex_grant_is_always_escalation():
    scope = TaskScope(shell_commands=["re:^make\\s+test$"])
    assert sc.task_escalation_block(scope) == {"shell_commands": ["re:^make\\s+test$"]}


# ── Combined: the exact audit-card shape from the bug report ──────────────────

def test_audit_card_recon_block_needs_no_signing():
    # "Stage 1 — Attack Surface Recon": writable .ziya/audit only.
    scope = TaskScope(paths=[ScopeEntry(path=".ziya/audit", write=True, is_dir=True)])
    assert sc.task_escalation_block(scope) == {}


def test_audit_card_grep_block_needs_no_signing():
    # "Write Policy Auditor": grep + writable .ziya/audit — all floor-covered.
    scope = TaskScope(
        shell_commands=["grep"],
        paths=[ScopeEntry(path=".ziya/audit", write=True, is_dir=True)],
    )
    assert sc.task_escalation_block(scope) == {}


def test_audit_card_rg_block_still_flags_only_rg():
    # "Shell Execution Auditor": grep + rg + .ziya/audit — only rg escalates.
    scope = TaskScope(
        shell_commands=["grep", "rg"],
        paths=[ScopeEntry(path=".ziya/audit", write=True, is_dir=True)],
    )
    assert sc.task_escalation_block(scope) == {"shell_commands": ["rg"]}


def test_mixed_hierarchy_floor_and_escalation():
    deck = TaskScope(paths=[ScopeEntry(path=".ziya/audit", write=True, is_dir=True)])
    card = TaskScope(shell_commands=["grep", "ls"])
    leaf = TaskScope(shell_commands=["rg"], paths=[ScopeEntry(path="out/", write=True)])
    esc = sc.task_escalation_block(deck, card, leaf)
    assert esc == {"shell_commands": ["rg"], "writable_paths": ["out/"]}
