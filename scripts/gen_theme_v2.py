#!/usr/bin/env python3
"""Die Eiserne Krone — Main Title, v2.  Theme and variations, ~2:58.

Tonal plan
----------
Written key: F# major (six sharps — the harps are set once and never touched).
The piece LIVES in D# Aeolian, the relative minor: riffs move i-VII-VI
(D#m - C# - B), the classic metal descent, and every note is diatonic to the
signature.  The final chorus resolves to F# major — the relative-major lift —
which is the only time the tonic major is allowed to win.

What v1 got wrong, and what this does instead
---------------------------------------------
* v1's "theme" was a triad arpeggio; here the chorus is a composed 8-bar
  melody with antecedent/consequent phrases, appoggiaturas, and a saved
  C#6 peak that appears exactly once (final chorus, second pass).
* v1 looped one progression for 108 bars; here harmony changes per section
  (verse ends on iv, the Klage opens off-tonic on G#m, the fugato builds a
  half cadence on C# into the F#-major arrival).
* v1 had no metal rhythm language; here the riff is a gallop (8-16-16) that
  the kick drum doubles — which is what double-kick IS.
* v1's guitar chords sounded an octave too high to be heavy; here the part
  assumes E-flat standard tuning (the most common metal tuning), whose low
  string is D#2 — the riff root.
* v1 had one velocity per dynamic; here every note gets a phrase-shaped,
  humanized velocity (seeded RNG — the output is reproducible).

Usage:  python3 scripts/gen_theme_v2.py
Writes: design/scores/eiserne_krone_theme_v2.json
"""

from __future__ import annotations

import importlib.util
import json
import random
from copy import deepcopy
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
OUT = ROOT / "design" / "scores" / "eiserne_krone_theme_v2.json"

_spec = importlib.util.spec_from_file_location("m2m", HERE / "music_spec_to_midi.py")
m2m = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(m2m)

RNG = random.Random(0xE15E)
KEYSIG = m2m.key_signature_map("F#")
Q = m2m.duration_quarters

# --------------------------------------------------------------- parts ----

PARTS = [
    ("Flute",           "Fl.",      "treble",     {"program": 73, "channel": 0}),
    ("Voice",           "V.",       "treble",     {"program": 53, "channel": 5}),
    ("Electric Guitar", "E. Gtr.",  "treble",     {"program": 30, "channel": 1,
                                                   "transpose": -12}),
    ("Drum Set",        "Dr.",      "percussion", {"channel": 9, "drums": True}),
    ("Pedal Harp",      "Hp.",      "treble",     {"program": 46, "channel": 2}),
    ("Pedal Hp. L.H.",  "Hp. L.H.", "bass",       {"program": 46, "channel": 3}),
    ("Lever Harp",      "Lv. Hp.",  "treble",     {"program": 46, "channel": 4}),
    ("Cello",           "Vc.",      "bass",       {"program": 42, "channel": 6}),
]
NAMES = [p[0] for p in PARTS]
TRANSPOSE = {p[0]: p[3].get("transpose", 0) for p in PARTS}

# Sounding-pitch ranges (MIDI).  Guitar floor 39 = D#2, the low string of
# E-flat standard tuning.
RANGES = {
    "Flute": (59, 98), "Voice": (63, 85), "Electric Guitar": (39, 88),
    "Cello": (36, 79), "Pedal Harp": (48, 103), "Pedal Hp. L.H.": (24, 72),
    "Lever Harp": (48, 91),
}

# ------------------------------------------------------------- helpers ----

def N(keys, dur, art=None, dyn=None, lyr=None, ann=None, roll=False):
    note = {"keys": [keys] if isinstance(keys, str) else list(keys),
            "duration": dur}
    if art:
        note["articulations"] = art if isinstance(art, list) else [art]
    if dyn:
        note["dynamic"] = dyn
    if lyr:
        note["lyric"] = {"text": lyr}
    if ann:
        note["annotations"] = [{"text": ann, "position": "above"}]
    if roll:
        note["arpeggio"] = True
    return note


def R(dur):
    return {"rest": True, "duration": dur}


def M(notes, arc=0):
    bar = {"notes": notes}
    if arc:
        bar["_arc"] = arc      # velocity delta across the bar (humanizer)
    return bar


def rest_bar(meter):
    return M({"4/4": [R("w")],
              "7/8": [R("h"), R("q"), R("8")],
              "12/8": [R("w"), R("h")]}[meter])


def met_quarters(meter):
    n, _, d = meter.partition("/")
    return int(n) * 4.0 / int(d)


def oct_shift(key, delta):
    letter, _, octv = key.partition("/")
    return f"{letter}/{int(octv) + delta}"


def shift_notes(notes, delta):
    out = []
    for note in notes:
        c = deepcopy(note)
        if not c.get("rest"):
            c["keys"] = [oct_shift(k, delta) for k in c["keys"]]
        out.append(c)
    return out


def triad_walk(letters, start_octave, count):
    """Ascending walk through a triad's letters, always rising in pitch."""
    keys, last, octv, i = [], -1, start_octave, 0
    while len(keys) < count:
        k = f"{letters[i % 3]}/{octv}"
        p = m2m.parse_key(k, KEYSIG)
        while p <= last:
            octv += 1
            k = f"{letters[i % 3]}/{octv}"
            p = m2m.parse_key(k, KEYSIG)
        keys.append(k)
        last = p
        i += 1
    return keys


TRI = {"i": ("d", "f", "a"), "VI": ("b", "d", "f"), "III": ("f", "a", "c"),
       "VII": ("c", "e", "g"), "iv": ("g", "b", "d"),
       "I": ("f", "a", "c"), "IV": ("b", "d", "f"), "V": ("c", "e", "g")}


def sweep_bar(chord, start_octave=4, dyn=None):
    """16 sixteenths: up two octaves through the triad and back (pedal harp)."""
    up = triad_walk(TRI[chord], start_octave, 8)
    notes = [N(k, "16") for k in up + list(reversed(up))]
    if dyn:
        notes[0]["dynamic"] = dyn
    return M(notes)


def bar_8ths(keys, accents=(0, 3, 6), dyn=None, ann=None):
    """Driving 8ths with a 3+3+2 accent pattern (guitar/cello chorus)."""
    notes = [N(keys, "8", art=["accent"] if i in accents else None)
             for i in range(8)]
    if dyn:
        notes[0]["dynamic"] = dyn
    if ann:
        notes[0]["annotations"] = [{"text": ann, "position": "above"}]
    return notes


def gallop_bar(root, fifth=None, tail=None, dyn=None, ann=None):
    """Beats 1-3: 8-16-16 gallop on the root; beat 4: two 8ths (default root)."""
    notes = []
    for b in range(3):
        head = [root, fifth] if (fifth and b == 0) else [root]
        notes += [N(head, "8", art=["accent"] if b == 0 else None),
                  N([root], "16"), N([root], "16")]
    notes += tail if tail else [N([root], "8"), N([root], "8")]
    if dyn:
        notes[0]["dynamic"] = dyn
    if ann:
        notes[0]["annotations"] = [{"text": ann, "position": "above"}]
    return notes


def drum_gallop_bar(china=False, fill=False, dyn=None):
    """Kick doubles the guitar gallop; snare on 2 and 4."""
    def beat(snare, first_extra=()):
        keys = ["f/4", "e/5"] + (["c/5"] if snare else []) + list(first_extra)
        return [N(keys, "8"), N("f/4", "16"), N("f/4", "16")]

    if fill:
        # beats 1-2 keep the gallop, beats 3-4 are a snare-to-toms fill
        notes = beat(False, ("g/5",) if china else ()) + beat(True)
        notes[0]["articulations"] = ["accent"]
        notes += [N("c/5", "16"), N("c/5", "16"), N("d/5", "16"), N("d/5", "16"),
                  N("b/4", "16"), N("b/4", "16"), N("a/4", "16"),
                  N(["a/4", "f/4"], "16", art=["accent"])]
    else:
        notes = beat(False, ("g/5", "a/5") if china else ())
        notes[0]["articulations"] = ["accent"]
        notes += beat(True) + beat(False)
        notes += [N(["f/4", "c/5", "e/5"], "8"), N(["f/4", "e/5"], "8")]
    if dyn:
        notes[0]["dynamic"] = dyn
    return M(notes)


def drum_chorus_bar(crash=None, fill=False, dyn=None):
    """Kick 8ths w/ syncopated push, snare 2+4, hats; optional crash key."""
    grid = [
        (["f/4", "e/5"], 0), (["e/5"], 0), (["c/5", "e/5"], 0), (["f/4", "e/5"], 1),
        (["f/4", "e/5"], 0), (["e/5"], 0), (["c/5", "e/5"], 0), (["e/5"], 0),
    ]
    notes = []
    for i, (keys, acc) in enumerate(grid):
        if i == 0 and crash:
            keys = keys + [crash]
        notes.append(N(keys, "8", art=["accent"] if (acc or i == 0) else None))
    if fill:
        notes = notes[:4] + [
            N("c/5", "16"), N("c/5", "16"), N("d/5", "16"), N("d/5", "16"),
            N("b/4", "16"), N("b/4", "16"), N(["a/4", "f/4"], "16"),
            N(["a/4", "f/4"], "16", art=["accent"])]
    if dyn:
        notes[0]["dynamic"] = dyn
    return M(notes)


# ------------------------------------------------------- humanization ----

DYN_V = {"pp": 36, "p": 48, "mp": 60, "mf": 74, "f": 88, "ff": 102, "fff": 114}
ACC_V = {"accent": 12, "marcato": 18, "tenuto": 3}
TOMS = {"g/4", "a/4", "b/4", "d/5"}


def _drum_color(keys):
    ks = set(keys)
    if ks == {"e/5"}:
        return -16                      # hats alone: ghosted
    if ks == {"b/5"}:
        return -8                       # ride alone
    delta = 0
    if ks & {"g/5", "a/5"}:
        delta += 8                      # crash / china
    if ks & TOMS:
        delta += 2
    return delta


def humanize(score, meters):
    for name, measures in score.items():
        drums = name == "Drum Set"
        dyn = "mf"
        for bi, bar in enumerate(measures):
            arc = bar.pop("_arc", 0)
            barq = met_quarters(meters[bi])
            cursor = 0.0
            for note in bar["notes"]:
                d = Q(note["duration"])
                if note.get("rest"):
                    cursor += d
                    continue
                if note.get("dynamic"):
                    dyn = note["dynamic"]
                v = DYN_V[dyn]
                for a in note.get("articulations", []):
                    v += ACC_V.get(a, 0)
                if cursor == 0.0:
                    v += 6
                elif abs(cursor - barq / 2) < 1e-9:
                    v += 3
                if arc:
                    v += int(round(arc * cursor / barq))
                if drums:
                    v += _drum_color(note["keys"])
                v += RNG.randint(-4, 4)
                note["velocity"] = max(18, min(126, v))
                cursor += d


# ------------------------------------------------------------ sections ----

SCORE = {name: [] for name in NAMES}
METERS = []
SECTIONS = []


def add_section(label, meter, bpm, parts, tempo_name=None):
    lengths = {len(v) for v in parts.values()}
    assert len(lengths) == 1, (label, {k: len(v) for k, v in parts.items()})
    bars = lengths.pop()
    for name in NAMES:
        measures = parts.get(name) or [rest_bar(meter) for _ in range(bars)]
        SCORE[name].extend(measures)
    first = SCORE[NAMES[0]][len(METERS)]
    first["timeSignature"] = meter
    first["tempo"] = {"name": tempo_name or label, "duration": "q", "bpm": bpm}
    METERS.extend([meter] * bars)
    SECTIONS.append((label, bars, meter, bpm))


# --- S1 · Cold Open (4 bars, 4/4, q=84) ------------------------------------

flute = [
    M([R("w")]),
    M([R("w")]),
    M([R("h"), N("a/4", "q", dyn="pp", ann="breathy"), N("d/5", "q")]),
    M([N("f/5", "h"), N("g/5", "h")]),
]
php = [M([N(k, "8") for k in
          ["d/4", "f/4", "a/4", "d/5", "f/5", "d/5", "a/4", "f/4"]])
       for _ in range(4)]
php[0]["notes"][0].update({"dynamic": "pp"})
php[0]["notes"][0]["annotations"] = [{"text": "quasi campane", "position": "above"}]
phl = [M([N("d/2", "h", dyn="pp"), N("a/2", "h")]),
       M([N("d/2", "w")]),
       M([N("d/2", "h"), N("a/2", "h")]),
       M([N("d/2", "w")])]
lvh = [M([R("w")]),
       M([R("h"), N("d/6", "h", dyn="pp")]),
       M([N("a/5", "w")]),
       M([N(["f/5", "a/5"], "w")])]
cello = [M([N("d/2", "w", dyn="pp", ann="sul tasto")])] + \
        [M([N("d/2", "w")]) for _ in range(3)]
add_section("Cold Open", "4/4", 84,
            {"Flute": flute, "Pedal Harp": php, "Pedal Hp. L.H.": phl,
             "Lever Harp": lvh, "Cello": cello},
            tempo_name="Misterioso")

# --- S2 · Riff (8 bars, 4/4, q=168) -----------------------------------------

def riff_gtr():
    b = []
    b.append(M(gallop_bar("d/3", "a/3", tail=[N("c/4", "8"), N("b/3", "8")],
                          dyn="ff", ann="E\u266d standard — palm mute")))
    b.append(M(gallop_bar("d/3", None, tail=[N("b/3", "8"), N("c/4", "8")])))
    b.append(M(gallop_bar("b/3", "f/4",
                          tail=[N("b/3", "8"), N("b/3", "8", art=["accent"])])))
    b.append(M(gallop_bar("c/4", "g/4", tail=[N("d/4", "8"), N("c/4", "8")])))
    b += [deepcopy(b[0]), deepcopy(b[1]), deepcopy(b[2])]
    b[4]["notes"][0].pop("annotations", None)
    b.append(M(gallop_bar("c/4", "g/4",
                          tail=[N("c/4", "8", art=["accent"]),
                                N("c/4", "8", art=["accent"])])))
    return b


def riff_cello():
    roots = ["d/2", "d/2", "b/2", "c/3", "d/2", "d/2", "b/2", "c/3"]
    bars = [M(gallop_bar(r)) for r in roots]
    bars[0]["notes"][0].update({"dynamic": "ff"})
    bars[0]["notes"][0]["annotations"] = [{"text": "spiccato", "position": "above"}]
    return bars


drums = [drum_gallop_bar(china=True, dyn="ff"), drum_gallop_bar(),
         drum_gallop_bar(), drum_gallop_bar(fill=True),
         drum_gallop_bar(china=True), drum_gallop_bar(),
         drum_gallop_bar(), drum_gallop_bar(fill=True)]
flute = [M([R("w")]) for _ in range(4)] + \
        [M([N("d/6", "w", dyn="mf")]), M([N("c/6", "w")]), M([N("b/5", "w")]),
         M([N("c/6", "h"), N("d/6", "h")])]
add_section("Riff", "4/4", 168,
            {"Electric Guitar": riff_gtr(), "Cello": riff_cello(),
             "Drum Set": drums, "Flute": flute},
            tempo_name="Allegro feroce")

# --- S3 · Verse (8 bars, 4/4) -----------------------------------------------

voice = [
    M([N("d/5", "q", dyn="mf", lyr="Aus"), N("d/5", "8", lyr="der"),
       N("d/5", "8", lyr="A-"), N("f/5", "q", lyr="-sche"),
       N("d/5", "q", lyr="steigt")]),
    M([N("c/5", "q", lyr="der"), N("d/5", "8", lyr="al-"),
       N("c/5", "8", lyr="-te"), N("b/4", "h", lyr="Thron,")]),
    M([N("b/4", "q", lyr="in"), N("d/5", "8", lyr="Ei-"),
       N("d/5", "8", lyr="-sen"), N("f/5", "q", lyr="ruft"),
       N("d/5", "q", lyr="das")]),
    M([N("e/5", "h", lyr="Blut"), N("c/5", "h", lyr="uns")]),
    M([N("d/5", "q", lyr="Die"), N("d/5", "8", lyr="Kro-"),
       N("d/5", "8", lyr="-ne"), N("f/5", "q", lyr="wacht"),
       N("d/5", "q", lyr="im")]),
    M([N("c/5", "q", lyr="Her-"), N("d/5", "8", lyr="-zen"),
       N("c/5", "8", lyr="vom"), N("a/4", "h", lyr="Berg,")]),
    M([N("b/4", "q", lyr="wer"), N("b/4", "8", lyr="sie"),
       N("b/4", "8", lyr="tr\u00e4gt"), N("d/5", "q", lyr="wird"),
       N("b/4", "q", lyr="Sturm")]),
    M([N("c/5", "hd", lyr="und"), N("g/5", "q", lyr="Stahl!")]),
]
verse_roots = ["d/3", "d/3", "b/3", "c/4", "d/3", "d/3", "g/3", "c/4"]
gtr = []
for i, root in enumerate(verse_roots):
    notes = [N(root, "8", art=["accent"] if j in (0, 5) else None)
             for j in range(8)]
    if i == 0:
        notes[0]["dynamic"] = "mp"
        notes[0]["annotations"] = [{"text": "palm mute", "position": "above"}]
    gtr.append(M(notes))
drums = []
for i in range(8):
    notes = [N(["f/4", "e/5"], "8"), N("e/5", "8"), N("e/5", "8"), N("e/5", "8"),
             N(["c/5", "e/5"], "8"), N("e/5", "8"),
             N(["f/4", "e/5"], "8"), N("f/4", "8")]
    if i == 0:
        notes[0]["dynamic"] = "mp"
    drums.append(M(notes))
cello = [M([N(r.replace("/3", "/2") if r in ("d/3",) else
             {"d/3": "d/2", "b/3": "b/2", "c/4": "c/3", "g/3": "g/2"}[r], "w")])
         for r in verse_roots]
cello[0]["notes"][0].update({"dynamic": "mp"})
lvh_dyads = {"d": ["f/5", "a/5"], "b": ["d/5", "f/5"],
             "c": ["e/5", "g/5"], "g": ["b/4", "d/5"]}
lvh = []
for i, root in enumerate(verse_roots):
    dyad = lvh_dyads[root[0]]
    notes = [R("q"), N(dyad, "q"), R("q"), N(dyad, "q")]
    if i == 0:
        notes[1]["dynamic"] = "p"
    lvh.append(M(notes))
add_section("Verse", "4/4", 168,
            {"Voice": voice, "Electric Guitar": gtr, "Drum Set": drums,
             "Cello": cello, "Lever Harp": lvh})

# --- S4 · Pre-Chorus (4 bars, 4/4) ------------------------------------------

voice = [
    M([N("f/5", "h", dyn="f", lyr="H\u00f6rst"), N("g/5", "h", lyr="du")]),
    M([N("a/5", "h", lyr="den"), N("g/5", "h", lyr="Ruf")]),
    M([N("f/5", "q", lyr="aus"), N("f/5", "q", lyr="Stahl"),
       N("g/5", "h", lyr="und")]),
    M([N("a/5", "h", lyr="Sturm"), N("g/5", "q", lyr="—"),
       N("a/5", "q", lyr="—")], arc=10),
]
gtr = [
    M([N(["b/3", "f/4"], "h", art=["accent"], dyn="f"), N(["b/3", "f/4"], "h")]),
    M([N(["c/4", "g/4"], "h", art=["accent"]), N(["c/4", "g/4"], "h")]),
    M([N(["b/3", "f/4"], "h", art=["accent"]), N(["b/3", "f/4"], "h")]),
    M(gallop_bar("c/4", "g/4",
                 tail=[N("c/4", "8", art=["accent"]),
                       N("c/4", "8", art=["accent"])]), arc=12),
]
drums = [
    M([N(["f/4", "g/5"], "q", dyn="f"), N("f/4", "q"), N("f/4", "q"),
       N("f/4", "q")]),
    M([N("f/4", "q"), N("f/4", "q"), N("f/4", "q"), N("f/4", "q")]),
    M([N("f/4", "8") for _ in range(8)]),
    M([N("c/5", "16") for _ in range(16)], arc=22),
]
cello = [M([N("b/2", "w", dyn="f")]), M([N("c/3", "w")]),
         M([N("b/2", "h"), N("f/3", "h")]),
         M([N("c/3", "8") for _ in range(8)], arc=12)]
php = [
    M([R("w")]), M([R("w")]),
    M([N(k, "8") for k in
       ["b/3", "d/4", "f/4", "b/4", "d/5", "f/5", "b/5", "d/6"]], arc=10),
    M([N(k, "8") for k in
       ["c/4", "e/4", "g/4", "c/5", "e/5", "g/5", "c/6", "e/6"]], arc=14),
]
php[2]["notes"][0]["dynamic"] = "mf"
phl = [M([N("b/2", "w", dyn="mf")]), M([N("c/3", "w")]),
       M([N("b/2", "w")]), M([N("c/3", "w")])]
lvh = [M([N(["d/5", "f/5"], "w", dyn="mf")]), M([N(["e/5", "g/5"], "w")]),
       M([N(["d/5", "f/5"], "w")]), M([N(["e/5", "g/5"], "w")])]
flute = [M([R("w")]), M([R("w")]),
         M([R("h"), N("d/6", "h", dyn="mf")]),
         M([N("e/6", "h"), N("g/6", "h")], arc=10)]
add_section("Pre-Chorus", "4/4", 168,
            {"Voice": voice, "Electric Guitar": gtr, "Drum Set": drums,
             "Cello": cello, "Pedal Harp": php, "Pedal Hp. L.H.": phl,
             "Lever Harp": lvh, "Flute": flute})

# --- S5 · Chorus x2 (16 bars, 4/4) ------------------------------------------

CHORUS_LYR = [
    ["Ei-", "-sen", "-kro-", "-ne", "ruft"],
    ["aus", "Nacht", "und", "Stein,"],
    ["durch", "Stahl", "und", "Sturm", "ge-", "-weiht"],
    ["—", "ihr", "Lied;"],
    ["Ei-", "-sen", "-kro-", "-ne", "steig"],
    ["em-", "-por", "im", "Zorn,"],
    ["aus", "Blut", "und", "Feu-", "-er", "neu", "ge-"],
    ["-bor'n."],
]


def chorus_melody(lyrics=True):
    L = CHORUS_LYR if lyrics else [[None] * 8 for _ in range(8)]
    def lyr(b, i):
        row = L[b]
        return row[i] if i < len(row) else None
    return [
        M([N("a/4", "8d", dyn="f", lyr=lyr(0, 0)), N("d/5", "16", lyr=lyr(0, 1)),
           N("d/5", "q", lyr=lyr(0, 2)), N("f/5", "q", lyr=lyr(0, 3)),
           N("g/5", "q", lyr=lyr(0, 4))]),
        M([N("a/5", "qd", lyr=lyr(1, 0)), N("g/5", "8", lyr=lyr(1, 1)),
           N("f/5", "q", lyr=lyr(1, 2)), N("d/5", "q", lyr=lyr(1, 3))]),
        M([N("f/5", "8d", lyr=lyr(2, 0)), N("g/5", "16", lyr=lyr(2, 1)),
           N("a/5", "q", lyr=lyr(2, 2)), N("g/5", "8", lyr=lyr(2, 3)),
           N("f/5", "8", lyr=lyr(2, 4)), N("e/5", "q", lyr=lyr(2, 5))]),
        M([N("e/5", "h", lyr=lyr(3, 0)), N("d/5", "q", lyr=lyr(3, 1)),
           N("c/5", "q", lyr=lyr(3, 2))]),
        M([N("a/4", "8d", lyr=lyr(4, 0)), N("d/5", "16", lyr=lyr(4, 1)),
           N("d/5", "q", lyr=lyr(4, 2)), N("f/5", "q", lyr=lyr(4, 3)),
           N("g/5", "q", lyr=lyr(4, 4))]),
        M([N("a/5", "qd", lyr=lyr(5, 0)), N("b/5", "8", lyr=lyr(5, 1)),
           N("a/5", "q", lyr=lyr(5, 2)), N("g/5", "q", lyr=lyr(5, 3))]),
        M([N("f/5", "8d", lyr=lyr(6, 0)), N("g/5", "16", lyr=lyr(6, 1)),
           N("f/5", "q", lyr=lyr(6, 2)), N("e/5", "8", lyr=lyr(6, 3)),
           N("d/5", "8", lyr=lyr(6, 4)), N("c/5", "8", lyr=lyr(6, 5)),
           N("b/4", "8", lyr=lyr(6, 6))]),
        M([N("d/5", "w", lyr=lyr(7, 0))]),
    ]


CHORUS_SEQ = ["i", "VI", "III", "VII", "i", "VI", "VII", "i"]
GTR_CH = {"i": ["d/3", "a/3"], "VI": ["b/3", "f/4"], "III": ["f/3", "c/4"],
          "VII": ["c/4", "g/4"], "iv": ["g/3", "d/4"],
          "I": ["f/3", "c/4"], "IV": ["b/3", "f/4"], "V": ["c/4", "g/4"]}
BASS_CH = {"i": "d/2", "VI": "b/2", "III": "f/2", "VII": "c/3", "iv": "g/2",
           "I": "f/2", "IV": "b/2", "V": "c/3"}
LVH_CH = {"i": ["d/5", "f/5", "a/5"], "VI": ["b/4", "d/5", "f/5"],
          "III": ["f/5", "a/5", "c/6"], "VII": ["c/5", "e/5", "g/5"],
          "I": ["f/5", "a/5", "c/6"], "IV": ["b/4", "d/5", "f/5"],
          "V": ["c/5", "e/5", "g/5"]}
PHL_CH = {"i": ["d/2", "d/3"], "VI": ["b/1", "b/2"], "III": ["f/2", "f/3"],
          "VII": ["c/2", "c/3"], "I": ["f/2", "f/3"], "IV": ["b/1", "b/2"],
          "V": ["c/2", "c/3"]}


def chorus_band(seq, crash_bars=(0,), fills=(7,), dyn="ff"):
    gtr, cello, drums, php, phl, lvh = [], [], [], [], [], []
    for i, ch in enumerate(seq):
        gtr.append(M(bar_8ths(GTR_CH[ch], dyn=dyn if i == 0 else None)))
        cello.append(M(bar_8ths([BASS_CH[ch]], dyn=dyn if i == 0 else None)))
        drums.append(drum_chorus_bar(
            crash="g/5" if i in crash_bars else None,
            fill=i in fills, dyn=dyn if i == 0 else None))
        php.append(sweep_bar(ch, dyn="f" if i == 0 else None))
        phl.append(M([N(PHL_CH[ch], "h"), N(PHL_CH[ch], "h")]))
        lvh.append(M([N(LVH_CH[ch], "q") for _ in range(4)]))
    return gtr, cello, drums, php, phl, lvh


gtr, cello, drums, php, phl, lvh = chorus_band(CHORUS_SEQ * 2,
                                               crash_bars=(0, 8),
                                               fills=(7, 15))
voice = chorus_melody(True) + chorus_melody(True)
flute = [M([R("w")]) for _ in range(8)] + \
        [M(shift_notes(bar["notes"], 1)) for bar in chorus_melody(False)]
flute[8]["notes"][0]["dynamic"] = "f"
add_section("Chorus", "4/4", 168,
            {"Voice": voice, "Electric Guitar": gtr, "Cello": cello,
             "Drum Set": drums, "Pedal Harp": php, "Pedal Hp. L.H.": phl,
             "Lever Harp": lvh, "Flute": flute},
            tempo_name="Thema")

# --- S6 · Var. I — Hexenjagd (12 bars, 7/8, q=184) --------------------------

def hexen_gtr_bar(root, fifth=None, dyn=None, ann=None):
    notes = []
    for b in range(3):
        head = [root, fifth] if (fifth and b == 0) else [root]
        notes += [N(head, "8", art=["accent"] if b == 0 else None),
                  N([root], "16"), N([root], "16")]
    notes.append(N([root], "8", art=["marcato"]))
    if dyn:
        notes[0]["dynamic"] = dyn
    if ann:
        notes[0]["annotations"] = [{"text": ann, "position": "above"}]
    return M(notes)


def hexen_drum_bar(fill=False, dyn=None):
    if fill:
        notes = [N(["f/4", "e/5"], "8", art=["accent"]), N("e/5", "8"),
                 N(["f/4", "e/5"], "8"), N("e/5", "8"),
                 N("d/5", "8"), N("b/4", "8"),
                 N(["a/4", "f/4"], "8", art=["marcato"])]
    else:
        notes = [N(["f/4", "e/5"], "8", art=["accent"]), N("e/5", "8"),
                 N(["f/4", "e/5"], "8"), N("e/5", "8"),
                 N(["f/4", "e/5"], "8"), N("e/5", "8"),
                 N(["c/5", "f/4"], "8", art=["marcato"])]
    if dyn:
        notes[0]["dynamic"] = dyn
    return M(notes)


HEXEN_FLUTE = [
    ["d/6", "c/6", "b/5", "a/5", "g/5", "f/5", "e/5"],
    ["d/6", "c/6", "b/5", "a/5", "g/5", "f/5", "d/5"],
    ["b/5", "a/5", "g/5", "f/5", "d/5", "f/5", "g/5"],
    ["g/5", "a/5", "b/5", "c/6", "b/5", "a/5", "g/5"],
]
HEXEN_ROOTS = [("d/3", "a/3"), ("d/3", None), ("b/3", "f/4"), ("c/4", "g/4")]
gtr, cello, drums, flute, php, phl = [], [], [], [], [], []
for rep in range(3):
    for i, (root, fifth) in enumerate(HEXEN_ROOTS):
        first = rep == 0 and i == 0
        gtr.append(hexen_gtr_bar(root, fifth, dyn="ff" if first else None,
                                 ann="2+2+2+1" if first else None))
        cello.append(hexen_gtr_bar(
            {"d/3": "d/2", "b/3": "b/2", "c/4": "c/3"}[root], None,
            dyn="ff" if first else None))
        drums.append(hexen_drum_bar(fill=(i == 3),
                                    dyn="ff" if first else None))
        notes = [N(k, "8") for k in HEXEN_FLUTE[i]]
        if first:
            notes[0]["dynamic"] = "f"
        flute.append(M(notes))
        chord = ["i", "i", "VI", "VII"][i]
        php.append(M([N([oct_shift(k, 0) for k in LVH_CH[chord]], "q",
                        art=["accent"]), R("q"), R("q"), R("8")]))
        phl.append(M([N(BASS_CH[chord], "hd"), R("8")]))
add_section("Var. I — Hexenjagd", "7/8", 184,
            {"Electric Guitar": gtr, "Cello": cello, "Drum Set": drums,
             "Flute": flute, "Pedal Harp": php, "Pedal Hp. L.H.": phl},
            tempo_name="Piu mosso, feroce")

# --- S7 · Var. II — Klage (10 bars, 4/4, q=72) -------------------------------

cello = [
    M([N("g/3", "h", dyn="mp", ann="espressivo"), N("b/3", "q"), N("d/4", "q")]),
    M([N("f/4", "hd"), N("d/4", "q")]),
    M([N("d/4", "h"), N("b/3", "q"), N("f/3", "q")]),
    M([N("e/4", "h"), N("c/4", "h")]),
    M([N("g/3", "q"), N("b/3", "q"), N("d/4", "h")]),
    M([N("f/4", "q"), N("g/4", "q"), N("a/4", "h")], arc=8),
    M([N("g/4", "hd"), N("f/4", "q")]),
    M([N("e/4", "h"), N("d/4", "q"), N("c/4", "q")]),
    M([N("d/4", "h"), N("a/3", "h")]),
    M([N("d/4", "w", dyn="p", ann="morendo")]),
]
KLAGE_CHORDS = [("g/4", "b/4", "d/5"), ("d/4", "f/4", "a/4"),
                ("b/3", "d/4", "f/4"), ("a/3", "c/4", "e/4"),
                ("g/4", "b/4", "d/5"), ("d/4", "f/4", "a/4"),
                ("b/3", "d/4", "f/4"), ("c/4", "e/4", "g/4"),
                ("d/4", "f/4", "a/4"), ("d/4", "f/4", "a/4")]
KLAGE_ROOTS = ["g/2", "d/2", "b/2", "a/2", "g/2", "d/2", "b/2", "c/3",
               "d/2", "d/2"]
php, phl = [], []
for i, (lo, mid, hi) in enumerate(KLAGE_CHORDS):
    notes = [N(lo, "8"), N(mid, "8"), N(hi, "8"), N(mid, "8"),
             N(lo, "8"), N(mid, "8"), N(hi, "8"), N(mid, "8")]
    if i == 0:
        notes[0]["dynamic"] = "p"
    php.append(M(notes))
    root = KLAGE_ROOTS[i]
    phl.append(M([N([root, oct_shift(root, 1)], "h"),
                  N([root, oct_shift(root, 1)], "h")]))
phl[0]["notes"][0]["dynamic"] = "p"
phl[9] = M([N(["d/2", "d/3"], "w")])
LVH_KLAGE = [["b/5", "d/6"], ["f/5", "a/5"], ["d/5", "f/5"], ["c/5", "e/5"],
             ["b/5", "d/6"], ["f/5", "a/5"], ["d/5", "f/5"], ["e/5", "g/5"],
             ["f/5", "a/5"], ["f/5", "a/5"]]
lvh = [M([N(d, "w", dyn="pp" if i == 0 else None)])
       for i, d in enumerate(LVH_KLAGE)]
flute = [M([R("w")]) for _ in range(6)] + [
    M([R("h"), N("d/6", "h", dyn="p")]),
    M([N("c/6", "h"), N("b/5", "h")]),
    M([N("c/6", "w")]),
    M([N("a/5", "w")]),
]
add_section("Var. II — Klage", "4/4", 72,
            {"Cello": cello, "Pedal Harp": php, "Pedal Hp. L.H.": phl,
             "Lever Harp": lvh, "Flute": flute},
            tempo_name="Adagio dolente")

# --- S8 · Var. III — Spieluhr (8 bars, 12/8, q=126) --------------------------

SPIEL_SEQ = ["i", "VI", "III", "VII", "i", "VI", "VII", "i"]
LVH_OST = {"i": ("d/5", "f/5", "a/5"), "VI": ("b/4", "d/5", "f/5"),
           "III": ("f/5", "a/5", "c/6"), "VII": ("c/5", "e/5", "g/5")}
lvh = []
for i, ch in enumerate(SPIEL_SEQ):
    a, b, c = LVH_OST[ch]
    notes = [N(a, "8"), N(b, "8"), N(c, "8")] * 4
    if i == 0:
        notes[0]["dynamic"] = "p"
        notes[0]["annotations"] = [{"text": "come carillon", "position": "above"}]
    lvh.append(M(notes))
flute = [
    M([N("d/5", "qd", dyn="p", ann="dolce"), N("f/5", "qd"), N("g/5", "qd"),
       N("a/5", "qd")]),
    M([N("a/5", "qd"), N("g/5", "8"), N("f/5", "8"), N("g/5", "8"),
       N("f/5", "qd"), N("d/5", "qd")]),
    M([N("f/5", "qd"), N("a/5", "qd"), N("c/6", "qd"), N("a/5", "qd")]),
    M([N("g/5", "qd"), N("e/5", "qd"), N("c/5", "hd")]),
    M([N("d/5", "qd"), N("f/5", "qd"), N("g/5", "qd"), N("a/5", "qd")]),
    M([N("a/5", "qd"), N("b/5", "qd"), N("a/5", "8"), N("g/5", "8"),
       N("f/5", "8"), N("g/5", "qd")]),
    M([N("f/5", "qd"), N("e/5", "qd"), N("d/5", "qd"), N("c/5", "qd")]),
    M([N("d/5", "wd")]),
]
SPIEL_ROOTS = {"i": "d/3", "VI": "b/2", "III": "f/2", "VII": "c/3"}
cello = []
for i, ch in enumerate(SPIEL_SEQ):
    notes = [N(SPIEL_ROOTS[ch], "qd") for _ in range(4)]
    if i == 0:
        notes[0]["dynamic"] = "p"
        notes[0]["annotations"] = [{"text": "pizz.", "position": "above"}]
    cello.append(M(notes))
CHIME = {"i": "d/6", "VI": "b/5", "III": "f/6", "VII": "c/6"}
php = [M([N(CHIME[ch], "hd", dyn="pp" if i == 0 else None), R("hd")])
       for i, ch in enumerate(SPIEL_SEQ)]
phl = [M([N(oct_shift(SPIEL_ROOTS[ch], 0), "wd",
           dyn="pp" if i == 0 else None)])
       for i, ch in enumerate(SPIEL_SEQ)]
drums = [M([N("b/5", "qd", dyn="pp" if i == 0 else None),
            N("b/5", "qd"), N("b/5", "qd"), N("b/5", "qd")])
         for i in range(8)]
add_section("Var. III — Spieluhr", "12/8", 126,
            {"Lever Harp": lvh, "Flute": flute, "Cello": cello,
             "Pedal Harp": php, "Pedal Hp. L.H.": phl, "Drum Set": drums},
            tempo_name="Allegretto, come carillon")

# --- S9 · Var. IV — Fugato (12 bars, 4/4) ------------------------------------

S1 = [N("a/3", "8"), N("d/4", "8"), N("f/4", "8"), N("g/4", "8"),
      N("a/4", "q"), N("g/4", "8"), N("f/4", "8")]
S2 = [N("a/4", "8"), N("g/4", "8"), N("f/4", "8"), N("d/4", "8"),
      N("c/4", "h")]
C1 = [N("d/4", "8"), N("c/4", "8"), N("b/3", "8"), N("a/3", "8"),
      N("b/3", "8"), N("c/4", "8"), N("d/4", "8"), N("e/4", "8")]
C2 = [N("f/4", "8"), N("e/4", "8"), N("d/4", "8"), N("c/4", "8"),
      N("b/3", "q"), N("c/4", "q")]
F1 = [N("d/4", "w")]
F2 = [N("c/4", "h"), N("b/3", "h")]


def entry(sub_delta, order):
    """order: list of ('S'|'C'|'F'|'rest', ...) per 2-bar slot."""
    bars = []
    for kind in order:
        if kind == "rest":
            bars += [M([R("w")]), M([R("w")])]
        else:
            a, b = {"S": (S1, S2), "C": (C1, C2), "F": (F1, F2)}[kind]
            bars += [M(shift_notes(a, sub_delta)), M(shift_notes(b, sub_delta))]
    return bars


cello = entry(-1, ["S", "C", "F", "C"])
cello[0]["notes"][0].update({"dynamic": "mf"})
cello[0]["notes"][0]["annotations"] = [{"text": "arco — I. Soggetto",
                                        "position": "above"}]
cello += [M(bar_8ths(["d/2"])), M(bar_8ths(["b/2"])), M(bar_8ths(["c/3"])),
          M([N("c/3", "q", art=["marcato"]), N("c/3", "q", art=["marcato"]),
             N("c/3", "h")])]
gtr = entry(0, ["rest", "S", "C", "F"])
gtr[2]["notes"][0].update({"dynamic": "mf"})
gtr[2]["notes"][0]["annotations"] = [{"text": "II.", "position": "above"}]
gtr += [M(bar_8ths(GTR_CH["i"], dyn="f")), M(bar_8ths(GTR_CH["VI"])),
        M(bar_8ths(GTR_CH["VII"])),
        M([N(GTR_CH["VII"], "q", art=["marcato"]),
           N(GTR_CH["VII"], "q", art=["marcato"]), N(GTR_CH["VII"], "h")])]
flute = entry(2, ["rest", "rest", "S", "C"])
flute[4]["notes"][0].update({"dynamic": "mf"})
flute[4]["notes"][0]["annotations"] = [{"text": "III.", "position": "above"}]
flute += [M([N("f/6", "w", dyn="f")]), M([N("f/6", "w")]),
          M([N("g/6", "w")]), M([N("g/6", "h"), N("e/6", "h")])]
php = entry(1, ["rest", "rest", "rest", "S"])
php[6]["notes"][0].update({"dynamic": "mf"})
php[6]["notes"][0]["annotations"] = [{"text": "IV.", "position": "above"}]
php += [sweep_bar("i", dyn="f"), sweep_bar("VI"), sweep_bar("VII"),
        sweep_bar("VII")]
phl = [M([R("w")]) for _ in range(8)] + \
      [M([N(["d/2", "d/3"], "h"), N(["d/2", "d/3"], "h")]),
       M([N(["b/1", "b/2"], "h"), N(["b/1", "b/2"], "h")]),
       M([N(["c/2", "c/3"], "h"), N(["c/2", "c/3"], "h")]),
       M([N(["c/2", "c/3"], "w")])]
lvh = [M([R("w")]) for _ in range(8)] + \
      [M([N(LVH_CH["i"], "q") for _ in range(4)]),
       M([N(LVH_CH["VI"], "q") for _ in range(4)]),
       M([N(LVH_CH["VII"], "q") for _ in range(4)]),
       M([N(LVH_CH["VII"], "w")])]
drums = [M([R("w")]) for _ in range(4)]
for i in range(4):
    notes = [N("e/5", "8") for _ in range(8)]
    if i == 0:
        notes[0]["dynamic"] = "pp"
    drums.append(M(notes, arc=6))
drums += [drum_chorus_bar(dyn="f"), drum_chorus_bar()]
m11 = [N(["f/4", "c/5"] if i in (4, 12) else "f/4", "16") for i in range(16)]
drums.append(M(m11, arc=10))
drums.append(M([N("c/5", "16")] * 4 + [N("d/5", "16")] * 2 +
               [N("b/4", "16")] * 2 + [N("a/4", "16")] * 4 +
               [N(["f/4", "g/4"], "16")] * 4, arc=16))
add_section("Var. IV — Fugato", "4/4", 168,
            {"Cello": cello, "Electric Guitar": gtr, "Flute": flute,
             "Pedal Harp": php, "Pedal Hp. L.H.": phl, "Lever Harp": lvh,
             "Drum Set": drums},
            tempo_name="Tempo I — Fugato")

# --- S10 · Final Chorus x2 (16 bars, 4/4, q=160, F# MAJOR) -------------------

FINAL_SEQ = ["I", "vi", "IV", "V"]
FINAL_MAP = {"I": "I", "vi": "i", "IV": "IV", "V": "V"}   # reuse minor-i shapes


def final_melody(peak=False):
    lyr1 = [["Ei-", "-sen", "-kro-", "-ne", "strahlt"],
            ["im", "Mor-", "-gen-", "-rot,"],
            ["aus", "Nacht", "ge-", "-schmie-", "-det"],
            ["—", "aus", "Not;"],
            ["Ei-", "-sen", "-kro-", "-ne", "tr\u00e4gt"],
            ["das", "Licht", "der", "Welt,"],
            ["wenn", "der", "Mor-", "-gen", "an-", "-bricht"],
            ["—", "und"]]
    lyr2 = [["Ei-", "-sen", "-kro-", "-ne", "steig"],
            ["im", "Mor-", "-gen-", "-rot,"],
            ["aus", "Nacht", "ge-", "-schmie-", "-det"],
            ["—", "aus", "Not;"],
            ["Ei-", "-sen", "-kro-", "-ne", "steig"],
            ["em-", "-por!", "im", "Licht,"],
            ["wenn", "der", "Mor-", "-gen", "an-", "-bricht"],
            ["kr\u00f6nt."]]
    L = lyr2 if peak else lyr1
    def lyr(b, i):
        return L[b][i] if i < len(L[b]) else None
    bars = [
        M([N("c/5", "8d", dyn="ff", lyr=lyr(0, 0)), N("f/5", "16", lyr=lyr(0, 1)),
           N("f/5", "q", lyr=lyr(0, 2)), N("g/5", "q", lyr=lyr(0, 3)),
           N("a/5", "q", lyr=lyr(0, 4))]),
        M([N("a/5", "qd", lyr=lyr(1, 0)), N("g/5", "8", lyr=lyr(1, 1)),
           N("f/5", "q", lyr=lyr(1, 2)), N("d/5", "q", lyr=lyr(1, 3))]),
        M([N("f/5", "8d", lyr=lyr(2, 0)), N("g/5", "16", lyr=lyr(2, 1)),
           N("a/5", "q", lyr=lyr(2, 2)), N("b/5", "q", lyr=lyr(2, 3)),
           N("a/5", "q", lyr=lyr(2, 4))]),
        M([N("g/5", "h", lyr=lyr(3, 0)), N("e/5", "q", lyr=lyr(3, 1)),
           N("c/5", "q", lyr=lyr(3, 2))]),
        M([N("c/5", "8d", lyr=lyr(4, 0)), N("f/5", "16", lyr=lyr(4, 1)),
           N("f/5", "q", lyr=lyr(4, 2)), N("g/5", "q", lyr=lyr(4, 3)),
           N("a/5", "q", lyr=lyr(4, 4))]),
        M([N("a/5", "qd", lyr=lyr(5, 0)), N("g/5", "8", lyr=lyr(5, 1)),
           N("f/5", "q", lyr=lyr(5, 2)), N("d/5", "q", lyr=lyr(5, 3))]),
        M([N("f/5", "8d", lyr=lyr(6, 0)), N("g/5", "16", lyr=lyr(6, 1)),
           N("a/5", "q", lyr=lyr(6, 2)), N("g/5", "8", lyr=lyr(6, 3)),
           N("f/5", "8", lyr=lyr(6, 4)), N("e/5", "q", lyr=lyr(6, 5))]),
        M([N("f/5", "h", lyr=lyr(7, 0)), N("g/5", "h", lyr=lyr(7, 1))]),
    ]
    if peak:
        bars[5] = M([N("a/5", "qd", lyr="em-"), N("c/6", "8", lyr="-por!"),
                     N("b/5", "q", lyr="im"), N("a/5", "q", lyr="Licht,")])
        bars[7] = M([N("f/5", "w", lyr="kr\u00f6nt.")])
    return bars


voice = final_melody(False) + final_melody(True)
seq16 = FINAL_SEQ * 4
gtr, cello, drums, php, phl, lvh = [], [], [], [], [], []
for i, ch in enumerate(seq16):
    shape = FINAL_MAP[ch]
    gtr.append(M(bar_8ths(GTR_CH[shape], dyn="ff" if i == 0 else None)))
    cello.append(M(bar_8ths([BASS_CH[shape]], dyn="ff" if i == 0 else None)))
    crash = "g/5" if (i == 0 or (i >= 8 and i % 2 == 0)) else (
        "a/5" if (i >= 8 and i % 2 == 1) else None)
    drums.append(drum_chorus_bar(crash=crash, fill=i in (7, 15),
                                 dyn="ff" if i == 0 else None))
    php.append(sweep_bar(shape, dyn="f" if i == 0 else None))
    phl.append(M([N(PHL_CH[shape], "h"), N(PHL_CH[shape], "h")]))
    lvh.append(M([N(LVH_CH[shape], "q") for _ in range(4)]))
flute = [M([N("c/6", "w", dyn="f")]), M([N("a/5", "w")]),
         M([N("f/6", "w")]), M([N("g/6", "w")]),
         M([N("c/6", "w")]), M([N("a/5", "w")]),
         M([N("f/6", "w")]), M([N("g/6", "w")])]
descant = [M(shift_notes(bar["notes"], 1)) for bar in final_melody(False)]
descant[5] = M([N("a/6", "qd"), N("a/6", "8"), N("b/6", "q"), N("a/6", "q")])
descant[7] = M([N("f/6", "w")])
descant[0]["notes"][0]["dynamic"] = "ff"
flute += descant
add_section("Final Chorus", "4/4", 160,
            {"Voice": voice, "Electric Guitar": gtr, "Cello": cello,
             "Drum Set": drums, "Pedal Harp": php, "Pedal Hp. L.H.": phl,
             "Lever Harp": lvh, "Flute": flute},
            tempo_name="Trionfale (F-Dur... F#-Dur!)")

# --- S11 · Button (3 bars, 4/4) ----------------------------------------------

STAB = ["q", "8", "8", "q", "q"]     # hit, rest, hit, hit, rest


def stab_bar(keys, dyn=None):
    return M([N(keys, "q", art=["marcato"], dyn=dyn), R("8"),
              N(keys, "8"), N(keys, "q", art=["marcato"]), R("q")])


gtr = [stab_bar(["f/3", "c/4"], dyn="fff"),
       M([N(["c/4", "g/4"], "w")]),
       M([N(["f/3", "c/4", "f/4"], "w", art=["fermata-above"],
            ann="let ring")])]
cello = [stab_bar("f/2", dyn="fff"),
         M([N("c/3", "w")]),
         M([N("f/2", "w", art=["fermata-above"])])]
drums = [M([N(["f/4", "g/5"], "q", art=["marcato"], dyn="fff"), R("8"),
            N("f/4", "8"), N(["f/4", "c/5"], "q", art=["marcato"]), R("q")]),
         M([N("c/5", "16")] * 4 + [N("d/5", "16")] * 4 +
           [N("b/4", "16")] * 4 + [N(["a/4", "g/4"], "16")] * 4, arc=18),
         M([N(["f/4", "a/5"], "w", art=["fermata-above"])])]
run = ["f/3", "g/3", "a/3", "b/3", "c/4", "d/4", "e/4", "f/4",
       "g/4", "a/4", "b/4", "c/5", "d/5", "e/5", "f/5", "g/5"]
php = [stab_bar(["f/4", "a/4", "c/5"], dyn="ff"),
       M([N(k, "16") for k in run], arc=24),
       M([N(["f/3", "c/4", "f/4", "a/4", "c/5", "f/5"], "w",
            art=["fermata-above"], roll=True)])]
phl = [stab_bar(["f/2", "f/3"], dyn="ff"),
       M([N(["c/2", "c/3"], "w")]),
       M([N(["f/1", "f/2"], "w", art=["fermata-above"])])]
lvh = [stab_bar(["f/5", "a/5", "c/6"], dyn="ff"),
       M([R("w")]),
       M([N(["f/5", "a/5", "c/6"], "w", art=["fermata-above"])])]
flute = [M([R("w")]),
         M([R("h"), N("e/6", "q"), N("g/6", "q")]),
         M([N("f/6", "w", art=["fermata-above"], dyn="fff")])]
add_section("Button", "4/4", 160,
            {"Electric Guitar": gtr, "Cello": cello, "Drum Set": drums,
             "Pedal Harp": php, "Pedal Hp. L.H.": phl, "Lever Harp": lvh,
             "Flute": flute},
            tempo_name="Maestoso")

# ------------------------------------------------------------ validate ----

def validate():
    problems = []
    for name, measures in SCORE.items():
        assert len(measures) == len(METERS), (name, len(measures), len(METERS))
        transpose = TRANSPOSE[name]
        for i, bar in enumerate(measures):
            want = met_quarters(METERS[i])
            got = sum(Q(n["duration"]) for n in bar["notes"])
            if abs(got - want) > 1e-6:
                problems.append(f"{name} bar {i + 1}: {got}q vs meter {want}q")
            if name == "Drum Set":
                continue
            lo, hi = RANGES[name]
            for note in bar["notes"]:
                if note.get("rest"):
                    continue
                for k in note["keys"]:
                    p = m2m.parse_key(k, KEYSIG) + transpose
                    if not lo <= p <= hi:
                        problems.append(
                            f"{name} bar {i + 1}: {k} sounds MIDI {p}, "
                            f"outside [{lo}, {hi}]")
    if problems:
        for p in problems:
            print("PROBLEM:", p)
        raise SystemExit(f"{len(problems)} problem(s)")


validate()
humanize(SCORE, METERS)


# The inline engraver's contract differs from the converter's historical one:
# there, a bare letter is a NATURAL (true sounding pitch), and augmentation
# dots are trailing "."s, not "d"s.  The converter accepts both grammars, so
# emit the engraver's -- explicit sharps for the six signature-sharped
# letters, "." dots -- and the one JSON file is valid for both consumers.
SHARP_LETTERS = set("cdefga")   # F# major sharps everything except B


def respell(score):
    for name, measures in score.items():
        drums = name == "Drum Set"          # drum keys are staff positions
        for bar in measures:
            for note in bar["notes"]:
                note["duration"] = str(note["duration"]).replace("d", ".")
                if drums or note.get("rest"):
                    continue
                keys = []
                for k in note["keys"]:
                    letter, _, octv = k.partition("/")
                    if letter in SHARP_LETTERS:
                        letter += "#"
                    keys.append(f"{letter}/{octv}")
                note["keys"] = keys


respell(SCORE)

spec = {
    "type": "music",
    "keySignature": "F#",
    "timeSignature": "4/4",
    "autoBeam": True,
    "title": "DIE EISERNE KRONE",
    "subtitle": "Main Title — Thema und Variationen (v2)",
    "composer": "D. Cohn",
    "lyricist": "Libretto: A. Vandenberg",
    "tempo": {"name": "Misterioso", "duration": "q", "bpm": 84},
    "staves": [
        {"name": name, "shortName": short, "clef": clef, "midi": midi,
         "measures": SCORE[name]}
        for name, short, clef, midi in PARTS
    ],
}
OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text(json.dumps(spec, ensure_ascii=False, indent=1))

total = 0.0
print(f"wrote {OUT} ({OUT.stat().st_size:,} bytes)")
bar_index = 0
for label, bars, meter, bpm in SECTIONS:
    seconds = bars * met_quarters(meter) * 60.0 / bpm
    print(f"  {label:24} {bars:3} bars  {meter:5} q={bpm:<4} "
          f"{int(total // 60)}:{total % 60:05.2f}  +{seconds:6.2f}s")
    total += seconds
    bar_index += bars
print(f"  TOTAL {bar_index} bars, {int(total // 60)}:{total % 60:05.2f} "
      f"({total:.2f}s)")
