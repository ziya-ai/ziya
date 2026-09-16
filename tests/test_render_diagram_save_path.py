"""render_diagram ``save_path``: the bytes on disk are the bytes returned.

The GFX sweep's golden discipline depends on this seam: a judge looks at the
image render_diagram returned, and `gfx_ledger.py record --light-png` is
handed a file.  If the file could differ from what was judged, "validated
golden" would mean nothing.  So the write happens at the tool's common
return point, from the base64 block itself, and is path-confined.

The render is stubbed: what is under test is the persistence wrapper, not
Chromium.
"""

from __future__ import annotations

import base64
from pathlib import Path

import pytest

from app.mcp.tools import diagram_render as dr

PNG = (b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)


def _fake_result(data: bytes = PNG):
    return {
        "_has_image_content": True,
        "content": [
            {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                         "data": base64.b64encode(data).decode()}},
            {"type": "text", "text": "Rendered"},
        ],
    }


@pytest.fixture
def tool(monkeypatch):
    t = dr.RenderDiagramTool()

    async def _stub(**kwargs):
        return _fake_result()
    monkeypatch.setattr(t, "_execute", _stub)
    return t


@pytest.fixture
def project_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".ziya").mkdir()
    return tmp_path


async def test_save_path_writes_exactly_the_returned_bytes(tool, project_cwd):
    dest = project_cwd / ".ziya" / "gfx-sweep" / "renders" / "x.light.png"
    res = await tool.execute(type="packet", definition="{}", save_path=str(dest))
    assert dest.read_bytes() == PNG
    img = next(b for b in res["content"] if b["type"] == "image")
    assert base64.b64decode(img["source"]["data"]) == dest.read_bytes()
    assert any("Saved" in b.get("text", "") for b in res["content"])


async def test_relative_save_path_resolves_under_cwd(tool, project_cwd):
    await tool.execute(type="packet", definition="{}", save_path=".ziya/r.png")
    assert (project_cwd / ".ziya" / "r.png").read_bytes() == PNG


async def test_save_path_outside_allowed_roots_is_refused_but_render_survives(tool, project_cwd):
    target = project_cwd / "app" / "evil.png"
    res = await tool.execute(type="packet", definition="{}", save_path=str(target))
    assert not target.exists()
    assert res.get("_has_image_content") is True, "the render result must not be lost"
    assert any("save_path not written" in b.get("text", "") for b in res["content"])


async def test_traversal_out_of_ziya_is_refused(tool, project_cwd):
    res = await tool.execute(type="packet", definition="{}",
                             save_path=".ziya/../escaped.png")
    assert not (project_cwd / "escaped.png").exists()
    assert any("save_path not written" in b.get("text", "") for b in res["content"])


async def test_non_image_extension_refused(tool, project_cwd):
    res = await tool.execute(type="packet", definition="{}", save_path=".ziya/x.py")
    assert not (project_cwd / ".ziya" / "x.py").exists()
    assert any("save_path not written" in b.get("text", "") for b in res["content"])


async def test_no_save_path_leaves_result_untouched(tool, project_cwd):
    res = await tool.execute(type="packet", definition="{}")
    assert res == _fake_result()


async def test_error_result_is_not_persisted(monkeypatch, project_cwd):
    t = dr.RenderDiagramTool()

    async def _err(**kwargs):
        return dr._error("boom")
    monkeypatch.setattr(t, "_execute", _err)
    res = await t.execute(type="packet", definition="{}", save_path=".ziya/e.png")
    assert not (project_cwd / ".ziya" / "e.png").exists()
    assert res == dr._error("boom")


def test_schema_exposes_save_path():
    assert "save_path" in dr.RenderDiagramInput.model_fields


# ── view_image: the read side of the same confinement ────────────────────

async def test_view_image_returns_exact_bytes(project_cwd):
    p = project_cwd / ".ziya" / "gfx-golden" / "packet" / "x.light.png"
    p.parent.mkdir(parents=True)
    p.write_bytes(PNG)
    res = await dr.ViewImageTool().execute(path=str(p))
    assert res.get("_has_image_content") is True
    img = next(b for b in res["content"] if b["type"] == "image")
    assert base64.b64decode(img["source"]["data"]) == PNG
    assert img["source"]["media_type"] == "image/png"


async def test_view_image_refuses_paths_outside_ziya(project_cwd):
    target = project_cwd / "app" / "secret.png"
    target.parent.mkdir()
    target.write_bytes(PNG)
    res = await dr.ViewImageTool().execute(path=str(target))
    assert not res.get("_has_image_content")
    assert "outside the allowed" in res["content"][0]["text"]


async def test_view_image_missing_file(project_cwd):
    res = await dr.ViewImageTool().execute(path=".ziya/nope.png")
    assert "no such file" in res["content"][0]["text"]


def test_view_image_is_registered_with_render_tools(monkeypatch):
    from app.mcp import builtin_tools
    from app.services import diagram_renderer
    monkeypatch.setattr(diagram_renderer, "_check_playwright", lambda: True)
    names = {t.name for t in builtin_tools.get_diagram_render_tools()}
    assert {"render_diagram", "recall_image", "view_image"} <= names
