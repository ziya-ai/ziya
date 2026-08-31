"""The feedback_delivered SSE ack must actually reach the browser's handlers.

Six backend sites emit a ``feedback_delivered`` chunk, server.py's relay has an
explicit pass-through branch for it, and two frontend components register a
``feedbackDelivered`` DOM listener.  Every half was correct in isolation — and
the feature was completely dead, because chatApi's *dispatch* of that DOM event
sat inside ``if (contentToAdd) { ... }``.

``contentToAdd`` is assigned only from ``jsonData.content`` or ``jsonData.text``.
The ack chunk carries neither (it has ``type`` and ``message`` only), so
contentToAdd stayed the empty string, the gate was false, and the dispatch never
ran.  Consequences, both user-visible:

  * The composer's ``pendingFeedbackRef`` was never pruned, so at turn end the
    recovery path treated already-delivered feedback as stranded and
    auto-submitted it a second time as a fresh user turn.
  * The status chip could never reach 'delivered'.

The pre-existing test ``test_delivered_ack_prunes_the_retained_copy`` passed
throughout, because it asserts the *handler* references pendingFeedbackRef —
it cannot see that nothing ever invokes the handler.  These tests assert the
connection instead of the endpoints.
"""

import re

CHAT_API = "frontend/src/apis/chatApi.ts"
EXECUTOR = "app/streaming_tool_executor.py"
TOOL_EXEC = "app/tool_execution.py"
SERVER = "app/server.py"
COMPOSER = "frontend/src/components/SendChatContainer.tsx"


def _lines(path):
    return open(path).read().split("\n")


def _indent(line):
    return len(line) - len(line.lstrip())


def _enclosing_blocks(lines, target_idx):
    """Block-opening lines enclosing ``target_idx``, innermost first.

    Indent-based rather than brace-matching: the file mixes template literals,
    regex literals containing braces, and JSX, all of which defeat a naive
    brace counter.  Indentation in this file is machine-consistent.
    """
    out = []
    cur = _indent(lines[target_idx])
    for i in range(target_idx - 1, -1, -1):
        line = lines[i]
        if not line.strip():
            continue
        ind = _indent(line)
        if ind < cur and line.rstrip().endswith("{"):
            out.append((i + 1, line.strip()))
            cur = ind
            if cur == 0:
                break
    return out


def _dispatch_line_idx(lines):
    """0-based index of the feedback_delivered type check in chatApi."""
    hits = [
        i for i, l in enumerate(lines)
        if "unwrappedData.type === 'feedback_delivered'" in l
    ]
    assert hits, (
        "chatApi no longer dispatches on feedback_delivered at all; the "
        "backend ack has no consumer and mid-stream feedback will be "
        "re-submitted as a duplicate turn at the end of every turn"
    )
    assert len(hits) == 1, (
        f"feedback_delivered is dispatched from {len(hits)} places in chatApi "
        f"(lines {[h + 1 for h in hits]}); a duplicate dispatch double-fires "
        f"the DOM event and can prune two retained texts for one delivery"
    )
    return hits[0]


# ── the defect ────────────────────────────────────────────────────────────

def test_dispatch_is_not_gated_on_stream_content():
    """The regression itself: an ack carries no content, so a content gate eats it."""
    lines = _lines(CHAT_API)
    idx = _dispatch_line_idx(lines)
    enclosing = [text for _, text in _enclosing_blocks(lines, idx)]
    offenders = [t for t in enclosing if "contentToAdd" in t or "insideCodeFence" in t]
    assert not offenders, (
        "the feedback_delivered dispatch is nested inside "
        f"{offenders!r}. The ack chunk has no content/text field, so "
        "contentToAdd is '' and this block never executes — the "
        "feedbackDelivered DOM event is never dispatched"
    )


def test_content_to_add_still_comes_only_from_content_or_text():
    """Premise of the test above.

    If contentToAdd ever starts being populated for fieldless chunks, the
    reasoning changes and this file should be revisited.
    """
    src = open(CHAT_API).read()
    assigns = re.findall(r"contentToAdd = (\w+(?:\.\w+)*)", src)
    assert set(assigns) <= {"jsonData.content", "jsonData.text"}, (
        f"contentToAdd now also assigned from {sorted(set(assigns))}; "
        "re-derive whether a feedback_delivered chunk can satisfy the gate"
    )


def test_ack_chunks_carry_no_content_field():
    """Why the gate rejects them — asserted against the emitters, not assumed."""
    for path in (EXECUTOR, TOOL_EXEC):
        src = open(path).read()
        for m in re.finditer(r"'type':\s*'feedback_delivered'", src):
            # The emitted dict literal, from the type key to its closing brace.
            tail = src[m.start():m.start() + 400]
            end = tail.index("}")
            payload = tail[:end]
            assert "'content'" not in payload and "'text'" not in payload, (
                f"{path}: a feedback_delivered chunk now carries a content/text "
                f"field. That would make it satisfy chatApi's content gate by "
                f"accident, and its text would be appended to the transcript "
                f"as model output: {payload!r}"
            )


def test_dispatch_sits_with_the_other_type_dispatches():
    """Positive control: the fix must land at the same nesting as its siblings.

    Guards against 'fixing' this by hoisting the block only part-way out.
    """
    lines = _lines(CHAT_API)
    idx = _dispatch_line_idx(lines)
    ref = next(
        i for i, l in enumerate(lines)
        if "unwrappedData.type === 'throttling_status'" in l
    )
    assert _indent(lines[idx]) == _indent(lines[ref]), (
        f"feedback_delivered dispatch is at indent {_indent(lines[idx])} but "
        f"the sibling throttling_status dispatch is at {_indent(lines[ref])}; "
        f"it is still nested inside something"
    )


# ── the rest of the chain, so a future break is attributed correctly ──────

def test_relay_passes_the_ack_through():
    src = open(SERVER).read()
    assert "chunk.get('type') == 'feedback_delivered'" in src, (
        "server.py's SSE relay no longer has a feedback_delivered branch; the "
        "chunk falls through to the unknown-chunk debug log and never reaches "
        "the browser"
    )


def test_dispatched_event_carries_the_fields_both_handlers_read():
    """SendChatContainer reads message + conversationId; Conversation.tsx reads
    conversationId.  A dispatch missing either silently no-ops."""
    src = open(CHAT_API).read()
    idx = src.index("unwrappedData.type === 'feedback_delivered'")
    block = src[idx:idx + 600]
    assert "new CustomEvent('feedbackDelivered'" in block
    detail = block[block.index("detail:"):]
    assert "message:" in detail[:300], (
        "the dispatched detail omits message; the composer's prune matches "
        "retained texts against it and would clear nothing"
    )
    assert "conversationId" in detail[:300], (
        "the dispatched detail omits conversationId; both handlers gate on it "
        "and would ignore every ack"
    )


def test_composer_still_listens_for_the_event():
    """The consumer half — asserted so a break here is not misread as the gate."""
    src = open(COMPOSER).read()
    assert "addEventListener('feedbackDelivered'" in src, (
        "the composer no longer listens for feedbackDelivered, so nothing "
        "prunes pendingFeedbackRef and every delivered feedback is re-sent"
    )
