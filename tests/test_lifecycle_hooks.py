"""Lifecycle hooks (app/utils/lifecycle_hooks.py): ``.ziya/hooks.yaml``
commands fired after a file is written, after a tool call, and when a
Task Card run ends.  Each hook is adjudicated by the shell policy before
it runs; awaited hooks feed their output back into the tool result.
"""
from __future__ import annotations

import json
import os
import time
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.utils import lifecycle_hooks as lh
from app.utils import policy_exec


def _allow(command, *, project_root, shell_commands=None):
    return True, "", {"PATH": os.environ.get("PATH", "")}


def _refuse(command, *, project_root, shell_commands=None):
    return False, "command 'frobnicate' is not in the allowlist", {}


@pytest.fixture
def project(tmp_path):
    (tmp_path / ".ziya").mkdir()
    return tmp_path


def _write_hooks(project, text: str) -> None:
    (project / ".ziya" / "hooks.yaml").write_text(text)
    # Make sure a rewrite within the same second invalidates the mtime cache.
    os.utime(project / ".ziya" / "hooks.yaml", (time.time() + 2, time.time() + 2))


def _log_records(project):
    p = project / ".ziya" / "hooks.log"
    if not p.exists():
        return []
    return [json.loads(l) for l in p.read_text().splitlines() if l.strip()]


# ----------------------------------------------------------------- config


def test_parse_reads_events_filters_and_await(project):
    _write_hooks(project, """
hooks:
  post_apply:
    - run: "ruff check {file}"
      match: "*.py"
      await: true
      timeout: 30
  post_tool:
    - "echo plain-string-form"
  run_finished:
    - run: "notify"
      status: [failed, held]
      await: true      # ignored: nothing consumes a run_finished result
""")
    hooks = lh.load_hooks(str(project))
    by = {h.event: h for h in hooks}
    assert by["post_apply"].await_result and by["post_apply"].timeout == 30
    assert by["post_apply"].match == "*.py"
    assert by["post_tool"].run == "echo plain-string-form"
    assert by["run_finished"].statuses == ["failed", "held"]
    assert by["run_finished"].await_result is False


def test_unknown_event_and_missing_run_are_logged_not_fatal(project):
    _write_hooks(project, """
hooks:
  on_sneeze:
    - run: "x"
  post_tool:
    - timeout: 5
    - run: "echo ok"
""")
    hooks = lh.load_hooks(str(project))
    assert [h.run for h in hooks] == ["echo ok"]
    msgs = [r["message"] for r in _log_records(project) if r.get("event") == "config_error"]
    assert any("on_sneeze" in m for m in msgs)
    assert any("no 'run'" in m for m in msgs)


def test_no_config_means_no_hooks(project):
    assert lh.load_hooks(str(project)) == []
    assert lh.fire("post_tool", {"tool": "x"}, project_root=str(project)) == []


def test_kill_switch(project, monkeypatch):
    _write_hooks(project, "hooks:\n  post_tool:\n    - run: 'echo hi'\n      await: true\n")
    monkeypatch.setenv("ZIYA_HOOKS", "0")
    with patch.object(policy_exec, "adjudicate", _allow):
        assert lh.fire("post_tool", {"tool": "x"}, project_root=str(project)) == []


# ----------------------------------------------------------------- firing


def test_awaited_hook_returns_exit_and_output_with_quoted_placeholder(project):
    _write_hooks(project, """
hooks:
  post_apply:
    - run: "echo checking {file}; echo 'E501 line too long' >&2; exit 1"
      await: true
""")
    target = project / "my file.py"
    with patch.object(policy_exec, "adjudicate", _allow):
        outs = lh.fire("post_apply", {"file": str(target)}, project_root=str(project))
    assert len(outs) == 1
    o = outs[0]
    assert o.ran and o.exit_code == 1
    assert "checking " + str(target) in o.output          # quoted path survived the space
    assert "E501 line too long" in o.output
    text = lh.feedback_text(outs)
    assert "exited 1" in text and "E501" in text
    rec = [r for r in _log_records(project) if r.get("event") == "post_apply"]
    assert rec and rec[-1]["exit_code"] == 1


def test_hook_receives_event_json_on_stdin_and_env(project):
    out_file = project / "seen.txt"
    _write_hooks(project, f"""
hooks:
  post_tool:
    - run: "cat > {out_file}; echo >> {out_file}; echo $ZIYA_HOOK_TOOL >> {out_file}; echo $ZIYA_HOOK_EVENT >> {out_file}"
      await: true
""")
    with patch.object(policy_exec, "adjudicate", _allow):
        lh.fire("post_tool", {"tool": "file_write", "ok": True}, project_root=str(project))
    body = out_file.read_text()
    first, tool, event = body.strip().split("\n")
    assert json.loads(first)["tool"] == "file_write"
    assert tool == "file_write" and event == "post_tool"


def test_match_filter_scopes_post_apply(project):
    _write_hooks(project, """
hooks:
  post_apply:
    - run: "echo py"
      match: "*.py"
      await: true
""")
    with patch.object(policy_exec, "adjudicate", _allow):
        assert lh.fire("post_apply", {"file": str(project / "a.md")}, project_root=str(project)) == []
        outs = lh.fire("post_apply", {"file": str(project / "a.py")}, project_root=str(project))
    assert len(outs) == 1 and outs[0].ok


def test_tools_and_status_filters(project):
    _write_hooks(project, """
hooks:
  post_tool:
    - run: "echo shell"
      tools: [run_shell_command]
      await: true
  run_finished:
    - run: "echo notify"
      status: [failed]
""")
    with patch.object(policy_exec, "adjudicate", _allow):
        assert lh.fire("post_tool", {"tool": "file_read"}, project_root=str(project)) == []
        assert len(lh.fire("post_tool", {"tool": "run_shell_command"}, project_root=str(project))) == 1
        # run_finished is never awaited; assert on the selection instead.
        hooks = [h for h in lh.load_hooks(str(project)) if h.event == "run_finished"]
        assert hooks[0].applies_to({"status": "failed"})
        assert not hooks[0].applies_to({"status": "done"})


def test_refused_hook_does_not_run_and_is_logged(project):
    marker = project / "ran"
    _write_hooks(project, f"hooks:\n  post_tool:\n    - run: 'frobnicate; touch {marker}'\n      await: true\n")
    with patch.object(policy_exec, "adjudicate", _refuse):
        outs = lh.fire("post_tool", {"tool": "x"}, project_root=str(project))
    assert not outs[0].ran and "not in the allowlist" in outs[0].error
    assert not marker.exists()
    assert "did not run" in lh.feedback_text(outs)
    assert any(r.get("refused") for r in _log_records(project))


def test_real_policy_refuses_an_always_blocked_hook(project):
    _write_hooks(project, "hooks:\n  post_tool:\n    - run: 'sudo rm -rf /'\n      await: true\n")
    outs = lh.fire("post_tool", {"tool": "x"}, project_root=str(project))
    assert not outs[0].ran, outs[0]


def test_background_hook_runs_without_blocking(project):
    marker = project / "bg-ran"
    _write_hooks(project, f"hooks:\n  post_tool:\n    - run: 'sleep 0.2; touch {marker}'\n")
    with patch.object(policy_exec, "adjudicate", _allow):
        t0 = time.time()
        outs = lh.fire("post_tool", {"tool": "x"}, project_root=str(project))
        assert outs == [] and time.time() - t0 < 0.15
        for _ in range(50):
            if marker.exists():
                break
            time.sleep(0.05)
    assert marker.exists()


def test_timeout_is_an_error_not_a_hang(project):
    _write_hooks(project, "hooks:\n  post_tool:\n    - run: 'sleep 5'\n      await: true\n      timeout: 1\n")
    with patch.object(policy_exec, "adjudicate", _allow):
        outs = lh.fire("post_tool", {"tool": "x"}, project_root=str(project))
    assert not outs[0].ran and "timed out" in outs[0].error


# -------------------------------------------------------------- the seams


@pytest.mark.asyncio
async def test_after_tool_fires_post_apply_for_file_write_and_appends_feedback(project):
    _write_hooks(project, """
hooks:
  post_apply:
    - run: "echo lint {file_rel}; exit 2"
      await: true
  post_tool:
    - run: "echo tool {tool}"
      await: true
""")
    with patch.object(policy_exec, "adjudicate", _allow):
        text = await lh.after_tool(
            "file_write", {"path": "src/a.py", "content": "x"}, {"success": True},
            "Wrote src/a.py", project_root=str(project))
    assert text.startswith("Wrote src/a.py")
    assert "tool file_write" in text
    assert "lint src/a.py" in text and "exited 2" in text


@pytest.mark.asyncio
async def test_after_tool_skips_post_apply_on_a_failed_write(project):
    _write_hooks(project, "hooks:\n  post_apply:\n    - run: 'echo lint'\n      await: true\n")
    with patch.object(policy_exec, "adjudicate", _allow):
        text = await lh.after_tool(
            "file_write", {"path": "a.py"}, {"error": True, "message": "denied"},
            "ERROR", project_root=str(project))
    assert text == "ERROR"


@pytest.mark.asyncio
async def test_after_tool_leaves_non_string_results_alone(project):
    _write_hooks(project, "hooks:\n  post_tool:\n    - run: 'echo x'\n      await: true\n")
    blocks = [{"type": "text", "text": "hi"}]
    with patch.object(policy_exec, "adjudicate", _allow):
        out = await lh.after_tool("file_read", {}, {}, blocks, project_root=str(project))
    assert out is blocks


def test_diff_pipeline_fires_post_apply_only_when_changes_were_written(project):
    from app.utils.diff_utils.pipeline import pipeline_manager as pm
    fired = []
    with patch.object(lh, "fire", lambda ev, payload, project_root: fired.append((ev, payload)) or []):
        pm._fire_post_apply_hooks({"changes_written": False}, "a.py", str(project))
        assert fired == []
        pm._fire_post_apply_hooks({"changes_written": True}, "a.py", str(project))
    assert fired[0][0] == "post_apply"
    assert fired[0][1]["file"] == str(project / "a.py") and fired[0][1]["source"] == "diff"


def test_diff_pipeline_attaches_awaited_feedback_to_the_result(project):
    from app.utils.diff_utils.pipeline import pipeline_manager as pm
    hook = lh.Hook(event="post_apply", run="lint", await_result=True, name="lint")
    outcome = lh.HookOutcome(hook, ran=True, exit_code=1, output="E501")
    result = {"changes_written": True}
    with patch.object(lh, "fire", lambda *a, **k: [outcome]):
        pm._fire_post_apply_hooks(result, "a.py", str(project))
    assert "exited 1" in result["hook_feedback"] and "E501" in result["hook_feedback"]


def test_run_driver_fires_run_finished_with_status_and_card(project):
    from app.agents import run_driver
    fired = []
    launch = SimpleNamespace(run_id="run-12345678", project_root=str(project),
                             card=SimpleNamespace(name="Nightly", id="card-1"), label="Task run")
    with patch.object(lh, "fire", lambda ev, payload, project_root: fired.append((ev, payload, project_root)) or []):
        run_driver._fire_run_finished_hooks(launch, "failed", {"error": "boom"})
    ev, payload, root = fired[0]
    assert ev == "run_finished" and root == str(project)
    assert payload["status"] == "failed" and payload["card"] == "Nightly"
    assert payload["run_id"] == "run-12345678" and payload["error"] == "boom"
