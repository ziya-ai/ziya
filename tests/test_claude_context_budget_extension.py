"""Tests for the Claude 5+ context-budget instruction.

claude_family_extension prepends a CONTEXT BUDGET block only when the
request's model is Claude major version 5 or later. The version is parsed
from context["model_name"], which PromptExtensionManager.apply_extensions
fills from its model_name kwarg -- the seam test below covers that hop.
"""

import pytest

from app.extensions.prompt_extensions.claude_extensions import (
    _claude_major_version,
    claude_family_extension,
)

MARKER = "CONTEXT BUDGET:"


def _ctx(model_name=None, enabled=True):
    ctx = {"config": {"enabled": enabled}}
    if model_name is not None:
        ctx["model_name"] = model_name
    return ctx


@pytest.mark.parametrize("name,expected", [
    ("opus4.8", 4),
    ("sonnet3.5-v2", 3),
    ("haiku-4.5", 4),
    ("sonnet5", 5),
    ("opus5.5", 5),
    ("fable5.1", 5),
    ("claude-opus-5-5", 5),
    ("claude-sonnet-4-6", 4),
    ("sonnet", None),
    (None, None),
])
def test_major_version_parsing(name, expected):
    assert _claude_major_version(name) == expected


@pytest.mark.parametrize("name", ["sonnet5", "opus5.5", "mythos5", "claude-opus-5-5", "claude-sonnet-5"])
def test_present_for_claude5_plus(name):
    out = claude_family_extension("base prompt", _ctx(name))
    assert MARKER in out
    assert "CLAUDE FAMILY INSTRUCTIONS" in out


@pytest.mark.parametrize("name", ["opus4.8", "sonnet4.6", "claude-opus-4-7", "haiku-4.5", "sonnet", None])
def test_absent_below_claude5_or_unversioned(name):
    out = claude_family_extension("base prompt", _ctx(name))
    assert "CLAUDE FAMILY INSTRUCTIONS" in out  # extension ran
    assert MARKER not in out


def test_disabled_skips_everything():
    assert claude_family_extension("base prompt", _ctx("opus5", enabled=False)) == "base prompt"


def test_seam_apply_extensions_passes_model_name():
    from app.extensions import init_extensions
    from app.utils.prompt_extensions import PromptExtensionManager

    init_extensions()
    v5 = PromptExtensionManager.apply_extensions("p", model_name="opus5", model_family="claude")
    v4 = PromptExtensionManager.apply_extensions("p", model_name="opus4.8", model_family="claude")
    assert MARKER in v5
    assert "CLAUDE FAMILY INSTRUCTIONS" in v4
    assert MARKER not in v4
