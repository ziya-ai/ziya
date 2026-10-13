"""scripts/memory_replay engine: sandbox, sim clock, record/replay cache,
scheduler, loss attribution, checkpoint/resume -- tested end to end through
the production memory code with a fake model and a fake embedder.

Every test runs against the conftest per-test ZIYA_HOME (the "real" home for
the sandbox's purposes), with a fake ALE encryptor so the engine's
no-plaintext-on-disk checks are exercised rather than bypassed.
"""
import asyncio
import hashlib
import json
import os
import re
import sys
import threading
from pathlib import Path

import numpy as np
import pytest

HARNESS = Path(__file__).resolve().parent.parent / "scripts" / "memory_replay"
if str(HARNESS) not in sys.path:
    sys.path.insert(0, str(HARNESS))

import attribution  # noqa: E402
import engine  # noqa: E402
import events as E  # noqa: E402
import llmcache  # noqa: E402
import sandbox as S  # noqa: E402
import simclock  # noqa: E402

T0 = int(1_760_000_000_000)          # 2025-10-09, far from wall-clock "today"
MIN = 60_000
HOUR = 60 * MIN


# ---------------------------------------------------------------- fixtures

class FakeEnc:
    """Reversible stand-in for the ALE encryptor (MAGIC-prefixed, XOR)."""

    def __init__(self):
        from app.utils.encryption import MAGIC
        self.magic = MAGIC

    def is_enabled(self, category=""):
        return True

    def encrypt(self, b, category=""):
        return self.magic + b"\x01\x00" + bytes(x ^ 0x5A for x in b)

    def decrypt(self, e):
        if not e.startswith(self.magic):
            return e
        return bytes(x ^ 0x5A for x in e[len(self.magic) + 2:])


class FakeProvider:
    dim = 1024

    def __init__(self):
        self.calls = 0

    def embed_text(self, text):
        self.calls += 1
        if not text or not text.strip():
            return None
        seed = int.from_bytes(hashlib.sha256(text.encode()).digest()[:8], "little")
        v = np.random.default_rng(seed).standard_normal(self.dim).astype(np.float32)
        return v / np.linalg.norm(v)


class FakeModel:
    """Deterministic per prompt, but numbered: a live call made twice would
    differ, so a replay that matches proves the cache served it."""

    def __init__(self):
        self.calls = 0

    async def __call__(self, category, system_prompt, user_message, max_tokens=2048, temperature=0.2):
        self.calls += 1
        from app.memory.extractor import TRIAGE_SYSTEM_PROMPT
        if "MEMORIES TO REVIEW" in user_message:
            ids = re.findall(r"\[(m_[0-9a-f]+)\]", user_message)
            return json.dumps({"remove": ids[:1], "merge": [], "reasons": {i: "junk" for i in ids[:1]}})
        if system_prompt == TRIAGE_SYSTEM_PROMPT:
            return json.dumps({"candidates": [{"gist": "orders db is postgres", "layer": "architecture",
                                               "quote": "postgres"}]})
        if "extract" in system_prompt.lower():
            return json.dumps([{"content": f"The orders service stores data in PostgreSQL 15 (call {self.calls}).",
                                "layer": "architecture", "tags": ["orders", "postgres"],
                                "durability": 0.9, "atomicity": 0.9, "self_containment": 0.9}])
        return "[]"


@pytest.fixture
def env(tmp_path, monkeypatch):
    import app.utils.encryption as enc
    monkeypatch.setattr(enc, "_encryptor", FakeEnc())
    monkeypatch.setenv("ZIYA_ENABLE_MEMORY", "true")
    home = Path(os.environ["ZIYA_HOME"])
    (home / "memory").mkdir(parents=True, exist_ok=True)
    (home / "memory" / "memories.json").write_bytes(b"REAL-STORE-BYTES")
    (home / "memory" / "probationary.jsonl").write_bytes(b"REAL-PROPOSALS")
    (home / "audit").mkdir(exist_ok=True)
    return {"home": home, "tmp": tmp_path}


def cfg(env, run, events, mode="record", ns="t", model=None, provider=None, **kw):
    return engine.Config(arm="test", run_dir=env["tmp"] / "runs" / run, real_home=env["home"],
                         cache_root=env["tmp"] / "cache", cache_ns=ns, cache_mode=mode,
                         events=events, live_call=model or FakeModel(),
                         provider=provider or FakeProvider(), **kw)


def ev_save(t, content, project="/w/ziya-app", chat="c-save"):
    return E.ReplayEvent(t=t, kind="save", chat_id=chat, project_id="p", project_path=project,
                         source=f"a:{t}", payload={"content": content, "tags": ["t"], "layer": "domain_context"})


def ev_propose(t, content, project="/w/ProjB-x", chat="c-prop"):
    return E.ReplayEvent(t=t, kind="propose", chat_id=chat, project_id="p", project_path=project,
                         source=f"a:{t}", payload={"content": content, "tags": ["k"], "layer": "lexicon"})


def ev_extract(t, history, reply="ok", chat="c-ex", project="/w/ziya-app", idx=1):
    return E.ReplayEvent(t=t, kind="extract", chat_id=chat, project_id="p", project_path=project,
                         msg_index=idx, source="timeline",
                         payload={"history": history, "reply": reply})


REF_HISTORY = [{"role": "human", "content": "see https://docs.python.org/3/library/asyncio.html for the event loop docs"}]
LONG_HISTORY = [
    {"role": "human", "content": "Our orders service keeps everything in PostgreSQL 15; the schema lives in db/orders.sql."},
    {"role": "assistant", "content": "Understood, PostgreSQL 15 is the database of record for orders."},
    {"role": "human", "content": "Right. We decided last quarter never to use MongoDB for orders again because of the migration pain."},
    {"role": "assistant", "content": "Noted: no MongoDB for orders; Postgres is the standard."},
    {"role": "human", "content": "Please remember that for future sessions when we design new order features and reporting."},
]


def run(c):
    return asyncio.run(engine.Engine(c).run())


def _sb_store(env, runname):
    from app.storage.proposals import ProposalsStore
    from app.storage.memory import MemoryStorage
    d = env["tmp"] / "runs" / runname / "home" / "memory"
    return MemoryStorage(memory_dir=d), ProposalsStore(memory_dir=d)


def _transitions(env, runname):
    p = env["tmp"] / "runs" / runname / "transitions.jsonl"
    return [json.loads(l) for l in open(p)] if p.exists() else []


# ------------------------------------------------------------------ sandbox

def test_sandbox_redirects_seams_and_replayed_events_reach_sandbox_proposals(env):
    before = S.hash_tree(env["home"] / "memory")
    evs = [ev_propose(T0, "SLA means service level agreement in the billing docs."),
           ev_extract(T0 + MIN, REF_HISTORY, chat="c-ref")]
    out = run(cfg(env, "seam", evs))
    assert out["status"] == "done"
    assert S.hash_tree(env["home"] / "memory") == before           # real store untouched
    assert not (env["home"] / "memory" / "domain_clocks.json").exists()
    assert not (env["home"] / "memory" / "activity_counter.json").exists()
    mem, props = _sb_store(env, "seam")
    contents = [p["content"] for p in props.list_all()]
    assert any("Export Administration" in c for c in contents)       # propose event -> sandbox
    assert any(p.get("layer") == "reference" for p in props.list_all())  # extract event -> sandbox
    assert (env["tmp"] / "runs" / "seam" / "home" / "memory" / "domain_clocks.json").exists()
    # the proposal carries the conversation and project the event came from
    kp = [p for p in props.list_all() if "Export" in p["content"]][0]
    assert kp["conversation_id"] == "c-prop"
    assert kp["scope"]["project_paths"] == ["/w/ProjB-x"]


def test_write_guard_refuses_and_aborts(env):
    sb = S.Sandbox(env["tmp"] / "sbx", real_home=env["home"])
    with pytest.raises(S.SandboxViolation):
        with sb:
            with pytest.raises(PermissionError):
                open(env["home"] / "memory" / "memories.json", "w")
            with pytest.raises(PermissionError):
                (env["home"] / "system_jobs.json").write_text("{}")
            sb.check()
    assert (env["home"] / "memory" / "memories.json").read_bytes() == b"REAL-STORE-BYTES"


def test_real_store_change_is_detected_and_attributed(env):
    # Another process (the live server) changed the real store: detected,
    # reported, and the run still ends non-zero -- but not as a replay write.
    sb = S.Sandbox(env["tmp"] / "sbx2", real_home=env["home"], guard=False)
    with pytest.raises(S.ExternalStoreChange):
        with sb:
            (env["home"] / "memory" / "memories.json").write_bytes(b"changed")
    assert sb.external_change["changed"] == ["memories.json"]
    # A replay-minted memory id showing up in the real store IS the replay.
    from app.models.memory import Memory
    sb = S.Sandbox(env["tmp"] / "sbx3", real_home=env["home"], guard=False)
    with pytest.raises(S.SandboxViolation):
        with sb:
            m = sb.stores["memory"].save(Memory(content="minted in the sandbox"))
            (env["home"] / "memory" / "memories.json").write_text(json.dumps([{"id": m.id}]))


def test_audit_writers_neutralised(env):
    import app.utils.tool_audit_log as tal
    with S.Sandbox(env["tmp"] / "sbx3", real_home=env["home"]) as sb:
        tal.log_security_event("memory_created", source_tool="memory_store", details={})
    assert sb.audit_counts == {"memory_created": 1}
    assert not list((env["home"] / "audit").glob("*.jsonl"))


# ---------------------------------------------------------------- sim clock

def test_simclock_replaces_module_time_only_in_memory_modules():
    import time as real_time
    import app.memory.organizer as org
    import app.models.memory as mm
    clock = simclock.SimClock(T0)
    undo = simclock.install(clock)
    try:
        assert org.time.time() == T0 / 1000
        first = mm.Memory(content="x")
        assert first.created == real_time.strftime("%Y-%m-%d", real_time.localtime(T0 / 1000))
        a, b = first.id, mm.Memory(content="y").id
        assert a != b
    finally:
        simclock.uninstall(undo)
    assert org.time is real_time
    c2 = simclock.SimClock(T0)
    undo = simclock.install(c2)
    try:
        assert mm.Memory(content="x").id == a                        # deterministic ids
    finally:
        simclock.uninstall(undo)


def test_sim_clock_in_created_and_archived_at_via_cleanup(env):
    evs = [ev_save(T0 + i * MIN, f"Durable fact number {i} about the orders pipeline and its retries.")
           for i in range(6)]
    evs.append(ev_extract(T0 + 7 * HOUR, REF_HISTORY, chat="c-late"))  # organize due at 6h, fires here
    run(cfg(env, "clock", evs))
    mem, _ = _sb_store(env, "clock")
    rows = mem._load_memories()
    import time as real_time
    day0 = real_time.strftime("%Y-%m-%d", real_time.localtime(T0 / 1000))
    assert {r["created"] for r in rows} == {day0}
    arch = [r for r in rows if r.get("status") == "archived"]
    assert arch, "organize cleanup should have archived one memory"
    tr = [t for t in _transitions(env, "clock") if t["kind"] == "memory" and t["to"] == "archived"]
    assert tr and tr[0]["cause"] == "cleanup_remove"
    assert arch[0]["archived_at"] == tr[0]["t"] == T0 + 7 * HOUR
    ticks = [json.loads(l) for l in open(env["tmp"] / "runs" / "clock" / "events.jsonl") if '"tick"' in l]
    assert any("memory_organize" in t["ran"] and t["t"] == T0 + 7 * HOUR for t in ticks)


# -------------------------------------------------------------------- cache

def test_strict_cache_raises_on_miss(env, tmp_path):
    import app.utils.encryption as enc
    c = llmcache.LLMCache(tmp_path / "c", "ns", "strict")
    wrapped = c.wrap_call(FakeModel())
    with pytest.raises(llmcache.CacheMiss):
        asyncio.run(wrapped(category="memory_extraction", system_prompt="s", user_message="u"))
    p = c.wrap_provider(FakeProvider())
    with pytest.raises(llmcache.CacheMiss):
        p.embed_text("hello")
    assert c.stats["miss"] == 2


def test_cache_files_are_encrypted(env, tmp_path):
    c = llmcache.LLMCache(tmp_path / "c", "ns", "record")
    asyncio.run(c.wrap_call(FakeModel())(category="x", system_prompt="SECRET-SYS", user_message="SECRET-USER"))
    files = list((tmp_path / "c" / "ns").rglob("*.enc"))
    assert len(files) == 1 and b"SECRET" not in files[0].read_bytes()


def test_exclusive_create_race_resolves_to_one_response(env, tmp_path):
    caches = [llmcache.LLMCache(tmp_path / "c", "shared", "record") for _ in range(8)]
    k = llmcache.LLMCache.key(["llm", "x"])
    results = [None] * 8
    barrier = threading.Barrier(8)

    def worker(i):
        barrier.wait()
        results[i] = caches[i].put(k, {"kind": "text", "text": f"response-{i}"})

    ts = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert len({r["text"] for r in results}) == 1
    assert sum(c.stats["race_lost"] for c in caches) == 7
    assert len(list((tmp_path / "c" / "shared").rglob("*.enc"))) == 1


def _determinism_events():
    evs = [ev_save(T0 + i * MIN, f"Durable fact number {i} about the orders pipeline and its retries.")
           for i in range(6)]
    evs += [ev_extract(T0 + 2 * HOUR + i * 10 * MIN, LONG_HISTORY + [{"role": "human", "content": f"turn {i} more detail"}],
                       reply="PostgreSQL 15 is the orders database.", chat=f"c-long-{i % 2}", idx=i)
            for i in range(4)]
    evs.append(ev_propose(T0 + 3 * HOUR, "qos means quality of service in the billing stack.",
                          project="/w/qos"))
    evs.append(ev_extract(T0 + 9 * HOUR, REF_HISTORY, chat="c-late"))
    return evs


def test_two_strict_replays_produce_identical_final_stores(env):
    model = FakeModel()
    rec = run(cfg(env, "rec", _determinism_events(), mode="record", ns="det", model=model))
    assert model.calls > 0
    dead = FakeModel()

    async def never(**k):
        raise AssertionError("strict replay must not call the model")
    a = run(cfg(env, "s1", _determinism_events(), mode="strict", ns="det", model=never))
    b = run(cfg(env, "s2", _determinism_events(), mode="strict", ns="det", model=never))
    assert a["status"] == b["status"] == "done"
    assert a["fingerprint"] == b["fingerprint"] == rec["fingerprint"]
    for name in ("memories.json", "mindmap.json", "probationary.jsonl"):
        pa = env["tmp"] / "runs" / "s1" / "home" / "memory" / name
        pb = env["tmp"] / "runs" / "s2" / "home" / "memory" / name
        if pa.exists():
            assert FakeEnc().decrypt(pa.read_bytes()) == FakeEnc().decrypt(pb.read_bytes())
    assert (env["tmp"] / "runs" / "s1" / "transitions.jsonl").read_text() == \
        (env["tmp"] / "runs" / "s2" / "transitions.jsonl").read_text()
    assert dead.calls == 0


def test_strict_replay_aborts_on_changed_prompt(env):
    run(cfg(env, "rec2", [ev_extract(T0, LONG_HISTORY)], mode="record", ns="chg"))
    changed = [ev_extract(T0, LONG_HISTORY[:-1] + [{"role": "human", "content": "something else entirely, remember it"}])]
    with pytest.raises(llmcache.CacheMiss):
        run(cfg(env, "s3", changed, mode="strict", ns="chg"))


# ---------------------------------------------------------------- scheduler

def test_scheduler_ticks_at_due_times_and_once_after_gap():
    s = E.Scheduler({"memory_lifecycle": 30 * MIN, "memory_organize": 6 * HOUR}, idle_gap_ms=HOUR)
    seen = []

    def at(t):
        ticks = s.ticks_before(t)
        for T in ticks:
            s.mark(T)
        s.saw_event(t)
        seen.append(ticks)
        return ticks

    assert at(0) == [0]                       # never-checked jobs check on the first tick
    assert at(10 * MIN) == []
    assert at(50 * MIN) == [30 * MIN]         # lifecycle at its due time, server up
    assert at(70 * MIN) == [60 * MIN]
    # 70m -> 10h: server up until 130m, then idle; everything else once at 10h
    assert at(10 * HOUR) == [90 * MIN, 120 * MIN, 10 * HOUR]
    assert s.last_check == {"memory_lifecycle": 10 * HOUR, "memory_organize": 10 * HOUR}
    # organize: 6h after 10h, inside a busy stretch -> at its due time
    for m in range(10 * 60 + 20, 16 * 60 + 30, 20):
        at(m * MIN)
    org = [T for ticks in seen for T in ticks if T == 16 * HOUR]
    assert org == [16 * HOUR]


def test_engine_fires_arm_jobs_on_simulated_schedule(env):
    evs = [ev_propose(T0, "EAR is a regulation."), ev_propose(T0 + 40 * MIN, "ITAR is a regulation too."),
           ev_propose(T0 + 5 * HOUR, "BIS administers the EAR.")]
    run(cfg(env, "sched", evs))
    ticks = [json.loads(l) for l in open(env["tmp"] / "runs" / "sched" / "events.jsonl") if '"tick"' in l]
    # server up for 1 h after the 40m event: lifecycle at 30m (before it), 60m and
    # 90m (after it); the rest of the gap collapses into one tick at 5h.
    assert [t["t"] - T0 for t in ticks] == [0, 30 * MIN, 60 * MIN, 90 * MIN, 5 * HOUR]
    assert ticks[0]["ran"] == []                                  # empty stores: both gates decline
    assert "memory_lifecycle" in ticks[1]["ran"]                 # open proposals -> gate passes


# -------------------------------------------------------------- attribution

def test_hard_delete_attribution_baseline_style(env, tmp_path):
    from app.storage.memory import MemoryStorage
    from app.storage.proposals import ProposalsStore
    from app.models.memory import Memory
    clock = simclock.SimClock(T0)
    with S.Sandbox(tmp_path / "h", real_home=env["home"]) as sb:
        store = sb.stores["memory"]
        att = attribution.Attribution(tmp_path, clock)
        att.seed([])
        att.install(MemoryStorage, ProposalsStore)
        try:
            ms = [Memory(content=f"memory {i}") for i in range(3)]
            for m in ms:
                store.save(m)

            def cleanup_corpus(store, mem_id=None, absorb_id=None):
                store.delete(absorb_id or mem_id)

            cleanup_corpus(store, mem_id=ms[0].id)
            cleanup_corpus(store, absorb_id=ms[1].id)
            store.delete(ms[2].id)
        finally:
            att.uninstall()
    rows = [json.loads(l) for l in open(tmp_path / "transitions.jsonl")]
    losses = {r["id"]: r["cause"] for r in rows if r["to"] == "deleted"}
    assert losses == {ms[0].id: "cleanup_remove", ms[1].id: "cleanup_merge", ms[2].id: "delete_other"}
    texts = attribution.EncLog.read(tmp_path / "texts.enc")
    assert {t["id"] for t in texts if t["kind"] == "lost"} == {m.id for m in ms}
    assert b"memory 0" not in (tmp_path / "texts.enc").read_bytes()


# ------------------------------------------------------------------- resume

def test_resume_after_mid_run_stop_matches_uninterrupted(env):
    full = run(cfg(env, "full", _determinism_events(), ns="res", checkpoint_every=3))
    part = run(cfg(env, "part", _determinism_events(), ns="res", mode="strict",
                   checkpoint_every=3, stop_after=4))
    assert part["status"] == "paused" and part["cursor"] == 4
    with pytest.raises(SystemExit):
        run(cfg(env, "part", _determinism_events(), ns="res", mode="strict", checkpoint_every=3))
    rest = run(cfg(env, "part", _determinism_events(), ns="res", mode="strict",
                   checkpoint_every=3, resume=True))
    assert rest["status"] == "done"
    assert rest["fingerprint"] == full["fingerprint"]
    for n in ("transitions.jsonl", "events.jsonl", "weekly_raw.jsonl"):
        assert (env["tmp"] / "runs" / "part" / n).read_text() == (env["tmp"] / "runs" / "full" / n).read_text()


def test_resume_tolerates_append_only_event_stream_growth(env):
    """A live audit log can gain a row with --until close to "now" while a
    chunk is running.  If everything already replayed (indices < cursor) is
    unchanged, resuming against the longer stream must succeed rather than
    raising SystemExit -- the old strict-equality check treated any length
    change as corpus tampering and would wedge forever near "now"."""
    base = _determinism_events()
    part = run(cfg(env, "grow", base, ns="grow-1", checkpoint_every=3, stop_after=4))
    assert part["status"] == "paused" and part["cursor"] == 4

    extra = list(base) + [ev_propose(T0 + 20 * HOUR, "a new audit row appeared after the checkpoint.")]
    rest = run(cfg(env, "grow", extra, ns="grow-1", checkpoint_every=3, resume=True))
    assert rest["status"] == "done"
    assert rest["n_events"] == len(extra)


def test_resume_still_rejects_a_changed_prefix(env):
    """Growth tolerance must not paper over a genuinely different corpus:
    if an already-replayed event (index < cursor) itself changed, resume
    must still abort loudly."""
    base = _determinism_events()
    part = run(cfg(env, "chg", base, ns="chg-1", checkpoint_every=3, stop_after=4))
    assert part["status"] == "paused" and part["cursor"] == 4

    mutated = list(base)
    mutated[0] = ev_save(mutated[0].t, "a different fact entirely", chat="different-chat")
    mutated.append(ev_propose(T0 + 20 * HOUR, "a new audit row appeared after the checkpoint."))
    with pytest.raises(SystemExit):
        run(cfg(env, "chg", mutated, ns="chg-1", checkpoint_every=3, resume=True))


def test_metrics_scorecard_from_run(env):
    run(cfg(env, "met", _determinism_events(), ns="met"))
    sc = json.loads((env["tmp"] / "runs" / "met" / "scorecard.json").read_text())["final"]
    assert sc["retention"]["ever_active"] >= 6
    assert sc["retention"]["lost_now_by_cause"].get("cleanup_remove", 0) >= 1
    assert "ziya" in sc["fairness"]["by_group"]
    assert sc["structure"]["active"] == sc["retention"]["active"]
    assert sc["volume"]["extract_events"] == 5
    assert (env["tmp"] / "runs" / "met" / "weekly.jsonl").exists()
