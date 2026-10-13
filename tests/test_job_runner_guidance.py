"""The job runner is advertised where a model decides how to run a command.

Registering the tools is not enough: a task card written before the runner
existed says ``bash -c '… &'``, gets refused, and only then sees the hint
in the timeout message.  Two surfaces carry the guidance ahead of time:

  * the built-in Task Cards skill (authoring time), whose long-running
    commands section now steers to job_start/job_wait first and keeps
    ``shell_timeout_secs`` as the fallback for a genuinely synchronous step;
  * the Session Context block every turn sees (run time, chat and task),
    gated on the ``jobs`` builtin category so a deployment that disabled
    it is not told about a tool it lacks.
"""
import os

import pytest

os.environ.setdefault("ZIYA_TESTING", "1")


def _task_cards_skill_prompt() -> str:
    from app.data import built_in_skills as bis
    text = ""
    for name in dir(bis):
        val = getattr(bis, name)
        if isinstance(val, list):
            for s in val:
                p = s.get("prompt") if isinstance(s, dict) else getattr(s, "prompt", None)
                if isinstance(p, str) and "task_card_validate" in p:
                    text += p
        elif isinstance(val, str) and "task_card_validate" in val:
            text += val
    assert text, "could not locate the Task Cards skill prompt"
    return text


def test_task_cards_skill_steers_long_commands_to_the_runner():
    p = _task_cards_skill_prompt()
    sec = p[p.index("## Long-running commands"):]
    sec = sec[:sec.index("\n## ", 1)]
    assert "job_start" in sec and "job_wait" in sec
    assert 'detach="run"' in sec
    assert "nohup" in sec, "the refused operators must be named so the model does not try them"
    # The runner is recommended BEFORE the synchronous fallback.
    assert sec.index("job_start") < sec.index("shell_timeout_secs")


def test_session_context_carries_jobs_guidance_when_enabled(monkeypatch):
    from app.utils import session_context_prompt as scp
    monkeypatch.setattr("app.mcp.builtin_tools.is_builtin_category_enabled", lambda c: c == "jobs")
    out = scp.build_session_context_section(project_root=None, task_scope=None)
    assert "### Long-running commands" in out
    assert "job_start" in out and "job_wait" in out and "nohup" in out


def test_session_context_omits_jobs_guidance_when_category_disabled(monkeypatch):
    from app.utils import session_context_prompt as scp
    monkeypatch.setattr("app.mcp.builtin_tools.is_builtin_category_enabled", lambda c: False)
    out = scp.build_session_context_section(project_root=None, task_scope=None)
    assert "job_start" not in out


def test_jobs_section_position_is_a_sibling_of_the_shell_grants_section():
    # The guidance rides the same section list as the shell grants, so a
    # task prompt shows grants and the runner side by side.
    from app.utils import session_context_prompt as scp
    import inspect
    src = inspect.getsource(scp.build_session_context_section)
    assert "_format_shell_section(task_scope)," in src
    assert src.index("_format_shell_section(task_scope),") < src.index("_format_jobs_section(),")
