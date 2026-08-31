"""
Seam tests: the emit_artifact / list_run_artifacts tool SCHEMAS must agree
with what app.utils.task_artifacts actually accepts.

Why this file exists.  A tool's parameter description is the only thing a
model sees at the call site — it is the user manual, not a comment.  When
the resolver grew support for referring to the CURRENT run and for naming
a card instead of a run id, the schema text was left describing the older
"run id only, always copies" behaviour.  Nothing failed: 140 artifact
tests passed, both tools imported, every backend path worked.  The
capability was simply undiscoverable, and worse, the schema contradicted
the system-prompt instruction that told the model to pass 'self'.

That class of drift is invisible to tests that exercise only the backend,
so these assert the seam between the two halves.  They are derived from
``_SELF_RUN_ALIASES`` rather than hardcoding prose, so adding an alias
does not silently leave it undocumented.

Note on matching: substring checks against bare words are useless here.
The stale description contained the words "self-contained" and "this
project", so naive ``'self' in text`` and ``'this' in text`` both passed
against text that documented neither alias.  These tests look for the
alias AS A QUOTED VALUE, which is how a schema actually offers a literal
to a caller.
"""

import pytest

from app.mcp.tools.emit_artifact import EmitArtifactTool
from app.mcp.tools.list_run_artifacts import ListRunArtifactsTool
from app.utils.task_artifacts import _SELF_RUN_ALIASES


def _field_description(tool_cls, field_name: str) -> str:
    fields = tool_cls.InputSchema.model_fields
    assert field_name in fields, (
        f"{tool_cls.name} has no {field_name!r} parameter — the schema and "
        f"the backend have diverged structurally, not just in wording"
    )
    return fields[field_name].description or ""


def _documents_literal(text: str, value: str) -> bool:
    """True if ``text`` offers ``value`` as a quoted literal.

    Deliberately stricter than a substring test: "self-contained" must
    not count as documenting the ``self`` alias.
    """
    lowered = text.lower()
    return any(
        q in lowered
        for q in (f"'{value}'", f'"{value}"', f"`{value}`")
    )


class TestEmitArtifactFromRunSchema:
    """The from_run parameter must advertise every accepted form."""

    def test_from_run_parameter_exists(self):
        # Positive control: without this, every assertion below could pass
        # vacuously on a schema that dropped the parameter entirely.
        desc = _field_description(EmitArtifactTool, "from_run")
        assert desc.strip(), "from_run has no description at all"

    def test_documents_a_self_reference_alias(self):
        """Referring to the CURRENT run must be discoverable.

        This is the case that a multi-card Call stack depends on: a Call
        executes inline in the caller's run, so an aggregating block
        reaching for evidence emitted by an earlier block is referencing
        its OWN run.  If the schema only mentions "an earlier run", the
        model never tries it — and it cannot reach the blob any other
        way, because the artifacts dir is outside the project root and
        covered by no read grant.
        """
        desc = _field_description(EmitArtifactTool, "from_run")
        documented = [a for a in sorted(_SELF_RUN_ALIASES)
                      if _documents_literal(desc, a)]
        assert documented, (
            "from_run accepts the self-reference aliases "
            f"{sorted(_SELF_RUN_ALIASES)} but the schema documents none of "
            "them as a literal value, so same-run reference is "
            f"undiscoverable. Description: {desc!r}"
        )

    def test_documents_card_reference(self):
        """Naming a card must be discoverable.

        A card author cannot know a run id — the run does not exist when
        the card is written.  Card-name resolution is therefore the only
        form usable from a card definition, which makes it the form most
        important to document.
        """
        desc = _field_description(EmitArtifactTool, "from_run").lower()
        assert "card" in desc, (
            "from_run resolves card ids and card names, but the schema "
            f"never mentions cards. Description: {desc!r}"
        )

    def test_does_not_claim_run_id_is_the_only_form(self):
        """Guard against the specific stale wording that regressed.

        Paired with the positive assertions above so this cannot pass by
        the description being empty or the field being absent.
        """
        desc = _field_description(EmitArtifactTool, "from_run")
        assert desc.strip()
        stale = "set this to that run's id"
        assert stale not in desc.lower(), (
            "from_run description still says the value is a run id, which "
            "is now false (self-aliases, card ids and card names are all "
            "accepted) and contradicts EMIT_ARTIFACT_INSTRUCTION"
        )


class TestDiscoveryToolIsReferenced:
    """emit_artifact's from_run is unusable without a way to enumerate."""

    def test_list_run_artifacts_shares_the_reference_vocabulary(self):
        """Both tools resolve through the same function, so both must
        describe the same accepted forms — a model that learns 'self'
        from one tool and is told 'run id' by the other will not trust
        either."""
        desc = _field_description(ListRunArtifactsTool, "from_run")
        documented = [a for a in sorted(_SELF_RUN_ALIASES)
                      if _documents_literal(desc, a)]
        assert documented, (
            "list_run_artifacts.from_run does not document any "
            f"self-reference alias. Description: {desc!r}"
        )
        assert "card" in desc.lower(), (
            "list_run_artifacts.from_run does not mention card references"
        )


class TestInstructionAndSchemaAgree:
    """The system-prompt instruction and the tool schema are two channels
    to the same model; disagreement between them is worse than either
    being terse, because the model cannot tell which is current."""

    def test_instruction_mentions_nothing_the_schema_omits(self):
        from app.utils.task_artifacts import EMIT_ARTIFACT_INSTRUCTION

        instruction = EMIT_ARTIFACT_INSTRUCTION.lower()
        schema = _field_description(EmitArtifactTool, "from_run").lower()

        # Only assert on forms the instruction actually promotes, so this
        # does not demand the schema restate the entire instruction.
        promoted = [a for a in sorted(_SELF_RUN_ALIASES)
                    if _documents_literal(instruction, a)]
        if not promoted:
            pytest.skip(
                "EMIT_ARTIFACT_INSTRUCTION does not promote a self alias; "
                "nothing for the schema to agree with"
            )
        assert any(_documents_literal(schema, a) for a in promoted), (
            f"the task instruction tells the model to pass {promoted} but "
            f"the from_run schema documents no such literal, so the two "
            f"channels contradict each other. Schema: {schema!r}"
        )
