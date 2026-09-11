"""iter-5 [backlog H13]: retrievability via compound-identifier sub-token matching.

The rubric-v1 RETRIEVABILITY dimension is scored by seeding a MemoryStorage
sandbox with the produced memories and asking, for each golden fact's
natural-language queries, whether the covering memory appears in
``MemoryStorage.search(q, limit=3)`` top-3 (embeddings are disabled in this
workspace, so this is the pure keyword path).

Defect: ``_tokenize`` keeps ``_`` as an in-token character, so a snake_case
identifier such as ``run_post_conversation_extraction`` survives as ONE opaque
token.  A natural-language query ("post conversation extraction") token-matches
none of them, so the memory that actually NAMES the queried entity scores 0 on
``word_score`` and never surfaces top-3.

Fix (surgical, ADDITIVE, in ``MemoryStorage.search`` keyword scoring): expand
each underscore-compound content token into its parts and award partial
(half-IDF) credit when a query token matches only via a part.

These tests exercise the exact scorer seam (``MemoryStorage.search`` top-3).
The first FAILS on the pre-change code (the compound memory is not returned)
and PASSES with the fix; the others guard that the additive change neither
breaks full-token matches nor manufactures spurious matches.
"""
from unittest.mock import patch

import pytest

from app.models.memory import Memory
from app.services.embedding_service import EmbeddingCache
from app.storage.memory import MemoryStorage


def _seed(tmp_path):
    """Seed a sandbox store; keep the embedding cache empty so the search
    semantic path is skipped and the keyword path (the scorer's real path in
    this embeddings-disabled workspace) is exercised deterministically."""
    store = MemoryStorage(memory_dir=tmp_path)
    cache = EmbeddingCache(memory_dir=tmp_path)
    mems = [
        Memory(
            content=(
                "The nightly memory job is orchestrated by "
                "run_post_conversation_extraction, which writes candidates to "
                "the probationary queue."
            ),
            layer="architecture",
            tags=[],
        ),
        Memory(
            content="Beam scheduling uses a round robin allocator across active cells.",
            layer="architecture",
            tags=[],
        ),
        Memory(
            content="The dashboard renders latency percentiles for each region.",
            layer="domain_context",
            tags=[],
        ),
    ]
    with patch("app.services.embedding_service.get_embedding_cache", return_value=cache):
        store.save_many(mems)
    return store, cache, mems


def test_snakecase_identifier_retrievable_by_nl_query(tmp_path):
    """The memory naming ``run_post_conversation_extraction`` must be in the
    top-3 for the NL query whose words only match the compound's PARTS.
    FAILS on backup (compound is one opaque token -> word_score 0 -> not
    returned); PASSES with the sub-token credit."""
    store, cache, mems = _seed(tmp_path)
    target = mems[0]
    with patch("app.services.embedding_service.get_embedding_cache", return_value=cache):
        results = store.search("post conversation extraction", limit=3)
    ids = [r.id for r in results]
    assert target.id in ids, (
        "memory naming run_post_conversation_extraction should be retrievable "
        "by the natural-language query 'post conversation extraction' via its "
        "compound-identifier parts"
    )


def test_full_token_match_still_works(tmp_path):
    """Additive change must not break a plain full-token match."""
    store, cache, mems = _seed(tmp_path)
    with patch("app.services.embedding_service.get_embedding_cache", return_value=cache):
        results = store.search("dashboard", limit=3)
    assert results, "a plain full-token query must still return its memory"
    assert results[0].id == mems[2].id


def test_no_spurious_match_for_unrelated_query(tmp_path):
    """Sub-token credit is confined to actual compound parts; an unrelated
    query must still return nothing (no fabricated matches)."""
    store, cache, mems = _seed(tmp_path)
    with patch("app.services.embedding_service.get_embedding_cache", return_value=cache):
        results = store.search("quantum chromodynamics", limit=3)
    assert results == []
