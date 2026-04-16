"""
Tests for memory extraction quality gates and intra-batch deduplication.

Verifies that the structural quality gate rejects:
- Refactoring notes
- Code descriptions
- Career narratives
And that intra-batch dedup catches paraphrases within the same extraction.
"""
import pytest
from app.utils.memory_extractor import quality_gate, deduplicate, _deduplicate_within_batch


class TestRefactoringGate:
    """Reject memories about code refactoring/extraction."""

    def test_rejects_extracted_from(self):
        candidates = [{"content": "Text delta processing was extracted from streaming_tool_executor.py into a dedicated module", "tags": ["refactoring"]}]
        assert quality_gate(candidates) == []

    def test_rejects_refactored(self):
        candidates = [{"content": "The streaming tool executor and server files have been completely refactored to replace broad exception handling", "tags": ["refactoring"]}]
        assert quality_gate(candidates) == []

    def test_rejects_lines_removed(self):
        candidates = [{"content": "Server.py was reduced from 7177 to 2868 lines of code after the Phase 4 extraction", "tags": ["refactoring"]}]
        assert quality_gate(candidates) == []

    def test_rejects_systematically_replaced(self):
        candidates = [{"content": "Broad exception handling was systematically replaced with targeted exception types across 36 files", "tags": ["code-quality"]}]
        assert quality_gate(candidates) == []

    def test_allows_non_refactoring(self):
        candidates = [{"content": "FRED routing path preference order: PER > TTC > Umbilical with dynamic mode handling", "tags": ["fred", "routing"]}]
        result = quality_gate(candidates)
        assert len(result) == 1


class TestCodeDescriptionGate:
    """Reject memories that describe what code does."""

    def test_rejects_implementation_uses(self):
        candidates = [{"content": "The current implementation uses os.getcwd() to determine the working directory for project context resolution", "tags": ["cwd"]}]
        assert quality_gate(candidates) == []

    def test_rejects_module_handles(self):
        candidates = [{"content": "The system handles streaming events by buffering text deltas and detecting code fences", "tags": ["streaming"]}]
        assert quality_gate(candidates) == []

    def test_rejects_polling_mechanism(self):
        candidates = [{"content": "The polling mechanism does not automatically re-trigger when a project is reloaded", "tags": ["ast", "polling"]}]
        assert quality_gate(candidates) == []

    def test_allows_domain_architecture(self):
        candidates = [{"content": "Every Kuiper satellite has Aldrin switches with full traffic management engines sitting completely idle", "tags": ["kuiper", "aldrin"]}]
        result = quality_gate(candidates)
        assert len(result) == 1


class TestCareerNarrativeGate:
    """Reject career strategy and self-promotion content."""

    def test_rejects_career_strategy(self):
        candidates = [{"content": "Career progression strategy involves associating with leading companies that provide long-term credibility", "tags": ["career"]}]
        assert quality_gate(candidates) == []

    def test_rejects_career_inflection(self):
        candidates = [{"content": "At this career inflection point, the AI movement represents a unique opportunity to leverage cross-disciplinary skills", "tags": ["career", "ai"]}]
        assert quality_gate(candidates) == []

    def test_rejects_most_valuable_professionals(self):
        candidates = [{"content": "The most valuable technical professionals at the AI frontier are those who can bridge infrastructure and ML tooling", "tags": ["ai"]}]
        assert quality_gate(candidates) == []

    def test_rejects_survived_politics(self):
        candidates = [{"content": "The project survived internal politics by carefully positioning itself as non-competitive to official tooling", "tags": ["strategy"]}]
        assert quality_gate(candidates) == []

    def test_allows_domain_decision(self):
        candidates = [{"content": "Business chose 500 MHz merlin mode for peak throughput and Hugo support, limiting to 10 spots per merlin", "tags": ["kuiper", "merlin"]}]
        result = quality_gate(candidates)
        assert len(result) == 1


class TestCombinedGates:
    """Test that multiple gates work together on mixed input."""

    def test_mixed_batch_filters_correctly(self):
        candidates = [
            # Should pass
            {"content": "SDN quantum = 4.8112s, SDN slot = 3 x SDN quantum = 14.4s for timing calculations", "tags": ["sdn", "timing"]},
            {"content": "Safety_inhibit ALWAYS overrides persistence in real flight mode operation", "tags": ["fred", "safety"]},
            # Should be rejected (refactoring)
            {"content": "The _handle_usage_event method was extracted from the streaming tool executor module", "tags": ["refactoring"]},
            # Should be rejected (code description)
            {"content": "The current implementation uses lazy imports for model manager to avoid circular dependencies", "tags": ["imports"]},
            # Should be rejected (career)
            {"content": "Career progression strategy involves entering transformative technology companies early", "tags": ["career"]},
            # Should be rejected (too short)
            {"content": "CSS fix applied", "tags": ["css"]},
        ]
        result = quality_gate(candidates)
        assert len(result) == 2
        contents = [c["content"] for c in result]
        assert any("SDN quantum" in c for c in contents)
        assert any("Safety_inhibit" in c for c in contents)


class TestIntraBatchDedup:
    """Test deduplication within a single extraction batch."""

    def test_removes_paraphrases(self):
        """Dedup catches paraphrases with >60% significant word overlap."""
        candidates = [
            {"content": "Ziya is context-management first: always aware of token depths and manages context prefetches for diffs", "tags": ["ziya"]},
            {"content": "Ziya is context-management first: manages token depths and context prefetches, presenting diffs for approval", "tags": ["ziya"]},
        ]
        result = _deduplicate_within_batch(candidates)
        # 73% word overlap — second is absorbed into first
        assert len(result) == 1
        # Should keep the longer version
        assert len(result[0]["content"]) == max(len(c["content"]) for c in candidates)

    def test_low_overlap_kept_separate(self):
        """Paraphrases with different vocabulary (< 60% overlap) are kept as separate memories."""
        candidates = [
            {"content": "Ziya is a self-hosted AI workbench where code and visual analysis converge", "tags": ["ziya"]},
            {"content": "Ziya is a comprehensive AI working environment for code analysis and visual diagnostics", "tags": ["ziya"]},
        ]
        # These share only ~50% of significant words — below the 60% threshold
        result = _deduplicate_within_batch(candidates)
        assert len(result) == 2

    def test_keeps_distinct_memories(self):
        candidates = [
            {"content": "FRED traffic control uses HTB qdiscs on the ttc0 interface with 16 priority classes", "tags": ["fred"]},
            {"content": "SDN quantum = 4.8112s, SDN slot = 3 x SDN quantum = 14.4s", "tags": ["sdn"]},
        ]
        result = _deduplicate_within_batch(candidates)
        assert len(result) == 2

    def test_single_candidate_unchanged(self):
        candidates = [{"content": "Some unique fact about networking", "tags": ["net"]}]
        result = _deduplicate_within_batch(candidates)
        assert len(result) == 1

    def test_empty_input(self):
        assert _deduplicate_within_batch([]) == []

    def test_deduplicate_calls_intra_batch(self):
        """deduplicate() should run intra-batch dedup even without existing memories."""
        candidates = [
            {"content": "Ziya is a self-hosted AI workbench where code and visual analysis converge in one place", "tags": ["ziya"]},
            {"content": "Ziya is a self-hosted AI technical workbench for code and visual analysis and diagnostics", "tags": ["ziya"]},
        ]
        result = deduplicate(candidates, existing_memories=[])
        assert len(result) == 1
