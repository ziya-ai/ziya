"""D-327 (chat-message-w1-12): embedded-mermaid capture race.

An assistant chat message can embed a fenced ``mermaid`` diagram. The mermaid
plugin mounts asynchronously (lazy JS chunk + layout), so the message *text* --
which includes the diagram source shown in the transient "Specification"
panel -- is present well before the rendered ``svg`` exists. The old
``build_rendered_predicate`` only checked for non-empty text (and optionally
``.katex``), so the render predicate passed on that early source text and the
screenshot captured the source panel instead of the diagram.

The fix adds an OPTIONAL mermaid gate: when the document embeds a mermaid
fence, the predicate additionally requires every
``[data-visualization-type="mermaid"]`` container to hold a rendered ``svg``
(or a settled plugin error card) before the message reads as rendered.

These tests FAIL against the pre-fix module: ``expects_mermaid`` did not exist
and ``build_rendered_predicate`` had no ``require_mermaid`` parameter.
"""
from __future__ import annotations

import pytest

from app.utils import chat_screenshot as cs


# -- expects_mermaid: drives whether a rendered diagram svg is required -------

@pytest.mark.parametrize("body", [
    "intro paragraph\n```mermaid\ngraph TD; A-->B\n```\n",
    "```mermaid\nflowchart LR\n  X --> Y\n```",
    "text\n````mermaid\nsequenceDiagram\n````",   # 4-backtick fence
    "MERMAID in caps\n```MERMAID\npie\n```",       # case-insensitive
])
def test_expects_mermaid_true_for_embedded_diagram(body):
    assert cs.expects_mermaid(body) is True


@pytest.mark.parametrize("body", [
    "just an ordinary sentence with no diagram at all",
    "```python\nx = 1\n```",
    "```\nplain fenced block naming mermaid in prose\n```",
    "the word mermaid appears but not as a fence language",
    "",
])
def test_expects_mermaid_false_without_embedded_fence(body):
    assert cs.expects_mermaid(body) is False


# -- the mermaid gate is emitted ONLY when a diagram is expected --------------

MERMAID_VIZ_SELECTOR = '[data-visualization-type="mermaid"]'


def test_rendered_predicate_requires_diagram_svg_only_when_mermaid_expected():
    with_mermaid = cs.build_rendered_predicate(
        ["Word"], "assistant", False, True
    )
    without = cs.build_rendered_predicate(
        ["Word"], "assistant", False, False
    )
    # The gate that waits for the rendered diagram svg is present only when
    # a mermaid diagram is expected -- statically inspectable, not a dead
    # runtime branch.
    assert MERMAID_VIZ_SELECTOR in with_mermaid
    assert "querySelector('svg')" in with_mermaid
    assert MERMAID_VIZ_SELECTOR not in without


def test_mermaid_gate_treats_error_card_as_settled():
    """A genuinely broken diagram (error card, no svg) must not hang the wait
    forever -- like a KaTeX error, the picture is the finding."""
    js = cs.build_rendered_predicate(["Word"], "assistant", False, True)
    assert "[data-diagram-error]" in js


def test_mermaid_and_math_gates_compose():
    """Both gates can be required at once (a message with math AND a diagram)."""
    js = cs.build_rendered_predicate(["Word"], "assistant", True, True)
    assert "querySelector('.katex')" in js
    assert MERMAID_VIZ_SELECTOR in js


def test_math_only_predicate_carries_no_mermaid_selector():
    """A math-only render must not accidentally wait on a diagram svg."""
    js = cs.build_rendered_predicate(["Word"], "assistant", True, False)
    assert "querySelector('.katex')" in js
    assert MERMAID_VIZ_SELECTOR not in js
