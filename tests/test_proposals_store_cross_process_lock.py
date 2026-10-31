"""
ProposalsStore appends must survive concurrent writers in DIFFERENT processes.

The event log is encrypted as one envelope, so every ``_append`` is a full
read-decrypt-rewrite.  The store guarded that with an in-process RLock only
and its own docstring said cross-process coordination was "not provided".
The developer runs 10+ Ziya servers against one ~/.ziya, so two of them
appending in the same window both read N events and both write N+1; the
second rename wins and the first process's event is silently gone.  The live
log carried the fingerprint on 2026-10-25: a duplicate ``record`` event for
one id (two processes proposed the same fact before either saw the other).

The fix is an fcntl advisory lock on a sidecar file around every
read-modify-write.  This test spawns real subprocesses — a threading lock
cannot reproduce the race — and asserts at the outer surface: every
proposal written is present afterwards.  FAILS before the fix (lost
updates: fewer than N open proposals).
"""
import json
import subprocess
import sys
import textwrap

import pytest

from app.storage.proposals import ProposalsStore

N_PROCS = 6
PER_PROC = 25

WRITER = textwrap.dedent("""
    import sys
    sys.path.insert(0, {root!r})
    from app.storage.proposals import ProposalsStore
    from app.models.memory import MemoryProposal
    st = ProposalsStore(memory_dir={mem!r})
    tag, n = sys.argv[1], int(sys.argv[2])
    for i in range(n):
        st.add(MemoryProposal(content=f"fact {{tag}}-{{i}} about subsystem {{tag}}",
                              conversation_id=f"conv-{{tag}}"), activity_count=1)
    print("done", tag)
""")


@pytest.mark.timeout(120)
def test_concurrent_processes_lose_no_appends(tmp_path, monkeypatch):
    # Explicit sandbox home for the children too, so nothing touches the real
    # store even if the autouse fixture's env does not propagate.
    home = tmp_path / "home"
    (home / "memory").mkdir(parents=True)
    monkeypatch.setenv("ZIYA_HOME", str(home))
    import pathlib
    root = str(pathlib.Path(__file__).resolve().parent.parent)
    script = WRITER.format(root=root, mem=str(home / "memory"))
    procs = [
        subprocess.Popen([sys.executable, "-c", script, f"p{k}", str(PER_PROC)],
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                         env={**__import__("os").environ, "ZIYA_HOME": str(home),
                              "ZIYA_EMBEDDING_PROVIDER": "none"})
        for k in range(N_PROCS)
    ]
    for p in procs:
        out, err = p.communicate(timeout=100)
        assert p.returncode == 0, err.decode()[-800:]

    st = ProposalsStore(memory_dir=home / "memory")
    rows = st.list_open()
    assert len(rows) == N_PROCS * PER_PROC, (
        f"lost updates: {N_PROCS * PER_PROC - len(rows)} of "
        f"{N_PROCS * PER_PROC} appends vanished under concurrent writers"
    )
    # Every event is exactly once: a duplicate record is the race's other face.
    events = st._read_lines()
    rec_ids = [e["id"] for e in events if e.get("kind") == "record"]
    assert len(rec_ids) == len(set(rec_ids))


def test_lock_file_lives_beside_the_log(tmp_path):
    # The lock must be a sibling of probationary.jsonl, so every process
    # that resolves the same memory dir contends on the same file.
    st = ProposalsStore(memory_dir=tmp_path / "memory")
    from app.models.memory import MemoryProposal
    st.add(MemoryProposal(content="one fact", conversation_id="c"), activity_count=1)
    assert (tmp_path / "memory" / "probationary.jsonl.lock").exists()


def test_single_process_behaviour_unchanged(tmp_path):
    # Positive control for the fix being purely a serialization change.
    from app.models.memory import MemoryProposal
    st = ProposalsStore(memory_dir=tmp_path / "memory")
    pid = st.add(MemoryProposal(content="x", conversation_id="a"), activity_count=1)
    assert st.add(MemoryProposal(content="x", conversation_id="b"), activity_count=1) == pid
    assert st.get(pid)["corroborations"] == 1
    assert st.mark_archived(pid, reason="test") is True
    # Two terminal rows, keep one: the older's events must be pruned.
    # (max_terminal_records=0 is NOT a valid "prune all": terminal_ids[-0:]
    # is the whole list, so 0 keeps everything — a pre-existing quirk,
    # irrelevant in production where the value is PRUNE_TERMINAL_MAX=500.)
    pid2 = st.add(MemoryProposal(content="y", conversation_id="a"), activity_count=1)
    assert st.mark_archived(pid2, reason="test") is True
    assert st.prune_terminal(max_terminal_records=1) >= 1
    assert st.get(pid) is None and st.get(pid2) is not None
