"""
Tests for T39: Retroactive crystal review logic.
"""

import pytest
from app.agents.compaction_engine import CompactionEngine
from app.models.delegate import MemoryCrystal, FileChange


def _crystal(delegate_id: str, files: list[tuple[str, str]]) -> MemoryCrystal:
    return MemoryCrystal(
        delegate_id=delegate_id,
        task=f"Task for {delegate_id}",
        summary="test summary",
        files_changed=[
            FileChange(path=path, action=action, line_delta="+10/-5")
            for path, action in files
        ],
        decisions=["decision 1"],
        original_tokens=5000,
        crystal_tokens=300,
        created_at=1000.0,
    )


@pytest.fixture
def engine():
    return CompactionEngine()


class TestRetroactiveReview:

    @pytest.mark.asyncio
    async def test_no_downstream_preserved(self, engine):
        late = _crystal("a", [("src/a.py", "modified")])
        assert await engine.retroactive_review(late, []) == "preserved"

    @pytest.mark.asyncio
    async def test_no_file_overlap_preserved(self, engine):
        late = _crystal("a", [("src/a.py", "modified")])
        ds = [_crystal("b", [("src/b.py", "modified")])]
        assert await engine.retroactive_review(late, ds) == "preserved"

    @pytest.mark.asyncio
    async def test_no_files_changed_preserved(self, engine):
        late = _crystal("a", [])
        ds = [_crystal("b", [("src/b.py", "modified")])]
        assert await engine.retroactive_review(late, ds) == "preserved"

    @pytest.mark.asyncio
    async def test_both_modified_same_file_discarded(self, engine):
        late = _crystal("a", [("src/shared.py", "modified")])
        ds = [_crystal("b", [("src/shared.py", "modified")])]
        assert await engine.retroactive_review(late, ds) == "discarded"

    @pytest.mark.asyncio
    async def test_late_deletes_downstream_modifies_discarded(self, engine):
        late = _crystal("a", [("src/shared.py", "deleted")])
        ds = [_crystal("b", [("src/shared.py", "modified")])]
        assert await engine.retroactive_review(late, ds) == "discarded"

    @pytest.mark.asyncio
    async def test_downstream_deletes_late_modifies_discarded(self, engine):
        late = _crystal("a", [("src/shared.py", "modified")])
        ds = [_crystal("b", [("src/shared.py", "deleted")])]
        assert await engine.retroactive_review(late, ds) == "discarded"

    @pytest.mark.asyncio
    async def test_both_created_same_file_extended(self, engine):
        late = _crystal("a", [("src/utils.py", "created")])
        ds = [_crystal("b", [("src/utils.py", "created")])]
        assert await engine.retroactive_review(late, ds) == "extended"

    @pytest.mark.asyncio
    async def test_multiple_downstream_one_conflicts(self, engine):
        late = _crystal("a", [
            ("src/a.py", "modified"),
            ("src/shared.py", "modified"),
        ])
        ds = [
            _crystal("b", [("src/b.py", "modified")]),
            _crystal("c", [("src/shared.py", "modified")]),
        ]
        assert await engine.retroactive_review(late, ds) == "discarded"

    @pytest.mark.asyncio
    async def test_review_stored_on_crystal_model(self, engine):
        crystal = _crystal("a", [("src/a.py", "modified")])
        assert crystal.retroactive_review is None
        crystal.retroactive_review = "preserved"
        assert crystal.retroactive_review == "preserved"
