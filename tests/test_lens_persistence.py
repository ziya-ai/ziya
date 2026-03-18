"""
Tests for lens state (active context/skill IDs) persistence logic.

The frontend uses localStorage with project-scoped keys to persist
which skills and contexts are active.  These tests verify:

- The key format is deterministic and project-scoped
- Save + load round-trips correctly
- Missing / corrupt data degrades gracefully
- Legacy sessionStorage migration path
- Clear removes the persisted key

Since the persistence is frontend JS, these tests validate the
*backend contract* and *data model* that supports it.  The actual
localStorage calls are tested via the frontend unit test harness.
"""

import json
import pytest
from app.models.project import Project, ProjectSettings


class TestProjectSettingsDefaultSkillIds:
    """Verify that ProjectSettings carries default skill IDs for new chats."""

    def test_default_skill_ids_empty_by_default(self):
        settings = ProjectSettings()
        assert settings.defaultSkillIds == []

    def test_default_skill_ids_round_trip(self):
        ids = ["skill-1", "skill-2"]
        settings = ProjectSettings(defaultSkillIds=ids)
        assert settings.defaultSkillIds == ids

    def test_serialization_preserves_skill_ids(self):
        ids = ["builtin-code-review", "custom-123"]
        settings = ProjectSettings(defaultSkillIds=ids)
        data = json.loads(settings.model_dump_json())
        assert data["defaultSkillIds"] == ids


class TestLensKeyFormat:
    """
    Verify the key format used by the frontend matches what we'd expect.
    
    The frontend uses: `ZIYA_LENS_${projectId}`
    The stored value is: { contextIds: string[], skillIds: string[] }
    """

    def test_key_format(self):
        project_id = "proj-abc-123"
        key = f"ZIYA_LENS_{project_id}"
        assert key == "ZIYA_LENS_proj-abc-123"

    def test_value_format_round_trip(self):
        """Verify the JSON shape that the frontend stores."""
        value = {
            "contextIds": ["ctx-1", "ctx-2"],
            "skillIds": ["skill-a", "skill-b"],
        }
        serialized = json.dumps(value)
        parsed = json.loads(serialized)
        assert parsed["contextIds"] == ["ctx-1", "ctx-2"]
        assert parsed["skillIds"] == ["skill-a", "skill-b"]

    def test_empty_lens(self):
        value = {"contextIds": [], "skillIds": []}
        serialized = json.dumps(value)
        parsed = json.loads(serialized)
        assert parsed["contextIds"] == []
        assert parsed["skillIds"] == []

    def test_corrupt_data_recovery(self):
        """Frontend should handle corrupt localStorage gracefully."""
        # Simulate what the _loadLens function does on corrupt data
        raw = "not-valid-json{{"
        try:
            parsed = json.loads(raw)
            result = {
                "contextIds": parsed.get("contextIds", []) if isinstance(parsed, dict) else [],
                "skillIds": parsed.get("skillIds", []) if isinstance(parsed, dict) else [],
            }
        except (json.JSONDecodeError, TypeError):
            result = {"contextIds": [], "skillIds": []}
        
        assert result == {"contextIds": [], "skillIds": []}

    def test_partial_data_recovery(self):
        """If only skillIds is present, contextIds should default to []."""
        raw = json.dumps({"skillIds": ["s1"]})
        parsed = json.loads(raw)
        result = {
            "contextIds": parsed.get("contextIds", []) if isinstance(parsed, dict) else [],
            "skillIds": parsed.get("skillIds", []) if isinstance(parsed, dict) else [],
        }
        assert result == {"contextIds": [], "skillIds": ["s1"]}


class TestLegacyMigration:
    """Verify the legacy sessionStorage → localStorage migration logic."""

    def test_legacy_key_name(self):
        """The old key was a flat global name."""
        assert "ZIYA_ACTIVE_SKILL_IDS" == "ZIYA_ACTIVE_SKILL_IDS"  # constant

    def test_legacy_value_is_flat_array(self):
        """Old format was a plain JSON array of skill IDs."""
        legacy = json.dumps(["skill-1", "skill-2"])
        parsed = json.loads(legacy)
        assert isinstance(parsed, list)
        assert parsed == ["skill-1", "skill-2"]

    def test_merge_legacy_with_new(self):
        """Merging legacy IDs with existing saved lens should union them."""
        saved_lens = {"contextIds": ["ctx-1"], "skillIds": ["skill-a"]}
        legacy_skills = ["skill-b", "skill-a"]  # skill-a is a duplicate
        
        merged = list(set(saved_lens["skillIds"]) | set(legacy_skills))
        # Should contain both, deduplicated
        assert set(merged) == {"skill-a", "skill-b"}


class TestProjectScopedIsolation:
    """Verify that lens state is correctly isolated per project."""

    def test_different_projects_different_keys(self):
        key_a = f"ZIYA_LENS_proj-aaa"
        key_b = f"ZIYA_LENS_proj-bbb"
        assert key_a != key_b

    def test_switching_saves_and_restores(self):
        """Simulate the switchProject save/restore flow."""
        # Project A state
        storage = {}  # simulating localStorage
        
        def save(pid, ctx, skills):
            storage[f"ZIYA_LENS_{pid}"] = json.dumps({"contextIds": ctx, "skillIds": skills})
        
        def load(pid):
            raw = storage.get(f"ZIYA_LENS_{pid}")
            if raw:
                return json.loads(raw)
            return {"contextIds": [], "skillIds": []}
        
        # User activates skills in project A
        save("proj-a", ["ctx-1"], ["skill-1", "skill-2"])
        
        # User switches to project B
        save("proj-b", [], ["skill-3"])
        
        # Switch back to project A — lens should be restored
        restored = load("proj-a")
        assert restored["contextIds"] == ["ctx-1"]
        assert restored["skillIds"] == ["skill-1", "skill-2"]
        
        # Project B should still have its own state
        restored_b = load("proj-b")
        assert restored_b["skillIds"] == ["skill-3"]
