#!/usr/bin/env python3
"""
Build the main-title theme-and-variations arrangement of "Die Eiserne Krone"
as a Ziya ```music``` spec, ready for scripts/music_spec_to_midi.py.

The whole piece is derived from one 8-bar hook (HOOK) over one 4-chord loop
(LOOP) in F# major.  Each section transforms that material rather than
introducing new material, which is what makes it read as a theme with
variations instead of a medley:

    S0  Cold open        4 bars   4/4  q=152   loop as unison stabs
    S1  Main title       8 bars   4/4  q=152   hook, thin scoring
    S2  Main title tutti 8 bars   4/4  q=152   hook + voice at the octave
    S3  Var I  chase    12 bars   7/8  q=176   hook fragmented into eighths
    S4  Var II lament   12 bars   4/4  q=76    hook augmented, vi-centred
    S5  Var III box     16 bars   3/4  q=138   hook re-metred as a waltz
    S6  Var IV fugato   20 bars   4/4  q=152   hook head as a canon subject
    S7  Bridge           8 bars   4/4  q=152   loop in half-time
    S8  Reprise         16 bars   4/4  q=160   hook, full ensemble
    S9  Button           4 bars   4/4  q=152   loop cadence + fermata

Every stave must contribute the same number of measures per section; Score
enforces that, because a stave that is short by one bar would otherwise slip
against the others for the rest of the piece.
"""

from __future__ import annotations

import json
from pathlib import Path

OUT = Path("design/scores/eiserne_krone_theme.json")

PARTS = [
    # (key, display name, short name, clef, midi config)
    ("Fl", "Flute", "Fl.", "treble", {"program": 73, "channel": 0}),
    ("Gtr", "Electric Guitar", "E. Gtr.", "treble",
     {"program": 30, "channel": 1, "transpose": -12}),
    ("Dr", "Drum Set", "Dr.", "percussion", {"channel": 9, "drums": True}),
    ("HpR", "Pedal Harp", "Hp.", "treble", {"program": 46, "channel": 2}),
    ("HpL", "Pedal Hp. L.H.", "Hp. L.H.", "bass", {"program": 46, "channel": 3}),
    ("Lv", "Lever Harp", "Lv. Hp.", "treble", {"program": 46, "channel": 4}),
    ("V", "Voice (Sop.)", "V.", "treble", {"program": 53, "channel": 5}),
    ("Vc", "Cello", "Vc.", "bass", {"program": 42, "channel": 6}),
]
KEYS = [p[0] for p in PARTS]

BAR_QUARTERS = {"4/4": 4.0, "7/8": 3.5, "3/4": 3.0}
QUARTERS = {"w": 4.0, "h": 2.0, "q": 1.0, "8": 0.5, "16": 0.25,
            "hd": 3.0, "qd": 1.5, "8d": 0.75}


# ------------------------------------------------------------- primitives ----

def N(keys, dur, **kw) -> dict:
    if isinstance(keys, str):
        keys = [keys]
    return {"keys": list(keys), "duration": dur, **kw}


def R(dur) -> dict:
    return {"rest": True, "duration": dur}


def M(notes, **kw) -> dict:
    return {"notes": list(notes), **kw}


REST_BAR = {
    "4/4": [R("w")],
    "7/8": [R("h"), R("q"), R("8")],
    "3/4": [R("h"), R("q")],
}


def rest_bars(meter: str, count: int) -> list[dict]:
    return [M(REST_BAR[meter]) for _ in range(count)]


def octave(key: str, delta: int) -> str:
    letter, _, num = key.partition("/")
    return f"{letter}/{int(num) + delta}"


def shift(bar: list[tuple], delta: int) -> list[tuple]:
    return [([octave(k, delta) for k in keys], dur) for keys, dur in bar]


def build(bar: list[tuple], **kw) -> list[dict]:
    """Turn (keys, duration) pairs into note dicts; kw applies to the first."""
    out = []
    for index, (keys, dur) in enumerate(bar):
        out.append(N(keys, dur, **(kw if index == 0 else {})))
    return out


# ----------------------------------------------------------------- material ----

# The 4-chord loop: I - vi - IV - V in F# major.
LOOP = [
    {"sym": "F#5", "bass": "f/2", "dyad": ["f/4", "c/5"],
     "tri": ["f/4", "a/4", "c/5"], "hi": ["f/5", "a/5", "c/6"],
     "arp": ["f/4", "a/4", "c/5", "f/5"], "fifth": "c/3"},
    {"sym": "D#m", "bass": "d/2", "dyad": ["d/4", "a/4"],
     "tri": ["d/4", "f/4", "a/4"], "hi": ["d/5", "f/5", "a/5"],
     "arp": ["d/4", "f/4", "a/4", "d/5"], "fifth": "a/2"},
    {"sym": "B", "bass": "b/1", "dyad": ["b/3", "f/4"],
     "tri": ["b/3", "d/4", "f/4"], "hi": ["b/4", "d/5", "f/5"],
     "arp": ["b/3", "d/4", "f/4", "b/4"], "fifth": "f/2"},
    {"sym": "C#", "bass": "c/2", "dyad": ["c/4", "g/4"],
     "tri": ["c/4", "e/4", "g/4"], "hi": ["c/5", "e/5", "g/5"],
     "arp": ["c/4", "e/4", "g/4", "c/5"], "fifth": "g/2"},
]


def chord(bar_index: int) -> dict:
    return LOOP[bar_index % 4]


# The hook: 8 bars of 4/4, written at flute octave.
HOOK: list[list[tuple]] = [
    [(["a/5"], "q"), (["c/6"], "q"), (["f/6"], "h")],
    [(["d/6"], "q"), (["c/6"], "q"), (["a/5"], "h")],
    [(["b/5"], "q"), (["d/6"], "q"), (["f/6"], "h")],
    [(["e/6"], "q"), (["d/6"], "q"), (["c/6"], "h")],
    [(["a/5"], "q"), (["c/6"], "q"), (["f/6"], "q"), (["e/6"], "q")],
    [(["d/6"], "h"), (["a/5"], "h")],
    [(["b/5"], "q"), (["c/6"], "q"), (["d/6"], "h")],
    [(["c/6"], "w")],
]

# The same hook re-metred as a waltz (3 quarters per bar).
HOOK_34: list[list[tuple]] = [
    [(["a/5"], "q"), (["c/6"], "q"), (["f/6"], "q")],
    [(["d/6"], "q"), (["c/6"], "q"), (["a/5"], "q")],
    [(["b/5"], "q"), (["d/6"], "q"), (["f/6"], "q")],
    [(["e/6"], "q"), (["d/6"], "h")],
    [(["a/5"], "q"), (["c/6"], "q"), (["f/6"], "q")],
    [(["d/6"], "q"), (["a/5"], "q"), (["f/5"], "q")],
    [(["b/5"], "q"), (["c/6"], "q"), (["d/6"], "q")],
    [(["c/6"], "hd")],
]

# The hook head compressed into eighths: the canon subject (2 bars of 4/4).
SUBJECT: list[list[tuple]] = [
    [(["a/5"], "8"), (["c/6"], "8"), (["f/6"], "8"), (["e/6"], "8"),
     (["d/6"], "8"), (["c/6"], "8"), (["a/5"], "8"), (["f/5"], "8")],
    [(["b/5"], "8"), (["d/6"], "8"), (["f/6"], "8"), (["d/6"], "8"),
     (["c/6"], "8"), (["a/5"], "8"), (["f/5"], "8"), (["c/5"], "8")],
]

# Var II: the hook augmented and centred on vi, at cello octave.
LAMENT: list[list[tuple]] = [
    [(["d/3"], "h"), (["f/3"], "h")],
    [(["a/3"], "hd"), (["f/3"], "q")],
    [(["b/2"], "h"), (["d/3"], "h")],
    [(["c/3"], "w")],
    [(["d/3"], "h"), (["a/3"], "h")],
    [(["b/3"], "hd"), (["a/3"], "q")],
    [(["f/3"], "h"), (["d/3"], "h")],
    [(["d/3"], "w")],
]


# ------------------------------------------------------------------ pattern ----

def dr_rock(crash: bool = False, fill: bool = False) -> list[dict]:
    """Eight eighths of 4/4 backbeat: kick / hat / snare / hat ..."""
    if fill:
        seq = ["c/5"] * 7 + ["f/4"]
        out = [N(k, "8") for k in seq]
        out[0]["articulations"] = ["accent"]
        out[-1] = N(["f/4", "g/5"], "8", articulations=["accent"])
        return out
    seq = ["f/4", "e/5", "c/5", "e/5", "f/4", "f/4", "c/5", "e/5"]
    out = [N(k, "8") for k in seq]
    if crash:
        out[0] = N(["f/4", "g/5"], "8", articulations=["accent"])
    return out


def dr_hats(meter: str, dynamic: str | None = None) -> list[dict]:
    counts = {"4/4": 8, "7/8": 7, "3/4": 6}[meter]
    out = [N("e/5", "8") for _ in range(counts)]
    out[0] = N("f/4", "8", **({"dynamic": dynamic} if dynamic else {}))
    return out


def dr_78() -> list[dict]:
    seq = ["f/4", "e/5", "c/5", "f/4", "e/5", "c/5", "f/4"]
    out = [N(k, "8") for k in seq]
    out[0] = N(["f/4", "g/5"], "8", articulations=["accent"])
    out[-1]["articulations"] = ["accent"]
    return out


def dr_halftime(crash: bool = False) -> list[dict]:
    out = [N("f/4", "h"), N("c/5", "h")]
    if crash:
        out[0] = N(["f/4", "g/5"], "h", articulations=["accent"])
    return out


def gtr_eighths(index: int, count: int, staccato: bool = False,
                **kw) -> list[dict]:
    dyad = chord(index)["dyad"]
    out = []
    for position in range(count):
        extra = dict(kw) if position == 0 else {}
        if position == 0:
            extra["chordSymbol"] = chord(index)["sym"]
        if staccato:
            extra["articulations"] = ["staccato"]
        out.append(N(dyad, "8", **extra))
    out[-1].setdefault("articulations", []).append("accent")
    return out


def harp_arp(index: int, meter: str, **kw) -> list[dict]:
    """Cycle the chord's arpeggio through the bar in eighths."""
    figure = chord(index)["arp"]
    figure = figure + figure[-2:0:-1]  # up then back down
    counts = {"4/4": 8, "7/8": 7, "3/4": 6}[meter]
    out = []
    for position in range(counts):
        extra = dict(kw) if position == 0 else {}
        out.append(N(figure[position % len(figure)], "8", **extra))
    return out


def assign_lyrics(measures: list[dict], words: list[list[str]]) -> None:
    """Attach syllables to the sounding notes of each measure, in order."""
    for measure, bar_words in zip(measures, words):
        sounding = [n for n in measure["notes"] if not n.get("rest")]
        if len(sounding) != len(bar_words):
            raise SystemExit(
                f"lyric mismatch: {len(bar_words)} syllables for "
                f"{len(sounding)} notes in {bar_words}"
            )
        for note, word in zip(sounding, bar_words):
            text = word
            syllabic = None
            if word.endswith("-"):
                text, syllabic = word[:-1], "begin"
            elif word.startswith("-"):
                text, syllabic = word[1:], "end"
            note["lyric"] = {"text": text}
            if syllabic:
                note["lyric"]["syllabic"] = syllabic


# -------------------------------------------------------------------- score ----

class Score:
    def __init__(self) -> None:
        self.parts: dict[str, list[dict]] = {k: [] for k in KEYS}

    def section(self, meter: str, bars: int, tempo: dict | None,
                label: str | None, parts: dict[str, list[dict]]) -> None:
        for key in KEYS:
            measures = parts.get(key)
            if measures is None:
                measures = rest_bars(meter, bars)
            if len(measures) != bars:
                raise SystemExit(
                    f"{label}: stave {key} has {len(measures)} bars, expected {bars}"
                )
            for measure in measures:
                total = sum(QUARTERS[str(n["duration"])] for n in measure["notes"])
                if abs(total - BAR_QUARTERS[meter]) > 1e-9:
                    raise SystemExit(
                        f"{label}: stave {key} bar totals {total} quarters, "
                        f"meter {meter} wants {BAR_QUARTERS[meter]}"
                    )
            measures[0]["timeSignature"] = meter
            if tempo:
                measures[0]["tempo"] = tempo
            if label and key == "Fl":
                measures[0].setdefault("annotations", []).append(
                    {"text": label, "position": "above"}
                )
            self.parts[key].extend(measures)

    def spec(self) -> dict:
        staves = []
        for key, name, short, clef, midi in PARTS:
            staves.append({
                "name": name, "shortName": short, "clef": clef,
                "midi": midi, "measures": self.parts[key],
            })
        return {
            "type": "music",
            "keySignature": "F#",
            "timeSignature": "4/4",
            "autoBeam": True,
            "maxSystemWidth": 1400,
            "title": "DIE EISERNE KRONE",
            "subtitle": "Main Title — Theme and Variations",
            "composer": "D. Cohn",
            "tempo": {"name": "Allegro feroce", "duration": "q", "bpm": 152},
            "endBar": "final",
            "staves": staves,
        }


# ------------------------------------------------------------ range guard ----

# Sounding-pitch limits per stave key.  The cello floor is the real one: C2 is
# the open bottom string, so anything below it is unplayable rather than merely
# uncomfortable, and the 4-chord loop's B root would otherwise land there.
RANGES = {
    "Fl": (59, 96),    # B3 .. C7
    "Gtr": (40, 88),   # E2 .. E6, sounding
    "HpR": (24, 103),
    "HpL": (24, 103),
    "Lv": (36, 91),
    "V": (60, 84),     # C4 .. C6
    "Vc": (36, 76),    # C2 .. E5
}

_SEMI = {"c": 0, "d": 2, "e": 4, "f": 5, "g": 7, "a": 9, "b": 11}
_SHARPED = {"f", "c", "g", "d", "a", "e"}  # F# major sharps every letter but B


def sounding(key: str, transpose: int) -> int:
    letter, _, num = key.partition("/")
    base = _SEMI[letter[0]] + (1 if letter[0] in _SHARPED and len(letter) == 1 else 0)
    if letter[1:] == "#":
        base = _SEMI[letter[0]] + 1
    elif letter[1:] == "n":
        base = _SEMI[letter[0]]
    return (int(num) + 1) * 12 + base + transpose


def to_key(letter_key: str, octaves: int) -> str:
    return octave(letter_key, octaves)


def enforce_ranges(spec: dict) -> int:
    """Move any out-of-range note by whole octaves, reporting every change.

    A generator that transposes shared material into several parts will
    eventually push a line past an instrument's limits; failing loudly here is
    the difference between a fixable report and an unplayable part.
    """
    moved = 0
    for (key, name, _short, _clef, midi), stave in zip(PARTS, spec["staves"]):
        if key not in RANGES:
            continue
        low, high = RANGES[key]
        transpose = int(midi.get("transpose", 0))
        for bar, measure in enumerate(stave["measures"], start=1):
            for note in measure["notes"]:
                for position, nk in enumerate(note.get("keys", [])):
                    pitch = sounding(nk, transpose)
                    delta = 0
                    while pitch + delta * 12 < low:
                        delta += 1
                    while pitch + delta * 12 > high:
                        delta -= 1
                    if delta:
                        note["keys"][position] = to_key(nk, delta)
                        print(f"  range: {name} m.{bar} {nk} -> "
                              f"{note['keys'][position]} ({delta:+d} oct)")
                        moved += 1
    return moved


def main() -> int:
    s = Score()

    # ---- S0  cold open: the loop as unison stabs -------------------------
    stabs = [0, 0, 1, 3]
    gtr, dr, vc, hpl = [], [], [], []
    for bar, index in enumerate(stabs):
        first = {"dynamic": "ff"} if bar == 0 else {}
        dyad, bass = chord(index)["dyad"], chord(index)["bass"]
        if bar < 3:
            gtr.append(M([N(dyad, "q", articulations=["marcato"],
                            chordSymbol=chord(index)["sym"], **first),
                          R("q"), N(dyad, "q", articulations=["marcato"]), R("q")]))
            dr.append(M([N(["f/4", "g/5"], "q", articulations=["accent"], **first),
                         R("q"), N(["f/4", "g/5"], "q", articulations=["accent"]),
                         R("q")]))
            vc.append(M([N(bass, "q", articulations=["marcato"], **first), R("q"),
                         N(bass, "q", articulations=["marcato"]), R("q")]))
            hpl.append(M([N(bass, "q", **first), R("q"), N(bass, "q"), R("q")]))
        else:
            gtr.append(M([N(dyad, "w", chordSymbol=chord(index)["sym"])]))
            dr.append(M(dr_rock(fill=True)))
            vc.append(M([N(bass, "w")]))
            hpl.append(M([N(bass, "w")]))
    s.section("4/4", 4, {"name": "Allegro feroce", "duration": "q", "bpm": 152},
              "Cold Open", {"Gtr": gtr, "Dr": dr, "Vc": vc, "HpL": hpl})

    # ---- S1  main title: hook, thin scoring ------------------------------
    fl = [M(build(bar, **({"dynamic": "mf"} if i == 0 else {})))
          for i, bar in enumerate(HOOK)]
    gtr = [M(gtr_eighths(i, 8, staccato=True,
                         **({"dynamic": "mp"} if i == 0 else {})))
           for i in range(8)]
    dr = rest_bars("4/4", 4) + [M(dr_hats("4/4", "mp" if i == 0 else None))
                                for i in range(4)]
    hpr = [M(harp_arp(i, "4/4", **({"dynamic": "p"} if i == 0 else {})))
           for i in range(8)]
    hpl = [M([N(chord(i)["bass"], "w", **({"dynamic": "p"} if i == 0 else {}))])
           for i in range(8)]
    vc = [M([N(chord(i)["bass"], "q", articulations=["staccato"],
               **({"dynamic": "mp"} if i == 0 else {})),
             N(chord(i)["fifth"], "q", articulations=["staccato"]),
             N(chord(i)["bass"], "q", articulations=["staccato"]),
             N(chord(i)["fifth"], "q", articulations=["staccato"])])
          for i in range(8)]
    s.section("4/4", 8, None, "Main Title",
              {"Fl": fl, "Gtr": gtr, "Dr": dr, "HpR": hpr, "HpL": hpl, "Vc": vc})

    # ---- S2  main title tutti: voice at the octave below -----------------
    fl = [M(build(bar, **({"dynamic": "ff"} if i == 0 else {})))
          for i, bar in enumerate(HOOK)]
    voice = [M(build(shift(bar, -1), **({"dynamic": "f"} if i == 0 else {})))
             for i, bar in enumerate(HOOK)]
    assign_lyrics(voice, [
        ["Hail", "the", "crown"],
        ["forged", "in", "flame"],
        ["steel", "and", "storm"],
        ["call", "your", "name"],
        ["we", "were", "born", "to"],
        ["hold", "on"],
        ["through", "the", "night"],
        ["now"],
    ])
    gtr = [M(gtr_eighths(i, 8, **({"dynamic": "ff"} if i == 0 else {})))
           for i in range(8)]
    dr = [M(dr_rock(crash=(i % 4 == 0), fill=(i == 7))) for i in range(8)]
    dr[0]["notes"][0]["dynamic"] = "ff"
    hpr = [M(harp_arp(i, "4/4", **({"dynamic": "ff"} if i == 0 else {})))
           for i in range(8)]
    hpl = [M([N(chord(i)["bass"], "h"), N(chord(i)["bass"], "h")])
           for i in range(8)]
    lv = [M([N(chord(i)["hi"], "h", **({"dynamic": "f"} if i == 0 else {})),
             N(chord(i)["hi"], "h")]) for i in range(8)]
    vc = [M([N(chord(i)["bass"], "8", **({"dynamic": "ff"} if i == 0 else {})),
             N(chord(i)["bass"], "8"), N(chord(i)["bass"], "8"),
             N(chord(i)["bass"], "8"), N(chord(i)["fifth"], "q",
                                        articulations=["accent"]),
             N(chord(i)["bass"], "q", articulations=["accent"])])
          for i in range(8)]
    s.section("4/4", 8, None, "Tutti",
              {"Fl": fl, "Gtr": gtr, "Dr": dr, "HpR": hpr, "HpL": hpl,
               "Lv": lv, "V": voice, "Vc": vc})

    # ---- S3  Var I: chase in 7/8 -----------------------------------------
    fl, gtr, dr, vc, hpl = [], [], [], [], []
    for bar in range(12):
        figure = SUBJECT[bar % 2]
        fl.append(M(build(figure[:7], **({"dynamic": "ff"} if bar == 0 else {}))))
        gtr.append(M(gtr_eighths(bar, 7,
                                 **({"dynamic": "ff"} if bar == 0 else {}))))
        dr.append(M(dr_78()))
        bass = chord(bar)["bass"]
        vc.append(M([N(bass, "8", **({"dynamic": "ff"} if bar == 0 else {})),
                     N(bass, "8"), N(chord(bar)["fifth"], "8"), N(bass, "8"),
                     N(bass, "8"), N(chord(bar)["fifth"], "8"),
                     N(bass, "8", articulations=["accent"])]))
        hpl.append(M([N(bass, "h"), N(bass, "q"),
                      N(bass, "8", articulations=["accent"])]))
    s.section("7/8", 12, {"name": "Piu mosso — Verfolgung", "duration": "q",
                          "bpm": 176}, "Var. I — La Caccia",
              {"Fl": fl, "Gtr": gtr, "Dr": dr, "Vc": vc, "HpL": hpl})

    # ---- S4  Var II: lament, no guitar or drums --------------------------
    vc = [M(build(bar, **({"dynamic": "p"} if i == 0 else {})))
          for i, bar in enumerate(LAMENT)]
    vc = rest_bars("4/4", 4) + vc[:8]
    hpr = [M(harp_arp(i + 1, "4/4", **({"dynamic": "pp"} if i == 0 else {})))
           for i in range(12)]
    hpl = [M([N(chord(i + 1)["bass"], "w",
                **({"dynamic": "pp"} if i == 0 else {}))]) for i in range(12)]
    fl = rest_bars("4/4", 6) + [
        M([N(["a/5"], "w", dynamic="p")]), M([N(["f/5"], "w")]),
        M([N(["b/5"], "w")]), M([N(["a/5"], "w")]),
        M([N(["c/6"], "h"), N(["d/6"], "h")]), M([N(["c/6"], "w")]),
    ]
    voice = rest_bars("4/4", 4) + [
        M([N(["d/5"], "h", dynamic="mp"), N(["f/5"], "h")]),
        M([N(["a/5"], "hd"), N(["f/5"], "q")]),
        M([N(["b/4"], "h"), N(["d/5"], "h")]),
        M([N(["c/5"], "w")]),
        M([N(["d/5"], "h"), N(["a/5"], "h")]),
        M([N(["b/5"], "hd"), N(["a/5"], "q")]),
        M([N(["f/5"], "h"), N(["d/5"], "h")]),
        M([N(["d/5"], "w")]),
    ]
    assign_lyrics(voice[4:], [
        ["Ash", "on"], ["wa-", "-ter"], ["cold", "as"], ["stone"],
        ["all", "the"], ["crowns", "we"], ["called", "our"], ["own"],
    ])
    s.section("4/4", 12, {"name": "Adagio dolente", "duration": "q", "bpm": 76},
              "Var. II — Klage",
              {"Fl": fl, "HpR": hpr, "HpL": hpl, "V": voice, "Vc": vc})

    # ---- S5  Var III: music-box waltz ------------------------------------
    fl = [M(build(HOOK_34[i % 8], **({"dynamic": "mp"} if i == 0 else {})))
          for i in range(16)]
    lv = []
    for bar in range(16):
        c = chord(bar)
        lv.append(M([N(c["hi"][0], "q", articulations=["staccato"],
                       **({"dynamic": "p"} if bar == 0 else {})),
                     N(c["hi"], "q", articulations=["staccato"]),
                     N(c["hi"], "q", articulations=["staccato"])]))
    hpr = [M(harp_arp(i, "3/4", **({"dynamic": "pp"} if i == 0 else {})))
           for i in range(16)]
    hpl = [M([N(chord(i)["bass"], "hd",
               **({"dynamic": "pp"} if i == 0 else {}))]) for i in range(16)]
    dr = [M(dr_hats("3/4", "pp" if i == 0 else None)) for i in range(16)]
    vc = [M([N(chord(i)["bass"], "q", articulations=["staccato"],
               **({"dynamic": "p"} if i == 0 else {})), R("q"), R("q")])
          for i in range(16)]
    gtr = rest_bars("3/4", 12) + [
        M([N(chord(i)["dyad"], "q", articulations=["staccato"],
             **({"dynamic": "mp"} if i == 0 else {})),
           N(chord(i)["dyad"], "q", articulations=["staccato"]),
           N(chord(i)["dyad"], "q", articulations=["staccato"])])
        for i in range(4)]
    s.section("3/4", 16, {"name": "Allegretto — Spieluhr", "duration": "q",
                          "bpm": 138}, "Var. III — Spieluhr",
              {"Fl": fl, "Gtr": gtr, "Dr": dr, "HpR": hpr, "HpL": hpl,
               "Lv": lv, "Vc": vc})

    # ---- S6  Var IV: fugato on the hook head -----------------------------
    def subject(bar: int, delta: int, **kw) -> list[dict]:
        return build(shift(SUBJECT[bar % 2], delta), **kw)

    fl, gtr, dr, hpr, hpl, lv, voice, vc = [], [], [], [], [], [], [], []
    for bar in range(20):
        # Entries: cello m.1, flute m.3, lever harp m.5, guitar m.7.
        vc.append(M(subject(bar, -2, **({"dynamic": "mf"} if bar == 0 else {}))
                    if bar < 8 else
                    [N(chord(bar)["bass"], "8") for _ in range(8)]))
        if bar < 2:
            fl.append(M(REST_BAR["4/4"]))
        elif bar < 8:
            fl.append(M(subject(bar, 0, **({"dynamic": "mf"} if bar == 2 else {}))))
        else:
            fl.append(M(build(HOOK[bar % 8],
                              **({"dynamic": "ff"} if bar == 8 else {}))))
        if bar < 4:
            lv.append(M(REST_BAR["4/4"]))
        elif bar < 8:
            lv.append(M(subject(bar, -1, **({"dynamic": "mf"} if bar == 4 else {}))))
        else:
            lv.append(M([N(chord(bar)["hi"], "h"), N(chord(bar)["hi"], "h")]))
        if bar < 6:
            gtr.append(M(REST_BAR["4/4"]))
        elif bar < 8:
            gtr.append(M(subject(bar, -1, **({"dynamic": "f"} if bar == 6 else {}))))
        else:
            gtr.append(M(gtr_eighths(bar, 8,
                                     **({"dynamic": "ff"} if bar == 8 else {}))))
        if bar < 6:
            dr.append(M(REST_BAR["4/4"]))
        elif bar < 8:
            dr.append(M(dr_hats("4/4", "mf" if bar == 6 else None)))
        else:
            dr.append(M(dr_rock(crash=(bar % 4 == 0), fill=(bar == 19))))
        hpr.append(M(REST_BAR["4/4"]) if bar < 8
                   else M(harp_arp(bar, "4/4",
                                   **({"dynamic": "ff"} if bar == 8 else {}))))
        hpl.append(M(REST_BAR["4/4"]) if bar < 8
                   else M([N(chord(bar)["bass"], "h"),
                           N(chord(bar)["bass"], "h")]))
        if bar < 16:
            voice.append(M(REST_BAR["4/4"]))
        else:
            voice.append(M([N(chord(bar)["hi"][0], "w",
                              **({"dynamic": "f"} if bar == 16 else {}))],))
    assign_lyrics(voice[16:], [["ah"], ["ah"], ["ah"], ["ah"]])
    s.section("4/4", 20, {"name": "Tempo I — Fugato", "duration": "q",
                          "bpm": 152}, "Var. IV — Fugato",
              {"Fl": fl, "Gtr": gtr, "Dr": dr, "HpR": hpr, "HpL": hpl,
               "Lv": lv, "V": voice, "Vc": vc})

    # ---- S7  bridge: the loop in half-time -------------------------------
    gtr = [M([N(chord(i)["dyad"], "w", chordSymbol=chord(i)["sym"],
               **({"dynamic": "f"} if i == 0 else {}))]) for i in range(8)]
    dr = [M(dr_halftime(crash=(i % 2 == 0))) for i in range(8)]
    dr[0]["notes"][0]["dynamic"] = "f"
    hpr = [M([N(chord(i)["hi"], "w", **({"dynamic": "mp"} if i == 0 else {}))])
           for i in range(8)]
    hpl = [M([N(chord(i)["bass"], "w")]) for i in range(8)]
    vc = [M([N(chord(i)["bass"], "w", articulations=["tenuto"],
               **({"dynamic": "f"} if i == 0 else {}))]) for i in range(8)]
    # Written an octave below the obvious placement: the same contour up at
    # sounding pitch would peak on a sustained D#6, which is a stunt note for
    # a whole bar at mf.  The flute and lever harp cover the octave above.
    voice = [
        M([N(["f/4"], "h", dynamic="mf"), N(["a/4"], "h")]),
        M([N(["c/5"], "w")]),
        M([N(["b/4"], "h"), N(["a/4"], "h")]),
        M([N(["f/4"], "w")]),
        M([N(["a/4"], "h"), N(["c/5"], "h")]),
        M([N(["d/5"], "w")]),
        M([N(["c/5"], "h"), N(["b/4"], "h")]),
        M([N(["c/5"], "w")]),
    ]
    assign_lyrics(voice, [
        ["One", "more"], ["dawn"], ["one", "more"], ["turn"],
        ["let", "the"], ["i-"], ["-ron", "crown"], ["burn"],
    ])
    s.section("4/4", 8, {"name": "Largamente", "duration": "q", "bpm": 152},
              "Bridge",
              {"Gtr": gtr, "Dr": dr, "HpR": hpr, "HpL": hpl, "V": voice,
               "Vc": vc})

    # ---- S8  reprise: full ensemble, two strophes ------------------------
    fl = [M(build(shift(HOOK[i % 8], 0) if i < 8 else HOOK[i % 8],
                  **({"dynamic": "fff"} if i == 0 else {})))
          for i in range(16)]
    voice = [M(build(shift(HOOK[i % 8], -1),
                     **({"dynamic": "ff"} if i == 0 else {})))
             for i in range(16)]
    assign_lyrics(voice, [
        ["Rise", "and", "stand"], ["up", "from", "stone"],
        ["one", "more", "dawn"], ["not", "a-", "-lone"],
        ["we", "will", "car-", "-ry"], ["the", "flame"],
        ["to", "the", "end"], ["now"],
        ["Hail", "the", "crown"], ["forged", "in", "flame"],
        ["steel", "and", "storm"], ["call", "your", "name"],
        ["we", "were", "born", "to"], ["hold", "on"],
        ["through", "the", "night"], ["now"],
    ])
    gtr = [M(gtr_eighths(i, 8, **({"dynamic": "fff"} if i == 0 else {})))
           for i in range(16)]
    dr = [M(dr_rock(crash=(i % 4 == 0), fill=(i in (7, 15)))) for i in range(16)]
    dr[0]["notes"][0]["dynamic"] = "fff"
    hpr = [M(harp_arp(i, "4/4", **({"dynamic": "fff"} if i == 0 else {})))
           for i in range(16)]
    hpl = [M([N(chord(i)["bass"], "h"), N(chord(i)["bass"], "h")])
           for i in range(16)]
    lv = [M([N(chord(i)["hi"], "h", **({"dynamic": "ff"} if i == 0 else {})),
             N(chord(i)["hi"], "h")]) for i in range(16)]
    vc = [M([N(chord(i)["bass"], "8", **({"dynamic": "fff"} if i == 0 else {})),
             N(chord(i)["bass"], "8"), N(chord(i)["fifth"], "8"),
             N(chord(i)["bass"], "8"), N(chord(i)["bass"], "q",
                                        articulations=["accent"]),
             N(chord(i)["fifth"], "q", articulations=["accent"])])
          for i in range(16)]
    s.section("4/4", 16, {"name": "Trionfale", "duration": "q", "bpm": 160},
              "Reprise",
              {"Fl": fl, "Gtr": gtr, "Dr": dr, "HpR": hpr, "HpL": hpl,
               "Lv": lv, "V": voice, "Vc": vc})

    # ---- S9  button ------------------------------------------------------
    cadence = [2, 3, 1, 0]
    fl, gtr, dr, hpr, hpl, lv, voice, vc = [], [], [], [], [], [], [], []
    for bar, index in enumerate(cadence):
        c = LOOP[index]
        last = bar == 3
        if last:
            fin = {"articulations": ["fermata-above"], "dynamic": "fff"}
            fl.append(M([N(["f/6"], "w", **fin)]))
            gtr.append(M([N(c["dyad"] + ["f/5"], "w", chordSymbol="F#5", **fin)]))
            dr.append(M([N(["f/4", "a/5"], "w", **fin)]))
            hpr.append(M([N(c["hi"], "w", **fin)]))
            hpl.append(M([N(c["bass"], "w", **fin)]))
            lv.append(M([N([octave(k, 0) for k in c["hi"]], "w", **fin)]))
            voice.append(M([N(["f/5"], "w", lyric={"text": "on"}, **fin)]))
            vc.append(M([N(c["bass"], "w", **fin)]))
            continue
        stab = {"articulations": ["marcato"]}
        fl.append(M([N(c["hi"][2], "q", **stab), R("q"),
                     N(c["hi"][2], "q", **stab), R("q")]))
        gtr.append(M([N(c["dyad"], "q", chordSymbol=c["sym"], **stab), R("q"),
                      N(c["dyad"], "q", **stab), R("q")]))
        dr.append(M([N(["f/4", "g/5"], "q", articulations=["accent"]), R("q"),
                     N(["f/4", "g/5"], "q", articulations=["accent"]), R("q")]))
        hpr.append(M([N(c["hi"], "q", **stab), R("q"), N(c["hi"], "q", **stab),
                      R("q")]))
        hpl.append(M([N(c["bass"], "q", **stab), R("q"), N(c["bass"], "q", **stab),
                      R("q")]))
        lv.append(M([N(c["hi"], "q", **stab), R("q"), N(c["hi"], "q", **stab),
                     R("q")]))
        voice.append(M(REST_BAR["4/4"]))
        vc.append(M([N(c["bass"], "q", **stab), R("q"), N(c["bass"], "q", **stab),
                     R("q")]))
    fl[0]["notes"][0]["dynamic"] = "fff"
    s.section("4/4", 4, {"name": "Maestoso", "duration": "q", "bpm": 152},
              "Button",
              {"Fl": fl, "Gtr": gtr, "Dr": dr, "HpR": hpr, "HpL": hpl,
               "Lv": lv, "V": voice, "Vc": vc})

    spec = s.spec()
    moved = enforce_ranges(spec)
    print(f"range guard: {moved} note(s) relocated")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(spec, indent=1))
    bars = len(spec["staves"][0]["measures"])
    print(f"wrote {OUT} ({OUT.stat().st_size:,} bytes), {bars} bars, "
          f"{len(spec['staves'])} staves")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
