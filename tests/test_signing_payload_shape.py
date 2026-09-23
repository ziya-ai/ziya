"""Seam test: a builtin that returns a bare payload dict (no `content`
envelope — task_card_stage / task_card_launch shape) must reach the
frontend as JSON, not Python repr.

WHY THIS FILE EXISTS
--------------------
The first fix for the missing model-staged tile went into
tool_execution._process_result and was unit-tested there in isolation —
and changed nothing in the live product.  For a builtin, sign_tool_result
runs FIRST, and it wraps a dict lacking `content` with str(result), so by
the time _process_result sees it the payload is already a content
envelope whose text is the repr.  The unit tests exercised a branch the
real payload never reaches.

So this asserts the SEAM: sign then process, the way tool_execution
actually chains them, and parses the output as JSON.  It fails against
the unpatched signing.py with JSONDecodeError.
"""
import json

from app.mcp.signing import sign_tool_result
from app.tool_execution import _process_result


PAYLOAD = {"success": True, "staged": True, "conversation_only": True,
           "card_id": "c-1", "binding_id": "b-1", "warnings": [],
           "run_id": None}


def _through_the_real_chain(payload):
    signed = sign_tool_result("task_card_stage", {"name": "x"}, payload, "chat-1")
    return _process_result(signed, "task_card_stage", "task_card_stage")


def test_bare_payload_dict_survives_signing_as_json():
    out = _through_the_real_chain(PAYLOAD)
    assert isinstance(out, str)
    parsed = json.loads(out)          # raises on {'success': True, ...}
    assert parsed == PAYLOAD
    # The two things chatApi.ts reads to fire the tile refresh.
    assert parsed["success"] is True and parsed["binding_id"] == "b-1"


def test_signing_wrap_preserves_signature_verification():
    """Changing the wrapped text must not break the signature round-trip:
    the canonical form is computed over `content`, which is exactly what
    we changed, so sign and verify must still agree on the new text."""
    from app.mcp.signing import verify_tool_result
    signed = sign_tool_result("task_card_stage", {"name": "x"}, dict(PAYLOAD), "chat-1")
    ok, err = verify_tool_result(signed, "task_card_stage", {"name": "x"})
    assert ok, err
    # Positive check that the path ran on the NEW text, not an envelope.
    assert json.loads(signed["content"][0]["text"]) == PAYLOAD


def test_content_envelope_is_left_alone():
    """A result that already carries `content` is not re-wrapped."""
    env = {"content": [{"type": "text", "text": "hello"}]}
    signed = sign_tool_result("t", {}, dict(env), "chat-1")
    assert signed["content"] == env["content"]


def test_unserialisable_value_does_not_raise():
    from pathlib import Path
    out = _through_the_real_chain({"path": Path("/tmp/x"), "ok": True})
    assert json.loads(out) == {"path": "/tmp/x", "ok": True}
