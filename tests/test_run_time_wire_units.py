"""Wire-format guard for the task-run stream.

Every timestamp a task run emits — persisted (run record, block state,
Artifact.created_at) or streamed (the relay's ``ts`` / ``at`` fields) —
is epoch MILLISECONDS.  The frontend hook passes wire ``ts`` straight
through into ``lastActivityTs`` and compares it against
``run.last_activity_at``; one emitter still stamping ``time.time()``
(seconds) would make that comparison prefer the wrong side and freeze the
tile's activity line, exactly the class of bug the unit unification
removed.

This test scans the emitter modules for the old spelling so the mistake is
caught at the source rather than by a user watching a stale "Ns ago".
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "app"

# Modules that build relay events or Artifacts.
EMITTERS = [
    ROOT / "agents" / "task_executor.py",
    ROOT / "agents" / "block_executor.py",
    ROOT / "api" / "task_cards.py",
    ROOT / "api" / "task_runs.py",
    ROOT / "storage" / "task_runs.py",
]

# A timestamp *field* being stamped from the seconds clock.  Elapsed-time
# arithmetic (``start = time.time()`` ... ``time.time() - start``) is not a
# field and is deliberately not matched.
SECONDS_STAMP = re.compile(
    r"""(?:"(?:ts|at)"\s*:|'(?:ts|at)'\s*:|\b\w*_at\s*=|\bcreated_at\s*=)"""
    r"""\s*_?time\.time\(\)"""
)


def test_no_timestamp_field_is_stamped_in_seconds():
    offenders = []
    for path in EMITTERS:
        for lineno, line in enumerate(path.read_text().splitlines(), 1):
            if SECONDS_STAMP.search(line):
                offenders.append(f"{path.relative_to(ROOT.parent)}:{lineno}: {line.strip()}")
    assert not offenders, (
        "timestamp fields must be stamped with now_ms(), not time.time():\n"
        + "\n".join(offenders)
    )
