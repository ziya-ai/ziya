"""
Per-project tool modes (design/git-tool.md step 5): disabled / gated /
autonomous, layered over the consent gate and the tool loader.

``_read_modes`` is patched so no real project.json is read; one test drives
the real ProjectStorage round trip to pin the settings seam.
"""

import asyncio

import pytest

from app.utils import consent_gate as cg
from app.utils import project_tool_modes as ptm

ROOT = "/proj/a"


class _Git:
    name = "git"
    consent_default = "ask"
    consent_op_key = "op"
    consent_op_defaults = {"status": "off"}


class _Other:
    name = "file_list"


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    cg.reset_for_tests(tmp_path / "consents")
    monkeypatch.setattr(cg, "_consent_config", lambda: {})
    ptm.invalidate()
    yield
    ptm.invalidate()
    cg.reset_for_tests()


def _set(monkeypatch, modes):
    monkeypatch.setattr(ptm, "_read_modes", lambda root: dict(modes) if root == ROOT else {})
    ptm.invalidate()


async def _decide(op, *, conversation_id="c1", project_root=ROOT, answer=None):
    events = []
    gen = cg.consent_gate(tool_name="git", tool_id=f"t-{op}", args={"op": op},
                          conversation_id=conversation_id, tool=_Git(),
                          project_root=project_root)
    async for ev in gen:
        events.append(ev)
        if ev["type"] == "consent_opened" and answer:
            cg.ledger().record_answer(ev["request_id"], answer)
    return events


def _decision(events):
    return [e for e in events if e["type"] == cg.DECISION_EVENT][-1]


# ── resolution ────────────────────────────────────────────────────────────

def test_unknown_values_are_ignored(monkeypatch):
    monkeypatch.setattr(ptm, "_read_modes",
                        lambda root: {k: v for k, v in {"git": "yolo", "x": "gated"}.items()
                                      if v in ptm.MODES})
    assert ptm.modes_for(ROOT) == {"x": "gated"}


def test_unreadable_settings_mean_inherit(monkeypatch):
    def boom(root):
        raise OSError("nope")
    monkeypatch.setattr(ptm, "_read_modes", boom)
    assert ptm.modes_for(ROOT) == {} and ptm.mode_for("git", ROOT) is None


# ── gate layering ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_autonomous_runs_a_mutating_op_without_asking(monkeypatch):
    _set(monkeypatch, {"git": "autonomous"})
    ev = await _decide("commit")
    assert not [e for e in ev if e["type"] == "consent_opened"]
    assert _decision(ev)["approved"] is True and _decision(ev)["asked"] is False


@pytest.mark.asyncio
async def test_autonomous_works_with_no_conversation(monkeypatch):
    """A task-run git step: nobody to ask, and autonomous does not need anyone."""
    _set(monkeypatch, {"git": "autonomous"})
    ev = await _decide("commit", conversation_id=None)
    assert _decision(ev)["approved"] is True


@pytest.mark.asyncio
async def test_gated_asks_for_mutating_ops(monkeypatch):
    _set(monkeypatch, {"git": "gated"})
    ev = await asyncio.wait_for(_decide("commit", answer="reject"), 5)
    assert [e for e in ev if e["type"] == "consent_opened"]
    assert _decision(ev)["approved"] is False


@pytest.mark.asyncio
async def test_gated_does_not_prompt_for_read_only_ops(monkeypatch):
    _set(monkeypatch, {"git": "gated"})
    ev = await _decide("status")
    assert not [e for e in ev if e["type"] == "consent_opened"]
    assert _decision(ev)["approved"] is True


@pytest.mark.asyncio
async def test_gated_overrides_a_global_always(monkeypatch):
    monkeypatch.setattr(cg, "_consent_config",
                        lambda: {"tools": {"git": {"default": "always"}}})
    _set(monkeypatch, {"git": "gated"})
    ev = await asyncio.wait_for(_decide("push", answer="approve"), 5)
    assert [e for e in ev if e["type"] == "consent_opened"], "project is more specific"


@pytest.mark.asyncio
async def test_disabled_refuses_every_op_including_status(monkeypatch):
    _set(monkeypatch, {"git": "disabled"})
    for op in ("status", "commit"):
        d = _decision(await _decide(op))
        assert d["approved"] is False and "disabled for this project" in d["answer"]["answer"]


@pytest.mark.asyncio
async def test_other_projects_inherit(monkeypatch):
    _set(monkeypatch, {"git": "autonomous"})
    ev = await asyncio.wait_for(_decide("commit", project_root="/proj/b", answer="reject"), 5)
    assert [e for e in ev if e["type"] == "consent_opened"], "declared ask applies elsewhere"


# ── advertising ───────────────────────────────────────────────────────────

class _Wrapped:
    def __init__(self, inst):
        self.tool_instance = inst
        self.name = inst.name


def test_gated_adds_git_when_the_category_is_off(monkeypatch):
    _set(monkeypatch, {"git": "gated"})
    tools, added, removed = ptm.apply_to_tools([_Other()], ROOT, wrap=_Wrapped)
    assert added == ["git"] and removed == []
    git = [t for t in tools if t.name == "git"][0]
    assert getattr(git.tool_instance, "consent_default", None) == "ask"


def test_disabled_removes_an_advertised_tool(monkeypatch):
    _set(monkeypatch, {"git": "disabled"})
    tools, added, removed = ptm.apply_to_tools([_Other(), _Wrapped(_Git())], ROOT, wrap=_Wrapped)
    assert [t.name for t in tools] == ["file_list"] and removed == ["git"]


def test_already_present_is_not_duplicated(monkeypatch):
    _set(monkeypatch, {"git": "autonomous"})
    tools, added, _ = ptm.apply_to_tools([_Wrapped(_Git())], ROOT, wrap=_Wrapped)
    assert added == [] and [t.name for t in tools] == ["git"]


def test_no_modes_is_a_no_op(monkeypatch):
    _set(monkeypatch, {})
    src = [_Other()]
    assert ptm.apply_to_tools(src, ROOT, wrap=_Wrapped) == (src, [], [])


# ── settings seam: the real model + storage round trip ────────────────────

def test_tool_modes_round_trip_through_project_storage(tmp_path, monkeypatch):
    from app.models.project import ProjectCreate, ProjectSettings, ProjectUpdate
    from app.storage.projects import ProjectStorage
    assert "toolModes" in ProjectSettings.model_fields, "ProjectSettings.toolModes missing"
    monkeypatch.setattr("app.utils.paths.get_ziya_home", lambda: tmp_path)
    storage = ProjectStorage(tmp_path)
    root = str(tmp_path / "work")
    (tmp_path / "work").mkdir()
    p = storage.create(ProjectCreate(name="w", path=root))
    storage.update(p.id, ProjectUpdate(settings=ProjectSettings(toolModes={"git": "autonomous"})))
    # A later save of a different setting must not wipe toolModes.
    storage.update(p.id, ProjectUpdate(settings=ProjectSettings(externalPaths=["/x"])))
    ptm.invalidate()
    assert ptm.mode_for("git", root) == "autonomous"


# ── seam: the real tool loader (_load_and_prepare_tools) ──────────────────

class TestLoaderSeam:
    """The project mode must bite where the tool list is BUILT."""

    def _executor(self):
        from app.streaming_tool_executor import StreamingToolExecutor
        return StreamingToolExecutor.__new__(StreamingToolExecutor)

    async def _load(self, registered, project_root):
        from unittest.mock import MagicMock, patch
        mgr = MagicMock()
        mgr.is_initialized = True
        with patch("app.mcp.manager.get_mcp_manager", return_value=mgr), \
             patch("app.mcp.enhanced_tools.create_secure_mcp_tools", return_value=registered):
            all_tools, bedrock_tools, *_ = await self._executor()._load_and_prepare_tools(
                project_root=project_root)
        return {t.name for t in all_tools}, {b["name"] for b in bedrock_tools}

    @pytest.mark.asyncio
    async def test_gated_project_is_offered_git_with_the_category_off(self, monkeypatch):
        from app.mcp.enhanced_tools import DirectMCPTool
        from app.mcp.tools.fileio import FileListTool
        _set(monkeypatch, {"git": "gated"})
        names, payload = await self._load([DirectMCPTool(FileListTool())], ROOT)
        assert "git" in names and any(n.endswith("git") for n in payload)
        # Positive control: another project gets the unchanged list.
        names_b, _ = await self._load([DirectMCPTool(FileListTool())], "/proj/b")
        assert "git" not in names_b and "file_list" in names_b

    @pytest.mark.asyncio
    async def test_disabled_project_drops_git_even_when_registered(self, monkeypatch):
        from app.mcp.enhanced_tools import DirectMCPTool
        from app.mcp.tools.fileio import FileListTool
        from app.mcp.tools.git_tool import GitTool
        _set(monkeypatch, {"git": "disabled"})
        names, payload = await self._load(
            [DirectMCPTool(FileListTool()), DirectMCPTool(GitTool())], ROOT)
        assert "git" not in names and not any(n.endswith("git") for n in payload)
        assert "file_list" in names
