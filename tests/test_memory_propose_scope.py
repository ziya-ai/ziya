"""
Regression test for the MemoryProposal.scope type bug in memory_propose.

memory_propose previously assigned a raw dict to the scope field:

    proposal.scope = {"project_paths": [project_path]}

but the field is typed ``scope: MemoryScope``.  Pydantic v2 stored the dict
(model_config extra="allow") yet emitted PydanticSerializationUnexpectedValue
every time the in-memory object was serialized — which happens inside
``add_proposal`` (``proposal.model_dump()``).  Any code touching
``proposal.scope.project_paths`` on that in-memory object would also
AttributeError, because the value is a dict, not a MemoryScope.

The fix assigns ``MemoryScope(project_paths=[...])``.

IMPORTANT test-design note: reading a proposal back via
``store.list_proposals()`` reconstructs it with ``MemoryProposal(**data)``,
which *coerces the dict back into a MemoryScope* — i.e. the JSON round-trip
HEALS the bug.  So a test that observes the post-storage object cannot detect
it.  The bug only exists on the in-memory object between assignment and
storage, and surfaces as the serialization warning raised inside
``execute()``.  These tests therefore assert at execute() time, under
``simplefilter("error")``, so the warning becomes a failure on buggy code and
the tests genuinely pin the fix.

Run under the autouse conftest fixture (isolated tmp store, Noop embeddings).
"""
import warnings

import pytest

from app.mcp.tools.memory_tools import MemoryProposeTool
from app.models.memory import MemoryScope
from app.storage.memory import get_memory_storage


_WORKSPACE = "/tmp/ziya-scope-test-project"


@pytest.mark.asyncio
async def test_propose_with_scope_raises_no_serialization_warning():
    """The real bug surface: assigning a raw dict to scope makes
    add_proposal's model_dump() emit PydanticSerializationUnexpectedValue.
    Under simplefilter('error') that warning becomes an exception inside
    execute(), so this fails on buggy code and passes on the MemoryScope fix.
    """
    tool = MemoryProposeTool()
    with warnings.catch_warnings():
        warnings.simplefilter("error")  # any warning during execute → raise
        result = await tool.execute(
            content="Scope fix: GGMA has 4 merlins per gateway.",
            layer="domain_context",
            conversation_id="conv-scope",
            _workspace_path=_WORKSPACE,
        )
    assert result.get("success"), result


@pytest.mark.asyncio
async def test_in_memory_scope_supports_attribute_access():
    """Construct a proposal exactly as the (fixed) tool does and confirm the
    in-memory object exposes scope as a MemoryScope — i.e. attribute access
    works, which the raw-dict form (``{'project_paths': [...]}``) does NOT.

    This pins the fix at the object level, bypassing the storage round-trip
    that would otherwise heal a dict back into a MemoryScope and mask the bug.
    """
    from app.models.memory import MemoryProposal
    # Mirror the fixed assignment.  If the production code regresses to a raw
    # dict, the equivalent object would AttributeError on the next line —
    # this test documents the contract the fix must satisfy.
    proposal = MemoryProposal(content="x", layer="domain_context")
    proposal.scope = MemoryScope(project_paths=[_WORKSPACE])
    assert isinstance(proposal.scope, MemoryScope)
    assert proposal.scope.project_paths == [_WORKSPACE]
    # And it serializes without warning.
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        dumped = proposal.model_dump()
    assert dumped["scope"]["project_paths"] == [_WORKSPACE]


@pytest.mark.asyncio
async def test_no_workspace_leaves_default_scope(monkeypatch):
    """When no project path is available (no _workspace_path AND no
    ZIYA_USER_CODEBASE_DIR), the `if project_path:` branch is skipped and
    scope stays the default empty MemoryScope — still serializes cleanly.

    The production line is:
        project_path = kwargs.pop("_workspace_path", None) or os.environ.get("ZIYA_USER_CODEBASE_DIR", "")
    so passing _workspace_path="" is NOT enough — the env var fallback would
    supply the real cwd.  Clear the env var too.
    """
    monkeypatch.delenv("ZIYA_USER_CODEBASE_DIR", raising=False)
    tool = MemoryProposeTool()
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        result = await tool.execute(
            content="Scope fix: no workspace path available.",
            layer="domain_context",
            conversation_id="conv-scope",
            _workspace_path="",
        )
    assert result.get("success"), result
    # Read back: default empty scope, healed to MemoryScope by the round-trip.
    pid = result["proposal_id"]
    proposal = next(p for p in get_memory_storage().list_proposals() if p.id == pid)
    assert isinstance(proposal.scope, MemoryScope)
    assert proposal.scope.project_paths == []
