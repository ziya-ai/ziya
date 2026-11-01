"""The diff guidance must tell the model NOT to self-correct an accepted fence.

Conversation 8063d5b2 (2026-09-28): the model emitted a diff for
app/api/chats.py, ran ``sed`` on the file, misread the output ("no blank
line after the raise" -- there was one), and re-emitted the whole 47-line
hunk with one context line changed.  The server hook had already validated
the first fence (one drifted context line is within the difflib stage's
tolerance) and never sent feedback, so the second fence was a duplicate the
user had to reconcile: after applying it, the first showed as already
applied.  The prompt's item 8 only covered "you received feedback"; this
pins the sentence that closes the other half.
"""
import re

from app.agents import prompts


def _item_8() -> str:
    src = prompts.__dict__  # module namespace; guidance lives in a string constant
    text = "\n".join(v for v in src.values() if isinstance(v, str))
    m = re.search(r"8\. Diff validation failures:(.*?)(?:\n\S|\Z)", text, re.S)
    assert m, "item 8 (diff validation failures) missing from the prompt"
    return m.group(1)


def test_item8_still_covers_received_feedback():
    # positive control: the pre-existing rule is intact and the new one sits with it
    assert "If you receive feedback that a diff failed validation" in _item_8()


def test_item8_forbids_self_correction_of_accepted_fence():
    body = _item_8()
    assert "Do NOT re-emit a diff on your own" in body
    assert "validated against the live file" in body
    assert "Correct a diff only when validation feedback reports a failure" in body


def test_header_section_does_not_invite_post_hoc_recount():
    # Conversation 9241dd7b (2026-10-03): the model re-issued two diffs whose
    # only differences were an @@ count and start lines, citing "off-by-a-few
    # headers" -- the case the applier tolerates.  The header section used to
    # say a miscount "obscures real bugs", which read as a reason to re-emit.
    text = "\n".join(v for v in prompts.__dict__.values() if isinstance(v, str))
    m = re.search(r"\*\*HUNK HEADER LINE COUNTS\*\*(.*?)\n\n", text, re.S)
    assert m, "hunk header section missing from the prompt"
    # Collapse whitespace: the prompt wraps lines, and a phrase split
    # across a line break is still the phrase the model reads.
    section = " ".join(m.group(1).split())
    assert "NOT a reason to re-emit" in section
    assert "obscures real bugs" not in section
