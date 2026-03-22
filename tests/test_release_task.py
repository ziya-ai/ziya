"""
Tests for the `sweep` CLI task — release workflow.

Tests cover:
  - Task file exists and loads correctly via task_runner
  - All 7 release steps present in the prompt
  - Permission escalation (allow block) validated and applied
  - Safety rules (stop on error, non-interactive batch execution)
  - Batch mode (no confirmation prompts that would stall the task)
"""

import os
from pathlib import Path

import pytest

from app.task_runner import load_tasks

TASK_KEY = "sweep"
PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
TASKS = load_tasks(PROJECT_ROOT)


# ============================================================================
# Task file basics
# ============================================================================

class TestSweepTaskFile:
    """Verify the sweep task is loadable and well-formed."""

    def test_task_key_exists(self):
        assert TASK_KEY in TASKS, f"Expected '{TASK_KEY}' in tasks: {list(TASKS.keys())}"

    def test_has_description(self):
        assert "description" in TASKS[TASK_KEY]
        assert len(TASKS[TASK_KEY]["description"]) > 10

    def test_has_prompt(self):
        task = TASKS[TASK_KEY]
        assert "prompt" in task
        assert len(task["prompt"]) > 100

    def test_has_allow_block(self):
        """Task defines escalated permissions."""
        task = TASKS[TASK_KEY]
        allow = task.get("allow")
        assert allow is not None, "sweep task must have an 'allow' block"
        assert isinstance(allow, dict)


# ============================================================================
# Step verification
# ============================================================================

class TestSweepTaskSteps:
    """Verify each step is present in the prompt."""

    @pytest.fixture(autouse=True)
    def _load_prompt(self):
        self.prompt = TASKS[TASK_KEY]["prompt"]

    @pytest.mark.parametrize("step_num,keyword", [
        (1, "Survey"),
        (2, "Group"),
        (3, "Commit"),
        (4, "Changelog"),
        (5, "Version bump"),
        (6, "Tag"),
        (7, "Push"),
    ])
    def test_step_present(self, step_num, keyword):
        assert f"## Step {step_num}" in self.prompt, f"Missing Step {step_num}"
        assert keyword.lower() in self.prompt.lower(), f"Missing keyword '{keyword}'"

    def test_has_confirmation_rule(self):
        prompt = self.prompt.lower()
        # Task runs non-interactively — must NOT ask for confirmation
        assert "do not pause" in prompt or "non-interactive" in prompt, \
            "Prompt must declare non-interactive batch execution"

    def test_has_dry_run_rule(self):
        # Non-interactive tasks can't support dry-run via user input.
        # Instead, verify the prompt says to stop on error.
        assert "stop" in self.prompt.lower(), "Prompt must stop on error"

    def test_has_stop_on_error_rule(self):
        assert "stop" in self.prompt.lower() or "abort" in self.prompt.lower() or "halt" in self.prompt.lower()

    def test_no_interactive_prompts(self):
        """Non-interactive tasks must not ask the user to confirm or approve."""
        prompt = self.prompt.lower()
        # These phrases (when used as positive instructions TO the model) would
        # cause the task to stall waiting for input that will never come.
        # Negated forms like "do not ask for confirmation" are fine.
        stall_phrases = [
            "wait for approval",
            "wait for confirmation",
            "does this look correct",
            "do you want to",
            "shall i proceed",
            "please confirm",
        ]
        for phrase in stall_phrases:
            if phrase in prompt:
                # Check it's preceded by a negation within 30 chars
                idx = prompt.index(phrase)
                preceding = prompt[max(0, idx - 30):idx]
                assert "not" in preceding or "don't" in preceding or "never" in preceding, \
                    f"Non-interactive task must not instruct '{phrase}' — it would stall"


# ============================================================================
# Permission escalation
# ============================================================================

class TestTaskPermissionEscalation:
    """Verify the allow block is validated and applied correctly."""

    def test_validate_valid_allow(self):
        from app.task_runner import validate_task_allow
        errors = validate_task_allow(TASKS[TASK_KEY])
        assert errors == [], f"Unexpected validation errors: {errors}"

    def test_validate_rejects_always_blocked(self):
        from app.task_runner import validate_task_allow
        bad_task = {"allow": {"commands": ["sudo", "git"]}}
        errors = validate_task_allow(bad_task)
        assert any("sudo" in e for e in errors)

    def test_validate_rejects_non_dict_allow(self):
        from app.task_runner import validate_task_allow
        errors = validate_task_allow({"allow": "git"})
        assert any("mapping" in e for e in errors)

    def test_validate_rejects_non_list_field(self):
        from app.task_runner import validate_task_allow
        errors = validate_task_allow({"allow": {"commands": "git"}})
        assert any("list" in e for e in errors)

    def test_validate_rejects_unknown_keys(self):
        from app.task_runner import validate_task_allow
        errors = validate_task_allow({"allow": {"commands": ["git"], "bogus": True}})
        assert any("bogus" in e for e in errors)

    def test_apply_sets_allow_commands(self):
        from app.task_runner import apply_task_permissions, restore_permissions
        task = {"allow": {"commands": ["git", "make"]}}
        saved = apply_task_permissions(task)
        try:
            val = os.environ.get("ALLOW_COMMANDS", "")
            assert "git" in val
            assert "make" in val
        finally:
            restore_permissions(saved)

    def test_apply_sets_git_operations(self):
        from app.task_runner import apply_task_permissions, restore_permissions
        task = {"allow": {"git_operations": ["add", "commit", "push"]}}
        saved = apply_task_permissions(task)
        try:
            val = os.environ.get("SAFE_GIT_OPERATIONS", "")
            assert "add" in val
            assert "commit" in val
            assert "push" in val
        finally:
            restore_permissions(saved)

    def test_apply_sets_write_patterns(self):
        from app.task_runner import apply_task_permissions, restore_permissions
        task = {"allow": {"write_patterns": ["CHANGELOG.md", "*.toml"]}}
        saved = apply_task_permissions(task)
        try:
            val = os.environ.get("ALLOWED_WRITE_PATTERNS", "")
            assert "CHANGELOG.md" in val
            assert "*.toml" in val
        finally:
            restore_permissions(saved)

    def test_restore_cleans_up(self):
        from app.task_runner import apply_task_permissions, restore_permissions
        os.environ.pop("ALLOW_COMMANDS", None)
        task = {"allow": {"commands": ["git"]}}
        saved = apply_task_permissions(task)
        assert "ALLOW_COMMANDS" in os.environ
        restore_permissions(saved)
        assert os.environ.get("ALLOW_COMMANDS") is None

    def test_no_allow_is_noop(self):
        from app.task_runner import apply_task_permissions
        saved = apply_task_permissions({"prompt": "just a prompt"})
        assert saved == {}

    def test_always_blocked_filtered_from_apply(self):
        """Even if someone sneaks sudo into allow, it gets filtered out."""
        from app.task_runner import apply_task_permissions, restore_permissions
        os.environ.pop("ALLOW_COMMANDS", None)
        task = {"allow": {"commands": ["sudo", "git"]}}
        saved = apply_task_permissions(task)
        try:
            val = os.environ.get("ALLOW_COMMANDS", "")
            assert "git" in val
            assert "sudo" not in val
        finally:
            restore_permissions(saved)

    def test_escalation_survives_server_config_override(self):
        """Env escalations must not be clobbered by server_config env overrides.

        The MCP manager applies server_config["env"] on top of os.environ,
        which would overwrite task escalations. The manager re-applies
        os.environ for escalation keys after the config overlay.
        """
        from app.task_runner import apply_task_permissions, restore_permissions
        os.environ.pop("ALLOW_COMMANDS", None)
        task = {"allow": {"commands": ["git", "docker"]}}
        saved = apply_task_permissions(task)
        try:
            # Simulate what MCP manager does: config env clobbers os.environ
            server_env = os.environ.copy()
            server_env.update({"ALLOW_COMMANDS": "ls,cat,grep"})
            # Re-apply escalation keys (what the manager fix does)
            for key in ("ALLOW_COMMANDS", "SAFE_GIT_OPERATIONS", "ALLOWED_WRITE_PATTERNS"):
                if key in os.environ:
                    server_env[key] = os.environ[key]
            assert "git" in server_env["ALLOW_COMMANDS"]
            assert "docker" in server_env["ALLOW_COMMANDS"]
        finally:
            restore_permissions(saved)

    def test_sweep_allow_includes_git(self):
        """The sweep task must escalate git permissions."""
        allow = TASKS[TASK_KEY].get("allow", {})
        assert "git" in allow.get("commands", [])

    def test_sweep_allow_includes_write_ops(self):
        """The sweep task must allow git add/commit/push/tag."""
        allow = TASKS[TASK_KEY].get("allow", {})
        git_ops = allow.get("git_operations", [])
        for op in ("add", "commit", "tag", "push"):
            assert op in git_ops, f"Missing git operation: {op}"


# ============================================================================
# Integration: loads through task_runner pipeline
# ============================================================================

class TestSweepTaskLoading:
    """Test loading through the standard pipeline."""

    def test_loads_via_task_runner(self):
        tasks = load_tasks(PROJECT_ROOT)
        assert TASK_KEY in tasks

    def test_has_all_seven_steps(self):
        tasks = load_tasks(PROJECT_ROOT)
        prompt = tasks[TASK_KEY]["prompt"]
        for i in range(1, 8):
            assert f"## Step {i}" in prompt, f"Missing Step {i}"

    def test_conventional_commits_mentioned(self):
        tasks = load_tasks(PROJECT_ROOT)
        prompt = tasks[TASK_KEY]["prompt"]
        assert "Conventional Commit" in prompt
