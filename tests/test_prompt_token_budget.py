"""
Guards on always-on system-prompt size.

Motivation: users who only ever use Ziya as a coding interface still pay for
every unconditional word in the prompt.  The skill system exists so that
expensive format specifications (music notation, packet frames, task cards)
cost a one-line catalog entry rather than their full body.  Two things can
silently erode that:

  1. A skill body leaking into the always-on catalog, which would put
     thousands of tokens in front of every request.  Nothing fails loudly if
     this happens -- the prompt just gets quietly more expensive.
  2. The unconditional VISUALIZATION CAPABILITIES block growing worked
     examples for features most users never invoke.

Both are asserted here rather than left to review discipline.
"""
import os
import re

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

#: Rough chars-per-token for English prose.  Only used to express budgets in
#: familiar units; the assertions themselves are on character counts so they
#: do not depend on a tokenizer being installed.
CHARS_PER_TOKEN = 4


def _viz_block() -> str:
    """The unconditional visualization section of the base template."""
    from app.agents.prompts import template

    start = template.index("CRITICAL: VISUALIZATION CAPABILITIES:")
    # Ends where the always-on block hands off to the KaTeX one-liner.
    end = template.index("Mathematical expressions in KaTeX", start)
    return template[start:end]


# ---------------------------------------------------------------------------
# The lazy-loading invariant
# ---------------------------------------------------------------------------

def test_catalog_excludes_skill_bodies():
    """The catalog must carry descriptions only, never `prompt` bodies.

    This is the load-bearing assertion for the whole on-demand design: if a
    skill's full instructions ever end up in the catalog, a coding-only user
    starts paying music_notation's ~2k tokens on every single request.
    """
    os.environ.setdefault("ZIYA_USER_CODEBASE_DIR", os.getcwd())
    from app.data.built_in_skills import BUILT_IN_SKILLS
    from app.utils.skill_catalog_prompt import get_skill_catalog_section

    catalog = get_skill_catalog_section()
    assert catalog, "skill catalog unexpectedly empty"

    for skill in BUILT_IN_SKILLS:
        body = skill.get("prompt") or ""
        if len(body) < 200:
            # Too short to be a meaningful body; a substring check on a tiny
            # string would be noise rather than signal.
            continue
        # Compare on a distinctive interior slice: the opening line of a body
        # is often close to its own description, so matching on the start
        # would produce false positives.
        probe = body[100:200]
        assert probe not in catalog, (
            f"skill {skill['id']!r} body text found in the always-on catalog; "
            "skill prompts must be loaded via get_skill_details, not injected"
        )


def test_catalog_stays_compact():
    """One line per skill keeps the catalog affordable as skills are added."""
    os.environ.setdefault("ZIYA_USER_CODEBASE_DIR", os.getcwd())
    from app.utils.skill_catalog_prompt import get_skill_catalog_section

    catalog = get_skill_catalog_section()
    entries = [ln for ln in catalog.splitlines() if ln.strip().startswith("•")]
    assert entries, "no catalog entries parsed"

    for entry in entries:
        assert len(entry) <= 320, (
            f"catalog entry is too long ({len(entry)} chars) -- catalog "
            f"descriptions should be a single summary line: {entry[:80]!r}"
        )


# ---------------------------------------------------------------------------
# The always-on visualization block
# ---------------------------------------------------------------------------

def test_visualization_block_within_budget():
    """Cap the unconditional visualization prose.

    Budget is deliberately close to the current size so that adding a new
    worked example forces an explicit decision (raise the budget, or move the
    detail into a skill) instead of silently taxing every request.
    """
    block = _viz_block()
    budget_tokens = 1250
    assert len(block) <= budget_tokens * CHARS_PER_TOKEN, (
        f"always-on visualization block is ~{len(block) // CHARS_PER_TOKEN} "
        f"tokens, over the {budget_tokens}-token budget. Move format details "
        "into a model-discoverable skill rather than the base template."
    )


def test_html_mockup_keeps_non_inferable_constraints():
    """Trimming the mockup section must not drop its two real constraints.

    There is no `html_mockup` skill to defer to, so these facts have nowhere
    else to live: a model cannot infer from the fence name that external
    stylesheets are unavailable or that scripts are stripped, and getting
    either wrong produces a mockup that renders blank or unstyled.
    """
    block = _viz_block()
    assert "html-mockup" in block, "html-mockup fence name missing"
    assert re.search(r"\bINLINE\b|inline styles", block), (
        "inline-styling requirement dropped from the html-mockup section"
    )
    assert re.search(r"script", block, re.IGNORECASE), (
        "script-stripping caveat dropped from the html-mockup section"
    )


def test_html_mockup_has_no_worked_example():
    """The worked example was the densest non-essential chunk; keep it gone."""
    block = _viz_block()
    assert "Login Form" not in block, (
        "the html-mockup worked example is back in the always-on prompt; it "
        "cost ~166 tokens on every request for a feature most users never use"
    )


@pytest.mark.parametrize("fence,label", [
    ("```packet```", "packet"),
    ("```music```", "music"),
])
def test_fence_hints_retained(fence, label):
    """Music and packet keep their one-line fence hints, by explicit choice.

    Without the hint the model cannot emit the fence without first spending a
    get_skill_details round-trip, which is a worse trade than the ~60 tokens
    each line costs.  Asserted so the lines are not trimmed as "redundant
    with the catalog" -- they are not: the catalog omits fence syntax.
    """
    block = _viz_block()
    assert fence in block, f"{label} fence hint removed from always-on prompt"
