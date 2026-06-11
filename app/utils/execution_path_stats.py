"""
Execution-path counters for the wrapper-retirement measurement (arch #1).

Tracks which model-execution stack actually serves traffic at runtime:
the normalized provider pipeline (StreamingToolExecutor → LLMProvider)
vs the legacy LangChain wrapper stack (ModelManager → agents/wrappers/*).

Read via GET /api/debug/execution-paths.  Once wrapper counters stay at
zero across real usage, Phases 2-3 (porting + deletion) become safe.
"""

from __future__ import annotations

import threading
import time
from collections import Counter
from typing import Dict, Any

_lock = threading.Lock()
_counts: Counter = Counter()
_last_seen: Dict[str, float] = {}
_started_at = time.time()


def record(path: str) -> None:
    """Increment a path counter. Never raises."""
    try:
        with _lock:
            _counts[path] += 1
            _last_seen[path] = time.time()
    except Exception:
        pass


def get_stats() -> Dict[str, Any]:
    with _lock:
        return {
            "since": _started_at,
            "counts": dict(_counts),
            "last_seen": dict(_last_seen),
        }
