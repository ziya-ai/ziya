"""
`diagram` and `from_run` are mutually exclusive on one emit_artifact call.

``diagram`` renders NEW content at emit time; ``from_run`` names content
captured ELSEWHERE (an earlier block of this run, or an earlier run).  A
single part cannot be both, and the pre-guard behaviour was to drop
``from_run`` silently: ``execute`` dispatched to ``_emit_diagram(name,
diagram, group, label, seq)`` — a signature with no ``from_run`` — so the
call reported success, one part was recorded (the fresh render), and the
prior evidence the caller asked to include simply never appeared.

That is the worst available failure shape for the case that motivates
combining them, a before/after: the report renders, the run reports
success, and the half that makes it a comparison is missing with nothing
anywhere saying so.  The remedy is not to guess which the caller meant
but to refuse and name the pattern that does work.

Test note: the tool module is delivered as a git diff, so this file
skips rather than failing collection when the diff is unapplied — the
same importorskip convention as tests/test_emit_artifact_tool.py.
"""

from contextlib import contextmanager
from unittest.mock import AsyncMock, patch

import pytest

emit_mod = pytest.importorskip(
    "app.mcp.tools.emit_artifact",
    reason="emit_artifact tool diff not applied yet",
)

from app.utils.task_artifacts import (  # noqa: E402
    finish_artifact_collection, start_artifact_collection,
)

DIAGRAM = {"type": "mermaid", "definition": "graph LR\n A-->B"}


@pytest.fixture
def tool():
    return emit_mod.EmitArtifactTool()


def _result_text(result) -> str:
    return result["content"][0]["text"]


def _open(tmp_path):
    return start_artifact_collection(
        block_id="t", artifacts_dir=str(tmp_path / "artifacts"), run_id="r",
    )


FAKE_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16


@contextmanager
def _stub_renderer():
    """Patch the headless renderer to return bytes without launching it.

    Yields the patched factory so a test can assert whether a render was
    even ATTEMPTED.

    Stubbed even in tests that expect a REFUSAL: unpatched, an unfixed
    tree reaches the real renderer and the assertion arrives ~30s later
    via the render timeout, which makes a fast deterministic failure look
    like a hung suite.  Stubbing keeps these tests measuring the guard
    rather than the renderer's availability.
    """
    mock_renderer = AsyncMock()
    mock_renderer.render_diagram_with_diagnostics = AsyncMock(
        return_value=(FAKE_PNG, {}),
    )
    with patch("app.services.diagram_renderer.get_diagram_renderer") as mock_get:
        mock_get.return_value = mock_renderer
        yield mock_get


class TestRefusal:
    @pytest.mark.asyncio
    async def test_combining_is_refused_and_records_nothing(self, tool, tmp_path):
        """The call must fail, and must not leave a part behind.

        Asserting only "an error came back" would still pass if the
        render had been recorded first, which is the defect — so the
        emptiness of the collector is the load-bearing assertion here.
        """
        token = _open(tmp_path)
        try:
            with _stub_renderer() as mock_get:
                result = await tool.execute(
                    name="after",
                    diagram=DIAGRAM,
                    from_run="self",
                    file_path="before.png",
                )
                # Refusal must precede the render: a rejected emit that
                # still paid for a headless render is a silent cost, and
                # for a several-hundred-part gallery not a small one.
                assert not mock_get.called, (
                    "renderer was invoked for a call that must be refused "
                    "before any render is attempted"
                )
        finally:
            parts = finish_artifact_collection(token)

        text = _result_text(result)
        assert "Error" in text, (
            f"combining diagram+from_run reported success: {text!r} — "
            f"from_run was silently dropped"
        )
        assert parts == [], (
            f"refused emit still recorded {len(parts)} part(s); a rejected "
            f"call must be a no-op"
        )

    @pytest.mark.asyncio
    async def test_message_names_both_conflicting_parameters(self, tool, tmp_path):
        """Naming only one side leaves the caller guessing what conflicted."""
        token = _open(tmp_path)
        try:
            with _stub_renderer():
                result = await tool.execute(
                    name="after", diagram=DIAGRAM,
                    from_run="self", file_path="before.png",
                )
        finally:
            finish_artifact_collection(token)
        text = _result_text(result)
        assert "diagram" in text and "from_run" in text, (
            f"error must name both parameters, got {text!r}"
        )

    @pytest.mark.asyncio
    async def test_message_names_the_pattern_that_works(self, tool, tmp_path):
        """An error that only forbids strands the caller.

        The reason to pass both is almost always a before/after, which IS
        expressible — two parts sharing one `group` with distinct
        `label`s — so the message has to point at that rather than just
        saying no.
        """
        token = _open(tmp_path)
        try:
            with _stub_renderer():
                result = await tool.execute(
                    name="after", diagram=DIAGRAM,
                    from_run="self", file_path="before.png",
                )
        finally:
            finish_artifact_collection(token)
        text = _result_text(result).lower()
        assert "group" in text and "label" in text, (
            f"error should name the two-part group/label remedy, got {text!r}"
        )


class TestGuardIsNarrow:
    """Positive controls: the guard must not widen into the normal paths.

    Without these, deleting the dispatch entirely would satisfy the
    refusal tests above.
    """

    @pytest.mark.asyncio
    async def test_diagram_alone_still_renders_and_records(self, tool, tmp_path):
        token = _open(tmp_path)
        try:
            with _stub_renderer():
                result = await tool.execute(name="after", diagram=DIAGRAM)
        finally:
            parts = finish_artifact_collection(token)

        assert "recorded" in _result_text(result)
        assert len(parts) == 1 and parts[0]["rendered"] is True

    @pytest.mark.asyncio
    async def test_absent_from_run_is_absence_not_conflict(self, tool, tmp_path):
        """`from_run=None` explicitly passed must not trip the guard.

        Pins the check to TRUTHINESS rather than ``is not None``: a
        caller that fills every field with an explicit null — or a
        wrapper that forwards ``**kwargs`` wholesale — would otherwise be
        refused for supplying nothing at all.
        """
        for empty in (None, ""):
            token = _open(tmp_path)
            try:
                with _stub_renderer():
                    result = await tool.execute(
                        name="after", diagram=DIAGRAM, from_run=empty,
                    )
            finally:
                parts = finish_artifact_collection(token)
            assert "recorded" in _result_text(result), (
                f"from_run={empty!r} should read as absent, not as a conflict"
            )
            assert len(parts) == 1

    @pytest.mark.asyncio
    async def test_from_run_alone_still_reaches_the_file_path(self, tool, tmp_path):
        """from_run without diagram must still be honoured.

        Guards this fix against the lazy inverse — refusing `from_run`
        outright.  The reference is unresolvable here (no such blob), so
        the assertion is that the failure is about the MISSING ARTIFACT,
        i.e. resolution was attempted, rather than about a conflict.
        """
        token = _open(tmp_path)
        try:
            result = await tool.execute(
                name="before", part_type="file",
                from_run="self", file_path="nonexistent.png",
            )
        finally:
            finish_artifact_collection(token)
        text = _result_text(result)
        assert "Error" in text
        assert "diagram" not in text.lower(), (
            f"from_run alone was rejected as a conflict: {text!r}"
        )
