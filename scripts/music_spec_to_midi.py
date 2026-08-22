#!/usr/bin/env python3
"""
Convert a Ziya ```music``` spec (the JSON schema used by the inline music
renderer) into a Standard MIDI File.  No third-party dependencies: the SMF
byte stream is emitted directly.

Usage:
    python3 scripts/music_spec_to_midi.py SPEC.json [-o OUT.mid] [--no-repeats]

Design notes
------------
Measure start ticks are computed from a GLOBAL meter map rather than by
accumulating each stave's own note durations.  Two reasons:

  1. Staves cannot drift relative to one another even if one measure in the
     source spec does not add up to its meter.
  2. A measure whose contents disagree with the meter becomes a reportable
     validation error instead of a silent timing shift in one part only.

Pitch spelling follows the key signature: a bare key like "f/5" in F# major
sounds F#5.  An explicit accidental in the key ("f#/5", "bn/4", "eb/4")
overrides the signature for that note.
"""

from __future__ import annotations

import argparse
import json
import struct
import sys
from pathlib import Path

TPQ = 480  # ticks per quarter note

# ---------------------------------------------------------------- pitch ----

LETTER_SEMITONE = {"c": 0, "d": 2, "e": 4, "f": 5, "g": 7, "a": 9, "b": 11}

SHARP_ORDER = ["f", "c", "g", "d", "a", "e", "b"]
FLAT_ORDER = ["b", "e", "a", "d", "g", "c", "f"]

# Number of sharps (positive) or flats (negative) per major-key name.
KEY_FIFTHS = {
    "C": 0, "G": 1, "D": 2, "A": 3, "E": 4, "B": 5, "F#": 6, "C#": 7,
    "F": -1, "Bb": -2, "Eb": -3, "Ab": -4, "Db": -5, "Gb": -6, "Cb": -7,
}


def key_signature_map(name: str) -> dict[str, int]:
    """Return {letter: semitone offset} implied by a major key signature."""
    fifths = KEY_FIFTHS.get((name or "C").strip(), 0)
    acc = {letter: 0 for letter in LETTER_SEMITONE}
    if fifths > 0:
        for letter in SHARP_ORDER[:fifths]:
            acc[letter] = 1
    elif fifths < 0:
        for letter in FLAT_ORDER[:-fifths]:
            acc[letter] = -1
    return acc


def parse_key(key: str, keysig: dict[str, int]) -> int:
    """Parse a VexFlow-style key ("f/5", "bb/3", "c#/6") to a MIDI number."""
    token, _, octave_txt = key.strip().lower().partition("/")
    if not token or not octave_txt:
        raise ValueError(f"malformed key {key!r}")
    letter, accidental_txt = token[0], token[1:]
    if letter not in LETTER_SEMITONE:
        raise ValueError(f"unknown note letter in {key!r}")

    if accidental_txt in ("", None):
        offset = keysig.get(letter, 0)
    elif accidental_txt == "n":
        offset = 0
    elif set(accidental_txt) == {"#"}:
        offset = len(accidental_txt)
    elif set(accidental_txt) == {"b"}:
        offset = -len(accidental_txt)
    else:
        raise ValueError(f"unknown accidental {accidental_txt!r} in {key!r}")

    return (int(octave_txt) + 1) * 12 + LETTER_SEMITONE[letter] + offset


# General MIDI percussion mapping for the drum staff.  Keys are the staff
# positions used in the score, not real pitches.
DRUM_MAP = {
    "f/4": 36,   # bottom space  -> kick
    "c/5": 38,   # third space   -> snare
    "e/5": 42,   # closed hi-hat
    "g/5": 49,   # above staff   -> crash
    "a/5": 52,   # china
    "g/4": 41,   # low floor tom
    "a/4": 43,   # high floor tom
    "b/4": 45,   # low tom
    "d/5": 47,   # low-mid tom
    "f/5": 46,   # open hi-hat
    "b/5": 51,   # ride
}

# ------------------------------------------------------------- duration ----

BASE_DURATION = {
    "w": 4.0, "h": 2.0, "q": 1.0,
    "8": 0.5, "16": 0.25, "32": 0.125, "64": 0.0625,
}


def duration_quarters(token: str) -> float:
    """Duration in quarter notes.  Trailing 'd's are augmentation dots."""
    token = str(token).strip().lower()
    dots = 0
    # Two dot grammars exist in the wild: the inline engraver's trailing "."
    # (its sanitizeDuration parses /\.*$/) and this converter's historical
    # trailing "d".  Accept both so one spec can serve both consumers.
    while token.endswith("d") or token.endswith("."):
        dots += 1
        token = token[:-1]
    if token.endswith("r"):  # some specs mark rests in the duration
        token = token[:-1]
    if token not in BASE_DURATION:
        raise ValueError(f"unknown duration {token!r}")
    value = BASE_DURATION[token]
    return value * (2.0 - 0.5 ** dots)


DYNAMIC_VELOCITY = {
    "ppp": 20, "pp": 32, "p": 45, "mp": 58,
    "mf": 72, "f": 88, "ff": 105, "fff": 120,
}

ACCENT_BONUS = {"accent": 14, "marcato": 20, "tenuto": 4}
GATE = {"staccato": 0.50, "staccatissimo": 0.35}
DEFAULT_GATE = 0.92


# ---------------------------------------------------------- SMF writing ----

def vlq(value: int) -> bytes:
    """MIDI variable-length quantity."""
    if value < 0:
        raise ValueError("negative delta time")
    out = bytearray([value & 0x7F])
    value >>= 7
    while value:
        out.append((value & 0x7F) | 0x80)
        value >>= 7
    return bytes(reversed(out))


def chunk(tag: bytes, body: bytes) -> bytes:
    return tag + struct.pack(">I", len(body)) + body


def track_bytes(events: list[tuple[int, int, bytes]]) -> bytes:
    """events: (tick, priority, raw_message).  Priority orders same-tick msgs."""
    events = sorted(events, key=lambda e: (e[0], e[1]))
    out = bytearray()
    last = 0
    for tick, _prio, message in events:
        out += vlq(tick - last) + message
        last = tick
    out += vlq(0) + b"\xff\x2f\x00"  # end of track
    return chunk(b"MTrk", bytes(out))


def meta_text(kind: int, text: str) -> bytes:
    payload = text.encode("utf-8")[:127]
    return bytes([0xFF, kind, len(payload)]) + payload


# ------------------------------------------------------------ conversion ----

class ConversionError(Exception):
    pass


def build_meter_map(staves: list[dict], default_ts: str) -> list[tuple[int, int]]:
    """One (numerator, denominator) per measure index, carried forward."""
    count = max(len(s.get("measures", [])) for s in staves)
    declared: list[str | None] = [None] * count
    for stave in staves:
        for index, measure in enumerate(stave.get("measures", [])):
            ts = measure.get("timeSignature")
            if ts and declared[index] is None:
                declared[index] = ts

    meters: list[tuple[int, int]] = []
    current = default_ts or "4/4"
    for index in range(count):
        if declared[index]:
            current = declared[index]
        numerator, _, denominator = current.partition("/")
        meters.append((int(numerator), int(denominator)))
    return meters


def quarter_bpm(tempo: dict) -> float:
    """Quarter-note BPM from a tempo dict, whose beat may be h/q/8/qd..."""
    bpm = float(tempo["bpm"])
    return bpm * duration_quarters(tempo.get("duration", "q"))


def build_tempo_map(
    staves: list[dict], spec: dict, count: int
) -> tuple[list[float], list[str | None]]:
    """One quarter-BPM value and optional marking per measure, carried forward.

    A measure may carry its own {"tempo": {...}} to change tempo mid-piece,
    which is what makes a multi-section arrangement (slow variation, faster
    reprise) playable from a single spec.
    """
    declared: list[dict | None] = [None] * count
    for stave in staves:
        for index, measure in enumerate(stave.get("measures", [])):
            tempo = measure.get("tempo")
            if isinstance(tempo, dict) and tempo.get("bpm") and declared[index] is None:
                declared[index] = tempo

    spec_tempo = spec.get("tempo")
    current = 120.0
    marking: str | None = None
    if isinstance(spec_tempo, dict) and spec_tempo.get("bpm"):
        current = quarter_bpm(spec_tempo)
        marking = spec_tempo.get("name")

    bpms: list[float] = []
    names: list[str | None] = []
    for index in range(count):
        if declared[index]:
            current = quarter_bpm(declared[index])
            marking = declared[index].get("name")
        bpms.append(current)
        names.append(marking)
        marking = None  # a marking prints once, at its own measure
    return bpms, names


def measure_ticks(meter: tuple[int, int]) -> int:
    numerator, denominator = meter
    return int(round(numerator * (4.0 / denominator) * TPQ))


def playback_order(staves: list[dict], count: int, honor_repeats: bool) -> list[int]:
    """Measure indices in playing order, expanding a single repeated span."""
    if not honor_repeats:
        return list(range(count))

    begin = None
    end = None
    for stave in staves:
        for index, measure in enumerate(stave.get("measures", [])):
            if measure.get("beginBar") == "repeat-begin" and begin is None:
                begin = index
            if measure.get("endBar") == "repeat-end" and end is None:
                end = index
    if begin is None or end is None or end < begin:
        return list(range(count))
    return list(range(0, end + 1)) + list(range(begin, count))


def stave_events(
    stave: dict,
    order: list[int],
    starts: list[int],
    meters: list[tuple[int, int]],
    keysig: dict[str, int],
    warnings: list[str],
) -> tuple[list[tuple[int, int, bytes]], int]:
    cfg = stave.get("midi", {}) or {}
    channel = int(cfg.get("channel", 0)) & 0x0F
    transpose = int(cfg.get("transpose", 0))
    is_drums = bool(cfg.get("drums")) or channel == 9
    measures = stave.get("measures", [])
    name = stave.get("name", "?")

    events: list[tuple[int, int, bytes]] = []
    events.append((0, 0, meta_text(0x03, name)))
    if not is_drums and "program" in cfg:
        program = int(cfg["program"]) & 0x7F
        events.append((0, 1, bytes([0xC0 | channel, program])))

    velocity = DYNAMIC_VELOCITY["mf"]
    note_count = 0
    last_index = order[-1] if order else -1

    for slot, index in enumerate(order):
        if index >= len(measures):
            continue
        measure = measures[index]
        bar_start = starts[slot]
        bar_len = measure_ticks(meters[index])
        cursor = 0

        for entry in measure.get("notes", []):
            try:
                span = int(round(duration_quarters(entry.get("duration", "q")) * TPQ))
            except ValueError as exc:
                raise ConversionError(f"{name} m.{index + 1}: {exc}") from exc

            if entry.get("dynamic"):
                velocity = DYNAMIC_VELOCITY.get(entry["dynamic"], velocity)

            if entry.get("rest"):
                cursor += span
                continue

            articulations = entry.get("articulations", []) or []
            ornaments = entry.get("ornaments", []) or []

            note_velocity = velocity
            for articulation in articulations:
                note_velocity += ACCENT_BONUS.get(articulation, 0)
            # A note-level "velocity" is an absolute override: a generator
            # that phrase-shapes and humanizes computes the FINAL value, so
            # layering the dynamic/articulation heuristics on top of it
            # would double-count the accents it already applied.
            override = entry.get("velocity")
            if override is not None:
                note_velocity = int(override)
            note_velocity = max(1, min(127, note_velocity))

            gate = DEFAULT_GATE
            for articulation in articulations:
                if articulation in GATE:
                    gate = GATE[articulation]

            sounding = span
            if index == last_index and any(
                a.startswith("fermata") for a in articulations
            ):
                sounding = int(span * 1.75)

            keys = entry.get("keys", []) or []
            stagger = 0
            if entry.get("arpeggio") and len(keys) > 1:
                stagger = max(1, min(TPQ // 8, span // (len(keys) * 3)))

            for position, key in enumerate(keys):
                if is_drums:
                    pitch = DRUM_MAP.get(key.strip().lower())
                    if pitch is None:
                        warnings.append(
                            f"{name} m.{index + 1}: no drum mapping for {key!r}"
                        )
                        continue
                else:
                    try:
                        pitch = parse_key(key, keysig) + transpose
                    except ValueError as exc:
                        raise ConversionError(
                            f"{name} m.{index + 1}: {exc}"
                        ) from exc
                    if not 0 <= pitch <= 127:
                        warnings.append(
                            f"{name} m.{index + 1}: {key} out of MIDI range"
                        )
                        continue

                onset = bar_start + cursor + position * stagger

                if is_drums:
                    events.append((onset, 5, bytes([0x90 | channel, pitch, note_velocity])))
                    events.append((onset + TPQ // 8, 4, bytes([0x80 | channel, pitch, 0])))
                    note_count += 1
                    continue

                if "trill" in ornaments and span >= TPQ:
                    step = TPQ // 4
                    upper = min(127, pitch + 2)
                    alternation = 0
                    while alternation * step < sounding:
                        this = pitch if alternation % 2 == 0 else upper
                        at = onset + alternation * step
                        events.append((at, 5, bytes([0x90 | channel, this, note_velocity])))
                        events.append(
                            (at + int(step * 0.9), 4, bytes([0x80 | channel, this, 0]))
                        )
                        alternation += 1
                        note_count += 1
                    continue

                release = onset + max(1, int(sounding * gate))
                events.append((onset, 5, bytes([0x90 | channel, pitch, note_velocity])))
                events.append((release, 4, bytes([0x80 | channel, pitch, 0])))
                note_count += 1

            cursor += span

        if cursor != bar_len:
            warnings.append(
                f"{name} m.{index + 1}: contents total {cursor / TPQ:g} quarters "
                f"but meter {meters[index][0]}/{meters[index][1]} wants "
                f"{bar_len / TPQ:g}"
            )

    return events, note_count


def convert(spec: dict, honor_repeats: bool) -> tuple[bytes, list[str], dict]:
    staves = spec.get("staves") or []
    if not staves:
        raise ConversionError("spec contains no staves")

    keysig = key_signature_map(spec.get("keySignature", "C"))
    meters = build_meter_map(staves, spec.get("timeSignature", "4/4"))
    count = len(meters)

    spec_midi = spec.get("midi", {}) or {}
    if "repeats" in spec_midi and honor_repeats:
        honor_repeats = bool(spec_midi["repeats"])
    order = playback_order(staves, count, honor_repeats)

    tempos, tempo_names = build_tempo_map(staves, spec, count)

    starts: list[int] = []
    tick = 0
    total_seconds = 0.0
    for index in order:
        starts.append(tick)
        span = measure_ticks(meters[index])
        tick += span
        total_seconds += span / TPQ * 60.0 / tempos[index]

    # Conductor track: names, tempo, and a time signature at every change.
    conductor: list[tuple[int, int, bytes]] = []
    title = spec.get("title") or "Untitled"
    if spec.get("subtitle"):
        title = f"{title} - {spec['subtitle']}"
    conductor.append((0, 0, meta_text(0x03, title)))
    if spec.get("composer"):
        conductor.append((0, 0, meta_text(0x02, spec["composer"])))

    previous_bpm: float | None = None
    tempo_changes = 0
    for slot, index in enumerate(order):
        bpm = tempos[index]
        if bpm != previous_bpm:
            micros = int(round(60_000_000.0 / bpm))
            conductor.append(
                (starts[slot], 1, b"\xff\x51\x03" + micros.to_bytes(3, "big"))
            )
            if previous_bpm is not None:
                tempo_changes += 1
            previous_bpm = bpm
        if tempo_names[index]:
            conductor.append((starts[slot], 0, meta_text(0x06, str(tempo_names[index]))))

    fifths = KEY_FIFTHS.get(str(spec.get("keySignature", "C")).strip(), 0)
    conductor.append(
        (0, 1, b"\xff\x59\x02" + bytes([fifths & 0xFF, 0]))
    )

    previous: tuple[int, int] | None = None
    for slot, index in enumerate(order):
        numerator, denominator = meters[index]
        if (numerator, denominator) != previous:
            power = max(0, denominator.bit_length() - 1)
            conductor.append(
                (
                    starts[slot],
                    1,
                    b"\xff\x58\x04" + bytes([numerator, power, 24, 8]),
                )
            )
            previous = (numerator, denominator)

    warnings: list[str] = []
    tracks = [track_bytes(conductor)]
    notes_written = 0
    for stave in staves:
        events, written = stave_events(
            stave, order, starts, meters, keysig, warnings
        )
        tracks.append(track_bytes(events))
        notes_written += written

    header = chunk(
        b"MThd", struct.pack(">HHH", 1, len(tracks), TPQ)
    )
    stats = {
        "measures_in_spec": count,
        "measures_played": len(order),
        "tracks": len(tracks),
        "notes": notes_written,
        "quarter_bpm": round(tempos[order[0]], 2) if order else 0,
        "tempo_changes": tempo_changes,
        "duration_seconds": round(total_seconds, 2),
    }
    return header + b"".join(tracks), warnings, stats


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("spec", type=Path)
    parser.add_argument("-o", "--output", type=Path)
    parser.add_argument(
        "--no-repeats", action="store_true", help="play each measure once"
    )
    args = parser.parse_args(argv)

    spec = json.loads(args.spec.read_text())
    output = args.output or args.spec.with_suffix(".mid")

    try:
        data, warnings, stats = convert(spec, honor_repeats=not args.no_repeats)
    except ConversionError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(data)

    print(f"wrote {output} ({len(data):,} bytes)")
    for key, value in stats.items():
        print(f"  {key}: {value}")
    if warnings:
        print(f"  {len(warnings)} warning(s):")
        for warning in warnings:
            print(f"    - {warning}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
