"""file_states.json backups are bounded, and saves never expose a partial file.

Before: every reset (an unreadable state file at load) left a
file_states.json.backup.<ts> that nothing ever read or deleted -- 476 files,
3.6 GB on one install.  And _save_state wrote in place (open truncates first),
so another server sharing ~/.ziya could load a partial envelope, fail to
decrypt it and move the live state aside: the source of many of those resets.
"""
import builtins
import json
import os
import threading
import time

import pytest

from app.utils import file_state_manager as fsm_mod
from app.utils.file_state_manager import FileStateManager


def _manager(tmp_path):
    """A manager on a temp state file, without the constructor's home-dir load."""
    m = FileStateManager.__new__(FileStateManager)
    m.state_file = str(tmp_path / "file_states.json")
    m.conversation_states = {}
    m.conversation_diffs = {}
    m._conversation_access_times = {}
    m._lock = threading.Lock()
    return m


def _make_backups(m, n):
    base = time.time() - 10_000
    paths = []
    for i in range(n):
        p = f"{m.state_file}.backup.{1700000000 + i}"
        with open(p, "w") as f:
            f.write("x")
        os.utime(p, (base + i, base + i))
        paths.append(p)
    return paths  # oldest first


def _backups(m):
    d = os.path.dirname(m.state_file)
    return sorted(n for n in os.listdir(d) if n.startswith("file_states.json.backup."))


def test_prune_keeps_newest_and_leaves_other_files(tmp_path):
    m = _manager(tmp_path)
    paths = _make_backups(m, 9)
    live = m.state_file
    tmp_inflight = live + ".tmp.123.456"
    for p in (live, tmp_inflight):
        with open(p, "w") as f:
            f.write("{}")
    removed = m._prune_backups()
    assert removed == 9 - fsm_mod._BACKUP_KEEP
    kept = _backups(m)
    assert kept == sorted(os.path.basename(p) for p in paths[-fsm_mod._BACKUP_KEEP:])
    assert os.path.exists(live) and os.path.exists(tmp_inflight)


def test_load_prunes_existing_backups(tmp_path):
    m = _manager(tmp_path)
    _make_backups(m, 12)
    with open(m.state_file, "w") as f:
        json.dump({}, f)
    m._load_state()
    assert len(_backups(m)) == fsm_mod._BACKUP_KEEP
    # Positive: the readable live file was loaded in place, not moved aside.
    assert os.path.exists(m.state_file)


def test_unreadable_file_is_backed_up_and_backups_stay_bounded(tmp_path):
    m = _manager(tmp_path)
    for _ in range(fsm_mod._BACKUP_KEEP + 3):
        with open(m.state_file, "w") as f:
            f.write("{not json")
        m._load_state()
        assert not os.path.exists(m.state_file)
        assert m.conversation_states == {}
        # Distinct timestamps, so each reset makes its own backup.
        time.sleep(1.01)
    assert len(_backups(m)) == fsm_mod._BACKUP_KEEP


def test_save_replaces_atomically_and_round_trips(tmp_path, monkeypatch):
    m = _manager(tmp_path)
    # A pre-existing live file: an in-place write would truncate it.
    with open(m.state_file, "w") as f:
        json.dump({"old": {}}, f)
    opened = []
    real_open = builtins.open

    def spy(file, mode="r", *a, **k):
        if any(c in mode for c in "wa+"):
            opened.append(os.fspath(file))
        return real_open(file, mode, *a, **k)

    monkeypatch.setattr(builtins, "open", spy)
    m._save_state()
    monkeypatch.setattr(builtins, "open", real_open)

    assert opened, "save wrote nothing"
    assert m.state_file not in opened, "live file opened for writing in place"
    assert all(p.startswith(m.state_file + ".tmp.") for p in opened)
    # No temp files left behind, and the result parses.
    assert not [n for n in os.listdir(tmp_path) if ".tmp." in n]
    with open(m.state_file) as f:
        assert json.load(f) == {}
    if os.name == "posix":
        assert (os.stat(m.state_file).st_mode & 0o777) == 0o600
