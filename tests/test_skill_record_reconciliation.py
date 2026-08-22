"""
Regression tests for duplicate and orphaned built-in skill records.

Two defects let junk accumulate in a project's skills directory, both
observed live in ~/.ziya (10 projects with a duplicate "Tests for
everything", 8 with an orphaned "Task Decomposition"):

1. **Adoption never fired.** ``_ensure_built_in_skills`` indexed records by
   EXACT name and only attempted adoption when the canonical built-in was
   ABSENT.  A real user record named "Tests for everything" (lowercase e)
   therefore survived forever beside the built-in "Tests for Everything" —
   two indistinguishable cards, only one of which a project template can
   seed.  Either half of the bug alone is enough to break it: even with
   case-insensitive matching, the ``existing is None`` gate skips adoption
   once the built-in exists, which is the state of every real project.

2. **Renaming a shipped skill orphans its file.** The on-disk id is derived
   from the name, so "Task Decomposition" -> "Task Decomposition,
   Delegation & Swarm" wrote a new file and left the old one listing as a
   built-in that no longer exists.

These drive the real SkillStorage against a scratch ZIYA_HOME so the
id-derivation and file-layout behaviour is exercised rather than restated.
"""

import json

import pytest

from app.data.built_in_skills import BUILT_IN_SKILLS
from app.services.token_service import TokenService
from app.storage.skills import SkillStorage


# The promoted skill whose real-world duplicate motivated this.  Asserted to
# still be shipped so a future rename fails loudly here instead of quietly
# turning these tests into no-ops.
PROMOTED_NAME = "Tests for Everything"

# A name that is deliberately NOT in BUILT_IN_SKILLS, standing in for a
# built-in that has been renamed away.
RETIRED_NAME = "Task Decomposition"
RETIRED_ID = "builtin-task-decomposition"


def _canonical_id(name: str) -> str:
    """Mirror of SkillStorage's own derivation."""
    return f"builtin-{name.lower().replace(' ', '-')}"


@pytest.fixture
def project_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("ZIYA_HOME", str(tmp_path / "home"))
    d = tmp_path / "home" / "projects" / "p1"
    (d / "skills").mkdir(parents=True)
    return d


def _write_skill(project_dir, skill_id, **overrides):
    record = {
        "id": skill_id,
        "name": PROMOTED_NAME,
        "description": "user's own version",
        "prompt": "old user prompt",
        "color": "#123456",
        "tokenCount": 3,
        "isBuiltIn": False,
        "source": "custom",
        "createdAt": 1,
        "lastUsedAt": 1,
    }
    record.update(overrides)
    (project_dir / "skills" / f"{skill_id}.json").write_text(
        json.dumps(record), encoding="utf-8")
    return record


def _storage(project_dir):
    return SkillStorage(project_dir, TokenService())


def _names(skills):
    return [s.name for s in skills]


class TestFixturesStillMatchShippedSkills:
    """Guard the assumptions the rest of the file rests on."""

    def test_promoted_name_is_shipped(self):
        assert any(b["name"] == PROMOTED_NAME for b in BUILT_IN_SKILLS)

    def test_retired_name_is_not_shipped(self):
        assert not any(b["name"] == RETIRED_NAME for b in BUILT_IN_SKILLS)


class TestCaseInsensitiveAdoption:
    def test_case_mismatched_custom_copy_is_deleted(self, project_dir):
        """Defect 1a: exact-name matching missed a one-character case diff."""
        _write_skill(project_dir, "dup-1", name="Tests for everything")
        _storage(project_dir)
        assert not (project_dir / "skills" / "dup-1.json").exists()

    def test_whitespace_padded_copy_is_deleted(self, project_dir):
        _write_skill(project_dir, "dup-2", name=f"  {PROMOTED_NAME} ")
        _storage(project_dir)
        assert not (project_dir / "skills" / "dup-2.json").exists()

    def test_adoption_fires_when_builtin_already_exists(self, project_dir):
        """Defect 1b: the ``existing is None`` gate made adoption dead code.

        This is the live situation — the built-in was created long before the
        duplicate was noticed, so adoption must work on an already-seeded
        project, not only on a fresh one.
        """
        _storage(project_dir)  # seeds the canonical built-in
        assert (project_dir / "skills" / f"{_canonical_id(PROMOTED_NAME)}.json").exists()

        _write_skill(project_dir, "dup-3", name="Tests for everything")
        _storage(project_dir)
        assert not (project_dir / "skills" / "dup-3.json").exists()

    def test_exactly_one_record_carries_the_name(self, project_dir):
        """The user-visible symptom: two cards with one name."""
        _storage(project_dir)
        _write_skill(project_dir, "dup-4", name="Tests for everything")
        skills = _storage(project_dir).list()

        matching = [s for s in skills if s.name.strip().lower() == PROMOTED_NAME.lower()]
        assert len(matching) == 1, _names(skills)
        assert matching[0].id == _canonical_id(PROMOTED_NAME)
        assert matching[0].isBuiltIn is True

    def test_surviving_record_carries_the_builtin_prompt(self, project_dir):
        """Built-in wins: the diverged user wording must not persist."""
        _write_skill(project_dir, "dup-5", name="Tests for everything",
                     prompt="old user prompt")
        skills = _storage(project_dir).list()
        adopted = next(s for s in skills if s.name == PROMOTED_NAME)
        shipped = next(b for b in BUILT_IN_SKILLS if b["name"] == PROMOTED_NAME)
        assert adopted.prompt == shipped["prompt"]
        assert adopted.prompt != "old user prompt"

    def test_token_count_reflects_the_adopted_prompt(self, project_dir):
        """A stale tokenCount misreports what enabling the skill costs."""
        _write_skill(project_dir, "dup-6", name="Tests for everything",
                     tokenCount=3)
        skills = _storage(project_dir).list()
        adopted = next(s for s in skills if s.name == PROMOTED_NAME)
        assert adopted.tokenCount > 3

    def test_adoption_is_idempotent(self, project_dir):
        _write_skill(project_dir, "dup-7", name="Tests for everything")
        first = _names(_storage(project_dir).list())
        second = _names(_storage(project_dir).list())
        assert sorted(first) == sorted(second)
        assert len(first) == len(set(first))


class TestAdoptionRestraint:
    def test_unrelated_custom_skill_survives(self, project_dir):
        _write_skill(project_dir, "keep-me", name="My Own Thing")
        skills = _storage(project_dir).list()
        assert (project_dir / "skills" / "keep-me.json").exists()
        assert "My Own Thing" in _names(skills)

    def test_file_backed_skill_is_never_deleted(self, project_dir):
        """A SKILL.md on disk outranks a shipped default, case notwithstanding.

        Case-insensitive matching widened the net, so the project/user
        exemption has to hold under the looser comparison too.
        """
        _write_skill(project_dir, "proj-1", name="tests for everything",
                     source="project")
        _storage(project_dir)
        assert (project_dir / "skills" / "proj-1.json").exists()

    def test_user_sourced_skill_is_never_deleted(self, project_dir):
        _write_skill(project_dir, "user-1", name="TESTS FOR EVERYTHING",
                     source="user")
        _storage(project_dir)
        assert (project_dir / "skills" / "user-1.json").exists()


class TestOrphanedBuiltInCleanup:
    def test_renamed_builtin_record_is_removed(self, project_dir):
        """Defect 2: create/update with no delete leaves a phantom built-in."""
        _write_skill(project_dir, RETIRED_ID, name=RETIRED_NAME,
                     isBuiltIn=True, source="builtin")
        skills = _storage(project_dir).list()
        assert not (project_dir / "skills" / f"{RETIRED_ID}.json").exists()
        assert RETIRED_NAME not in _names(skills)

    def test_currently_shipped_builtins_are_retained(self, project_dir):
        """The GC must not mistake a live built-in for an orphan."""
        _storage(project_dir)
        skills = _storage(project_dir).list()
        shipped = {b["name"] for b in BUILT_IN_SKILLS}
        assert shipped.issubset(set(_names(skills)))

    def test_orphan_removal_does_not_touch_custom_records(self, project_dir):
        """Only ``isBuiltIn`` records are eligible for name-based GC."""
        _write_skill(project_dir, "custom-orphan-name", name=RETIRED_NAME)
        _storage(project_dir)
        assert (project_dir / "skills" / "custom-orphan-name.json").exists()

    def test_cleanup_is_idempotent(self, project_dir):
        _write_skill(project_dir, RETIRED_ID, name=RETIRED_NAME,
                     isBuiltIn=True, source="builtin")
        first = _names(_storage(project_dir).list())
        second = _names(_storage(project_dir).list())
        assert sorted(first) == sorted(second)


class TestCombinedRealWorldState:
    def test_a_project_in_the_observed_broken_state_converges(self, project_dir):
        """Both defects present at once, as seen live in ~/.ziya."""
        _storage(project_dir)
        _write_skill(project_dir, "dup-live", name="Tests for everything")
        _write_skill(project_dir, RETIRED_ID, name=RETIRED_NAME,
                     isBuiltIn=True, source="builtin")

        skills = _storage(project_dir).list()
        names = _names(skills)

        assert len(names) == len(set(names)), names
        assert RETIRED_NAME not in names
        assert names.count(PROMOTED_NAME) == 1
        assert not (project_dir / "skills" / "dup-live.json").exists()
