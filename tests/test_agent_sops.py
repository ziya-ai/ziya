"""
Agent SOPs (``*.sop.md``, https://github.com/strands-agents/agent-sop).

  * app/utils/agent_sops.py on real directories: which files count, which
    root wins, how a description is taken.
  * Seams: skill discovery lists a SOP as kind ``sop`` with its body; a
    plugin's roots reach discovery through app.plugins.
The agent overlay's use of a SOP is in tests/test_agent_overlay.py.
"""
from __future__ import annotations

from pathlib import Path

import pytest

import app.plugins as plugins
from app.services import skill_discovery
from app.utils import agent_sops as sops

SOP = """# Handover

## Overview

Hands a shift over to the next engineer.
Second line of the same paragraph.

Another paragraph.

## Parameters

- **shift** (required): which shift

## Steps

### 1. Collect

**Constraints:**
- You MUST list open tickets
"""


class _Tokens:
    def count_tokens(self, text):
        return len(text.split())


def _sop(root: Path, rel: str, text: str = SOP) -> Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


@pytest.fixture
def dirs(tmp_path, monkeypatch):
    home, project, extra = tmp_path / "home", tmp_path / "project", tmp_path / "extra"
    for d in (home, project, extra):
        d.mkdir()
    monkeypatch.setenv("ZIYA_HOME", str(home))
    monkeypatch.delenv(sops.SOP_PATHS_ENV, raising=False)
    saved = list(getattr(plugins, "_sop_root_providers", []))
    yield home, project, extra
    if hasattr(plugins, "_sop_root_providers"):
        plugins._sop_root_providers[:] = saved


def test_only_sop_md_files_with_a_plain_name_count(dirs):
    home, _project, _extra = dirs
    root = home / "sops"
    _sop(root, "handover.sop.md")
    _sop(root, "team/deep/nested.sop.md")                # grouping folders are searched
    _sop(root, "a/b/c/too-deep.sop.md")                  # below MAX_SOP_DEPTH
    _sop(root, "notes.md")
    _sop(root, "has space.sop.md")
    _sop(root, ".hidden/secret.sop.md")
    _sop(root, "scripts/run.sh", "echo")
    assert sorted(sops.find_sops()) == ["handover", "nested"]
    assert sops.sop_name(Path("x/Code_Assist.v2.sop.md")) == "Code_Assist.v2"
    assert sops.sop_name(Path("x/SKILL.md")) is None


def test_the_first_root_holding_a_name_wins(dirs, monkeypatch):
    home, project, extra = dirs
    _sop(home / "sops", "handover.sop.md", "# user copy\n")
    _sop(home / "sops", "only-user.sop.md")
    assert sops.read_sop("handover")[1] == "# user copy\n"

    _sop(project / ".ziya" / "sops", "handover.sop.md", "# project copy\n")
    assert sops.read_sop("handover", project_root=str(project))[1] == "# project copy\n"
    assert sops.read_sop("handover")[1] == "# user copy\n"          # no project: its root is not read

    # ZIYA_SOP_PATHS has the form of --sop-paths: several directories, the
    # first listed wins, ~ and project-relative paths are accepted.
    _sop(extra, "handover.sop.md", "# env copy\n")
    _sop(project / "rel", "handover.sop.md", "# relative copy\n")
    monkeypatch.setenv(sops.SOP_PATHS_ENV, f"{extra}:rel:/does/not/exist")
    assert sops.read_sop("handover", project_root=str(project))[1] == "# env copy\n"
    monkeypatch.setenv(sops.SOP_PATHS_ENV, f"rel:{extra}")
    assert sops.read_sop("handover", project_root=str(project))[1] == "# relative copy\n"
    assert set(sops.find_sops(str(project))) == {"handover", "only-user"}
    assert sops.read_sop("absent") is None


def test_an_agent_gets_its_own_packages_copy(dirs, monkeypatch):
    _home, _project, extra = dirs
    a, b = extra / "PkgA" / "agent-sops", extra / "PkgB" / "agent-sops"
    _sop(a, "resolver.sop.md", "# A\n")
    _sop(b, "resolver.sop.md", "# B\n")
    monkeypatch.setenv(sops.SOP_PATHS_ENV, f"{a}:{b}")
    assert sops.read_sop("resolver")[1] == "# A\n"                              # first root
    assert sops.read_sop("resolver", prefer_under=str(extra / "PkgB"))[1] == "# B\n"
    assert sops.read_sop("resolver", prefer_under=str(extra / "PkgC"))[1] == "# A\n"   # no such copy: unchanged


def test_the_description_is_the_overviews_first_paragraph():
    assert sops.describe(SOP) == ("Hands a shift over to the next engineer. "
                                  "Second line of the same paragraph.")
    assert sops.describe("# Title\n\nJust prose.\n\n## Steps\n") == "Just prose."
    assert sops.describe("# Title\n\n## Overview\n\n## Steps\n\ntext\n") == ""   # an empty Overview borrows nothing


def test_discovery_lists_a_sop_as_its_own_kind_with_the_body_verbatim(dirs):
    home, project, _extra = dirs
    _sop(home / "sops", "handover.sop.md")
    found = {s.name: s for s in skill_discovery.discover_all_skills(str(project), _Tokens())}
    s = found["handover"]
    assert s.kind == "sop" and s.source == "user" and s.visibility == "user_selectable"
    assert s.prompt == SOP                                                      # constraints reach the model unedited
    assert s.description.startswith("Hands a shift over")
    assert s.skillPath.endswith("handover.sop.md") and s.tokenCount > 0
    light = {x.name: x for x in skill_discovery.discover_all_skills(
        str(project), _Tokens(), load_body=False)}["handover"]
    assert light.prompt == "" and light.id == s.id and light.tokenCount == s.tokenCount


def test_a_skill_of_the_same_name_is_the_one_listed(dirs):
    """The Bench, the catalog and get_skill_details are keyed by name, so a
    SOP that shares a skill's name is not listed beside it.  It is still
    found by name for an agent (read_sop)."""
    home, project, _extra = dirs
    _sop(home / "sops", "handover.sop.md")
    skill = home / "skills" / "handover"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\nname: handover\ndescription: the skill\n---\nBody.\n")
    listed = [s for s in skill_discovery.discover_all_skills(str(project), _Tokens())
              if s.name == "handover"]
    assert [s.kind for s in listed] == ["skill"]
    assert sops.read_sop("handover")[1] == SOP


def test_a_plugins_roots_reach_discovery(dirs):
    _home, project, extra = dirs
    _sop(extra / "pkg" / "agent-sops", "team/packaged.sop.md")

    class _Roots:
        provider_id = "test-sop-roots"

        def roots(self):
            return [extra / "pkg" / "agent-sops", (extra / "missing", False)]

    class _Boom:
        def roots(self):
            raise RuntimeError("boom")

    plugins.register_sop_root_provider(_Boom())                                 # a failing provider hides nothing
    plugins.register_sop_root_provider(_Roots())
    assert "packaged" in sops.find_sops()
    names = {s.name for s in skill_discovery.discover_all_skills(str(project), _Tokens())
             if s.kind == "sop"}
    assert names == {"packaged"}
