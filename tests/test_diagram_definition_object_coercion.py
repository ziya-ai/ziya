"""
Object-vs-string polymorphism at the diagram ``definition`` boundary.

Every frontend plugin consumes a diagram spec via
``JSON.parse(spec.definition)`` (frontend/src/plugins/d3/*.ts), so the
field must cross the Python->browser boundary as TEXT.  But the
structured diagram types -- vega-lite, vega, plotly, packet, music,
joint, chord, network, d3 -- have specs that are natively JSON objects,
and a caller holding one passes the object as readily as its serialized
string.

Before the fix that asymmetry failed differently at each call site:

  * ``emit_artifact`` truncated the definition for its record with
    ``definition[:MAX_DIAGRAM_DEF_CHARS]``.  On a dict that raises
    ``KeyError: slice(None, 50000, None)`` -- and the slice sat ABOVE the
    try/except that exists to preserve render failures as evidence, so
    the emit produced nothing at all instead of an error artifact.
  * ``render_diagram`` forwarded the object, so ``len(definition)``
    silently reported a key count and the browser handed ``JSON.parse``
    the string "[object Object]".

These tests assert the seam at all three hops: the shared normalizer,
and each of the two builtin tools that feed it.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from app.utils.task_artifacts import (
    finish_artifact_collection, start_artifact_collection,
)

# A genuine structured spec of the kind a model produces for vega-lite --
# an object, not a string.
VEGA_SPEC = {
    "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
    "data": {"values": [{"tool": "ziya", "score": 4}, {"tool": "other", "score": 4}]},
    "mark": "bar",
    "encoding": {
        "x": {"field": "tool", "type": "nominal"},
        "y": {"field": "score", "type": "quantitative"},
    },
}

FAKE_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


def _text_of(result) -> str:
    return result["content"][0]["text"]


class TestNormalizer:
    """The shared chokepoint every render call site funnels through."""

    def test_dict_definition_is_serialized(self):
        from app.services.diagram_renderer import normalize_spec_definition

        out = normalize_spec_definition(
            {"type": "vega-lite", "definition": VEGA_SPEC, "theme": "light"}
        )
        assert isinstance(out["definition"], str)
        # Round-trips to the same spec -- serialization, not stringification.
        assert json.loads(out["definition"]) == VEGA_SPEC
        # Sibling keys survive untouched.
        assert out["type"] == "vega-lite" and out["theme"] == "light"

    def test_list_definition_is_serialized(self):
        from app.services.diagram_renderer import normalize_spec_definition

        out = normalize_spec_definition({"type": "d3", "definition": [1, 2, 3]})
        assert out["definition"] == "[1, 2, 3]"

    def test_string_definition_passes_through_unchanged(self):
        from app.services.diagram_renderer import normalize_spec_definition

        spec = {"type": "mermaid", "definition": "graph LR\n A-->B"}
        assert normalize_spec_definition(spec)["definition"] == "graph LR\n A-->B"

    def test_missing_definition_is_not_invented(self):
        from app.services.diagram_renderer import normalize_spec_definition

        assert "definition" not in normalize_spec_definition({"type": "mermaid"})

    def test_non_dict_spec_is_returned_as_is(self):
        from app.services.diagram_renderer import normalize_spec_definition

        assert normalize_spec_definition(None) is None


class TestEmitArtifactObjectDefinition:
    """The exact call that produced KeyError: slice(None, 50000, None)."""

    @pytest.mark.asyncio
    async def test_object_spec_emits_rendered_png_part(self, tmp_path):
        from app.mcp.tools.emit_artifact import EmitArtifactTool

        token = start_artifact_collection(
            block_id="t", artifacts_dir=str(tmp_path / "artifacts"), run_id="r",
        )
        try:
            with patch("app.services.diagram_renderer.get_diagram_renderer") as mock_get:
                renderer = AsyncMock()
                renderer.render_diagram_with_diagnostics = AsyncMock(
                    return_value=(FAKE_PNG, {"console_warnings": [], "console_errors": []}),
                )
                mock_get.return_value = renderer
                result = await EmitArtifactTool().execute(
                    name="scores",
                    diagram={"type": "vega-lite", "definition": VEGA_SPEC},
                )
                spec_sent = renderer.render_diagram_with_diagnostics.await_args[0][0]
        finally:
            parts = finish_artifact_collection(token)

        # Positive assertion that the path actually ran, not merely that
        # nothing blew up: a real rendered file part was recorded.
        assert "recorded" in _text_of(result), _text_of(result)
        assert len(parts) == 1
        part = parts[0]
        assert part["part_type"] == "file"
        assert part["status"] == "ok"
        assert part["rendered"] is True

        # The recorded definition is the serialized spec, not "{'$schema'..."
        assert json.loads(part["diagram_definition"]) == VEGA_SPEC

        # And the renderer received text, because the browser JSON.parses it.
        assert isinstance(spec_sent["definition"], str)

    @pytest.mark.asyncio
    async def test_unserializable_object_reports_instead_of_raising(self, tmp_path):
        from app.mcp.tools.emit_artifact import EmitArtifactTool

        token = start_artifact_collection(
            block_id="t", artifacts_dir=str(tmp_path / "artifacts"), run_id="r",
        )
        try:
            result = await EmitArtifactTool().execute(
                name="bad", diagram={"type": "vega-lite", "definition": {"k": object()}},
            )
        finally:
            finish_artifact_collection(token)
        assert "Error" in _text_of(result)
        assert "serializable" in _text_of(result)

    @pytest.mark.asyncio
    async def test_string_definition_still_works(self, tmp_path):
        """Guard against the fix regressing the ordinary text-spec path."""
        from app.mcp.tools.emit_artifact import EmitArtifactTool

        token = start_artifact_collection(
            block_id="t", artifacts_dir=str(tmp_path / "artifacts"), run_id="r",
        )
        try:
            with patch("app.services.diagram_renderer.get_diagram_renderer") as mock_get:
                renderer = AsyncMock()
                renderer.render_diagram_with_diagnostics = AsyncMock(
                    return_value=(FAKE_PNG, {"console_warnings": [], "console_errors": []}),
                )
                mock_get.return_value = renderer
                await EmitArtifactTool().execute(
                    name="flow",
                    diagram={"type": "mermaid", "definition": "graph LR\n A-->B"},
                )
        finally:
            parts = finish_artifact_collection(token)
        assert parts[0]["diagram_definition"] == "graph LR\n A-->B"


class TestRenderDiagramObjectDefinition:
    @pytest.mark.asyncio
    async def test_object_spec_reaches_renderer_as_text(self):
        from app.mcp.tools.diagram_render import RenderDiagramTool

        with patch("app.services.diagram_renderer.get_diagram_renderer") as mock_get:
            renderer = AsyncMock()
            renderer.render_diagram_with_diagnostics = AsyncMock(
                return_value=(FAKE_PNG, {"console_warnings": [], "console_errors": []}),
            )
            mock_get.return_value = renderer
            result = await RenderDiagramTool().execute(
                type="vega-lite", definition=VEGA_SPEC,
            )
            spec_sent = renderer.render_diagram_with_diagnostics.await_args[0][0]

        assert isinstance(spec_sent["definition"], str)
        assert json.loads(spec_sent["definition"]) == VEGA_SPEC
        # An image block came back -- the render path completed.
        assert any(b.get("type") == "image" for b in result["content"]), result

    @pytest.mark.asyncio
    async def test_empty_object_still_reports_missing_definition(self):
        """An empty spec is missing input, not something to serialize."""
        from app.mcp.tools.diagram_render import RenderDiagramTool

        result = await RenderDiagramTool().execute(type="vega-lite", definition={})
        assert "'definition' is required" in _text_of(result)
