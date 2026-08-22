"""
Tests for app/utils/chat_screenshot.py — the harness that screenshots a
markdown message as the production chat UI renders it.

What is worth testing here, and what is not
-------------------------------------------
The valuable part of this module is NOT "does Playwright take a picture" — that
needs a live server and is covered by scripts/chat_screenshot_probe.py, which
was used to prove the mechanism empirically before the module existed.  The
valuable part is the set of decisions that determine whether the picture is
trustworthy:

  * the wait predicate must REQUIRE typeset math when math was asked for, and
    must NOT require it otherwise (a prose-only render would otherwise hang
    until timeout and be reported as a rendering defect);
  * the lazy-mount placeholder must never satisfy the predicate — screenshotting
    a '…' placeholder and grading it as output is the specific silent failure
    the whole design exists to prevent;
  * the caller's markdown must reach storage byte-for-byte.  The message is
    located by words drawn FROM the document rather than by an injected marker,
    precisely so the document being judged is not altered by the act of
    judging it;
  * the message must go in via ChatUpdate, because ChatStorage.create()
    hardcodes messages=[] — a regression to create-only seeding yields an empty
    conversation and a timeout that looks like a frontend bug.

Those are all pure functions or thin seams, so they are tested without a
browser.
"""
from __future__ import annotations

import json

import pytest

from app.utils import chat_screenshot as cs


# -- expects_math: drives whether .katex is required ----------------------

@pytest.mark.parametrize("body", [
    "a $$x$$ b",
    "inline $x^2$ here",
    r"display \[x\] here",
    r"paren \(x\) here",
    "```math\nx = 1\n```",
    "```latex\nx = 1\n```",
    r"\begin{pmatrix} 1 & 2 \end{pmatrix}",
])
def test_expects_math_true_for_real_math(body):
    assert cs.expects_math(body) is True


@pytest.mark.parametrize("body", [
    "just some prose with no math at all",
    "",
    # Currency is the load-bearing negative case.  If this returned True the
    # predicate would demand a .katex node that correctly never appears, and
    # every currency-bearing document would time out and be misreported as a
    # rendering defect.
    "costs $900 deposit and then $300 more later",
])
def test_expects_math_false_without_math(body):
    assert cs.expects_math(body) is False


# -- derive_locators: locate our message without touching its content -----

def test_derive_locators_skips_short_words_and_dedupes():
    locs = cs.derive_locators("Heading heading quantum quantum tiny of a")
    assert locs == ["Heading", "quantum"]


def test_derive_locators_respects_limit():
    body = "alphabet bravocharlie deltaecho foxtrotgolf hotelindia juliett"
    assert len(cs.derive_locators(body, limit=2)) == 2


def test_derive_locators_tolerates_empty():
    assert cs.derive_locators("") == []
    assert cs.derive_locators("$$ \\frac{1}{2} $$") == []


# -- seed script: bootstrap keys and their exact formats ------------------

def test_seed_script_sets_both_bootstrap_keys():
    js = cs.build_seed_script("pid-1", "conv-9", dark=False)
    # ProjectContext reads this from localStorage during init.
    assert "ZIYA_LAST_PROJECT_ID" in js and "pid-1" in js
    # ChatContext restores the selected conversation via getTabState, which
    # reads sessionStorage — localStorage would not be consulted for this key.
    assert "sessionStorage.setItem('ZIYA_CURRENT_CONVERSATION_ID'" in js
    assert "conv-9" in js


def test_seed_script_theme_is_a_json_boolean_not_a_word():
    """ThemeContext does JSON.parse on ZIYA_THEME_PREFERENCE, so the stored
    value must be 'true'/'false'.  Writing 'dark' would throw inside
    getInitialTheme and take the whole app down before anything renders."""
    dark = cs.build_seed_script("p", "c", dark=True)
    light = cs.build_seed_script("p", "c", dark=False)
    assert json.dumps(json.dumps(True)) in dark
    assert json.dumps(json.dumps(False)) in light
    assert "'dark'" not in dark


# -- wait predicates: the trustworthiness of the screenshot ---------------

def test_rendered_predicate_requires_katex_only_when_math_expected():
    with_math = cs.build_rendered_predicate(["Word"], "assistant", True)
    without = cs.build_rendered_predicate(["Word"], "assistant", False)
    assert ".katex" in with_math
    # The no-math predicate must not gate on .katex at all, or prose hangs.
    assert ".katex" not in without


def test_predicates_reject_the_lazy_mount_placeholder():
    """LazyMarkdownRenderer renders a bare '…' for any message over 400 chars
    until its IntersectionObserver fires.  Both predicates must treat that as
    NOT-rendered; accepting it is how a placeholder gets graded as output."""
    ellipsis = "\u2026"
    for js in (cs.build_presence_predicate(["W"], "assistant"),
               cs.build_rendered_predicate(["W"], "assistant", True)):
        assert ellipsis in js


def test_locator_js_targets_the_requested_role_class():
    assert '"ai"' in cs.build_locator_js(["W"], "assistant")
    assert '"human"' in cs.build_locator_js(["W"], "human")


def test_dom_probe_reports_the_silent_failure_modes():
    js = cs.build_dom_probe(["W"], "assistant")
    for signal in ("katex_error", "math_fallback", "leaked_math_marker",
                   "is_lazy_placeholder", "has_chat_chrome"):
        assert signal in js


# -- seeding seam ---------------------------------------------------------

class _FakeChat:
    def __init__(self, cid="chat-1"):
        self.id = cid
        self.messages = []


class _FakeStorage:
    """Mimics ChatStorage's actual contract, including the trap: create()
    ignores messages entirely."""

    def __init__(self):
        self.created_titles = []
        self.updated = None
        self.deleted = []

    def create(self, data, *a, **kw):
        self.created_titles.append(getattr(data, "title", None))
        return _FakeChat()

    def update(self, chat_id, data):
        self.updated = (chat_id, data)
        chat = _FakeChat(chat_id)
        chat.messages = list(data.messages or [])
        return chat

    def delete(self, chat_id):
        self.deleted.append(chat_id)
        return True


@pytest.fixture
def fake_storage(monkeypatch):
    st = _FakeStorage()
    monkeypatch.setattr(cs, "_chat_storage", lambda project_id: st)
    return st


def test_seed_conversation_passes_markdown_through_byte_for_byte(fake_storage):
    """No marker injection.  If this module ever prepended a locator token to
    the content, the document under judgement would differ from the document
    the caller wrote — an extra paragraph, and an extra token for the inline
    math classifier to see."""
    body = "Exact $x^2$ body with\n\nmultiple lines and $900 currency."
    cs.seed_conversation("pid", body, role="assistant")
    _, update = fake_storage.updated
    assert len(update.messages) == 1
    assert update.messages[0].content == body


def test_seed_conversation_uses_update_not_create_for_messages(fake_storage):
    """ChatStorage.create() hardcodes messages=[]; the message MUST arrive via
    ChatUpdate or the conversation renders empty and the render times out."""
    cs.seed_conversation("pid", "some body text here", role="assistant")
    assert fake_storage.updated is not None, "message never sent via update()"


def test_seed_conversation_honours_role(fake_storage):
    cs.seed_conversation("pid", "body", role="human")
    _, update = fake_storage.updated
    assert update.messages[0].role == "human"


def test_seeded_title_is_identifiable_for_orphan_sweeping(fake_storage):
    cs.seed_conversation("pid", "body", role="assistant")
    assert fake_storage.created_titles[0].startswith(cs.CHAT_TITLE_PREFIX)


def test_seed_conversation_raises_if_message_did_not_persist(monkeypatch):
    class _Silent(_FakeStorage):
        def update(self, chat_id, data):
            return _FakeChat(chat_id)  # messages stay empty

    monkeypatch.setattr(cs, "_chat_storage", lambda pid: _Silent())
    with pytest.raises(RuntimeError, match="did not persist"):
        cs.seed_conversation("pid", "body", role="assistant")


def test_delete_conversation_never_raises(monkeypatch):
    """Cleanup runs in a finally next to the caller's result; if it raised it
    would replace a successful render with a cleanup traceback."""
    class _Boom:
        def delete(self, chat_id):
            raise OSError("disk gone")

    monkeypatch.setattr(cs, "_chat_storage", lambda pid: _Boom())
    assert cs.delete_conversation("pid", "chat-1") is False


def test_sweep_orphans_only_removes_old_seeded_chats(monkeypatch):
    now_ms = 10_000_000

    class _Entry:
        def __init__(self, cid, title, created):
            self.id, self.title, self.createdAt = cid, title, created

    class _Listing(_FakeStorage):
        def list(self):
            return [
                # ours, stale -> delete
                _Entry("a", f"{cs.CHAT_TITLE_PREFIX} aaa", 0),
                # ours, fresh (possibly an in-flight render) -> keep
                _Entry("b", f"{cs.CHAT_TITLE_PREFIX} bbb", now_ms),
                # a real user conversation -> must never be touched
                _Entry("c", "Real user conversation", 0),
            ]

    st = _Listing()
    monkeypatch.setattr(cs, "_chat_storage", lambda pid: st)
    monkeypatch.setattr(cs.time, "time", lambda: now_ms / 1000.0)
    removed = cs.sweep_orphans("pid", max_age_ms=1000)
    assert removed == 1
    assert st.deleted == ["a"]


def test_sweep_orphans_survives_a_storage_that_cannot_list(monkeypatch):
    class _NoList:
        def list(self):
            raise RuntimeError("index unavailable")

    monkeypatch.setattr(cs, "_chat_storage", lambda pid: _NoList())
    assert cs.sweep_orphans("pid") == 0


def test_resolve_project_id_error_names_every_path_tried(monkeypatch):
    """A wrong-project seed produces a chat the frontend never shows, i.e. a
    timeout that looks like a render bug.  The error must point at the real
    cause."""
    class _NoProjects:
        def get_by_path(self, path):
            return None

    monkeypatch.setattr("app.storage.projects.ProjectStorage",
                        lambda *_a, **_k: _NoProjects())
    with pytest.raises(RuntimeError, match="Paths tried"):
        cs.resolve_project_id("/nonexistent/workspace/path")
