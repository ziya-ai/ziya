"""Tests for app.agents.task_templating — pure substitution logic.

Covers:
  - Index / item / previous / all placeholders
  - Nested item.key access for dict / list items
  - Unknown placeholders left verbatim (typo-visible)
  - Known-but-missing placeholders render empty (iteration 0)
  - for_each_source JSON parsing: happy, empty, malformed, non-array
"""

import pytest

from app.agents.task_templating import (
    IterationBindings, render, parse_for_each_source,
)
from app.models.task_card import Artifact


class TestRenderBasics:
    def test_empty_template(self):
        assert render("", IterationBindings()) == ""
        assert render(None, IterationBindings()) == ""  # type: ignore[arg-type]

    def test_no_placeholders(self):
        assert render("plain text", IterationBindings()) == "plain text"

    def test_index(self):
        assert render("i={{index}}", IterationBindings(index=3)) == "i=3"

    def test_unknown_placeholder_preserved(self):
        # Typos surface to the user rather than vanishing.
        out = render("x={{nope}}", IterationBindings(index=0))
        assert out == "x={{nope}}"

    def test_whitespace_inside_placeholder(self):
        assert render("i={{ index }}", IterationBindings(index=7)) == "i=7"


class TestItemBindings:
    def test_string_item(self):
        out = render("doing {{item}}", IterationBindings(item="foo.py"))
        assert out == "doing foo.py"

    def test_dict_item_key_access(self):
        b = IterationBindings(item={"id": 42, "name": "alpha"})
        assert render("{{item.name}}: {{item.id}}", b) == "alpha: 42"

    def test_missing_item_key(self):
        b = IterationBindings(item={"id": 1})
        assert render("{{item.missing}}", b) == ""

    def test_list_item_index_access(self):
        b = IterationBindings(item=["a", "b", "c"])
        assert render("{{item.1}}", b) == "b"

    def test_non_string_non_container_item(self):
        # Raw {{item}} of a dict renders as compact JSON so the
        # model sees a parseable form.
        b = IterationBindings(item={"x": 1})
        assert render("{{item}}", b) == '{"x": 1}'

    def test_item_none(self):
        # None item resolves to empty — consistent with other missing refs.
        assert render("x={{item}}", IterationBindings(item=None)) == "x="


class TestPreviousBindings:
    def test_previous_none_renders_empty(self):
        # Iteration 0: no previous.  All previous.* placeholders are
        # known heads so they render empty, not preserved.
        b = IterationBindings(index=0, previous=None)
        assert render("last: {{previous.summary}}", b) == "last: "
        assert render("{{previous}}", b) == ""

    def test_previous_summary(self):
        b = IterationBindings(
            index=1,
            previous=Artifact(summary="saw 3 errors"),
        )
        assert render("prior: {{previous.summary}}", b) == "prior: saw 3 errors"

    def test_previous_bare_renders_summary(self):
        # Convenience: {{previous}} alone == {{previous.summary}}.
        b = IterationBindings(previous=Artifact(summary="hi"))
        assert render("{{previous}}", b) == "hi"

    def test_previous_decisions_joined(self):
        b = IterationBindings(
            previous=Artifact(decisions=["chose A", "rejected B"]),
        )
        assert render("{{previous.decisions}}", b) == "chose A\nrejected B"

    def test_previous_unknown_field_empty(self):
        b = IterationBindings(previous=Artifact(summary="s"))
        assert render("{{previous.bogus}}", b) == ""


class TestAllBindings:
    def test_all_summaries_empty(self):
        b = IterationBindings(all_summaries=[])
        assert render("{{all.summaries}}", b) == ""

    def test_all_summaries_joined(self):
        b = IterationBindings(all_summaries=["one", "two", "three"])
        out = render("{{all.summaries}}", b)
        assert out == "one\n\ntwo\n\nthree"


class TestParseForEachSource:
    def test_none(self):
        assert parse_for_each_source(None) is None

    def test_empty_string(self):
        assert parse_for_each_source("") is None
        assert parse_for_each_source("   ") is None

    def test_json_array_of_strings(self):
        assert parse_for_each_source('["a", "b"]') == ["a", "b"]

    def test_json_array_of_objects(self):
        got = parse_for_each_source('[{"id": 1}, {"id": 2}]')
        assert got == [{"id": 1}, {"id": 2}]

    def test_malformed_returns_none(self):
        # Caller falls back to count-based plan.
        assert parse_for_each_source("[not, valid, json") is None

    def test_non_array_returns_none(self):
        assert parse_for_each_source('{"k": "v"}') is None
        assert parse_for_each_source('"just a string"') is None

    def test_empty_array(self):
        # An empty array IS valid — yields zero iterations.
        assert parse_for_each_source("[]") == []


class TestCombinations:
    def test_multiple_placeholders_one_template(self):
        b = IterationBindings(
            index=2,
            item={"file": "x.py"},
            previous=Artifact(summary="OK"),
        )
        tmpl = "iter {{index}} on {{item.file}}, prior: {{previous.summary}}"
        assert render(tmpl, b) == "iter 2 on x.py, prior: OK"

    def test_unknown_mixed_with_known(self):
        b = IterationBindings(index=0)
        # Known placeholder substitutes; unknown stays.
        assert render("{{index}} / {{unknown}}", b) == "0 / {{unknown}}"
