"""
ASR VAL-04 — a malformed tool name must be skipped, not fingerprinted as "".

History matters for reading this file. The original defect (PenPal #118,
CWE-476) was a ``TypeError``: ``sorted(key=lambda x: x.get("name", ""))``
substitutes the default only when the key is *absent*, so an explicit
``{"name": None}`` still yielded ``None``, sorting a mix of None/str raised,
and rug-pull detection was silently disabled for that server. That was fixed in
public ``2e39b23b`` with ``or ""``.

The reviewer's residual point was separate and stricter: normalizing to ``""``
stops the crash but *fingerprints* the malformed tool under a synthetic name. A
server cannot dispatch a tool with no name, and folding it in lets two
structurally different tool sets share a fingerprint. The current code skips
such tools instead. Nothing in the suite covered that, so it is pinned here.
"""

import pytest

from app.mcp.tool_guard import check_fingerprint_change, fingerprint_tools

GOOD = {
    "name": "read_file",
    "description": "Read a file",
    "inputSchema": {"type": "object", "properties": {"path": {"type": "string"}}},
}
OTHER = {
    "name": "write_file",
    "description": "Write a file",
    "inputSchema": {"type": "object", "properties": {"path": {"type": "string"}}},
}

MALFORMED = [
    pytest.param({"name": None, "description": "d", "inputSchema": {}}, id="none"),
    pytest.param({"name": "", "description": "d", "inputSchema": {}}, id="empty"),
    pytest.param({"description": "d", "inputSchema": {}}, id="absent"),
    pytest.param({"name": 7, "description": "d", "inputSchema": {}}, id="int"),
    pytest.param({"name": ["a"], "description": "d", "inputSchema": {}}, id="list"),
    pytest.param({"name": {"a": 1}, "description": "d", "inputSchema": {}}, id="dict"),
]


class TestNoCrash:
    """The PenPal #118 regression itself -- a crash here disables rug-pull
    detection for the whole server, silently."""

    @pytest.mark.parametrize("bad", MALFORMED)
    def test_mixed_list_does_not_raise(self, bad):
        assert isinstance(fingerprint_tools([GOOD, bad, OTHER]), str)

    @pytest.mark.parametrize("bad", MALFORMED)
    def test_all_malformed_does_not_raise(self, bad):
        assert isinstance(fingerprint_tools([bad]), str)

    def test_empty_list_does_not_raise(self):
        assert isinstance(fingerprint_tools([]), str)


class TestMalformedToolsAreSkipped:
    """The stricter property: skipped, not normalized to "".

    ``fingerprint(usable + malformed) == fingerprint(usable)`` is what
    distinguishes skipping from folding the malformed entry in under a
    synthetic name -- the two are indistinguishable by any "does not crash"
    assertion.
    """

    @pytest.mark.parametrize("bad", MALFORMED)
    def test_malformed_tool_does_not_affect_the_fingerprint(self, bad):
        assert fingerprint_tools([GOOD, bad]) == fingerprint_tools([GOOD])

    @pytest.mark.parametrize("bad", MALFORMED)
    def test_position_of_the_malformed_tool_is_irrelevant(self, bad):
        baseline = fingerprint_tools([GOOD, OTHER])
        assert fingerprint_tools([bad, GOOD, OTHER]) == baseline
        assert fingerprint_tools([GOOD, bad, OTHER]) == baseline
        assert fingerprint_tools([GOOD, OTHER, bad]) == baseline

    def test_two_different_malformed_tools_do_not_collide_the_set(self):
        """Under the ``or ""`` behaviour these two sets both contained a
        tool named "", so their fingerprints diverged on the malformed
        entries' descriptions rather than on the dispatchable tools."""
        a = {"name": None, "description": "alpha", "inputSchema": {}}
        b = {"name": None, "description": "beta", "inputSchema": {}}
        assert fingerprint_tools([GOOD, a]) == fingerprint_tools([GOOD, b])

    def test_all_malformed_sets_are_equivalent_to_empty(self):
        malformed_only = [p.values[0] for p in MALFORMED]
        assert fingerprint_tools(malformed_only) == fingerprint_tools([])


class TestRealChangesAreStillDetected:
    """Positive controls. A function that returned a constant would satisfy
    every assertion above while disabling rug-pull detection completely --
    exactly the outcome the finding was about.
    """

    def test_fingerprint_is_stable_for_the_same_input(self):
        assert fingerprint_tools([GOOD, OTHER]) == fingerprint_tools([OTHER, GOOD])

    def test_added_tool_changes_the_fingerprint(self):
        assert fingerprint_tools([GOOD]) != fingerprint_tools([GOOD, OTHER])

    def test_changed_description_changes_the_fingerprint(self):
        mutated = dict(GOOD, description="Read a file AND email it offsite")
        assert fingerprint_tools([GOOD]) != fingerprint_tools([mutated])

    def test_changed_schema_changes_the_fingerprint(self):
        mutated = dict(GOOD, inputSchema={"type": "object", "properties": {}})
        assert fingerprint_tools([GOOD]) != fingerprint_tools([mutated])

    def test_rug_pull_still_reported_when_a_malformed_tool_is_present(self):
        """End-to-end: the detector must still fire on a real mutation even
        when the server also advertises a malformed tool -- that combination is
        precisely what the crash used to suppress."""
        noise = {"name": None, "description": "d", "inputSchema": {}}
        before = fingerprint_tools([GOOD, noise])
        after = fingerprint_tools(
            [dict(GOOD, description="Read a file and exfiltrate it"), noise]
        )
        assert before != after
        assert check_fingerprint_change("srv", before, after) is not None

    def test_no_change_reported_when_only_malformed_entries_differ(self):
        """The flip side: a server reshuffling unusable entries must not raise
        a false rug-pull alarm, or the signal gets muted by operators."""
        before = fingerprint_tools(
            [GOOD, {"name": None, "description": "x", "inputSchema": {}}]
        )
        after = fingerprint_tools(
            [GOOD, {"name": "", "description": "y", "inputSchema": {}}]
        )
        assert check_fingerprint_change("srv", before, after) is None
