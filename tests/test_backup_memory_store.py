"""scripts/backup_memory_store.py: durable, verifiable store snapshots.

All 2026-09-25..27 memory backups lived in /tmp and were purged over a
weekend.  Pinned here: a snapshot is byte-identical to the source and its
manifest detects tampering; pruning drops only unlabelled snapshots; the
scratch sandbox dir is not captured.
"""
import importlib.util
import json
import os

_spec = importlib.util.spec_from_file_location(
    "bk", os.path.join(os.path.dirname(__file__), "..", "scripts", "backup_memory_store.py"))
bk = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bk)


def _store(tmp_path):
    src = tmp_path / "memory"
    src.mkdir()
    (src / "memories.json").write_bytes(b"ZIYA-ALE-V1\x00encrypted-bytes")
    (src / "probationary.jsonl").write_bytes(b"\x01\x02\x03")
    (src / "priming-sandbox").mkdir()
    (src / "priming-sandbox" / "junk.json").write_text("{}")
    return src


def test_snapshot_is_verifiable_and_skips_scratch(tmp_path):
    src = _store(tmp_path)
    dest = bk.snapshot(src, tmp_path / "bk")
    m = json.loads((dest / "manifest.json").read_text())
    assert set(m["files"]) == {"memories.json", "probationary.jsonl"}
    assert (dest / "memories.json").read_bytes() == (src / "memories.json").read_bytes()
    assert bk.verify(dest) == []
    (dest / "probationary.jsonl").write_bytes(b"\x01\x02")        # tamper
    assert bk.verify(dest) == ["probationary.jsonl"]


def test_prune_keeps_labelled_and_newest(tmp_path, monkeypatch):
    src = _store(tmp_path)
    root = tmp_path / "bk"
    stamps = iter(["20260101-000000", "20260102-000000", "20260103-000000", "20260104-000000"])
    monkeypatch.setattr(bk.time, "strftime", lambda *_: next(stamps))
    bk.snapshot(src, root, keep=99)
    bk.snapshot(src, root, label="pre-partc", keep=99)
    bk.snapshot(src, root, keep=99)
    bk.snapshot(src, root, keep=2)                                # prunes the oldest unlabelled
    names = sorted(p.name for p in root.iterdir())
    assert names == ["20260102-000000-pre-partc", "20260103-000000", "20260104-000000"]
