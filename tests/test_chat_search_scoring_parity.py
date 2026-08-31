"""
Cross-language parity: the chat-search relevance score and result ordering are
implemented TWICE, and the two copies must agree numerically.

Why this exists
---------------
Conversation search has two execution paths that must produce identical
ordering for identical input:

  app/storage/chat_search.py       _relevance_score() + the sort blocks in
                                   search_chats()   -- the primary path
  frontend/src/utils/db.ts         scoreOf() + the sort blocks in
                                   _searchConversationsLocal()  -- the offline
                                   fallback, used when the server is briefly
                                   unreachable or chats are not yet synced

Nothing connects them at build time.  If the weighting constants or the
comparator tie-breaks drift apart, search results silently REORDER depending
on whether the server answered -- and the user has no way to tell which
ranking they are looking at.  A constant nudged on one side only is invisible
from either side in isolation: both implementations remain internally
consistent and both pass their own tests.

What is asserted
----------------
1. Every weighting constant exists in BOTH sources with the same value.
2. The JS scorer, EXTRACTED FROM THE REAL db.ts SOURCE and executed under
   node, returns the same number as the Python scorer for a shared fixture
   set spanning every branch (title hit, opening bonus, tf saturation,
   length normalisation).
3. The JS sort comparators, likewise extracted and executed, produce the same
   result order as the Python sort for all three modes -- including the
   `oldest` unknown-timestamp rule, where 0 must sort LAST rather than first.

The JS side is parsed out of the real source and run, not reimplemented here.
A reimplementation would agree with itself and certify nothing about the file
that actually ships.
"""

import json
import math
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from app.storage.chat_search import (
    LENGTH_NORM_FREE_MESSAGES,
    OPENING_MESSAGE_COUNT,
    OPENING_MULTIPLIER,
    TF_K1,
    TITLE_WEIGHT,
    _relevance_score,
    search_chats,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
DB_TS = REPO_ROOT / "frontend" / "src" / "utils" / "db.ts"

# name -> Python value.  Parity is checked against the Python module's real
# attributes, so renaming a Python constant without touching db.ts fails here
# rather than silently skipping the check.
SCORING_CONSTANTS = {
    "TITLE_WEIGHT": TITLE_WEIGHT,
    "OPENING_MESSAGE_COUNT": OPENING_MESSAGE_COUNT,
    "OPENING_MULTIPLIER": OPENING_MULTIPLIER,
    "TF_K1": TF_K1,
    "LENGTH_NORM_FREE_MESSAGES": LENGTH_NORM_FREE_MESSAGES,
}

node = pytest.mark.skipif(
    shutil.which("node") is None,
    reason="node not available; JS-side parity cannot be executed",
)


# --------------------------------------------------------------------------
# Extraction from the real db.ts source
# --------------------------------------------------------------------------

def _db_source() -> str:
    assert DB_TS.exists(), f"expected frontend db source at {DB_TS}"
    return DB_TS.read_text(encoding="utf-8")


def _parse_js_constant(source: str, name: str) -> float:
    """Pull `const NAME = <number>;` out of the TS source."""
    match = re.search(rf"const\s+{re.escape(name)}\s*=\s*(-?[0-9.]+)\s*;", source)
    assert match, (
        f"{name} not found in {DB_TS.name}. The offline search fallback must "
        f"mirror app/storage/chat_search.py's scoring constants; if the "
        f"constant was renamed, rename it on both sides."
    )
    return float(match.group(1))


def _extract_js_block(source: str, pattern: str, what: str) -> str:
    match = re.search(pattern, source, re.DOTALL)
    assert match, (
        f"could not locate {what} in {DB_TS.name}. This test executes the real "
        f"shipped implementation; if it was refactored, update the extraction "
        f"pattern rather than reimplementing the logic here."
    )
    return match.group(1)


def _js_scorer_body(source: str) -> str:
    return _extract_js_block(
        source,
        r"const\s+scoreOf\s*=\s*\([^)]*\)\s*:\s*number\s*=>\s*\{(.*?)\n\s*\};",
        "the scoreOf() arrow function",
    )


def _js_sort_block(source: str) -> str:
    """The age/rel accessors plus the three-way sort dispatch, as runnable JS.

    The extracted accessors are typed (`(r: SearchResult) => ...`), which plain
    node cannot evaluate.  Only the parameter type annotation is removed; the
    comparator expressions -- which are what this test exists to verify -- are
    executed exactly as written in db.ts.
    """
    block = _extract_js_block(
        source,
        r"(const\s+age\s*=\s*\(r:.*?results\.sort\(\(a, b\) => \(rel\(b\).*?\n\s*\})",
        "the result-ordering block",
    )
    stripped = re.sub(r"\(\s*(\w+)\s*:\s*[A-Za-z_$][\w.<>\[\]]*\s*\)", r"(\1)", block)
    assert ":" not in stripped.split("=>")[0], (
        "failed to strip TypeScript parameter annotations from the sort block"
    )
    return stripped


def _run_node(script: str, payload) -> object:
    """Execute a JS snippet with `payload` as JSON on stdin; return parsed stdout.

    Payload goes over stdin rather than argv: under `node -e` the argv offset
    differs from script-file invocation, and one fixture carries hundreds of
    highlight positions, which argv length limits could truncate.
    """
    proc = subprocess.run(
        ["node", "-e", script],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, (
        f"node harness failed (exit {proc.returncode}):\n"
        f"STDERR: {proc.stderr}\nSTDOUT: {proc.stdout}"
    )
    return json.loads(proc.stdout)


def _js_scores(cases, constant_overrides=None):
    """Score every fixture with the JS implementation lifted from db.ts."""
    source = _db_source()
    values = {n: _parse_js_constant(source, n) for n in SCORING_CONSTANTS}
    if constant_overrides:
        values.update(constant_overrides)
    decls = "\n".join(f"const {n} = {v};" for n, v in values.items())
    script = (
        decls
        + "\nconst scoreOf = (ms, titleHit, total) => {"
        + _js_scorer_body(source)
        + "\n};\n"
        + "const cases = JSON.parse(require('fs').readFileSync(0, 'utf8'));\n"
        + "console.log(JSON.stringify("
        "cases.map(c => scoreOf(c.matches, c.title, c.total))));\n"
    )
    return _run_node(script, cases)


def _js_order(results, sort_mode):
    """Order results with the JS comparators lifted from db.ts."""
    source = _db_source()
    script = (
        f"const sort = {json.dumps(sort_mode)};\n"
        "let results = JSON.parse(require('fs').readFileSync(0, 'utf8'));\n"
        + _js_sort_block(source)
        + "\nconsole.log(JSON.stringify(results.map(r => r.conversationId)));\n"
    )
    return _run_node(script, results)


# --------------------------------------------------------------------------
# Shared fixtures -- one per behavioural branch of the score
# --------------------------------------------------------------------------

def _match(index, occurrences=1):
    return {
        "messageIndex": index,
        "highlightPositions": [{"start": i, "length": 3} for i in range(occurrences)],
    }


SCORE_CASES = [
    # title hit alone, no body hits
    {"id": "title-only", "matches": [], "title": True, "total": 2},
    # incidental mentions scattered late through a long conversation
    {"id": "verbose-incidental",
     "matches": [_match(i) for i in range(20, 32)], "title": False, "total": 60},
    # one message repeating the term many times (tf saturation)
    {"id": "single-shouty", "matches": [_match(0, 10)], "title": False, "total": 1},
    # sustained on-topic short conversation
    {"id": "focused-short",
     "matches": [_match(i) for i in range(4)], "title": False, "total": 4},
    # same hits, sprawling conversation (length normalisation)
    {"id": "focused-sprawling",
     "matches": [_match(i) for i in range(4)], "title": False, "total": 80},
    # opening-message bonus boundary: index 1 gets it, index 2 does not
    {"id": "opening-boundary-in", "matches": [_match(1)], "title": False, "total": 6},
    {"id": "opening-boundary-out", "matches": [_match(2)], "title": False, "total": 6},
    # title AND body
    {"id": "title-plus-body", "matches": [_match(0)], "title": True, "total": 1},
    # under the length-norm free threshold, norm must clamp to 1
    {"id": "at-free-threshold", "matches": [_match(5)], "title": False, "total": 10},
    # extreme repetition -- asymptote, not linear growth
    {"id": "extreme-repetition",
     "matches": [_match(9, 500)], "title": False, "total": 12},
    # no hits at all (degenerate, but the function must not diverge)
    {"id": "empty", "matches": [], "title": False, "total": 1},
]


def _py_scores(cases):
    return [_relevance_score(c["matches"], c["title"], c["total"]) for c in cases]


# --------------------------------------------------------------------------
# 1. Constant parity
# --------------------------------------------------------------------------

@pytest.mark.parametrize("name", sorted(SCORING_CONSTANTS))
def test_scoring_constant_matches_frontend(name):
    """Each Python scoring constant must appear in db.ts with the same value."""
    js_value = _parse_js_constant(_db_source(), name)
    py_value = float(SCORING_CONSTANTS[name])
    assert js_value == py_value, (
        f"{name} disagrees: chat_search.py={py_value} db.ts={js_value}. "
        f"Search results would reorder depending on whether the server "
        f"answered or the offline fallback ran."
    )


# --------------------------------------------------------------------------
# 2. Executable score parity
# --------------------------------------------------------------------------

@node
def test_js_and_python_scores_are_identical():
    """The real db.ts scorer must return the same numbers as the Python one."""
    py = _py_scores(SCORE_CASES)
    js = _js_scores(SCORE_CASES)
    assert len(js) == len(SCORE_CASES)
    mismatches = [
        (c["id"], p, j)
        for c, p, j in zip(SCORE_CASES, py, js)
        if not math.isclose(p, j, rel_tol=0, abs_tol=1e-9)
    ]
    assert not mismatches, (
        "score divergence between chat_search.py and db.ts "
        f"(case, python, js): {mismatches}"
    )


@node
def test_score_parity_is_not_vacuous():
    """Perturbing a constant on the JS side alone must break parity.

    Without this, a regex that silently failed to match -- or a harness that
    quietly fell back to a default -- would let the parity assertion pass
    while comparing nothing.
    """
    py = _py_scores(SCORE_CASES)
    perturbed = _js_scores(SCORE_CASES, constant_overrides={"TITLE_WEIGHT": 3.5})
    diverged = [
        c["id"] for c, p, j in zip(SCORE_CASES, py, perturbed)
        if not math.isclose(p, j, rel_tol=0, abs_tol=1e-9)
    ]
    assert diverged, (
        "changing TITLE_WEIGHT on the JS side produced identical scores, so "
        "the harness is not actually executing the extracted implementation."
    )


@node
def test_extracted_js_scorer_reproduces_known_ranking():
    """Positive control: the JS scorer must rank a titled hit above a verbose one.

    Guards against a harness that runs but computes something degenerate
    (e.g. all zeros), which would satisfy parity if Python were equally
    broken.  Asserted on the JS side specifically, since the Python side's
    ranking behaviour is covered in test_chat_search_ranking.py.
    """
    by_id = {c["id"]: s for c, s in zip(SCORE_CASES, _js_scores(SCORE_CASES))}
    assert by_id["title-only"] > by_id["verbose-incidental"], (
        "a conversation whose title matches must outrank one with a dozen "
        "incidental late mentions"
    )
    assert by_id["focused-short"] > by_id["single-shouty"], (
        "sustained hits across messages must outrank one repetitive message"
    )
    assert by_id["focused-short"] > by_id["focused-sprawling"], (
        "identical hit counts must favour the shorter conversation"
    )
    assert by_id["opening-boundary-in"] > by_id["opening-boundary-out"], (
        "the opening-message bonus must apply below OPENING_MESSAGE_COUNT only"
    )
    assert by_id["empty"] == 0.0


# --------------------------------------------------------------------------
# 3. Ordering parity, including the unknown-timestamp rule
# --------------------------------------------------------------------------

# Deliberately includes a 0 ("unknown") timestamp and a relevance tie so both
# the tie-break and the `oldest` unknown-handling are exercised.
ORDER_ROWS = [
    {"conversationId": "hi-rel-old", "relevanceScore": 9.0, "lastActivityAt": 1_000},
    {"conversationId": "lo-rel-new", "relevanceScore": 2.0, "lastActivityAt": 9_000},
    {"conversationId": "tie-a", "relevanceScore": 5.0, "lastActivityAt": 3_000},
    {"conversationId": "tie-b", "relevanceScore": 5.0, "lastActivityAt": 7_000},
    {"conversationId": "unknown-ts", "relevanceScore": 4.0, "lastActivityAt": 0},
]


def _py_order(rows, sort_mode):
    """Apply search_chats' ordering rules to prebuilt rows."""
    ordered = list(rows)
    if sort_mode == "newest":
        ordered.sort(key=lambda r: (r["lastActivityAt"], r["relevanceScore"]),
                     reverse=True)
    elif sort_mode == "oldest":
        ordered.sort(key=lambda r: (r["lastActivityAt"] or float("inf"),
                                    -r["relevanceScore"]))
    else:
        ordered.sort(key=lambda r: (r["relevanceScore"], r["lastActivityAt"]),
                     reverse=True)
    return [r["conversationId"] for r in ordered]


@node
@pytest.mark.parametrize("mode", ["relevance", "newest", "oldest"])
def test_sort_order_matches_between_paths(mode):
    """Both implementations must order the same rows identically."""
    assert _js_order(ORDER_ROWS, mode) == _py_order(ORDER_ROWS, mode), (
        f"sort={mode} orders differently in db.ts than in chat_search.py"
    )


@node
def test_oldest_puts_unknown_timestamps_last_in_both_paths():
    """A 0 timestamp means 'unknown' and must not pose as the oldest chat.

    This is the one ordering rule where the obvious implementation is wrong in
    a way that looks right: sorting ascending on a raw 0 makes every
    timestamp-less conversation appear to be the project's oldest.
    """
    js = _js_order(ORDER_ROWS, "oldest")
    py = _py_order(ORDER_ROWS, "oldest")
    assert js == py
    assert js[-1] == "unknown-ts", (
        f"unknown timestamp should sort last under 'oldest', got order {js}"
    )
    assert js[0] == "hi-rel-old", (
        f"the genuinely oldest conversation should lead 'oldest', got {js}"
    )


# --------------------------------------------------------------------------
# 4. The ordering rules asserted above are the ones search_chats() really uses
# --------------------------------------------------------------------------

def _write_chat(chats_dir: Path, chat_id: str, *, title: str,
                body: str, last_active: int):
    chats_dir.mkdir(parents=True, exist_ok=True)
    (chats_dir / f"{chat_id}.json").write_text(json.dumps({
        "id": chat_id,
        "title": title,
        "messages": [{"id": "m1", "role": "human", "content": body}],
        "createdAt": 1,
        "lastActiveAt": last_active,
    }))


@pytest.mark.parametrize("mode", ["relevance", "newest", "oldest"])
def test_py_order_helper_agrees_with_search_chats(tmp_path, mode):
    """Pin _py_order to the real search_chats() ordering.

    The helper above duplicates search_chats' sort keys so the JS comparison
    can run on synthetic rows.  If search_chats' ordering changed, the parity
    tests would keep comparing the frontend against a stale local copy and
    pass while both had drifted from the shipped behaviour.
    """
    home = tmp_path / ".ziya"
    chats = home / "projects" / "p1" / "chats"
    # Distinct relevance (title hit vs body-only) and distinct activity, so
    # relevance and newest/oldest each produce a different order.
    _write_chat(chats, "titled", title="quota policy", body="unrelated text",
                last_active=1_000)
    _write_chat(chats, "bodyonly", title="misc", body="the quota appears here",
                last_active=9_000)

    got = [r["conversationId"] for r in search_chats(
        ziya_home=home, project_id="p1", query="quota", sort=mode)]
    rows = [
        {"conversationId": r["conversationId"],
         "relevanceScore": r["relevanceScore"],
         "lastActivityAt": r["lastActivityAt"]}
        for r in search_chats(ziya_home=home, project_id="p1", query="quota")
    ]
    assert got == _py_order(rows, mode), (
        f"sort={mode}: search_chats returned {got}, local ordering helper "
        f"predicted {_py_order(rows, mode)}"
    )
