#!/usr/bin/env python3
"""Build the replay timeline: one event per point where production would
have run memory extraction.

Production (app/server.py, after a stream completes) calls
``run_post_conversation_extraction(request_messages, conversation_id,
project_path=...)`` once per assistant response.  ``request_messages`` is
the history the model was asked about: everything BEFORE the reply just
generated.  So an assistant message at index i is an extraction event at
its timestamp over ``messages[:i]``.

Rules, each of which changes the event count and is covered by tests:
  * forks (``branchedFrom`` + ``branchedAtMessageIndex``) repeat their
    parent's history with the parent's timestamps; only turns at or after
    the branch index are events, or the prefix is extracted twice.
  * swarm delegate chats (``delegateMeta``) never go through the streaming
    call site; excluded unless ``include_delegates``.
  * shell records (``_isShell``) and list-shaped files are skipped.
  * the history handed to extraction drops muted messages, ``system``
    rows (model-change notes) and empty/non-string content, as the request
    path does.

The timeline holds references only (chat id, index, timestamp, project);
message text stays in the encrypted chat records and is loaded at replay
time by ``load_history``.

Limits (recorded, not fixable from stored records): edited or truncated
messages are replayed in their final form, and the production system
prompt (part of request_messages) is not reconstructed.

Usage:
    python3 scripts/memory_replay/timeline.py              # build + report
    python3 scripts/memory_replay/timeline.py --since 2026-09-01
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import glob
import json
import os
import sys
from dataclasses import asdict, dataclass
from typing import Any, Dict, Iterable, Iterator, List, Optional

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

DEFAULT_OUT = os.path.join(ROOT, ".ziya", "memory-replay", "timeline.jsonl")
HISTORY_ROLES = ("human", "user", "assistant", "ai")


@dataclass
class Event:
    t: int                      # ms, the assistant reply's timestamp
    chat_id: str
    project_id: Optional[str]
    project_path: Optional[str]
    msg_index: int              # index of the assistant reply in the record
    history_len: int            # messages handed to extraction (after filtering)
    kind: str = "extract"


def _ms(ts: Any) -> Optional[int]:
    try:
        x = float(ts)
    except (TypeError, ValueError):
        return None
    return int(x if x > 1e11 else x * 1000)


def history_for(messages: List[Dict[str, Any]], upto: int) -> List[Dict[str, Any]]:
    """What production hands extraction for the reply at ``upto``."""
    out = []
    for m in messages[:upto]:
        if not isinstance(m, dict) or m.get("muted"):
            continue
        role, content = m.get("role"), m.get("content")
        if role not in HISTORY_ROLES or not isinstance(content, str) or not content.strip():
            continue
        out.append({"role": role, "content": content})
    return out


def events_for_chat(chat: Dict[str, Any], project_path: Optional[str] = None,
                    include_delegates: bool = False) -> Iterator[Event]:
    """Extraction events for one decrypted chat record."""
    if not isinstance(chat, dict) or chat.get("_isShell"):
        return
    if chat.get("delegateMeta") and not include_delegates:
        return
    messages = chat.get("messages") or []
    start = 0
    if chat.get("branchedFrom") is not None:
        try:
            start = max(0, int(chat.get("branchedAtMessageIndex") or 0))
        except (TypeError, ValueError):
            start = 0
    for i, m in enumerate(messages):
        if i < start or not isinstance(m, dict) or m.get("role") not in ("assistant", "ai"):
            continue
        if m.get("muted") or not isinstance(m.get("content"), str) or not m["content"].strip():
            continue
        t = _ms(m.get("timestamp"))
        if t is None:
            continue
        hist = history_for(messages, i)
        if not any(h["role"] in ("human", "user") for h in hist):
            continue
        yield Event(t=t, chat_id=chat.get("id"), project_id=chat.get("projectId"),
                    project_path=project_path, msg_index=i, history_len=len(hist))


def iter_chat_records(home: Optional[str] = None) -> Iterator[Dict[str, Any]]:
    """Decrypted chat records under <ziya home>/projects/*/chats (read-only)."""
    from app.utils.encryption import get_encryptor, is_encrypted
    from app.utils.paths import get_ziya_home
    base = home or str(get_ziya_home())
    enc = get_encryptor()
    for f in sorted(glob.glob(os.path.join(base, "projects", "*", "chats", "*.json"))):
        try:
            raw = open(f, "rb").read()
            if is_encrypted(raw):
                raw = enc.decrypt(raw)
            d = json.loads(raw)
        except Exception:
            continue
        if isinstance(d, dict):
            d.setdefault("projectId", os.path.basename(os.path.dirname(os.path.dirname(f))))
            yield d


def project_paths(home: Optional[str] = None) -> Dict[str, Optional[str]]:
    from app.storage.projects import ProjectStorage
    from app.utils.paths import get_ziya_home
    from pathlib import Path
    ps = ProjectStorage(Path(home) if home else get_ziya_home())
    out: Dict[str, Optional[str]] = {}
    base = home or str(get_ziya_home())
    for d in glob.glob(os.path.join(base, "projects", "*")):
        pid = os.path.basename(d)
        if not os.path.isdir(d):
            continue
        try:
            p = ps.get(pid)
            out[pid] = getattr(p, "path", None)
        except Exception:
            out[pid] = None
    return out


def build_timeline(chats: Iterable[Dict[str, Any]], paths: Dict[str, Optional[str]],
                   since_ms: int = 0, include_delegates: bool = False) -> List[Event]:
    evs = [e for c in chats
           for e in events_for_chat(c, paths.get(c.get("projectId")), include_delegates)
           if e.t >= since_ms]
    evs.sort(key=lambda e: (e.t, e.chat_id or "", e.msg_index))
    return evs


def load_history(event: Event, home: Optional[str] = None) -> List[Dict[str, Any]]:
    """Re-read one event's history from the encrypted record."""
    from app.utils.encryption import get_encryptor, is_encrypted
    from app.utils.paths import get_ziya_home
    base = home or str(get_ziya_home())
    f = os.path.join(base, "projects", event.project_id or "*", "chats", f"{event.chat_id}.json")
    hits = glob.glob(f)
    raw = open(hits[0], "rb").read()
    if is_encrypted(raw):
        raw = get_encryptor().decrypt(raw)
    return history_for(json.loads(raw).get("messages") or [], event.msg_index)


def write_timeline(events: List[Event], path: str = DEFAULT_OUT) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        for e in events:
            f.write(json.dumps(asdict(e)) + "\n")


def read_timeline(path: str = DEFAULT_OUT) -> List[Event]:
    with open(path) as f:
        return [Event(**json.loads(line)) for line in f if line.strip()]


def report(events: List[Event]) -> Dict[str, Any]:
    """Counts that decide what a replay costs and what it exercises."""
    b = lambda p: os.path.basename((p or "(no project)").rstrip("/"))
    month = collections.Counter(dt.datetime.fromtimestamp(e.t / 1000).strftime("%Y-%m") for e in events)
    proj = collections.Counter(b(e.project_path) for e in events)
    # Interleaving: distinct projects active per hour, and switches between
    # consecutive events -- what the global activity clock got wrong.
    per_hour: Dict[int, set] = collections.defaultdict(set)
    for e in events:
        per_hour[e.t // 3600_000].add(b(e.project_path))
    switches = sum(1 for a, c in zip(events, events[1:]) if a.project_path != c.project_path)
    return {
        "events": len(events),
        "chats": len({e.chat_id for e in events}),
        "span": [dt.datetime.fromtimestamp(events[0].t / 1000).isoformat(timespec="minutes"),
                 dt.datetime.fromtimestamp(events[-1].t / 1000).isoformat(timespec="minutes")]
        if events else None,
        "by_month": dict(sorted(month.items())),
        "by_project": dict(proj.most_common()),
        "history_msgs_total": sum(e.history_len for e in events),
        "active_hours": len(per_hour),
        "hours_with_2plus_projects": sum(1 for s in per_hour.values() if len(s) >= 2),
        "project_switches": switches,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--since", help="YYYY-MM-DD")
    ap.add_argument("--include-delegates", action="store_true")
    ap.add_argument("--out", default=DEFAULT_OUT)
    a = ap.parse_args()
    os.environ.setdefault("ZIYA_ENABLE_MEMORY", "true")
    from app.plugins import initialize as initialize_plugins
    initialize_plugins()
    since = int(dt.datetime.strptime(a.since, "%Y-%m-%d").timestamp() * 1000) if a.since else 0
    evs = build_timeline(iter_chat_records(), project_paths(), since, a.include_delegates)
    write_timeline(evs, a.out)
    print(json.dumps(report(evs), indent=1))
    print(f"timeline: {a.out}")


if __name__ == "__main__":
    main()
