"""
Chat data models.
"""
from pydantic import BaseModel
from typing import List, Optional, Any, Dict
from .group import ChatGroup
from .delegate import DelegateMeta

class Message(BaseModel):
    model_config = {"extra": "allow"}
    
    id: str
    role: str  # 'human' | 'assistant' | 'system'
    content: str
    timestamp: int
    images: Optional[List[Any]] = None
    muted: Optional[bool] = False

class HandoffDoc(BaseModel):
    """The handoff document carried by a continuation conversation.

    Stored on the CHILD record and injected as a per-turn prelude (not as a
    message), so the user can edit it at any point in the child's life.
    Only the model-authored parts are stored here; the "open threads"
    section is derived live from the shared bead tree at prompt-build time.
    See design/conversation-handoff.md.
    """
    model_config = {"extra": "allow"}
    document: str
    generatedAt: int
    editedAt: Optional[int] = None
    sourceMessageCount: int = 0
    tokenEstimate: Optional[int] = None

class Chat(BaseModel):
    model_config = {"extra": "allow"}
    
    id: str
    title: str
    groupId: Optional[str] = None
    contextIds: List[str] = []
    skillIds: List[str] = []
    # Bench conversation-layer placements, ``kind:name -> always|ondemand|off``
    # (design/capabilities-hub.md).  Absent means every item inherits.
    placements: Optional[Dict[str, Optional[str]]] = None
    additionalFiles: List[str] = []
    additionalPrompt: Optional[str] = None
    messages: List[Message] = []
    createdAt: int
    lastActiveAt: int
    # Frontend-specific fields that we preserve for round-tripping
    projectId: Optional[str] = None
    isActive: Optional[bool] = True
    folderId: Optional[str] = None
    hasUnreadResponse: Optional[bool] = False
    displayMode: Optional[str] = None
    lastAccessedAt: Optional[int] = None
    # Saved per-conversation model pin (alias string, e.g. "opus4.5").
    # Durable layer of per-conversation model selection — survives
    # restarts and is shared across tabs.  Declared explicitly (rather
    # than relying on extra="allow") to match the folderId/lineageRootId
    # round-trip convention.  None = inherit folder → project → default.
    modelPreference: Optional[str] = None
    # Delegate fields — None for regular conversations.
    # See design/newux-context.md for DelegateMeta schema.
    delegateMeta: Optional[DelegateMeta] = None
    # Branch lineage — None for trunk/unbranched conversations.  Authored
    # at fork time when splitting from a bead (a parked bead is an un-taken
    # branch point recorded with its message_index seam).  Declared
    # explicitly — rather than relying on extra="allow" — so the round-trip
    # is first-class and documented, matching the projectId/folderId/
    # delegateMeta convention above.  See design/bead-branching.md.
    branchedFrom: Optional[str] = None
    branchedAtMessageIndex: Optional[int] = None
    branchedFromLabel: Optional[str] = None
    # Fork-lineage root for shared bead trees (design/bead-branching.md "b2").
    # A plain fork ("continue this work in a fresh space") stamps this with
    # its lineage's ROOT id; beads live on the root record and every
    # conversation in the lineage resolves to that one shared, state-synced
    # tree.  None on a root/trunk conversation (it is its own root).
    lineageRootId: Optional[str] = None
    # How this conversation relates to branchedFrom: 'fork' (copy of the
    # whole transcript), 'branch' (cut at a bead seam), or 'handoff' (empty
    # transcript + HandoffDoc prelude).  None on a trunk conversation and on
    # forks created before the discriminator existed (those never set
    # branchedFrom either — see design/conversation-handoff.md, "new forks
    # only").
    lineageKind: Optional[str] = None
    # Present only when lineageKind == 'handoff'.
    handoff: Optional[HandoffDoc] = None
    # The LIVING draft on a source conversation, maintained by the model via
    # the handoff_write tool (and editable by the user in the drawer).
    # Commit copies it to the child's `handoff`; it stays here afterwards so
    # it keeps living if the user continues in this conversation.  See
    # design/conversation-handoff.md, "the living draft".
    handoffDraft: Optional[HandoffDoc] = None
    # Set on the SOURCE when a continuation has been created from it, so the
    # sidebar and the conversation view can dim it and link forward.  The
    # source is never locked — it stays fully usable.
    handedOffTo: Optional[str] = None

class ChatCreate(BaseModel):
    model_config = {"extra": "allow"}
    groupId: Optional[str] = None
    contextIds: Optional[List[str]] = None
    skillIds: Optional[List[str]] = None
    additionalFiles: Optional[List[str]] = None
    additionalPrompt: Optional[str] = None
    title: Optional[str] = None

class ChatUpdate(BaseModel):
    model_config = {"extra": "allow"}
    
    title: Optional[str] = None
    groupId: Optional[str] = None
    contextIds: Optional[List[str]] = None
    skillIds: Optional[List[str]] = None
    placements: Optional[Dict[str, Optional[str]]] = None
    additionalFiles: Optional[List[str]] = None
    additionalPrompt: Optional[str] = None
    messages: Optional[List[Message]] = None

class ChatSummary(BaseModel):
    """Chat without messages, for list views."""
    model_config = {"extra": "allow"}
    id: str
    title: str
    groupId: Optional[str]
    contextIds: List[str]
    skillIds: List[str]
    additionalFiles: List[str]
    messageCount: int
    createdAt: int
    lastActiveAt: int
    delegateMeta: Optional[DelegateMeta] = None
    branchedFrom: Optional[str] = None
    branchedAtMessageIndex: Optional[int] = None
    branchedFromLabel: Optional[str] = None
    # Lineage kind + handoff linkage on the SUMMARY for the same reason as
    # flags below: the sidebar renders chain rows and dimming from the
    # listing and never sees the full record.  hasHandoff is a boolean
    # rather than the document itself so listings stay small.
    lineageKind: Optional[str] = None
    handedOffTo: Optional[str] = None
    hasHandoff: bool = False
    # Conversation triage flags (frontend-authored; see
    # frontend/src/utils/conversationFlags.ts).  Declared HERE and not only
    # on Chat because the sidebar renders them from the SUMMARY listing:
    # Chat round-trips them via extra="allow", but ChatSummary is built
    # field-by-field from the raw record, so an undeclared field is dropped
    # on the one path the sidebar actually reads.  A flag set in another
    # browser was therefore invisible until something forced a full fetch.
    # flagColor is single-select (or None); flags is the multi-select label
    # id list.  Both default to the "unset" value rather than being absent
    # so a consumer never has to distinguish absent from empty.
    flags: List[str] = []
    flagColor: Optional[str] = None
    # Cheap derived "open work" counts for the sidebar indicators.  Always
    # present (default 0); recomputed from the chat record's _beads /
    # _work_items on each summary build.  openWorkItemCount is a correct
    # shell — 0 until the work-item queue exists.
    openBeadCount: int = 0
    openWorkItemCount: int = 0
    _version: Optional[int] = None


class ChatBulkSync(BaseModel):
    """Request body for bulk sync endpoint."""
    chats: List[Chat]


class ChatGroupBulkSync(BaseModel):
    """Request body for bulk group/folder sync endpoint."""
    groups: List[ChatGroup]
