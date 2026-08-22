"""Tests for converter v2 additions: per-note velocity override, extended drum map.

Standalone: loads scripts/music_spec_to_midi.py via importlib so it does not
depend on packaging, mirroring tests/test_music_spec_to_midi.py.
"""
import importlib.util
import struct
from pathlib import Path

_PATH = Path(__file__).resolve().parent.parent / "scripts" / "music_spec_to_midi.py"
_loader_spec = importlib.util.spec_from_file_location("m2m_v2", _PATH)
m2m = importlib.util.module_from_spec(_loader_spec)
_loader_spec.loader.exec_module(m2m)


def _vlq(b: bytes, i: int):
    v = 0
    while True:
        c = b[i]
        i += 1
        v = (v << 7) | (c & 0x7F)
        if not c & 0x80:
            return v, i


def _note_ons(data: bytes):
    """[(track_index, pitch, velocity)] for every note-on with velocity > 0."""
    assert data[:4] == b"MThd"
    _fmt, ntrk, _tpq = struct.unpack(">HHH", data[8:14])
    pos = 14
    out = []
    for t in range(ntrk):
        assert data[pos:pos + 4] == b"MTrk"
        ln = struct.unpack(">I", data[pos + 4:pos + 8])[0]
        body = data[pos + 8:pos + 8 + ln]
        pos += 8 + ln
        i = 0
        running = None
        while i < len(body):
            _, i = _vlq(body, i)
            status = body[i]
            if status == 0xFF:
                i += 2  # 0xFF + meta type
                length, i = _vlq(body, i)
                i += length
                continue
            if status & 0x80:
                running = status
                i += 1
            hi = running & 0xF0
            if hi in (0x80, 0x90):
                pitch, vel = body[i], body[i + 1]
                i += 2
                if hi == 0x90 and vel > 0:
                    out.append((t, pitch, vel))
            elif hi in (0xA0, 0xB0, 0xE0):
                i += 2
            elif hi in (0xC0, 0xD0):
                i += 1
            else:
                raise AssertionError(f"bad status byte {running:#x}")
    return out


def _one_stave(notes, midi=None):
    return {
        "keySignature": "C",
        "timeSignature": "4/4",
        "staves": [{
            "name": "P",
            "clef": "treble",
            "midi": midi or {"channel": 0},
            "measures": [{"notes": notes}],
        }],
    }


def test_velocity_override_wins_over_dynamic():
    spec = _one_stave([
        {"keys": ["c/4"], "duration": "q", "dynamic": "ff", "velocity": 41},
        {"keys": ["c/4"], "duration": "q", "dynamic": "ff"},
        {"keys": ["c/4"], "duration": "h", "velocity": 300},  # clamps to 127
    ])
    data, warnings, _ = m2m.convert(spec, honor_repeats=False)
    vels = [v for _, _, v in _note_ons(data)]
    assert vels[0] == 41
    assert vels[1] == m2m.DYNAMIC_VELOCITY["ff"]
    assert vels[2] == 127
    assert not warnings


def test_velocity_absent_keeps_dynamic_and_accent_path():
    spec = _one_stave([
        {"keys": ["c/4"], "duration": "h", "dynamic": "p"},
        {"keys": ["c/4"], "duration": "h", "articulations": ["accent"]},
    ])
    data, _, _ = m2m.convert(spec, honor_repeats=False)
    vels = [v for _, _, v in _note_ons(data)]
    assert vels[0] == m2m.DYNAMIC_VELOCITY["p"]
    assert vels[1] == m2m.DYNAMIC_VELOCITY["p"] + m2m.ACCENT_BONUS["accent"]


def test_velocity_override_suppresses_accent_bonus():
    # Override is the FINAL value: articulation heuristics must not stack.
    spec = _one_stave([
        {"keys": ["c/4"], "duration": "w",
         "articulations": ["marcato"], "velocity": 70},
    ])
    data, _, _ = m2m.convert(spec, honor_repeats=False)
    assert _note_ons(data)[0][2] == 70


def test_extended_drum_map():
    hits = [
        ("f/4", 36), ("c/5", 38), ("e/5", 42), ("g/5", 49), ("a/5", 52),
        ("g/4", 41), ("a/4", 43), ("b/4", 45), ("d/5", 47), ("f/5", 46),
        ("b/5", 51),
    ]
    notes = [{"keys": [k], "duration": "16"} for k, _ in hits]
    notes.append({"rest": True, "duration": "q"})     # 11*0.25 + 1.0
    notes.append({"rest": True, "duration": "16"})    # + 0.25 = 4.0
    spec = _one_stave(notes, midi={"channel": 9, "drums": True})
    data, warnings, _ = m2m.convert(spec, honor_repeats=False)
    assert [p for _, p, _ in _note_ons(data)] == [p for _, p in hits]
    assert not warnings


def test_velocity_override_applies_to_drums():
    # Ghost notes need shaped drum velocities too.
    spec = _one_stave(
        [{"keys": ["c/5"], "duration": "w", "velocity": 30}],
        midi={"channel": 9, "drums": True},
    )
    data, _, _ = m2m.convert(spec, honor_repeats=False)
    assert _note_ons(data)[0][2] == 30


def test_both_dot_grammars_accepted():
    # Engraver grammar: trailing "."; converter's historical grammar: "d".
    assert m2m.duration_quarters("q.") == 1.5
    assert m2m.duration_quarters("qd") == 1.5
    assert m2m.duration_quarters("8.") == 0.75
    assert m2m.duration_quarters("8..") == 0.875
    spec = _one_stave([
        {"keys": ["c/4"], "duration": "8."},
        {"keys": ["c/4"], "duration": "16"},
        {"keys": ["c/4"], "duration": "q"},
        {"keys": ["c/4"], "duration": "h"},
    ])
    _, warnings, _ = m2m.convert(spec, honor_repeats=False)
    assert not warnings  # bar sums to exactly 4 quarters
