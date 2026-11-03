"""Where the ``sweep`` task lives, resolved the way ``ziya task`` resolves it.

``ziya task <name>`` reads ``.ziya/tasks.yaml`` first and, when no file task
claims the name, falls through to the project's deck for a card of that
name (app/cli.py ``cmd_task``; stored decision m_d63731f3).  The linear
``sweep`` file task was demoted to ``sweep-old`` on 2026-10-01 so the
orchestrated ``sweep`` CARD could take the name.  Tests that read
``tasks["sweep"]`` broke that day -- not because sweep disappeared but
because they hard-coded one of the two places it may live.

``load_sweep()`` returns a :class:`SweepSource` for whichever the CLI would
run, or None when neither exists (the deck is per machine; a fresh checkout
has no card).  Callers skip in that case rather than fail.

Call it from a MODULE-scoped fixture.  tests/conftest.py redirects
``ZIYA_HOME`` to a per-test sandbox in a function-scoped autouse fixture;
pytest sets up module-scoped fixtures before function-scoped ones, so a
module fixture sees the real ``~/.ziya`` (where the operator's deck is) and
an inline call from a test body sees an empty sandbox.  These tests are
about operator-local configuration -- they already skip when tasks.yaml is
absent -- so reading the real deck, read-only, is the point.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
TASKS_FILE = REPO_ROOT / ".ziya" / "tasks.yaml"

# Why the last card lookup returned None, for an assertion message.
last_error: Optional[str] = None


@dataclass
class SweepSource:
    kind: str                      # "yaml" | "card"
    text: str                      # whitespace-flattened instructions
    # Ordered step titles.  yaml: the "## Step N — Title" headers.  card:
    # every task block's name, in execution order.
    steps: List[str] = field(default_factory=list)
    # card only: (task name, enclosing group name or "") in execution order.
    tasks: List[tuple] = field(default_factory=list)

    def step_index(self, needle: str) -> int:
        """Position of the first step whose title contains *needle*."""
        for i, s in enumerate(self.steps):
            if needle.lower() in s.lower():
                return i
        raise KeyError(needle)


def _flatten(text: str) -> str:
    return re.sub(r"\s+", " ", text)


def _yaml_sweep() -> Optional[SweepSource]:
    if not TASKS_FILE.is_file():
        return None
    import yaml
    data = yaml.safe_load(TASKS_FILE.read_text(encoding="utf-8")) or {}
    task = data.get("sweep")
    if not (isinstance(task, dict) and task.get("prompt")):
        return None
    prompt = task["prompt"]
    steps = [t.strip() for _n, t in re.findall(r"^\s*## Step (\d+) — (.+)$", prompt, re.M)]
    return SweepSource(kind="yaml", text=_flatten(prompt), steps=steps)


def _card_sweep() -> Optional[SweepSource]:
    global last_error
    try:
        logging.disable(logging.CRITICAL)
        from app.plugins import initialize
        initialize()  # the deck may be encrypted; plugins register the key
        from app.cli_card_runner import resolve_card
        card, _pid = resolve_card(str(REPO_ROOT), "sweep")
    except Exception as exc:  # noqa: BLE001 -- no deck, unreadable store
        last_error = f"{type(exc).__name__}: {exc}"
        return None
    finally:
        logging.disable(logging.NOTSET)
    if card is None:
        last_error = "no card named 'sweep' in this project's deck"
        return None

    parts: List[str] = []
    tasks: List[tuple] = []

    def walk(block, group_name: str = "") -> None:
        if block.block_type == "task":
            tasks.append((block.name or "", group_name))
        ins = getattr(block, "instructions", None) or ""
        if ins:
            parts.append(ins)
        inner = block.name or group_name if block.block_type != "task" else group_name
        for child in (getattr(block, "body", None) or []):
            walk(child, inner)

    walk(card.root)
    return SweepSource(
        kind="card", text=_flatten("\n\n".join(parts)),
        steps=[name for name, _g in tasks], tasks=tasks,
    )


def load_sweep() -> Optional[SweepSource]:
    """The sweep task the CLI would run, else None.  See module docstring
    for why this must be called from a module-scoped fixture."""
    return _yaml_sweep() or _card_sweep()
