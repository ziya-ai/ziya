"""The standing prompt must point the model at the dataset tools.

Originally these asserted on the base template in ``app.agents.prompts``;
that text was removed at some point and the tests sat red, which is one
reason nobody noticed the model had stopped reaching for
``dataset_register`` / ``analyze``.  The guidance now lives in the SHARED
session-context section (``app.utils.session_context_prompt``), which both
chat and task prompts receive, so that is the surface asserted here.
"""
import datetime
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

NOW = datetime.datetime(2026, 1, 2, 3, 4, 5)


@pytest.fixture
def section(monkeypatch):
    monkeypatch.setattr(
        "app.mcp.builtin_tools.is_builtin_category_enabled",
        lambda name: name in {"datasets", "output_contracts", "evidence"},
    )
    monkeypatch.setattr(
        "app.config.write_policy.effective_policy_for_root",
        lambda _root="": {"safe_write_paths": [".ziya/"], "allowed_write_patterns": [],
                          "direct_write_mode": "none"},
    )
    from app.utils.session_context_prompt import build_session_context_section
    out = build_session_context_section(project_root="/p", now=NOW)
    return out[out.index("### Working with data"):]


def test_shared_prompt_directs_model_to_dataset_register_and_analyze(section):
    assert "dataset_register" in section
    assert "analyze(" in section


def test_shared_prompt_names_the_failure_mode(section):
    """A bare 'use this tool' is what models skip under concision pressure;
    the section must say what it replaces and what goes wrong without it."""
    low = section.lower()
    assert "bespoke json store" in low
    assert "transcribe" in low or "hand-cop" in low
    assert "snapshot-copy" in low
