"""
Apply ledger: what happened to the diffs the model proposed in a turn.

design/await-diff-apply.md.  When an interactive response ends with closed
```diff fences, the streaming executor does not end the turn: it holds the
tool chain open until every diffed file has an apply result, then hands the
model the per-file outcome and lets it continue.  A partial or failed apply
is reported at once.  A message the user sends ends the wait and goes to the
model as feedback, carrying whatever has been applied by then.

This module is the bookkeeping between the two sides:

* ``/api/apply-changes`` calls ``record`` with the outcome of each click.
  Results are kept per conversation in arrival order under a monotonically
  increasing ``seq``, so a click that lands while the model is still typing
  counts as soon as the wait starts.
* The executor builds the expected set with ``expected_from_text`` over the
  turn's text, polls ``wait_state`` with the seq the turn started at, and
  gives the model ``format_report``.
* An apply that lands after its turn has ended reaches nobody that way.
  ``begin_turn``, called when the next turn starts, appends a note to the
  incoming user message listing every result the model has not been shown,
  and the diffs of its last response that have no apply recorded.

Pure bookkeeping, in memory, no I/O.  Lost on restart, which is harmless:
a restart also ends the held turn.
"""

from __future__ import annotations

import hashlib
import re
import threading
import time
from typing import Any, Dict, List, Optional

MAX_RESULTS_PER_CONVERSATION = 200

_lock = threading.Lock()
_seq = 0
_results: Dict[str, List[Dict[str, Any]]] = {}
# conversation -> ledger seq when its latest turn started (begin_turn).
_turn_start: Dict[str, int] = {}

_FENCE_RE = re.compile(r"```diff[^\n]*\n(.*?)```", re.DOTALL)


def reset_for_tests() -> None:
    global _seq
    with _lock:
        _seq = 0
        _results.clear()
        _turn_start.clear()


# ── expected set ──────────────────────────────────────────────────────────

def diff_key(diff_text: Optional[str]) -> Optional[str]:
    """Content key of one file's diff: a hash of its added and removed
    lines.  File headers, hunk headers, context and trailing whitespace are
    left out, because the Apply button trims the text and rewrites hunk
    headers before sending it; the key of what the server applied then
    equals the key of the fence the model wrote.  None when the text has no
    changed lines.  A removed line whose own text starts with ``-- `` reads
    as a header and is skipped on both sides alike."""
    if not isinstance(diff_text, str):
        return None
    h = hashlib.sha256()
    n = 0
    for ln in diff_text.splitlines():
        if not ln or ln[0] not in "+-" or ln.startswith(("--- ", "+++ ")):
            continue
        h.update(ln.rstrip().encode("utf-8", "replace"))
        h.update(b"\n")
        n += 1
    return h.hexdigest()[:16] if n else None


# The same test as VALID_HUNK_HEADER in frontend/src/components/MarkdownRenderer.tsx.
_NUMERIC_HUNK_RE = re.compile(r"^@@\s+-\d+(?:,\d+)?\s+\+\d+(?:,\d+)?\s+@@")


def _chains_to_previous(body: str) -> bool:
    """Whether the client merges this headerless fence into the headed diff
    before it (chainHeaderlessContinuationDiffs in MarkdownRenderer.tsx): it
    starts with a numeric hunk header, or its body only adds lines.  The two
    fences then get one Apply button and are sent as one diff, so they must
    be keyed as one."""
    text = (body or "").lstrip()
    if not text:
        return False
    if _NUMERIC_HUNK_RE.match(text):
        return True
    if text.startswith("@@"):
        text = text.split("\n", 1)[1] if "\n" in text else ""
    lines = [ln for ln in text.split("\n") if ln != "" and not ln.startswith("\\")]
    return bool(lines) and all(ln.startswith("+") for ln in lines)


# ── fences a later fence replaces ─────────────────────────────────────────
# Mirror of findSupersededDiffIndices in frontend/src/utils/diffUtils.ts: the
# UI fades an earlier diff as "Superseded by a later revision below" and the
# hold must not wait for it.  The two are held together by
# tests/fixtures/diff_supersede_cases.json; change one only with the other.

_RANGE_RE = re.compile(r"@@ -(\d+)(?:,(\d+))? \+")
_NOPOS_RE = re.compile(r"^@@.*@@\s*ZIYA_NOPOS\s*(.*)$", re.MULTILINE)
_MIN_SUBSTANTIVE_LINE_CHARS = 4
_MIN_LINES_FOR_POSITION_FREE_MATCH = 6
_REDO_OVERLAP_THRESHOLD = 0.8
_NEAR_GAP_LINES = 10


def _synth_hunk_headers(diff: str) -> str:
    """synthesizeMissingHunkHeaders (MarkdownRenderer.tsx): a hunk header
    without a numeric range becomes a placeholder range tagged ZIYA_NOPOS,
    keeping its section hint.  The client runs supersede detection on this
    form."""
    lines = diff.split("\n")
    if not any(l.startswith("@@") and not _NUMERIC_HUNK_RE.match(l) for l in lines):
        return diff
    out: List[str] = []
    for i, line in enumerate(lines):
        if not line.startswith("@@") or _NUMERIC_HUNK_RE.match(line):
            out.append(line)
            continue
        hint = re.sub(r"@@.*$", "", re.sub(r"^@@+", "", line)).strip()
        old = new = 0
        for j in range(i + 1, len(lines)):
            b = lines[j]
            if b.startswith("@@") or b.startswith("diff --git"):
                break
            if b == "" and j == len(lines) - 1:
                continue
            if b[:1] == "+":
                new += 1
            elif b[:1] == "-":
                old += 1
            elif b.startswith("\\"):
                pass
            else:
                old += 1
                new += 1
        out.append(f"@@ -1,{old} +1,{new} @@ ZIYA_NOPOS" + (f" {hint}" if hint else ""))
    return "\n".join(out)


def _hunk_ranges(diff: str) -> List[tuple]:
    out = []
    for m in _RANGE_RE.finditer(diff):
        start = int(m.group(1))
        count = int(m.group(2)) if m.group(2) is not None else 1
        if start == 0 and count == 0:
            continue                      # new-file creation
        out.append((start, start + max(count - 1, 0)))
    return out


def _ranges_overlap(a: List[tuple], b: List[tuple]) -> bool:
    for a0, a1 in a:
        for b0, b1 in b:
            if a0 <= b1 and b0 <= a1:
                size = min(a1, b1) - max(a0, b0) + 1
                smaller = min(a1 - a0 + 1, b1 - b0 + 1)
                if smaller > 0 and size / smaller > 0.5:
                    return True
    return False


def _removed_lines(diff: str) -> set:
    """Trimmed text of the lines a diff removes (file headers excluded)."""
    out = set()
    for ln in diff.split("\n"):
        if not ln.startswith("-") or ln.startswith("---"):
            continue
        body = ln[1:].strip()
        if body:
            out.add(body)
    return out


def _is_sequential_pair(earlier: str, later: str) -> bool:
    """The earlier diff only removes (makes way) and the later adds.

    Not when both remove the same source line: the later diff was then
    written against the same base as the earlier, so it is a second attempt
    at that edit.  Once the earlier is applied the line is gone and the
    later could not remove it again."""
    if _removed_lines(earlier) & _removed_lines(later):
        return False
    skip = ("@@", "diff ", "---", "+++")
    e_add = e_rem = l_add = 0
    for ln in earlier.split("\n"):
        if ln.startswith(skip):
            continue
        if ln.startswith("+"):
            e_add += 1
        elif ln.startswith("-"):
            e_rem += 1
    for ln in later.split("\n"):
        if not ln.startswith(skip) and ln.startswith("+"):
            l_add += 1
    return e_rem > 0 and e_add <= 1 and l_add > 0


def _change_signature(diff: str) -> set:
    sig = set()
    for ln in diff.split("\n"):
        if ln.startswith(("+++", "---")) or ln[:1] not in ("+", "-"):
            continue
        body = ln[1:].strip()
        if len(body) < _MIN_SUBSTANTIVE_LINE_CHARS or not re.search(r"[A-Za-z0-9]", body):
            continue
        sig.add(ln[0] + body)
    return sig


def _is_redo(earlier: str, later: str, e_ranges: List[tuple], l_ranges: List[tuple]) -> bool:
    """The later diff repeats the earlier one's change body at another anchor."""
    if not e_ranges or not l_ranges:
        return False
    a, b = _change_signature(earlier), _change_signature(later)
    smaller = min(len(a), len(b))
    if smaller == 0 or len(a & b) / smaller < _REDO_OVERLAP_THRESHOLD:
        return False
    if smaller >= _MIN_LINES_FOR_POSITION_FREE_MATCH:
        return True
    gap = min(max(a0 - b1 - 1, b0 - a1 - 1, 0) for a0, a1 in e_ranges for b0, b1 in l_ranges)
    return gap <= _NEAR_GAP_LINES


def _superseded_indices(paths: List[str], diffs: List[str]) -> set:
    """Indices of single-file diffs a later diff for the same file replaces."""
    if len(diffs) <= 1:
        return set()
    texts = [_synth_hunk_headers(d) for d in diffs]
    ranges = [_hunk_ranges(t) for t in texts]
    locs = [{m.group(1).strip() for m in _NOPOS_RE.finditer(t) if m.group(1).strip()} for t in texts]
    out: set = set()
    for i in range(len(texts)):
        if not paths[i]:
            continue
        for j in range(i + 1, len(texts)):
            if paths[j] != paths[i]:
                continue
            if not ranges[i] and not ranges[j]:          # two new-file diffs
                out.add(i)
                break
            if locs[i] or locs[j]:
                if locs[i] & locs[j] and not _is_sequential_pair(texts[i], texts[j]):
                    out.add(i)
                    break
                continue
            if _ranges_overlap(ranges[i], ranges[j]) or _is_redo(texts[i], texts[j], ranges[i], ranges[j]):
                if _is_sequential_pair(texts[i], texts[j]):
                    continue
                out.add(i)
                break
    return out


def _diff_files(text: str) -> List[Dict[str, Any]]:
    """Every (path, hunk count) a closed ```diff fence in ``text`` targets.
    An entry a later fence for the same file replaces (the client's
    supersede rule) carries ``superseded: True``."""
    from app.utils.diff_utils.parsing.diff_parser import (
        extract_target_file_from_diff, split_combined_diff)
    out: List[Dict[str, Any]] = []
    last: Optional[int] = None   # entry of the last headed fence's final file
    for i, m in enumerate(_FENCE_RE.finditer(text or "")):
        body = m.group(1)
        parts = split_combined_diff(body) or [body]
        headed = False
        for part in parts:
            path = extract_target_file_from_diff(part)
            if not path:
                continue
            headed = True
            hunks = sum(1 for ln in part.splitlines() if ln.startswith("@@"))
            # part_key: the headed part alone, before any continuation fence
            # is merged into it.  The validation hook sees fences one at a
            # time, so this is the key it can compute (settled_parts).
            out.append({"path": path.strip(), "hunks": hunks, "fence_index": i,
                        "key": diff_key(part), "part_key": diff_key(part), "_text": part})
            last = len(out) - 1
        # A headerless continuation belongs to the diff before it; it adds
        # hunks to that entry, not an entry of its own.
        if headed or last is None or not _chains_to_previous(body):
            continue
        prev = out[last]
        # As the client merges it: a bare body gets a placeholder hunk
        # marker, so it reads as a hunk of its own (no effect on the key).
        prev["_text"] += ("\n" if body.lstrip().startswith("@@") else "\n@@\n") + body
        prev["hunks"] += max(1, sum(1 for ln in body.splitlines() if ln.startswith("@@")))
        prev["key"] = diff_key(prev["_text"])
    gone = _superseded_indices([_norm(e["path"]) for e in out], [e["_text"] for e in out])
    for n, entry in enumerate(out):
        entry.pop("_text", None)
        if n in gone:
            entry["superseded"] = True
    return out


def expected_from_text(text: str, files: Optional[List[str]] = None) -> Dict[str, Any]:
    """``{"expected": [{path, count, hunks, keys}], "not_found": [...]}``.

    ``files`` restricts to those paths; names with no diff in the text are
    reported, not waited on.  Two independent fences for one file give
    ``count`` 2 and two ``keys`` (the ``diff_key`` of each fence, in order).

    A fence a later fence in the same text replaces (the one the UI fades
    as superseded) is not expected.  Its key comes back in ``superseded``,
    for the caller to rule out: an apply of it must not stand in for the
    revision."""
    found = _diff_files(text)
    by_path: Dict[str, Dict[str, Any]] = {}
    replaced: set = set()
    for f in found:
        if f.get("superseded"):
            if f.get("key"):
                replaced.add(f["key"])
            continue
        e = by_path.setdefault(f["path"], {"path": f["path"], "count": 0, "hunks": 0, "keys": []})
        e["count"] += 1
        e["hunks"] += f["hunks"]
        if f.get("key"):
            e["keys"].append(f["key"])
    not_found: List[str] = []
    if files:
        wanted = [str(p).strip() for p in files if str(p).strip()]
        norm = {p.lstrip("./"): p for p in by_path}
        keep: Dict[str, Dict[str, Any]] = {}
        for w in wanted:
            k = norm.get(w.lstrip("./"))
            if k is None:
                not_found.append(w)
            else:
                keep[k] = by_path[k]
        by_path = keep
    live = {k for e in by_path.values() for k in e["keys"]}
    return {"expected": list(by_path.values()), "not_found": not_found,
            "superseded": replaced - live}


def expected_for_turn(segments: List[str]) -> Dict[str, Any]:
    """Expected set of a turn the user has replied into.

    ``segments`` are the turn's assistant text split where a user message
    ended a wait, oldest first.  Diffs from every segment stay expected, so
    a reply does not drop the ones still unapplied.  The exception is a file
    diffed again after the reply: the model has revised that diff, so only
    the newest segment's fences for the path are awaited.  The keys of the
    replaced fences come back as ``superseded``; an apply of one must not
    stand in for its replacement.

    ``{"expected": [...], "superseded": set}``."""
    by_path: Dict[str, Dict[str, Any]] = {}
    superseded: set = set()
    for text in segments or []:
        got = expected_from_text(text)
        # Fences replaced within one response (the UI's "superseded").
        superseded |= got["superseded"]
        for e in got["expected"]:
            old = by_path.pop(e["path"], None)
            if old:
                superseded |= set(old.get("keys") or [])
            by_path[e["path"]] = e
    live = {k for e in by_path.values() for k in e.get("keys") or []}
    return {"expected": list(by_path.values()), "superseded": superseded - live}


def turn_text(messages: List[Dict[str, Any]], current: str = "") -> str:
    """Assistant text of this turn: the text blocks of ``messages`` (the
    assistant turns appended since the turn started) plus ``current``.  A diff
    emitted before a tool call is still awaited when the response ends."""
    parts: List[str] = []
    for m in messages or []:
        if not isinstance(m, dict) or m.get("role") != "assistant":
            continue
        c = m.get("content")
        if isinstance(c, str):
            parts.append(c)
        elif isinstance(c, list):
            parts.extend(b.get("text", "") for b in c
                         if isinstance(b, dict) and b.get("type") == "text")
    # By the time the executor's turn-end hold runs, the current response
    # may already have been appended to the conversation.  Counting it again
    # would double every fence (``count`` 2) and the wait would need two
    # applies per file.
    cur = (current or "").strip()
    if cur and not any(cur in p for p in parts):
        parts.append(current)
    return "\n".join(p for p in parts if p)


# ── results ───────────────────────────────────────────────────────────────

def _status_of(result: Dict[str, Any]) -> str:
    s = str(result.get("status") or "").lower()
    if s in ("success", "already_applied"):
        return "applied"
    if s == "partial":
        return "partial"
    return "failed"


def _hunk_statuses(result: Dict[str, Any]) -> Dict[str, Any]:
    hs = result.get("hunk_statuses")
    if not isinstance(hs, dict):
        d = result.get("details")
        hs = d.get("hunk_statuses") if isinstance(d, dict) else None
    return hs if isinstance(hs, dict) else {}


def _failed_hunks(hs: Dict[str, Any]) -> List[Dict[str, str]]:
    out = []
    for hid, st in sorted(hs.items(), key=lambda kv: str(kv[0])):
        if not isinstance(st, dict) or str(st.get("status")) != "failed":
            continue
        err = st.get("error_details")
        reason = ""
        if isinstance(err, dict):
            reason = str(err.get("error") or err.get("message") or "")
        elif err:
            reason = str(err)
        out.append({"id": str(hid), "reason": reason[:160]})
    return out


def record(conversation_id: str, path: str, result: Dict[str, Any], *,
           request_id: Optional[str] = None,
           diff: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Append one apply outcome.  ``diff`` is the text that was applied; its
    ``diff_key`` ties the result to the fence it came from rather than to
    any diff for the same file.  Never raises."""
    global _seq
    if not conversation_id or not path or not isinstance(result, dict):
        return None
    path = str(path).strip()
    with _lock:
        lst = _results.setdefault(conversation_id, [])
        _seq += 1
        hs = _hunk_statuses(result)
        entry = {
            "seq": _seq, "path": path, "status": _status_of(result),
            "key": diff_key(diff),
            "hunks_total": len(hs),
            "hunks_ok": sum(1 for v in hs.values()
                            if isinstance(v, dict) and v.get("status") in ("succeeded", "already_applied")),
            "failed": _failed_hunks(hs),
            "message": str(result.get("message") or result.get("error") or "")[:200],
            "request_id": request_id, "at": time.time(),
        }
        lst.append(entry)
        if len(lst) > MAX_RESULTS_PER_CONVERSATION:
            del lst[: len(lst) - MAX_RESULTS_PER_CONVERSATION]
        return dict(entry)


def results_since(conversation_id: str, seq: int) -> List[Dict[str, Any]]:
    with _lock:
        return [dict(r) for r in _results.get(conversation_id, []) if r["seq"] > seq]


def current_seq() -> int:
    with _lock:
        return _seq


# ── matching ──────────────────────────────────────────────────────────────

def _norm(p: str) -> str:
    return str(p or "").strip().lstrip("./")


def keys_in(messages: List[Dict[str, Any]]) -> set:
    """Content keys of every diff in the assistant turns of ``messages``.

    Each message is read on its own and all of them joined, because the
    client merges a continuation fence only within one rendered message,
    and one rendered message can span several entries here.  Extra keys are
    harmless: they are only used to rule applies out."""
    msgs = [m for m in messages or [] if isinstance(m, dict) and m.get("role") == "assistant"]
    keys = {f["key"] for f in _diff_files(turn_text(msgs)) if f.get("key")}
    for m in msgs:
        keys |= {f["key"] for f in _diff_files(turn_text([m])) if f.get("key")}
    return keys


def outcomes(expected: List[Dict[str, Any]], results: List[Dict[str, Any]],
             foreign: Optional[set] = None) -> List[Dict[str, Any]]:
    """Per expected entry: the results filled so far.  An entry with
    ``count`` 2 needs two results for its path.

    A result is matched to a fence by content key first.  A result for the
    path whose key is unknown (no diff text recorded, or text matching no
    fence) still counts, so a hold cannot
    be left waiting on a normalisation mismatch.  A result whose key is in
    ``foreign`` (a diff from an earlier response) never counts: applying an
    old diff for the file says nothing about this one."""
    foreign = foreign or set()
    out: List[Dict[str, Any]] = []
    for e in expected:
        n = int(e.get("count") or 1)
        keys = list(e.get("keys") or [])
        same = [r for r in results if _norm(r["path"]) == _norm(e["path"])]
        hits: List[Dict[str, Any]] = []
        used: set = set()
        for k in keys:
            r = next((r for r in same if r.get("key") == k and r["seq"] not in used), None)
            if r is not None:
                hits.append(r)
                used.add(r["seq"])
        for r in same:
            if len(hits) >= n:
                break
            k = r.get("key")
            if r["seq"] in used or k in keys or k in foreign:
                continue
            hits.append(r)
            used.add(r["seq"])
        hits = sorted(hits, key=lambda r: r["seq"])[:n]
        out.append({**e, "results": hits, "remaining": max(0, n - len(hits))})
    return out


def wait_state(conversation_id: str, expected: List[Dict[str, Any]], since_seq: int,
               foreign: Optional[set] = None) -> Dict[str, Any]:
    """Where a held turn stands.

    ``complete``: every expected diff applied cleanly.  ``failed``: some
    result is partial or failed, which ends the wait at once.  ``remaining``:
    paths still lacking a result.  ``foreign``: keys of diffs from before
    this turn (``keys_in``); applies of those are ignored."""
    outs = outcomes(expected, results_since(conversation_id, since_seq), foreign)
    bad = [o["path"] for o in outs if any(r["status"] != "applied" for r in o["results"])]
    remaining = [o["path"] for o in outs if o["remaining"] > 0]
    return {
        "expected": expected,
        "outcomes": outs,
        "remaining": remaining,
        "failed": bad,
        "complete": not remaining and not bad,
    }


def turn_start_seq(conversation_id: str) -> Optional[int]:
    """Ledger seq when the conversation's current turn began; None when
    ``begin_turn`` has not run for it in this process."""
    with _lock:
        return _turn_start.get(conversation_id)


def settled_parts(conversation_id: Optional[str], text: str) -> Dict[tuple, str]:
    """Diffs in ``text`` that must not be dry-run against the live file,
    as ``{(path, part_key): reason}``.

    End-of-stream diff validation applies each fence to a copy of the file
    as it is NOW.  With the diff-apply hold the stream does not end at the
    fence: the user applies, the model resumes, often writes and gets
    applied a second diff for the same file.  By the time validation runs
    the file already contains the first diff (or has moved past it), so a
    fence that applied cleanly is reported as "did not apply" and a
    correction is requested for nothing.

    A fence is settled when:

    * an apply result with its content key is in the ledger (``applied``,
      ``partial`` or ``failed``: either way the Apply click is the
      authoritative outcome and the model is told by the hold report or the
      next turn's note);
    * the turn start is known and an apply result for its path, whose key
      matches no fence in ``text``, was recorded this turn (the same loose
      match ``outcomes`` uses, so a normalisation mismatch cannot bring the
      false rejection back);
    * a LATER fence for the same path is settled (``superseded``): the file
      has moved past the base this fence was written against.

    ``part_key`` is ``diff_key`` of the headed per-file part on its own,
    which is what a caller holding one fence can compute.  Never raises.
    """
    out: Dict[tuple, str] = {}
    if not conversation_id or not text:
        return out
    try:
        with _lock:
            results = [dict(r) for r in _results.get(conversation_id, [])]
            start = _turn_start.get(conversation_id)
        if not results:
            return out
        files = _diff_files(text)
        known = {f["key"] for f in files if f.get("key")}
        by_path: Dict[str, List[Dict[str, Any]]] = {}
        for f in files:
            by_path.setdefault(_norm(f["path"]), []).append(f)
        for path, fences in by_path.items():
            same = [r for r in results if _norm(r["path"]) == path]
            if not same:
                continue
            status: List[Optional[str]] = [None] * len(fences)
            for i, f in enumerate(fences):
                # Newest result for this key: a failed apply retried
                # successfully reads as applied.
                hit = next((r for r in reversed(same)
                            if f.get("key") and r.get("key") == f["key"]), None)
                if hit is not None:
                    status[i] = hit["status"]
            if start is not None:
                loose = [r for r in same if r["seq"] > start and r.get("key") not in known]
                for i in range(len(fences)):
                    if status[i] is None and loose:
                        status[i] = loose.pop(0)["status"]
            last = max((i for i, s in enumerate(status) if s is not None), default=-1)
            for i, f in enumerate(fences):
                reason = status[i] or ("superseded" if i < last else None)
                if reason and f.get("part_key"):
                    out[(path, f["part_key"])] = reason
    except Exception:  # noqa: BLE001 -- bookkeeping must never break validation
        return {}
    return out


# ── the model's report ────────────────────────────────────────────────────

def _result_tail(r: Dict[str, Any]) -> str:
    """``(2/3 hunks; failed: h2 'reason')`` for one apply result."""
    tail = f"({r['hunks_ok']}/{r['hunks_total']} hunks)" if r["hunks_total"] else ""
    if r["failed"]:
        fails = "; ".join(f"h{f['id']} {f['reason']!r}" if f["reason"] else f"h{f['id']}"
                          for f in r["failed"])
        tail = (tail[:-1] + f"; failed: {fails})") if tail else f"(failed: {fails})"
    elif r["status"] == "failed" and r.get("message"):
        tail = f"({r['message']})"
    return tail


def format_report(state: Dict[str, Any], reason: str = "") -> str:
    """Per-file outcome, for the user turn that resumes the model."""
    exp = state.get("expected") or []
    head = {
        "complete": "All the diffs from your last response were applied",
        "failed": "An apply did not succeed cleanly",
        "feedback": "The user replied before applying everything",
    }.get(reason, "Diff application results")
    lines = [f"[Diff apply results] {head} ({len(exp)} file{'s' if len(exp) != 1 else ''}):"]
    width = max((len(e["path"]) for e in exp), default=0)
    for o in state.get("outcomes") or []:
        for r in o["results"]:
            lines.append(f"  {o['path']:<{width}}  {r['status']:<8} {_result_tail(r)}".rstrip())
        for _ in range(o["remaining"]):
            lines.append(f"  {o['path']:<{width}}  not_applied")
    if reason == "complete":
        lines.append("Continue with what the change was for (run the tests, re-read the "
                     "file). If nothing is left to do, say so in one line.")
    elif reason == "failed":
        lines.append("Look at what failed before going further; re-read the file "
                     "rather than assuming its contents.")
    return "\n".join(lines)


def chip_view(state: Dict[str, Any]) -> Dict[str, Any]:
    """What the ``diff_wait`` stream event carries to the frontend."""
    exp = state.get("expected") or []
    total = sum(int(e.get("count") or 1) for e in exp)
    left = sum(o["remaining"] for o in state.get("outcomes") or [])
    return {"total": total, "remaining": left, "remaining_paths": state.get("remaining") or []}


# ── between turns ─────────────────────────────────────────────────────────

def _mark(conversation_id: str, seqs: List[int]) -> None:
    want = set(seqs)
    if not want:
        return
    with _lock:
        for r in _results.get(conversation_id, []):
            if r["seq"] in want:
                r["reported"] = True


def mark_state_reported(conversation_id: str, state: Dict[str, Any]) -> None:
    """The model is being handed ``state`` (a hold's report); its results
    must not be repeated by the next turn's note."""
    _mark(conversation_id, [r["seq"] for o in (state or {}).get("outcomes") or []
                            for r in o.get("results") or []])


def _last_assistant_text(conversation: List[Dict[str, Any]]) -> str:
    for m in reversed(conversation or []):
        if isinstance(m, dict) and m.get("role") == "assistant":
            return turn_text([m])
    return ""


def _attach(conversation: List[Dict[str, Any]], note: str) -> bool:
    """Append ``note`` to the last message when it is the user's."""
    if not conversation or not isinstance(conversation[-1], dict):
        return False
    last = conversation[-1]
    if last.get("role") != "user":
        return False
    content = last.get("content")
    if isinstance(content, str):
        last["content"] = content + "\n\n" + note
        return True
    if isinstance(content, list):
        last["content"] = content + [{"type": "text", "text": note}]
        return True
    return False


def _key_ages(conversation: List[Dict[str, Any]]) -> Dict[str, int]:
    """Content key -> how many assistant responses back its diff was last
    written (1 = the last response)."""
    ages: Dict[str, int] = {}
    back = 0
    for m in reversed(conversation or []):
        if not isinstance(m, dict) or m.get("role") != "assistant":
            continue
        back += 1
        for f in _diff_files(turn_text([m])):
            if f.get("key"):
                ages.setdefault(f["key"], back)
    return ages


def _format_between_turns(new: List[Dict[str, Any]], unapplied: List[Dict[str, Any]],
                          ages: Optional[Dict[str, int]] = None) -> str:
    ages = ages or {}
    width = max(len(p) for p in [r["path"] for r in new] + [o["path"] for o in unapplied])
    lines = ["[Diff apply status since your last turn; a system note, not typed by the user]"]
    older = False
    for r in new:
        back = ages.get(r.get("key") or "", 1)
        origin = ""
        if back > 1:
            older = True
            origin = f"  [a diff from {back} responses back]"
        lines.append(f"  {r['path']:<{width}}  {r['status']:<8} {_result_tail(r)}".rstrip() + origin)
    for o in unapplied:
        for _ in range(o["remaining"]):
            lines.append(f"  {o['path']:<{width}}  no apply recorded")
    if older:
        lines.append('"from N responses back": an older diff of yours for that file was '
                     "applied, not the one in your last response.")
    if unapplied:
        lines.append('"no apply recorded": the Apply button was not used for that diff from '
                     "your last response. The file may have been changed another way; "
                     "re-read it before relying on its contents.")
    if any(r["status"] != "applied" for r in new):
        lines.append("Re-read a file whose apply was partial or failed before going further.")
    return "\n".join(lines)


def begin_turn(conversation_id: str, conversation: List[Dict[str, Any]]) -> Optional[str]:
    """Called once when a turn starts.  Appends to the incoming user message
    a note of every apply result the model has not been shown, plus the diffs
    of its last response with no apply recorded since that turn began, and
    returns the note (None when there is nothing to say or nowhere to put it).

    Results are marked as shown only when the note was attached.  The
    "no apply recorded" part needs the previous turn's start, so it is
    skipped on the first turn this process sees for the conversation."""
    if not conversation_id:
        return None
    with _lock:
        known = conversation_id in _turn_start
        prev_start = _turn_start.get(conversation_id, 0)
        _turn_start[conversation_id] = _seq
        results = [dict(r) for r in _results.get(conversation_id, [])]
    new = [r for r in results if not r.get("reported")]
    last = expected_from_text(_last_assistant_text(conversation)) if known else {}
    expected = last.get("expected") or []
    if not new and not expected:
        return None
    ages = _key_ages(conversation)
    unapplied: List[Dict[str, Any]] = []
    if expected:
        older = {k for k, back in ages.items() if back > 1} | (last.get("superseded") or set())
        since_prev = [r for r in results if r["seq"] > prev_start]
        unapplied = [o for o in outcomes(expected, since_prev, older) if o["remaining"] > 0]
    if not new and not unapplied:
        return None
    note = _format_between_turns(new, unapplied, ages)
    if not _attach(conversation, note):
        return None
    _mark(conversation_id, [r["seq"] for r in new])
    return note
