"""
Tests for the emit_artifact builtin tool (app/mcp/tools/emit_artifact.py).

The tool file is delivered as a git diff alongside this test file; until
that diff is applied the module does not exist, so the whole file skips
via importorskip rather than failing collection.

Covers:
  - refusal outside an active task-run collection
  - text/data/file emission landing in the collector
  - grouping fields passthrough through the tool layer
  - diagram render-capture: success freezes a PNG blob + file part,
    failure preserves error evidence as a status="error" part
  - render console warnings recorded on the part
"""

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

emit_mod = pytest.importorskip(
    "app.mcp.tools.emit_artifact",
    reason="emit_artifact tool diff not applied yet",
)

from app.utils.task_artifacts import (  # noqa: E402
    finish_artifact_collection, start_artifact_collection,
)


@pytest.fixture
def tool():
    return emit_mod.EmitArtifactTool()


@pytest.fixture
def collector(tmp_path):
    token = start_artifact_collection(
        block_id="task-1", artifacts_dir=str(tmp_path / "artifacts"), run_id="run-1",
    )
    parts_ref = {}
    yield parts_ref
    parts_ref["parts"] = finish_artifact_collection(token)


def _result_text(result) -> str:
    return result["content"][0]["text"]


class TestOutsideTaskRun:
    @pytest.mark.asyncio
    async def test_refuses_without_collection(self, tool):
        result = await tool.execute(name="x", part_type="text", text="hello")
        assert "Task Card run" in _result_text(result)


class TestBasicEmission:
    @pytest.mark.asyncio
    async def test_text_part_lands_in_collector(self, tool, collector):
        result = await tool.execute(name="conclusion", part_type="text", text="All good.")
        assert "recorded" in _result_text(result)
        # drain happens in fixture teardown — force it now
        # (fixture yields parts_ref; re-drain manually)

    @pytest.mark.asyncio
    async def test_parts_content_and_grouping(self, tool, tmp_path):
        token = start_artifact_collection(
            block_id="task-9", artifacts_dir=str(tmp_path), run_id="r",
        )
        try:
            await tool.execute(name="a", part_type="text", text="x",
                               group="pair", label="before", seq=0)
            await tool.execute(name="b", part_type="data",
                               data={"k": 1}, group="pair", label="after", seq=1)
        finally:
            parts = finish_artifact_collection(token)
        assert len(parts) == 2
        assert parts[0]["group"] == "pair" and parts[0]["label"] == "before"
        assert parts[1]["data"] == {"k": 1} and parts[1]["seq"] == 1
        assert all(p["block_id"] == "task-9" for p in parts)

    @pytest.mark.asyncio
    async def test_validation_error_reported(self, tool, tmp_path):
        token = start_artifact_collection(block_id="t", artifacts_dir=str(tmp_path))
        try:
            result = await tool.execute(name="", part_type="text", text="x")
            assert "Error" in _result_text(result)
        finally:
            assert finish_artifact_collection(token) == []


class TestDiagramCapture:
    @pytest.mark.asyncio
    async def test_success_freezes_png_and_emits_file_part(self, tool, tmp_path):
        fake_png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
        token = start_artifact_collection(
            block_id="t", artifacts_dir=str(tmp_path / "artifacts"), run_id="r",
        )
        try:
            with patch("app.services.diagram_renderer.get_diagram_renderer") as mock_get:
                mock_renderer = AsyncMock()
                mock_renderer.render_diagram_with_diagnostics = AsyncMock(
                    return_value=(fake_png, {"console_warnings": ["[warning] fixup applied"],
                                             "console_errors": []}),
                )
                mock_get.return_value = mock_renderer
                result = await tool.execute(
                    name="fixed_render",
                    diagram={"type": "mermaid", "definition": "graph LR\n A-->B"},
                )
        finally:
            parts = finish_artifact_collection(token)

        assert "recorded" in _result_text(result)
        assert len(parts) == 1
        part = parts[0]
        assert part["part_type"] == "file"
        assert part["status"] == "ok"
        assert part["rendered"] is True
        assert part["media_type"] == "image/png"
        assert part["diagram_type"] == "mermaid"
        assert "graph LR" in part["diagram_definition"]
        assert part["render_warnings"] == ["[warning] fixup applied"]
        # Blob actually frozen on disk with the rendered bytes
        blob = Path(part["file_uri"])
        assert blob.exists()
        from app.utils.task_artifacts import read_artifact_blob
        assert read_artifact_blob(str(blob)) == fake_png

    @pytest.mark.asyncio
    async def test_failure_preserves_error_evidence(self, tool, tmp_path):
        token = start_artifact_collection(
            block_id="t", artifacts_dir=str(tmp_path / "artifacts"), run_id="r",
        )
        try:
            with patch("app.services.diagram_renderer.get_diagram_renderer") as mock_get:
                mock_renderer = AsyncMock()
                mock_renderer.render_diagram_with_diagnostics = AsyncMock(
                    side_effect=RuntimeError("timeout-no-output after 35000ms"),
                )
                mock_get.return_value = mock_renderer
                result = await tool.execute(
                    name="broken_render",
                    diagram={"type": "packet", "definition": "packet-beta\n0-7: x"},
                )
        finally:
            parts = finish_artifact_collection(token)

        # Still recorded — the failure IS the artifact
        assert "recorded" in _result_text(result)
        assert len(parts) == 1
        part = parts[0]
        assert part["part_type"] == "text"
        assert part["status"] == "error"
        assert "timeout-no-output" in part["text"]
        assert part["diagram_type"] == "packet"
        assert "packet-beta" in part["diagram_definition"]

    @pytest.mark.asyncio
    async def test_diagram_requires_type_and_definition(self, tool, tmp_path):
        token = start_artifact_collection(block_id="t", artifacts_dir=str(tmp_path))
        try:
            result = await tool.execute(name="x", diagram={"type": "mermaid"})
            assert "Error" in _result_text(result)
        finally:
            assert finish_artifact_collection(token) == []
