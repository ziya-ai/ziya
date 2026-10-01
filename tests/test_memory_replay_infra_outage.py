"""An infrastructure outage must never become a cached model answer.

2026-09-27: the replay cache stored any failure that survived 3 quick
retries as an ``error`` entry and replayed it forever.  A credential expiry
(07:54) and a train dropout (18:36) left 21 CredentialRetrievalError
entries, plus 4 embeddings recorded as ``none``.  The memory code fails
open on both (contradiction classify -> AGREES, no embedding -> no dedup),
so every arm sharing the namespace silently took the network-down code
path, and the engine's own error counter stayed at {}.

Pinned here:
  - an outage is waited out and never cached; the eventual answer is;
  - an outage longer than the window raises ReplayInfraError (a
    BaseException, so fail-open ``except Exception`` cannot swallow it);
  - a genuine model-side failure is still cached (determinism kept);
  - entries recorded as outages by the old code are dropped on read;
  - the engine stops WITHOUT checkpointing the half-done event, and a
    resume is byte-identical to a clean run over the same answers;
  - wall-time checkpoints mean a chunk that dies early keeps its work.
"""
import asyncio
import json

import pytest

from tests.test_memory_replay_engine import (  # noqa: F401  (env is a fixture)
    FakeModel, FakeProvider, _determinism_events, cfg, env, llmcache, run)


class CredentialRetrievalError(Exception):
    """Same class name botocore raises for an expired/unreachable credential process."""


def _cache(env, ns="u", **kw):
    return llmcache.LLMCache(env["tmp"] / "cache", ns, "record", backoff_s=0.001,
                             infra_backoff_max_s=0.001, **kw)


def _entries(env, ns):
    c = llmcache.LLMCache.__new__(llmcache.LLMCache)
    import app.utils.encryption as enc
    out = []
    for p in (env["tmp"] / "cache" / ns).rglob("*.enc"):
        out.append(json.loads(enc._encryptor.decrypt(p.read_bytes())))
    return out


def _flaky(fail_times, exc=CredentialRetrievalError("Failed to force refresh the credentials")):
    state = {"n": 0}

    async def live(category, system_prompt, user_message, max_tokens=2048, temperature=0.2):
        state["n"] += 1
        if state["n"] <= fail_times:
            raise exc
        return "answer"
    return live, state


def test_outage_is_waited_out_and_never_cached(env):
    c = _cache(env, infra_wait_s=5)
    live, st = _flaky(2)
    got = asyncio.run(c.wrap_call(live)("memory_organization", "s", "u"))
    assert got == "answer" and st["n"] == 3
    assert [e["kind"] for e in _entries(env, "u")] == ["text"]
    assert c.stats["errors_cached"] == 0 and c.stats["infra_retries"] == 2


def test_long_outage_raises_base_exception_and_caches_nothing(env):
    c = _cache(env, infra_wait_s=0.02)
    live, _ = _flaky(10_000)
    with pytest.raises(llmcache.ReplayInfraError):
        try:
            asyncio.run(c.wrap_call(live)("memory_organization", "s", "u"))
        except Exception:                       # what fail-open memory code does
            pytest.fail("ReplayInfraError was catchable as Exception")
    assert _entries(env, "u") == []
    assert c.stats["infra_abort"] == 1


def test_model_side_failure_is_still_cached(env):
    """Positive control: determinism for genuine failures is kept."""
    c = _cache(env, infra_wait_s=5)
    live, _ = _flaky(10_000, exc=ValueError("prompt is too long for this model"))
    with pytest.raises(RuntimeError):
        asyncio.run(c.wrap_call(live)("memory_organization", "s", "u"))
    assert [e["kind"] for e in _entries(env, "u")] == ["error"]


def test_legacy_poisoned_entries_are_dropped_and_refetched(env):
    c = _cache(env, infra_wait_s=5)
    r = c._resolve("memory_organization")
    k = c.key(["llm", "memory_organization", r["endpoint"], r["model_id"], "s", "u", 2048, 0.2])
    c.put(k, {"kind": "error", "type": "CredentialRetrievalError", "msg": "expired"})
    live, st = _flaky(0)
    assert asyncio.run(c.wrap_call(live)("memory_organization", "s", "u")) == "answer"
    assert st["n"] == 1 and c.stats["poisoned_dropped"] == 1
    assert [e["kind"] for e in _entries(env, "u")] == ["text"]
    # an old "none" embedding entry is likewise not an answer
    assert llmcache.is_infra_entry({"kind": "none"})
    assert not llmcache.is_infra_entry({"kind": "error", "type": "ValidationException", "msg": "bad"})


def test_embedding_none_is_retried_not_cached(env):
    c = _cache(env, infra_wait_s=5)
    inner = FakeProvider()
    real = inner.embed_text
    seq = iter([None, None])
    inner.embed_text = lambda t: next(seq, None) or real(t)
    v = c.wrap_provider(inner).embed_text("some text to embed")
    assert v is not None
    assert [e["kind"] for e in _entries(env, "u")] == ["vec"]


class _OutageAfter:
    """FakeModel that goes down after ``ok`` live calls."""

    def __init__(self, ok):
        self.inner, self.ok, self.n = FakeModel(), ok, 0

    async def __call__(self, *a, **kw):
        self.n += 1
        if self.n > self.ok:
            raise CredentialRetrievalError("Failed to force refresh the credentials")
        return await self.inner(*a, **kw)


def test_engine_stops_cleanly_and_resume_matches_a_clean_run(env):
    evs = _determinism_events()
    # checkpoint_every is never reached: only the wall-time cadence
    # (every event, here) can checkpoint -- the early-death case.
    common = dict(ns="out", checkpoint_every=10_000, checkpoint_every_s=0.0, infra_wait_s=0.02)
    # The fixture makes exactly 2 live model calls (6 of 12 events are
    # saves); the outage starts at the second, mid-way through the stream.
    model = _OutageAfter(ok=1)
    stopped = run(cfg(env, "part", evs, model=model, **common))
    assert model.n >= 2, "the outage was never reached"
    assert stopped["status"] == "infra_unavailable"
    assert stopped["cursor"] > 0, "work before the outage was checkpointed"
    assert stopped["infra_unavailable"]["resume_from"] == stopped["cursor"]
    rd = env["tmp"] / "runs" / "part"
    assert (rd / "infra_stops.jsonl").exists()
    assert all(e["kind"] != "error" for e in _entries(env, "out")), "outage was cached"

    rest = run(cfg(env, "part", evs, resume=True, **common))
    assert rest["status"] == "done"
    # Reference: a clean run PAUSED at the same cursor and resumed, over the
    # same cached answers.  Comparing against an uninterrupted run instead
    # conflates this claim with "reloading the embedding cache from disk is
    # invisible", which this fixture does not satisfy (its lifecycle job
    # fails on the in-memory cache and passes on a reloaded one -- the
    # IndexError seen in every fixture run, absent from real replay logs).
    ref_kw = dict(mode="strict", ns="out", checkpoint_every=10_000, checkpoint_every_s=0.0)
    paused = run(cfg(env, "clean", evs, stop_after=stopped["cursor"], **ref_kw))
    assert paused["status"] == "paused" and paused["cursor"] == stopped["cursor"]
    clean = run(cfg(env, "clean", evs, resume=True, **ref_kw))
    assert rest["fingerprint"] == clean["fingerprint"]
    for n in ("transitions.jsonl", "events.jsonl"):
        assert (rd / n).read_text() == (env["tmp"] / "runs" / "clean" / n).read_text()
