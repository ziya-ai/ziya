"""
SKILL.md frontmatter: multi-line values (agentskills.io lenient parsing).

The three fixtures are verbatim shapes from real installed skills:
  * PLAIN    -- a user-installed telemetry skill
               (plain scalar continued over indented lines; one continuation
               line contains "Sources:", which the old parser turned into a key)
  * LITERAL  -- a build-metrics skill   (``description: |``)
  * FOLDED   -- a recommendations skill (``description: >-``)

Before the fix, discovery reported their descriptions as the first line,
"|" and ">-" respectively, and truncated ``compatibility`` the same way.
"""
from __future__ import annotations

import textwrap

import pytest

from app.utils.skill_frontmatter import parse_frontmatter

PLAIN = textwrap.dedent("""\
    name: fleet-telemetry
    description: Query fleet telemetry — live (streaming) or historical (stored) —
      for any entity in the system, from individual hosts up to the whole fleet.
      Sources: the Metrics API (warehouse SQL, or the columnar store via the `cs;` prefix) via
      the fleetmetrics CLI plugin.
    compatibility: Requires the fleetmetrics CLI plugin installed (see
      Installation), cloud credentials via the SSO helper.
    metadata:
      ziya-visibility: user_selectable
    """)

LITERAL = textwrap.dedent("""\
    name: builder-insights
    description: |
      Query build-health metrics for a team.
      Use when asked about pipeline freshness.
    """)

FOLDED = textwrap.dedent("""\
    name: golden-path-recommendations
    description: >-
      Retrieve current in-house guidance from the recommendation service. Use for
      recommended architecture or security decisions.
    tags: [skill, recommendations, audit]
    """)


class TestMultiLineValues:

    def test_plain_continuation_is_joined(self):
        fm = parse_frontmatter(PLAIN)
        assert fm["description"] == (
            "Query fleet telemetry — live (streaming) or historical (stored) — "
            "for any entity in the system, from individual hosts up to the "
            "whole fleet. Sources: the Metrics API (warehouse SQL, or the columnar store "
            "via the `cs;` prefix) via the fleetmetrics CLI plugin.")
        assert fm["compatibility"] == (
            "Requires the fleetmetrics CLI plugin installed (see Installation), "
            "cloud credentials via the SSO helper.")

    def test_continuation_line_with_colon_is_not_a_key(self):
        fm = parse_frontmatter(PLAIN)
        assert "Sources" not in fm
        assert set(fm) == {"name", "description", "compatibility", "metadata"}

    def test_map_after_multiline_values_still_parses(self):
        assert parse_frontmatter(PLAIN)["metadata"] == "ziya-visibility=user_selectable"

    def test_literal_block_keeps_newlines(self):
        assert parse_frontmatter(LITERAL)["description"] == (
            "Query build-health metrics for a team.\n"
            "Use when asked about pipeline freshness.")

    def test_folded_block_joins_lines(self):
        fm = parse_frontmatter(FOLDED)
        assert fm["description"] == (
            "Retrieve current in-house guidance from the recommendation service. "
            "Use for recommended architecture or security decisions.")
        # Non-block values after the block are unaffected and stay strings.
        assert fm["tags"] == "[skill, recommendations, audit]"

    @pytest.mark.parametrize("header", ["|-", "|+", ">", "|2", ">- # note"])
    def test_block_header_variants(self, header):
        fm = parse_frontmatter(f"description: {header}\n  a\n  b\nname: x\n")
        assert fm["description"].startswith("a")
        assert fm["name"] == "x"

    def test_folded_blank_line_is_a_newline(self):
        fm = parse_frontmatter("description: >\n  one\n  two\n\n  three\n")
        assert fm["description"] == "one two\nthree"

    def test_plain_value_with_unquoted_colon(self):
        # The guide's most common malformed-YAML case; must load as-is.
        assert parse_frontmatter("description: Use when: x happens\n")["description"] \
            == "Use when: x happens"


class TestFlatBehaviourUnchanged:

    def test_basic_pairs(self):
        assert parse_frontmatter("name: test\ndescription: A test skill") == \
            {"name": "test", "description": "A test skill"}

    def test_quoted_values(self):
        fm = parse_frontmatter('name: "test-skill"\nversion: \'1.0\'')
        assert fm == {"name": "test-skill", "version": "1.0"}

    def test_comments_and_bare_keys(self):
        assert parse_frontmatter("# c\nname: t\nempty:\n") == {"name": "t"}

    def test_metadata_map_with_quotes(self):
        fm = parse_frontmatter('metadata:\n  author: org\n  version: "1.0"\n')
        assert fm["metadata"] == "author=org,version=1.0"


# ---------------------------------------------------------------------------
# Seam: discovery must use this parser (outermost surface: the Skill record)
# ---------------------------------------------------------------------------

def _write(root, name, fm_text, body="Body."):
    d = root / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(f"---\n{fm_text}---\n\n{body}\n", encoding="utf-8")
    return d


@pytest.mark.parametrize("fm_text,name,needle", [
    (PLAIN, "fleet-telemetry", "from individual hosts up to the whole fleet"),
    (LITERAL, "builder-insights", "Use when asked about pipeline freshness."),
    (FOLDED, "golden-path-recommendations", "architecture or security decisions"),
])
def test_discovered_skill_carries_full_description(tmp_path, fm_text, name, needle):
    from app.services.skill_discovery import discover_project_skills
    from app.services.token_service import TokenService
    _write(tmp_path / ".agents" / "skills", name, fm_text)
    skills = discover_project_skills(str(tmp_path), TokenService(), load_body=False)
    assert [s.name for s in skills] == [name]
    assert needle in skills[0].description
    assert skills[0].description not in ("|", ">-")


def test_discovered_skill_full_compatibility_and_visibility(tmp_path):
    from app.services.skill_discovery import discover_project_skills
    from app.services.token_service import TokenService
    _write(tmp_path / ".agents" / "skills", "fleet-telemetry",
           PLAIN.replace("user_selectable", "model_discoverable"))
    s = discover_project_skills(str(tmp_path), TokenService(), load_body=False)[0]
    assert s.compatibility.endswith("cloud credentials via the SSO helper.")
    assert s.visibility == "model_discoverable"
