"""The runtime message for an unresolvable PRECISE for_each source names
the hop that missed.

Why: the renderer collapses every miss to "" (unknown block, absent
part, absent key are all "no result yet" to it), so the executor could
only report "resolved to empty text" with one remedy - fix the upstream
emit_artifact.  On the measured failure that remedy was wrong: the
source ``{{sibling("Build design-doc roster").outputs.roster.docs}}``
named the upstream block by NAME.  The upstream part may well have been
fine; nothing said the reference was the problem.

These tests drive ``_resolve_for_each_items`` directly with a populated
``artifact_registry`` and assert on the outermost surface: the text of
the raised ``ForEachSourceError``.
"""

import pytest

from app.agents import block_executor as bx
from app.agents.block_executor import ExecutionContext, ForEachSourceError
from app.models.task_card import Artifact, ArtifactPart, Block


def _part(name, payload):
    return ArtifactPart(part_type="data", data=payload, name=name)


def _repeat(source):
    return Block(
        block_type="repeat", id="b-fan", name="Fan out",
        repeat_mode="for_each", repeat_for_each_source=source,
        body=[Block(block_type="task", id="b-one", instructions="x")],
    )


def _ctx_with(registry):
    ctx = ExecutionContext(run_id="")
    ctx.artifact_registry.update(registry)
    return ctx


def _fails_with(source, registry):
    with pytest.raises(ForEachSourceError) as ei:
        bx._resolve_for_each_items(_repeat(source), _ctx_with(registry))
    return str(ei.value)


ROSTER = Artifact(summary="built roster", created_at=0.0,
                  outputs=[_part("roster", {"docs": ["a.md", "b.md"]})])


class TestPreciseMissDiagnosis:
    def test_by_id_resolves(self):
        # Positive path: the diagnosis code is never reached.
        items = bx._resolve_for_each_items(
            _repeat('{{sibling("b-roster").outputs.roster.docs}}'),
            _ctx_with({"b-roster": ROSTER}),
        )
        assert items == ["a.md", "b.md"]

    def test_unknown_block_id_says_so_and_mentions_name_vs_id(self):
        # The measured failure: a block NAME where an id belongs.
        msg = _fails_with(
            '{{sibling("Build roster").outputs.roster.docs}}',
            {"b-roster": ROSTER},
        )
        assert "No block with id 'Build roster' has completed" in msg
        assert "not its name" in msg
        assert "b-roster" in msg  # the id that WAS there
        # And the old, misleading remedy is gone from this case.
        assert "whole-string JSON array" not in msg

    def test_missing_part_lists_emitted_parts(self):
        msg = _fails_with(
            '{{sibling("b-roster").outputs.plan.docs}}',
            {"b-roster": ROSTER},
        )
        assert "emitted no artifact part named 'plan'" in msg
        assert "['roster']" in msg

    def test_missing_key_lists_available_keys(self):
        msg = _fails_with(
            '{{sibling("b-roster").outputs.roster.files}}',
            {"b-roster": ROSTER},
        )
        assert "has no key 'files'" in msg
        assert "['docs']" in msg

    def test_non_array_value_is_named(self):
        art = Artifact(summary="s", created_at=0.0,
                       outputs=[_part("roster", {"docs": {"a": 1}})])
        msg = _fails_with(
            '{{sibling("b-roster").outputs.roster.docs}}', {"b-roster": art},
        )
        assert "is a dict, not a JSON array" in msg

    def test_previous_sibling_with_no_prior_block(self):
        msg = _fails_with(
            '{{previous_sibling.outputs.roster.docs}}', {},
        )
        assert "no previous sibling" in msg

    def test_generic_hint_still_used_for_iteration_scoped_previous(self):
        # {{previous.…}} is not locatable from the registry; the generic
        # precise-source remedy remains.
        msg = _fails_with('{{previous.outputs.roster.docs}}', {})
        assert "whole-string JSON array" in msg

    def test_parts_regex_accepts_exactly_what_anchor_regex_accepts(self):
        # The two regexes must not drift.
        cases = [
            '{{sibling("a").outputs.r.d}}',
            "{{ sibling('a').outputs_all.r }}",
            '{{previous_sibling.outputs.r.d.e}}',
            '{{previous.outputs.r.d}}',
            'prose {{sibling("a").outputs.r.d}}',   # not precise
            '{{sibling("a").outputs}}',              # no part
            '{{sibling("a").summary}}',              # not outputs
        ]
        for c in cases:
            assert bool(bx._PRECISE_SOURCE_RE.match(c)) == \
                bool(bx._PRECISE_SOURCE_PARTS_RE.match(c)), c
