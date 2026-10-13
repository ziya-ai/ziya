"""Skill API handlers must not read the skill store on the asyncio event loop.

Same defect class, same guard, as ``test_chat_api_not_event_loop_blocking``:
a zero-await ``async def`` handler runs its whole body on the loop.  Every
skill handler builds a ``SkillStorage`` (project lookup, SKILL.md discovery
in the workspace) and reads/writes skill files synchronously; a 252 KB
skill-store read was observed stalling the loop under startup load.  Every
handler in ``app/api/skills.py`` that awaits nothing must be plain ``def``
so Starlette runs it on its worker threadpool.  None genuinely awaits.
"""
from pathlib import Path

import pytest

from tests.test_chat_api_not_event_loop_blocking import _route_handlers

SKILLS_PY = Path(__file__).parent.parent / "app" / "api" / "skills.py"


def test_no_skill_handler_is_a_zero_await_coroutine():
    handlers = _route_handlers(SKILLS_PY.read_text())
    offenders = [
        (name, ln) for name, ln, is_async, n_awaits in handlers
        if is_async and n_awaits == 0
    ]
    assert offenders == [], (
        "These handlers are 'async def' but await nothing, so their skill-store "
        "I/O runs ON the event loop:\n"
        + "\n".join(f"  L{ln} {name}" for name, ln in offenders)
        + "\n\nMake them plain 'def' or wrap the work in asyncio.to_thread."
    )


def test_scan_is_not_vacuous():
    handlers = _route_handlers(SKILLS_PY.read_text())
    assert len(handlers) >= 4, f"only {len(handlers)} handlers found; detector broken?"


@pytest.mark.parametrize("name", ["list_skills", "get_skill", "delete_skill"])
def test_known_hot_handlers_are_sync(name):
    """list_skills is fetched on project load; get/delete touch the store and chats."""
    handlers = {h[0]: h for h in _route_handlers(SKILLS_PY.read_text())}
    assert name in handlers, f"{name} not found -- renamed?"
    assert not handlers[name][2], f"{name} must be plain 'def'"
