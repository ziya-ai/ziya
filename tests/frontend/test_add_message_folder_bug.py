"""
Regression test for the "swarm folder re-rooting" bug.

Bug: ChatContext.addMessageToConversation captured `currentFolderId` and
overwrote the conversation's folderId on every message add. When a user
viewed a delegate conversation (which sets currentFolderId to the swarm's
TaskPlan folder) and then created a new conversation, the first message
send would move the new conversation into the swarm folder.

Fix: The existing-conversation branch no longer touches folderId.
     The new-conversation branch guards against TaskPlan folders.

Since the bug is in React/TypeScript code, this test validates the *logic*
by reimplementing the critical decision in Python and verifying the expected
behavior. The actual fix is in frontend/src/context/ChatContext.tsx.
"""

import pytest
from typing import Any, Dict, List, Optional


# ---------- Minimal types mirroring the real ones ----------

def make_conversation(
    conv_id: str,
    folder_id: Optional[str] = None,
    messages: Optional[List[Dict]] = None,
) -> Dict[str, Any]:
    return {
        "id": conv_id,
        "title": "New Conversation",
        "messages": messages or [],
        "folderId": folder_id,
        "lastAccessedAt": 0,
        "_version": 0,
        "isActive": True,
        "hasUnreadResponse": False,
    }


def make_folder(
    folder_id: str,
    name: str,
    task_plan: Optional[Dict] = None,
) -> Dict[str, Any]:
    return {
        "id": folder_id,
        "name": name,
        "taskPlan": task_plan,
    }


# ---------- Pure function mirroring the FIXED logic ----------

def add_message_to_conversation(
    prev_conversations: List[Dict],
    message: Dict,
    conversation_id: str,
    current_folder_id: Optional[str],
    folders: List[Dict],
    dynamic_title_length: int = 40,
) -> List[Dict]:
    """
    Reimplements the core of addMessageToConversation AFTER the fix.
    """
    existing = next((c for c in prev_conversations if c["id"] == conversation_id), None)

    if existing is not None:
        # FIXED: Do NOT overwrite folderId — preserve existing value.
        return [
            {
                **c,
                "messages": [*c["messages"], message],
                # folderId is NOT touched here (the fix)
                "title": (
                    message["content"][:dynamic_title_length]
                    if len(c["messages"]) == 0 and message["role"] == "human"
                    else c["title"]
                ),
            }
            if c["id"] == conversation_id
            else c
            for c in prev_conversations
        ]

    # Brand-new inline conversation
    is_task_plan_folder = current_folder_id is not None and any(
        f["id"] == current_folder_id and f.get("taskPlan")
        for f in folders
    )

    return [
        *prev_conversations,
        {
            "id": conversation_id,
            "title": (
                message["content"][:dynamic_title_length]
                if message["role"] == "human"
                else "New Conversation"
            ),
            "messages": [message],
            # FIXED: Never auto-place inside a TaskPlan (swarm) folder.
            "folderId": None if is_task_plan_folder else current_folder_id,
            "lastAccessedAt": 0,
            "_version": 0,
            "isActive": True,
            "hasUnreadResponse": False,
        },
    ]


# ---------- Tests ----------

class TestAddMessageFolderAssignment:
    """Verify addMessageToConversation folder handling after the fix."""

    swarm_folder = make_folder(
        "swarm-folder-1",
        "Task Plan: My Swarm",
        task_plan={"status": "running"},
    )
    regular_folder = make_folder(
        "regular-folder-1",
        "My Notes",
        task_plan=None,
    )
    folders = [swarm_folder, regular_folder]

    def test_existing_conv_folder_not_overwritten_by_swarm(self):
        """Adding a message must NOT move a root-level conversation into a swarm folder."""
        conv = make_conversation("conv-1", folder_id=None)

        result = add_message_to_conversation(
            [conv],
            {"role": "human", "content": "Hello world"},
            "conv-1",
            current_folder_id=self.swarm_folder["id"],  # Bug trigger
            folders=self.folders,
        )

        updated = next(c for c in result if c["id"] == "conv-1")
        assert updated["folderId"] is None, (
            "Existing conversation folderId must be preserved, not overwritten "
            "with currentFolderId pointing at a swarm folder"
        )
        assert len(updated["messages"]) == 1

    def test_existing_conv_regular_folder_preserved(self):
        """A conversation in a regular folder must stay there."""
        conv = make_conversation(
            "conv-2",
            folder_id=self.regular_folder["id"],
            messages=[{"role": "human", "content": "First"}],
        )

        result = add_message_to_conversation(
            [conv],
            {"role": "assistant", "content": "Reply"},
            "conv-2",
            current_folder_id=self.swarm_folder["id"],
            folders=self.folders,
        )

        updated = next(c for c in result if c["id"] == "conv-2")
        assert updated["folderId"] == self.regular_folder["id"]

    def test_new_inline_conv_not_placed_in_swarm_folder(self):
        """A brand-new conversation must NOT be placed in a TaskPlan folder."""
        result = add_message_to_conversation(
            [],
            {"role": "human", "content": "New chat while viewing swarm"},
            "conv-new",
            current_folder_id=self.swarm_folder["id"],
            folders=self.folders,
        )

        created = next(c for c in result if c["id"] == "conv-new")
        assert created["folderId"] is None, (
            "New inline conversation must not be placed inside a swarm folder"
        )

    def test_new_inline_conv_placed_in_regular_folder(self):
        """A brand-new conversation should inherit currentFolderId for regular folders."""
        result = add_message_to_conversation(
            [],
            {"role": "human", "content": "New chat in folder"},
            "conv-new-2",
            current_folder_id=self.regular_folder["id"],
            folders=self.folders,
        )

        created = next(c for c in result if c["id"] == "conv-new-2")
        assert created["folderId"] == self.regular_folder["id"]

    def test_new_inline_conv_at_root_when_no_folder(self):
        """A brand-new conversation at root level stays at root."""
        result = add_message_to_conversation(
            [],
            {"role": "human", "content": "Root-level chat"},
            "conv-new-3",
            current_folder_id=None,
            folders=self.folders,
        )

        created = next(c for c in result if c["id"] == "conv-new-3")
        assert created["folderId"] is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
