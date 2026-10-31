"""In-memory candidate filter for chat search (app/utils/chat_search_index.py).

Before: every search opened, decrypted and parsed every chat file in scope
(~9 s for all projects on a 2,350-chat install).  Overlapping searches, one
per debounced keystroke, went past the browser's 25 s deadline and the
client fell back to a partial IndexedDB scan: "searches miss terms".

The filter only decides which files the unchanged matcher reads, so the
property that matters is equivalence: results with the index equal results
of a full scan, for every query shape the matcher accepts.
"""
import json
import os
import random
import threading
import time

import pytest

from app.storage import chat_search as chat_search_mod
from app.storage.chat_search import compile_query, search_chats
from app.utils import chat_search_index as csi
from tests.test_chat_history_tools import NOW_MS, _msg, env  # noqa: F401

TRICKY = {
    "c_mono": "the counter is nonmonotonic per key_id bin",
    "c_stall": "log line: ⏱️  STALL detected after 30s",
    "c_longs": "an old ſtall spelled with a long s",
    "c_kelvin": "temperature in \u212aelvin units",
    "c_turk": "flew to \u0130stanbul then home",
    "c_wrap": "phrase split foo\n\nbar across lines",
    "c_punct": "loss was 2.1% via file_read: app/x.py",
    "c_hex": "commit E352B804 fixed it",
    "c_short": "ab cd",
}


@pytest.fixture
def corpus(env):
    chats = env["ziya_home"] / "projects" / env["pid_b"] / "chats"
    for i, (cid, body) in enumerate(TRICKY.items()):
        rec = {"id": cid, "title": f"T {cid}", "messages": [
            _msg("human", "question", NOW_MS - 10 + i),
            _msg("assistant", [{"type": "text", "text": body}], NOW_MS - 5 + i),
        ], "createdAt": NOW_MS - 100, "lastActiveAt": NOW_MS - 50 + i,
            "_version": NOW_MS}
        (chats / f"{cid}.json").write_text(json.dumps(rec))
    csi.get_index().clear()
    yield env
    csi.get_index().clear()


def _search(env, q, monkeypatch, indexed, **kw):
    monkeypatch.setenv("ZIYA_DISABLE_CHAT_SEARCH_INDEX", "" if indexed else "1")
    return [(r["conversationId"], r["totalMatches"]) for r in
            search_chats(env["ziya_home"], env["pid_a"], q, all_projects=True, **kw)]


QUERIES = ["monotonic", "MONOTONIC", "stall", "⏱️ STALL", "kelvin", "istanbul",
           "foo bar", "file_read:", "2.1%", "ab", "e352b804", "widget_parser",
           "zzzqqq never present", "per key_id"]


@pytest.mark.parametrize("q", QUERIES)
@pytest.mark.parametrize("case_sensitive", [False, True])
def test_indexed_results_equal_full_scan(corpus, monkeypatch, q, case_sensitive):
    full = _search(corpus, q, monkeypatch, indexed=False, case_sensitive=case_sensitive)
    fast = _search(corpus, q, monkeypatch, indexed=True, case_sensitive=case_sensitive)
    assert fast == full


def test_equivalence_set_is_not_vacuous(corpus, monkeypatch):
    # Positive control: the fold cases really do match under the old scan.
    for q, cid in [("stall", "c_longs"), ("kelvin", "c_kelvin"),
                   ("istanbul", "c_turk"), ("monotonic", "c_mono"),
                   ("foo bar", "c_wrap"), ("e352b804", "c_hex")]:
        assert cid in {c for c, _ in _search(corpus, q, monkeypatch, indexed=False)}, q


def test_filter_never_drops_a_true_match_randomised():
    rnd = random.Random(1234)
    alphabet = "abcXYZ019_ -.:/\n\tſ\u212a\u0130\u0131é⏱️"
    for _ in range(600):
        doc = "".join(rnd.choice(alphabet) for _ in range(rnd.randint(5, 80)))
        i = rnd.randrange(len(doc))
        q = doc[i:i + rnd.randint(1, 20)]
        if not q.strip() or not compile_query(q).search(doc):
            continue
        bm = csi.text_bitmap(doc)
        for c in csi.query_codes(q):
            assert bm[c >> 3] & (1 << (c & 7)), (doc, q)


def test_second_search_reads_only_candidates(corpus, monkeypatch):
    monkeypatch.delenv("ZIYA_DISABLE_CHAT_SEARCH_INDEX", raising=False)
    search_chats(corpus["ziya_home"], corpus["pid_a"], "warmup", all_projects=True)
    reads = []
    real = chat_search_mod._read_chat_data
    monkeypatch.setattr(chat_search_mod, "_read_chat_data",
                        lambda p: reads.append(p.stem) or real(p))
    hits = search_chats(corpus["ziya_home"], corpus["pid_a"], "monotonic",
                        all_projects=True)
    assert [h["conversationId"] for h in hits] == ["c_mono"]
    assert reads == ["c_mono"]


def test_query_without_trigrams_falls_back_to_full_scan(corpus, monkeypatch):
    assert csi.query_codes("2.1%") == [] and csi.query_codes("ab") == []
    assert csi.candidate_files(corpus["ziya_home"], corpus["pid_a"], True, "ab") is None
    # ...and the full scan still finds it.
    assert ("c_short", 1) in _search(corpus, "ab", monkeypatch, indexed=True)


def test_changed_new_deleted_and_inactive_chats(corpus, monkeypatch):
    monkeypatch.delenv("ZIYA_DISABLE_CHAT_SEARCH_INDEX", raising=False)
    home, pa = corpus["ziya_home"], corpus["pid_a"]
    chats = home / "projects" / corpus["pid_b"] / "chats"
    ids = lambda q: {h["conversationId"] for h in search_chats(home, pa, q, all_projects=True)}
    assert ids("quokka") == set()

    rec = json.loads((chats / "c_mono.json").read_text())
    rec["messages"].append(_msg("assistant", "a quokka appeared", NOW_MS))
    (chats / "c_mono.json").write_text(json.dumps(rec))
    new = {**rec, "id": "c_new", "messages": [_msg("human", "another quokka", NOW_MS)]}
    (chats / "c_new.json").write_text(json.dumps(new))
    assert ids("quokka") == {"c_mono", "c_new"}

    (chats / "c_new.json").unlink()
    rec["isActive"] = False
    (chats / "c_mono.json").write_text(json.dumps(rec))
    assert ids("quokka") == set()
    assert not any(k.endswith("c_new.json") for k in csi.get_index()._entries)


def test_encrypted_chat_is_indexed(corpus, monkeypatch):
    monkeypatch.delenv("ZIYA_DISABLE_CHAT_SEARCH_INDEX", raising=False)
    hits = search_chats(corpus["ziya_home"], corpus["pid_a"], "imported",
                        all_projects=True)
    assert [h["conversationId"] for h in hits] == [corpus["beta"]]


def test_project_scope_is_respected(corpus, monkeypatch):
    monkeypatch.delenv("ZIYA_DISABLE_CHAT_SEARCH_INDEX", raising=False)
    assert search_chats(corpus["ziya_home"], corpus["pid_a"], "monotonic") == []
    hits = search_chats(corpus["ziya_home"], corpus["pid_b"], "monotonic")
    assert [h["conversationId"] for h in hits] == ["c_mono"]


def test_concurrent_cold_searches_build_once(corpus, monkeypatch):
    monkeypatch.delenv("ZIYA_DISABLE_CHAT_SEARCH_INDEX", raising=False)
    built = []
    real = csi.text_bitmap
    monkeypatch.setattr(csi, "text_bitmap", lambda t: built.append(1) or real(t))
    threads = [threading.Thread(target=search_chats, args=(
        corpus["ziya_home"], corpus["pid_a"], "monotonic"), kwargs={"all_projects": True})
        for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    n_files = sum(1 for p in (corpus["ziya_home"] / "projects").glob("*/chats/*.json")
                  if not p.name.startswith("_"))
    assert len(built) == n_files


def test_memory_is_one_bitmap_per_chat(corpus, monkeypatch):
    monkeypatch.delenv("ZIYA_DISABLE_CHAT_SEARCH_INDEX", raising=False)
    search_chats(corpus["ziya_home"], corpus["pid_a"], "warmup", all_projects=True)
    idx = csi.get_index()
    assert len(idx) > 0
    assert all(len(e.bitmap) == csi.BITMAP_BYTES == 6332 for e in idx._entries.values())


def test_unreadable_file_is_cached_then_retried(corpus, monkeypatch):
    monkeypatch.delenv("ZIYA_DISABLE_CHAT_SEARCH_INDEX", raising=False)
    home, pa = corpus["ziya_home"], corpus["pid_a"]
    bad = home / "projects" / corpus["pid_b"] / "chats" / "c_bad.json"
    good = json.dumps({"id": "c_bad", "title": "t",
                       "messages": [_msg("human", "a quokka", NOW_MS)]})
    # Same size as the readable version, so restoring it with the same
    # mtime leaves the stamp unchanged: only the retry window can find it.
    bad.write_bytes(b"\x95" * len(good.encode()))
    reads = []
    real = chat_search_mod._read_chat_data
    monkeypatch.setattr(chat_search_mod, "_read_chat_data",
                        lambda p: reads.append(p.stem) or real(p))
    search_chats(home, pa, "quokka", all_projects=True)
    assert reads.count("c_bad") == 1
    reads.clear()
    search_chats(home, pa, "quokka", all_projects=True)
    assert "c_bad" not in reads            # cached failure, not re-read
    st = bad.stat()
    bad.write_text(good)
    os.utime(bad, ns=(st.st_atime_ns, st.st_mtime_ns))
    assert bad.stat().st_size == st.st_size
    # Within the window the cached failure still hides it...
    assert search_chats(home, pa, "quokka", all_projects=True) == []
    # ...past it, the file is read again and found.
    csi.get_index()._entries[str(bad)].failed_at -= csi.UNREADABLE_RETRY_S + 1
    hits = search_chats(home, pa, "quokka", all_projects=True)
    assert [h["conversationId"] for h in hits] == ["c_bad"]


def test_kill_switch(corpus, monkeypatch):
    monkeypatch.setenv("ZIYA_DISABLE_CHAT_SEARCH_INDEX", "1")
    assert csi.candidate_files(corpus["ziya_home"], corpus["pid_a"], True, "monotonic") is None
