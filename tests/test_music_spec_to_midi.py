"""Tests for scripts/music_spec_to_midi.py, the ```music``` spec -> SMF writer.

The assertions read the emitted byte stream rather than the converter's own
return values, because the point of the converter is the file: a bug that
produces a plausible-looking stats dict but an undecodable MIDI file would
otherwise pass.
"""

from __future__ import annotations

import importlib.util
import struct
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "music_spec_to_midi.py"


def _load():
    spec = importlib.util.spec_from_file_location("music_spec_to_midi", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


m2m = _load()


# ------------------------------------------------------------------ helpers ----

def _vlq(body: bytes, index: int) -> tuple[int, int]:
    value = 0
    while True:
        byte = body[index]
        index += 1
        value = (value << 7) | (byte & 0x7F)
        if not byte & 0x80:
            return value, index


def parse(data: bytes) -> dict:
    """Decode an SMF into tempo/meter/note events, asserting it is well-formed."""
    assert data[:4] == b"MThd", "missing MThd"
    fmt, ntrk, tpq = struct.unpack(">HHH", data[8:14])
    pos = 14
    out = {"format": fmt, "tracks": ntrk, "tpq": tpq,
            "tempos": [], "meters": [], "markers": [], "notes": {}}
    for _ in range(ntrk):
        assert data[pos:pos + 4] == b"MTrk", "missing MTrk"
        length = struct.unpack(">I", data[pos + 4:pos + 8])[0]
        body = data[pos + 8:pos + 8 + length]
        pos += 8 + length
        index = 0
        tick = 0
        running = None
        name = None
        ons: list[tuple[int, int, int]] = []
        while index < len(body):
            delta, index = _vlq(body, index)
            tick += delta
            status = body[index]
            if status == 0xFF:
                index += 1
                kind = body[index]
                index += 1
                size, index = _vlq(body, index)
                payload = body[index:index + size]
                index += size
                if kind == 0x03:
                    name = payload.decode("utf-8", "replace")
                elif kind == 0x51:
                    bpm = 60_000_000 / int.from_bytes(payload, "big")
                    out["tempos"].append((tick, round(bpm, 3)))
                elif kind == 0x58:
                    out["meters"].append((tick, f"{payload[0]}/{1 << payload[1]}"))
                elif kind == 0x06:
                    out["markers"].append((tick, payload.decode("utf-8", "replace")))
                continue
            if status & 0x80:
                running = status
                index += 1
            kind = running & 0xF0
            if kind in (0x80, 0x90):
                pitch, velocity = body[index], body[index + 1]
                index += 2
                if kind == 0x90 and velocity > 0:
                    ons.append((tick, pitch, velocity))
            elif kind in (0xA0, 0xB0, 0xE0):
                index += 2
            elif kind in (0xC0, 0xD0):
                index += 1
            else:  # pragma: no cover - would mean a malformed stream
                raise AssertionError(f"unexpected status byte {running:#x}")
        if ons:
            out["notes"][name] = ons
    assert pos == len(data), "trailing bytes after last track"
    return out


def bar(notes):
    return {"notes": notes}


def single(measures, **stave):
    return {
        "keySignature": stave.pop("key", "C"),
        "timeSignature": stave.pop("ts", "4/4"),
        "tempo": stave.pop("tempo", {"duration": "q", "bpm": 120}),
        "staves": [{"name": "P", "clef": "treble",
                    "midi": {"channel": 0}, "measures": measures}],
    }


# -------------------------------------------------------------- pitch/meter ----

def test_bare_key_follows_key_signature():
    """"f/5" is F#5 in F# major but F5 in C major - the signature must apply."""
    sharp = m2m.key_signature_map("F#")
    assert m2m.parse_key("f/5", sharp) == 78          # F#5
    assert m2m.parse_key("b/5", sharp) == 83          # B natural: the one plain degree
    natural = m2m.key_signature_map("C")
    assert m2m.parse_key("f/5", natural) == 77        # F5
    # An explicit accidental overrides the signature in both directions.
    assert m2m.parse_key("fn/5", sharp) == 77
    assert m2m.parse_key("b#/5", natural) == 84


def test_dotted_durations():
    assert m2m.duration_quarters("q") == 1.0
    assert m2m.duration_quarters("qd") == 1.5
    assert m2m.duration_quarters("hdd") == 3.5


def test_measure_totalling_wrong_is_reported_not_silently_shifted():
    """A short bar must warn; staves are still aligned by the global meter map."""
    spec = {
        "keySignature": "C", "timeSignature": "4/4",
        "tempo": {"duration": "q", "bpm": 120},
        "staves": [
            {"name": "A", "clef": "treble", "midi": {"channel": 0}, "measures": [
                bar([{"keys": ["c/4"], "duration": "h"}]),          # short: 2 of 4
                bar([{"keys": ["d/4"], "duration": "w"}]),
            ]},
            {"name": "B", "clef": "treble", "midi": {"channel": 1}, "measures": [
                bar([{"keys": ["e/4"], "duration": "w"}]),
                bar([{"keys": ["f/4"], "duration": "w"}]),
            ]},
        ],
    }
    data, warnings, _stats = m2m.convert(spec, honor_repeats=False)
    assert any("A m.1" in w for w in warnings), warnings
    parsed = parse(data)
    # Bar 2 starts at the same tick in both parts despite the short bar in A.
    assert parsed["notes"]["A"][1][0] == parsed["notes"]["B"][1][0] == 4 * m2m.TPQ


# ---------------------------------------------------------------- tempo map ----

def test_single_tempo_emits_one_tempo_event():
    spec = single([bar([{"keys": ["c/4"], "duration": "w"}])],
                  tempo={"duration": "q", "bpm": 120})
    data, _w, stats = m2m.convert(spec, honor_repeats=False)
    parsed = parse(data)
    assert parsed["tempos"] == [(0, 120.0)]
    assert stats["tempo_changes"] == 0
    assert stats["duration_seconds"] == pytest.approx(2.0, abs=0.01)


def test_measure_tempo_change_emits_event_at_that_measure():
    """A per-measure tempo is what makes multi-section arrangements playable."""
    spec = single([
        bar([{"keys": ["c/4"], "duration": "w"}]),
        {"tempo": {"bpm": 60, "duration": "q", "name": "Adagio"},
         "notes": [{"keys": ["d/4"], "duration": "w"}]},
    ], tempo={"duration": "q", "bpm": 120})
    data, _w, stats = m2m.convert(spec, honor_repeats=False)
    parsed = parse(data)
    assert parsed["tempos"] == [(0, 120.0), (4 * m2m.TPQ, 60.0)]
    assert stats["tempo_changes"] == 1
    # 2s at 120 + 4s at 60: the duration must follow the map, not the first tempo.
    assert stats["duration_seconds"] == pytest.approx(6.0, abs=0.01)
    assert (4 * m2m.TPQ, "Adagio") in parsed["markers"]


def test_tempo_marking_prints_once_not_on_every_later_measure():
    spec = single([
        {"tempo": {"bpm": 90, "duration": "q", "name": "Andante"},
         "notes": [{"keys": ["c/4"], "duration": "w"}]},
        bar([{"keys": ["d/4"], "duration": "w"}]),
        bar([{"keys": ["e/4"], "duration": "w"}]),
    ])
    data, _w, _s = m2m.convert(spec, honor_repeats=False)
    markers = [text for _tick, text in parse(data)["markers"]]
    assert markers.count("Andante") == 1, markers


def test_non_quarter_tempo_beat_is_converted():
    """bpm counts the given beat: 60 half notes per minute is 120 quarter BPM."""
    spec = single([bar([{"keys": ["c/4"], "duration": "w"}])],
                  tempo={"duration": "h", "bpm": 60})
    data, _w, stats = m2m.convert(spec, honor_repeats=False)
    assert parse(data)["tempos"] == [(0, 120.0)]
    assert stats["quarter_bpm"] == 120.0


def test_meter_change_emits_time_signature_and_shortens_the_bar():
    spec = single([
        bar([{"keys": ["c/4"], "duration": "w"}]),
        {"timeSignature": "7/8",
         "notes": [{"keys": ["d/4"], "duration": "h"},
                   {"keys": ["e/4"], "duration": "q"},
                   {"keys": ["f/4"], "duration": "8"}]},
        bar([{"keys": ["g/4"], "duration": "h"},
             {"keys": ["a/4"], "duration": "q"},
             {"keys": ["b/4"], "duration": "8"}]),
    ])
    data, warnings, _s = m2m.convert(spec, honor_repeats=False)
    assert warnings == []
    parsed = parse(data)
    assert parsed["meters"] == [(0, "4/4"), (4 * m2m.TPQ, "7/8")]
    onsets = [tick for tick, _p, _v in parsed["notes"]["P"]]
    # The 7/8 bar occupies 3.5 quarters, so bar 3 starts at 7.5 quarters.
    assert onsets[1] == 4 * m2m.TPQ
    assert onsets[4] == int(7.5 * m2m.TPQ)


# ------------------------------------------------------- dynamics / drums ----

def test_dynamic_persists_and_accent_raises_velocity():
    spec = single([
        bar([{"keys": ["c/4"], "duration": "q", "dynamic": "pp"},
             {"keys": ["d/4"], "duration": "q"},
             {"keys": ["e/4"], "duration": "q", "articulations": ["accent"]},
             {"keys": ["f/4"], "duration": "q", "dynamic": "ff"}]),
    ])
    data, _w, _s = m2m.convert(spec, honor_repeats=False)
    velocities = [v for _t, _p, v in parse(data)["notes"]["P"]]
    assert velocities[0] == velocities[1] == m2m.DYNAMIC_VELOCITY["pp"]
    assert velocities[2] == m2m.DYNAMIC_VELOCITY["pp"] + m2m.ACCENT_BONUS["accent"]
    assert velocities[3] == m2m.DYNAMIC_VELOCITY["ff"]


def test_drum_staff_maps_positions_and_warns_on_unmapped():
    spec = {
        "keySignature": "C", "timeSignature": "4/4",
        "tempo": {"duration": "q", "bpm": 120},
        "staves": [{"name": "Dr", "clef": "percussion",
                    "midi": {"channel": 9, "drums": True}, "measures": [
                        bar([{"keys": ["f/4"], "duration": "h"},
                             {"keys": ["c/5"], "duration": "h"}]),
                        bar([{"keys": ["g/2"], "duration": "w"}]),
                    ]}],
    }
    data, warnings, _s = m2m.convert(spec, honor_repeats=False)
    pitches = [p for _t, p, _v in parse(data)["notes"]["Dr"]]
    assert pitches == [m2m.DRUM_MAP["f/4"], m2m.DRUM_MAP["c/5"]]
    assert any("no drum mapping" in w for w in warnings), warnings


def test_transpose_shifts_sounding_pitch():
    spec = {
        "keySignature": "C", "timeSignature": "4/4",
        "tempo": {"duration": "q", "bpm": 120},
        "staves": [{"name": "G", "clef": "treble",
                    "midi": {"channel": 0, "transpose": -12}, "measures": [
                        bar([{"keys": ["c/4"], "duration": "w"}])]}],
    }
    data, _w, _s = m2m.convert(spec, honor_repeats=False)
    assert parse(data)["notes"]["G"][0][1] == 48  # C4 written -> C3 sounding


def test_repeat_span_is_expanded_and_can_be_disabled():
    measures = [
        {"beginBar": "repeat-begin", "notes": [{"keys": ["c/4"], "duration": "w"}]},
        {"endBar": "repeat-end", "notes": [{"keys": ["d/4"], "duration": "w"}]},
        bar([{"keys": ["e/4"], "duration": "w"}]),
    ]
    spec = single(measures)
    _d, _w, played = m2m.convert(spec, honor_repeats=True)
    assert played["measures_played"] == 5      # 1,2 then 1,2,3
    spec = single([dict(x) for x in measures])
    _d, _w, once = m2m.convert(spec, honor_repeats=False)
    assert once["measures_played"] == 3


def test_empty_spec_raises_rather_than_writing_a_headerless_file():
    with pytest.raises(m2m.ConversionError):
        m2m.convert({"staves": []}, honor_repeats=False)


# ------------------------------------------------------------ real artifacts ----

SCORES = ROOT / "design" / "scores"


@pytest.mark.parametrize("name,expected_bars,expected_seconds", [
    ("die_eiserne_krone.json", 12, 24.87),
    ("eiserne_krone_theme.json", 108, 179.19),
])
def test_committed_scores_convert_without_meter_warnings(
    name, expected_bars, expected_seconds
):
    """Any bar whose contents disagree with its meter is a defect in the score."""
    path = SCORES / name
    if not path.exists():
        pytest.skip(f"{name} not generated")
    import json
    spec = json.loads(path.read_text())
    data, warnings, stats = m2m.convert(spec, honor_repeats=True)
    assert warnings == [], warnings
    assert stats["measures_in_spec"] == expected_bars
    assert stats["duration_seconds"] == pytest.approx(expected_seconds, abs=0.05)
    parse(data)  # must decode cleanly


def test_theme_runs_about_three_minutes_and_changes_tempo():
    path = SCORES / "eiserne_krone_theme.json"
    if not path.exists():
        pytest.skip("theme not generated")
    import json
    _data, _w, stats = m2m.convert(json.loads(path.read_text()), honor_repeats=True)
    assert 170 <= stats["duration_seconds"] <= 190, stats["duration_seconds"]
    assert stats["tempo_changes"] >= 5, "a theme and variations must change tempo"
