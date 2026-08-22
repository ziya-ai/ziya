"""A built-in skill's tokenCount must follow its prompt.

``_ensure_built_in_skills`` re-syncs a persisted built-in when the shipped
definition changes, so editing a prompt in ``built_in_skills.py`` reaches
existing projects.  It updated ``prompt`` but not ``tokenCount``.

That number is rendered to the user (``SkillsSection.tsx``: "N tokens ·
source"), so a stale value misreports what enabling the skill costs.  The
error also grows in the worst direction: a prompt that was rewritten from
11 lines to 100 keeps advertising its original size, so the skill that
costs the most looks like the one that costs the least.

Both sibling paths already recompute — ``create`` at construction and
``update`` on a prompt change — which is what makes the omission a
straightforward oversight in the third path rather than a design choice.
"""

import time
from pathlib import Path

import pytest

from app.models.skill import Skill
from app.storage.skills import SkillStorage


class _CountingTokenService:
    """Deterministic stand-in: one token per whitespace-separated word.

    Real tokenization is irrelevant here — what matters is that the count
    is a FUNCTION of the prompt, so a stale count is detectable.  Counts
    calls too, so a test can assert the recompute actually happened rather
    than inferring it from a number that might coincide.
    """

    def __init__(self):
        self.calls = 0

    def count_tokens(self, text: str) -> int:
        self.calls += 1
        return len((text or "").split())


@pytest.fixture
def project_dir(tmp_path: Path) -> Path:
    d = tmp_path / "proj"
    d.mkdir()
    return d


def _seed_stale_builtin(
    project_dir: Path, name: str, prompt: str, token_count: int,
) -> str:
    """Write a persisted built-in record as an older Ziya would have.

    Mirrors the canonical id scheme in ``_ensure_built_in_skills`` so the
    sync path treats this as an EXISTING built-in to update, not as a
    missing one to create — the create path already recomputes, so seeding
    it wrongly would test the wrong branch.
    """
    skills_dir = project_dir / "skills"
    skills_dir.mkdir(exist_ok=True)
    canonical_id = f"builtin-{name.lower().replace(' ', '-')}"
    now = int(time.time() * 1000)
    skill = Skill(
        id=canonical_id,
        name=name,
        description="stale description",
        prompt=prompt,
        source="builtin",
        color="#65a30d",
        tokenCount=token_count,
        isBuiltIn=True,
        createdAt=now,
        lastUsedAt=now,
    )
    (skills_dir / f"{canonical_id}.json").write_text(skill.model_dump_json())
    return canonical_id


class TestTokenCountFollowsPrompt:

    def test_recomputed_when_the_shipped_prompt_changes(self, project_dir):
        """The regression: prompt updated, count left behind."""
        from app.data.built_in_skills import BUILT_IN_SKILLS

        target = next(
            s for s in BUILT_IN_SKILLS if s["id"] == "test_everything"
        )
        # Seed with a DIFFERENT prompt and a count that matches that old
        # prompt, which is exactly the state a project is in after the
        # shipped definition is edited.
        stale_prompt = "short old prompt"
        cid = _seed_stale_builtin(
            project_dir, target["name"], stale_prompt,
            token_count=len(stale_prompt.split()),
        )

        svc = _CountingTokenService()
        storage = SkillStorage(project_dir, svc)  # runs the sync
        got = storage.get(cid)

        assert got is not None
        # Prompt synced (pre-existing behaviour, asserted so a regression
        # here is not mistaken for the count bug).
        assert got.prompt == target["prompt"]
        # And the count followed it.
        assert got.tokenCount == len(target["prompt"].split()), (
            f"tokenCount {got.tokenCount} does not match the synced prompt "
            f"({len(target['prompt'].split())} words) — a user reading the "
            f"skills panel is told the wrong context cost"
        )

    def test_the_stale_value_is_not_merely_coincidental(self, project_dir):
        """Guards the test itself.

        If the seeded count happened to equal the correct one, the test
        above would pass without the fix.  Seeding an obviously-wrong 1
        makes that impossible.
        """
        from app.data.built_in_skills import BUILT_IN_SKILLS

        target = next(
            s for s in BUILT_IN_SKILLS if s["id"] == "test_everything"
        )
        cid = _seed_stale_builtin(
            project_dir, target["name"], "x", token_count=1,
        )
        storage = SkillStorage(project_dir, _CountingTokenService())
        got = storage.get(cid)
        assert got is not None
        assert got.tokenCount != 1, (
            "tokenCount is still the seeded placeholder, so the sync path "
            "did not recompute it"
        )
        assert got.tokenCount > 1

    def test_no_rewrite_when_nothing_changed(self, project_dir):
        """The recompute must not make every load dirty.

        ``dirty`` gates a disk write per skill per project on every
        SkillStorage construction.  Recomputing unconditionally — rather
        than inside the prompt-changed branch — would mark a clean record
        dirty and turn a no-op startup into a write burst across every
        built-in in every project.
        """
        from app.data.built_in_skills import BUILT_IN_SKILLS

        target = next(
            s for s in BUILT_IN_SKILLS if s["id"] == "test_everything"
        )
        svc = _CountingTokenService()
        correct = len(target["prompt"].split())
        cid = _seed_stale_builtin(
            project_dir, target["name"], target["prompt"], correct,
        )
        # Match the shipped record fully so no field is dirty.
        path = project_dir / "skills" / f"{cid}.json"
        import json
        data = json.loads(path.read_text())
        data["description"] = target["description"]
        data["keywords"] = target.get("keywords")
        data["visibility"] = target.get("visibility")
        path.write_text(json.dumps(data))
        before = path.stat().st_mtime_ns

        time.sleep(0.01)
        SkillStorage(project_dir, svc)

        assert path.stat().st_mtime_ns == before, (
            "an unchanged built-in was rewritten, so the recompute is "
            "outside the prompt-changed branch and every startup now "
            "writes every built-in in every project"
        )


class TestSiblingPathsAlreadyAgree:
    """The two paths that were already correct, pinned so the three cannot
    drift apart again — the drift is what produced the bug."""

    def test_create_path_computes_from_the_prompt(self, project_dir):
        from app.data.built_in_skills import BUILT_IN_SKILLS

        target = next(
            s for s in BUILT_IN_SKILLS if s["id"] == "test_everything"
        )
        storage = SkillStorage(project_dir, _CountingTokenService())
        cid = f"builtin-{target['name'].lower().replace(' ', '-')}"
        got = storage.get(cid)
        assert got is not None
        assert got.tokenCount == len(target["prompt"].split())

    def test_update_path_computes_from_the_prompt(self, project_dir):
        from app.models.skill import SkillCreate, SkillUpdate

        storage = SkillStorage(project_dir, _CountingTokenService())
        made = storage.create(SkillCreate(
            name="custom", description="d", prompt="one two three",
        ))
        assert made.tokenCount == 3
        edited = storage.update(
            made.id, SkillUpdate(prompt="one two three four five"),
        )
        assert edited is not None
        assert edited.tokenCount == 5
