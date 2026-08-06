"""
Request-scoped retention floor for image tool results.

Why a ContextVar rather than a field on the tool's return value: the tool
result flattener (app/tool_execution.py) returns the bare content-block
list for image results and drops every sibling key, so a ``_image_retain``
field on the returned dict never reaches the streaming executor.  Encoding
the request inside the content list instead would put it in front of the
model or, worse, in front of the provider API.

The floor is a REQUEST, not a guarantee: the executor takes
``max(mode_default, floor)``, so a tool can widen the retention window but
never narrow it, and a lost/never-consumed floor degrades to normal
behavior rather than to an unbounded context.
"""

import contextvars
import logging

logger = logging.getLogger(__name__)

_image_retain_floor: contextvars.ContextVar[int] = contextvars.ContextVar(
    "image_retain_floor", default=0,
)

# "pin" is not literally forever — an unbounded window is how a run ends up
# re-sending 40 MB of base64 on iteration 30.  It is "wide enough to hold a
# comparison set", still subject to the byte ceiling.
RETAIN_LEVELS = {
    "auto": 0,
    "turn": 3,
    "pin": 8,
}


def request_image_retain(level: str) -> None:
    """Ask that the next compaction sweep keep a wider image window.

    Idempotent-ish: repeated calls take the MAX, so two renders in one
    iteration asking for different levels get the wider of the two.
    """
    want = RETAIN_LEVELS.get((level or "auto").strip().lower(), 0)
    if want <= 0:
        return
    try:
        current = _image_retain_floor.get()
        if want > current:
            _image_retain_floor.set(want)
            logger.debug(f"🖼️ IMAGE_RETAIN: floor raised to {want} ({level!r})")
    except Exception as exc:  # noqa: BLE001 — retention must never break a tool
        logger.debug(f"🖼️ IMAGE_RETAIN: floor set failed: {exc}")


def take_image_retain_floor() -> int:
    """Read and CLEAR the floor.  Cleared on read so a single ``retain``
    request applies to the sweep that follows it and does not silently
    pin every later render in the run."""
    try:
        value = _image_retain_floor.get()
        if value:
            _image_retain_floor.set(0)
        return value
    except Exception:  # noqa: BLE001
        return 0
