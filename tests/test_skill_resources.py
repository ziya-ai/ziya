"""
Bundled skill resources (scripts/, references/, assets/) per agentskills.io.

The integration guide's "Structured wrapping" / "Listing bundled resources"
sections define what the model must receive when a file-backed skill is
activated: the absolute skill directory, a statement that relative paths
resolve against it, and a <skill_resources> list of bundled files that are
enumerated but not read.  "Permission allowlisting" asks that skill
directories be readable without a permission failure.

Before this change every activation path sent only "[Skill: name]\\n{body}",
so "run scripts/helper.py" in a SKILL.md had no anchor, and a user-level
skill's references/ were unreadable through file_read.

Layout:
  * unit tests pin the manifest format against the spec example;
  * seam tests drive each activation path (get_skill_details, bench
    "always" placement, task cards, SkillStorage.get -> the field the
    client prompt is built from) and assert on what the model is sent;
  * one test runs the script at the disclosed path through the real shell
    policy, and one reads a user-level reference through the real
    file_read tool.
"""
from __future__ import annotations

import asyncio
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from app.utils.skill_resources import (
    MAX_LISTED_FILES,
    list_skill_resources,
    skill_activation_body,
    skill_resource_manifest,
)

NAME = "csv-helper"
BODY = textwrap.dedent(f"""\
    ---
    name: {NAME}
    description: Summarise CSV files with the bundled helper script.
    keywords: csv summarise
    visibility: model_discoverable
    ---

    # CSV helper

    Run `scripts/summarise.py <file>` and report its output.
    """)
SCRIPT = 'import sys\nprint("SUMMARY-OK", *sys.argv[1:])\n'


def _make_skill(root: Path, name: str = NAME, *, bundled: bool = True) -> Path:
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(BODY.replace(NAME, name), encoding="utf-8")
    if bundled:
        (d / "scripts").mkdir()
        (d / "scripts" / "summarise.py").write_text(SCRIPT, encoding="utf-8")
        (d / "scripts" / "__pycache__").mkdir()
        (d / "scripts" / "__pycache__" / "summarise.cpython-312.pyc").write_bytes(b"x")
        (d / "references").mkdir()
        (d / "references" / "REFERENCE.md").write_text("# Ref\n", encoding="utf-8")
        (d / "assets").mkdir()
        (d / "assets" / "template.csv").write_text("a,b\n", encoding="utf-8")
        (d / ".hidden").write_text("x", encoding="utf-8")
    return d


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    """No real ~/.ziya/skills or ~/.agents/skills may leak into discovery."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("ZIYA_HOME", str(home / ".ziya"))
    return home


@pytest.fixture
def project(tmp_path, isolated_home, monkeypatch):
    ws = tmp_path / "ws"
    ws.mkdir()
    monkeypatch.setenv("ZIYA_USER_CODEBASE_DIR", str(ws))
    return ws


def _manifest_dir(text: str) -> Path:
    line = next(l for l in text.splitlines() if l.startswith("Skill directory: "))
    return Path(line[len("Skill directory: "):])


# ---------------------------------------------------------------------------
# Format (spec example)
# ---------------------------------------------------------------------------

class TestManifestFormat:

    def test_matches_spec_shape(self, tmp_path):
        d = _make_skill(tmp_path / ".skills")
        assert skill_resource_manifest(str(d)) == "\n".join([
            f"Skill directory: {d}",
            "Relative paths in this skill are relative to the skill directory.",
            "",
            "<skill_resources>",
            "  <file>assets/template.csv</file>",
            "  <file>references/REFERENCE.md</file>",
            "  <file>scripts/summarise.py</file>",
            "</skill_resources>",
        ])

    def test_enumerates_without_reading(self, tmp_path):
        d = _make_skill(tmp_path)
        assert "SUMMARY-OK" not in skill_resource_manifest(str(d))

    def test_excludes_skill_md_hidden_and_caches(self, tmp_path):
        files, omitted = list_skill_resources(_make_skill(tmp_path))
        assert "SKILL.md" not in files and ".hidden" not in files
        assert not any("__pycache__" in f or f.endswith(".pyc") for f in files)
        assert omitted == 0

    def test_skill_without_bundle_has_directory_but_no_list(self, tmp_path):
        d = _make_skill(tmp_path, bundled=False)
        m = skill_resource_manifest(str(d))
        assert m.startswith(f"Skill directory: {d}\n")
        assert "<skill_resources>" not in m

    def test_large_listing_is_capped_and_says_so(self, tmp_path):
        d = _make_skill(tmp_path, bundled=False)
        (d / "scripts").mkdir()
        for i in range(MAX_LISTED_FILES + 7):
            (d / "scripts" / f"s{i:03d}.py").write_text("", encoding="utf-8")
        m = skill_resource_manifest(str(d))
        assert m.count("<file>") == MAX_LISTED_FILES
        assert "<!-- 7 more files not listed -->" in m

    def test_file_names_are_xml_escaped(self, tmp_path):
        d = _make_skill(tmp_path, bundled=False)
        (d / "references").mkdir()
        (d / "references" / "a&b<c>.md").write_text("", encoding="utf-8")
        assert "<file>references/a&amp;b&lt;c&gt;.md</file>" in skill_resource_manifest(str(d))

    def test_relative_skill_path_is_made_absolute(self, tmp_path, monkeypatch):
        _make_skill(tmp_path / "root")
        monkeypatch.chdir(tmp_path)
        assert _manifest_dir(skill_resource_manifest(f"root/{NAME}")).is_absolute()


class TestActivationBody:

    class _S:
        def __init__(self, prompt, skillPath=None):
            self.prompt, self.skillPath = prompt, skillPath

    def test_appends_manifest_after_body(self, tmp_path):
        d = _make_skill(tmp_path)
        out = skill_activation_body(self._S("Do the thing.\n", str(d)))
        assert out.startswith("Do the thing.\n\nSkill directory: ")
        assert "<file>scripts/summarise.py</file>" in out

    def test_builtin_skill_unchanged(self):
        assert skill_activation_body(self._S("body")) == "body"

    def test_missing_directory_unchanged(self, tmp_path):
        assert skill_activation_body(self._S("body", str(tmp_path / "gone"))) == "body"

    def test_empty_body_stays_empty(self, tmp_path):
        # Callers read "" as "body not loaded"; a bare manifest must not
        # turn that into a non-empty skill with no instructions.
        assert skill_activation_body(self._S("", str(_make_skill(tmp_path)))) == ""


# ---------------------------------------------------------------------------
# Seams: every path that hands a skill to the model
# ---------------------------------------------------------------------------

def _run_get_skill_details(name):
    from app.mcp.tools.skill_tools import GetSkillDetailsTool
    return asyncio.run(GetSkillDetailsTool().execute(skill_name=name))


class TestGetSkillDetails:

    def test_project_dot_skills_root(self, project):
        d = _make_skill(project / ".skills")
        out = _run_get_skill_details(NAME)
        assert not out.get("error"), out
        content = out["content"]
        assert content.startswith(f"[Skill: {NAME}]")  # capability_events key
        assert "Run `scripts/summarise.py <file>`" in content
        assert f"Skill directory: {d}" in content
        assert "<file>scripts/summarise.py</file>" in content

    def test_user_level_skill_gets_its_own_directory(self, project, isolated_home):
        d = _make_skill(isolated_home / ".ziya" / "skills")
        content = _run_get_skill_details(NAME)["content"]
        assert _manifest_dir(content) == d

    def test_builtin_skill_has_no_manifest(self, project):
        content = _run_get_skill_details("code_review")["content"]
        assert "Skill directory:" not in content and "<skill_resources>" not in content


class TestTaskCardSkills:

    def test_task_card_prompt_carries_manifest(self, project, tmp_path, monkeypatch):
        from app.agents import task_executor as te
        d = _make_skill(project / ".agents" / "skills")
        meta = tmp_path / "_meta"
        meta.mkdir()
        monkeypatch.setattr("app.utils.paths.get_project_dir", lambda pid: meta)
        prompts, warnings = te._load_skill_prompts("p", [NAME], project_root=str(project))
        assert warnings == []
        assert prompts[0].startswith(f"[Active Skill: {NAME}]\n")
        assert f"Skill directory: {d}" in prompts[0]
        assert "<file>scripts/summarise.py</file>" in prompts[0]


class TestBenchAlwaysPlacement:

    def test_pinned_file_skill_carries_manifest(self, project, tmp_path, monkeypatch):
        from app.services import bench as svc
        from app.services.token_service import TokenService
        from app.storage.skills import SkillStorage
        d = _make_skill(project / ".skills")
        storage = SkillStorage(tmp_path / "projects" / "p1", TokenService(),
                               workspace_path=str(project))
        monkeypatch.setattr(svc, "_skill_storage", lambda pid: storage)
        monkeypatch.setattr(svc, "_mcp_items", lambda: [])
        import app.mcp.builtin_tools as bt
        monkeypatch.setattr(bt, "is_builtin_category_enabled", lambda c: True)

        svc.place("p1", None, f"skill:{NAME}", "project", "always")
        pi = svc.prompt_inputs("p1", None)
        assert NAME in pi.always_names
        assert f"[Active Skill: {NAME}]" in pi.system_prompt_addition
        assert f"Skill directory: {d}" in pi.system_prompt_addition
        assert "<file>scripts/summarise.py</file>" in pi.system_prompt_addition


class TestClientHydrationField:
    """The client assembles "[Active Skill]" prompts from GET /skills/{id};
    the manifest must ride on that record."""

    def test_storage_get_populates_manifest(self, project, tmp_path):
        from app.services.token_service import TokenService
        from app.storage.skills import SkillStorage
        d = _make_skill(project / ".skills")
        storage = SkillStorage(tmp_path / "meta", TokenService(), workspace_path=str(project))
        listed = next(s for s in storage.list() if s.name == NAME)
        # list() is the cheap catalog: no body, so no manifest either.
        assert not listed.resourceManifest
        full = storage.get(listed.id)
        assert full.resourceManifest == skill_resource_manifest(str(d))
        assert "resourceManifest" in full.model_dump()


# ---------------------------------------------------------------------------
# The disclosed paths actually work
# ---------------------------------------------------------------------------

class TestDisclosedPathsAreUsable:

    def test_listed_script_runs_through_shell_policy(self, project):
        _make_skill(project / ".skills")
        content = _run_get_skill_details(NAME)["content"]
        script = _manifest_dir(content) / "scripts" / "summarise.py"
        assert "<file>scripts/summarise.py</file>" in content
        cmd = f"python3 {script} data.csv"

        from app.mcp_servers import shell_server as ss
        cls = next(getattr(ss, n) for n in dir(ss)
                   if isinstance(getattr(ss, n), type)
                   and hasattr(getattr(ss, n), "is_command_allowed"))
        allowed, reason = cls().is_command_allowed(cmd)
        assert allowed, reason

        r = subprocess.run([sys.executable, str(script), "data.csv"],
                           capture_output=True, text=True, timeout=30)
        assert r.stdout.strip() == "SUMMARY-OK data.csv"

    def _file_read(self, path, workspace):
        from app.mcp.tools.fileio import FileReadTool
        return asyncio.run(FileReadTool().execute(path=path, _workspace_path=str(workspace)))

    def test_user_level_reference_is_readable_with_file_read(self, project, isolated_home):
        d = _make_skill(isolated_home / ".ziya" / "skills")
        out = self._file_read(str(d / "references" / "REFERENCE.md"), project)
        assert not out.get("error"), out
        assert out["content"] == "# Ref\n"

    def test_symlinked_user_skill_is_readable(self, project, isolated_home, tmp_path):
        # Installers such as `npx skills add -g` link each skill into
        # ~/.agents/skills from a store elsewhere.
        store = _make_skill(tmp_path / "store")
        root = isolated_home / ".agents" / "skills"
        root.mkdir(parents=True)
        (root / NAME).symlink_to(store, target_is_directory=True)
        out = self._file_read(str(root / NAME / "references" / "REFERENCE.md"), project)
        assert not out.get("error"), out
        assert out["content"] == "# Ref\n"

    def test_unrelated_home_file_is_still_rejected(self, project, isolated_home):
        # Positive control: the allowlist widens to skill roots only.
        secret = isolated_home / "notes.txt"
        secret.write_text("x", encoding="utf-8")
        _make_skill(isolated_home / ".ziya" / "skills")
        out = self._file_read(str(secret), project)
        assert out.get("error")
