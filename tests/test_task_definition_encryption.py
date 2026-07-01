"""
Tests for the task-definition encryption opt-out.

Task card *definitions* (block trees, instructions, counts) are
authored configuration — not sensitive content — so they default to
plaintext at rest via EncryptionPolicy.never_encrypted_categories.
Task *run artifacts* stay encrypted (they can carry model output about
the codebase).

Covers:
  - EncryptionPolicy.requires_encryption honors the never-encrypt set
    BEFORE the empty-set "encrypt everything" rule
  - The never-encrypt opt-out only applies when encryption is enabled
  - A provider can force encryption by dropping the category (the merge
    intersects, so encryption wins if any provider demands it)
  - BaseStorage._infer_category routes task_cards/ → task_definition
    while task_runs/ stays session_data
"""

from pathlib import Path

from app.plugins.interfaces import EncryptionPolicy
from app.storage.base import BaseStorage


# ── requires_encryption: never-encrypt opt-out ──────────────────

def test_task_definition_not_encrypted_under_default_empty_policy():
    # Empty categories_requiring_encryption normally means "encrypt
    # everything"; the never-encrypt set must win for task_definition.
    policy = EncryptionPolicy(enabled=True)
    assert policy.requires_encryption("task_definition") is False


def test_session_data_still_encrypted_under_default_empty_policy():
    # The catch-all still encrypts everything else.
    policy = EncryptionPolicy(enabled=True)
    assert policy.requires_encryption("session_data") is True
    assert policy.requires_encryption("conversation_data") is True


def test_never_encrypt_opt_out_is_inert_when_encryption_disabled():
    # When ALE is off, nothing is encrypted regardless of the sets.
    policy = EncryptionPolicy(enabled=False)
    assert policy.requires_encryption("task_definition") is False
    assert policy.requires_encryption("session_data") is False


def test_provider_can_force_task_definition_encryption():
    # A security-conscious provider drops task_definition from the
    # never-encrypt set; with an explicit requires set that includes it,
    # it must encrypt.
    policy = EncryptionPolicy(
        enabled=True,
        categories_requiring_encryption={"task_definition", "session_data"},
        never_encrypted_categories=set(),  # opt-out removed
    )
    assert policy.requires_encryption("task_definition") is True


def test_explicit_requires_set_does_not_auto_encrypt_other_categories():
    # With a non-empty requires set, only listed categories encrypt.
    policy = EncryptionPolicy(
        enabled=True,
        categories_requiring_encryption={"conversation_data"},
    )
    assert policy.requires_encryption("conversation_data") is True
    # task_definition still opted out by default
    assert policy.requires_encryption("task_definition") is False
    # session_data not in the requires set → not encrypted
    assert policy.requires_encryption("session_data") is False


def test_never_encrypt_default_contains_task_definition():
    # The default factory ships the opt-out so the common case is
    # plaintext definitions without any provider configuration.
    policy = EncryptionPolicy()
    assert "task_definition" in policy.never_encrypted_categories


# ── _infer_category routing ─────────────────────────────────────

def test_infer_category_task_cards_is_task_definition():
    p = Path("/x/ziya/projects/p1/task_cards/card_abc.json")
    assert BaseStorage._infer_category(p) == "task_definition"


def test_infer_category_task_runs_stays_session_data():
    # Runs are NOT opted out — their artifacts can hold codebase output.
    p = Path("/x/ziya/projects/p1/task_runs/run_abc.json")
    assert BaseStorage._infer_category(p) == "session_data"


def test_infer_category_chats_unchanged():
    p = Path("/x/ziya/projects/p1/chats/chat_abc.json")
    assert BaseStorage._infer_category(p) == "conversation_data"


def test_infer_category_skills_unchanged():
    p = Path("/x/ziya/projects/p1/skills/skill_abc.json")
    assert BaseStorage._infer_category(p) == "session_data"


# ── end-to-end: a task_definition file round-trips as plaintext ──

def test_task_definition_category_resolves_to_not_required():
    # Ties the two halves together: the category _infer_category assigns
    # to a task card is exactly the one the default policy opts out of.
    policy = EncryptionPolicy(enabled=True)
    card_path = Path("/x/ziya/projects/p1/task_cards/card_abc.json")
    category = BaseStorage._infer_category(card_path)
    assert category == "task_definition"
    assert policy.requires_encryption(category) is False
