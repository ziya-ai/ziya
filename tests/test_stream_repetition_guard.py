"""
End-to-end tests for the streaming sentence-repetition guard in
StreamingToolExecutor.stream_with_tools() (the "STREAM_REPETITION:
Suppressing after 3x" check).

Incident (2026-10-05): a gallery of Vega-Lite charts whose specs each carried
the same ~200-char `calculate` expression was cut off mid-JSON on the THIRD
copy of that expression, twice in a row (once at chart #28, once at #30).

Root cause, two defects meeting at one seam:

1. The guard skips text only while ``code_block_tracker['in_block']`` is True.
   Visualization fences (vega-lite, mermaid, graphviz, plotly, ...) are
   buffered in ``viz_buffer`` by text_delta_processor and the tracker is only
   updated when the CLOSING fence arrives. So for the whole body of a viz
   block ``in_block`` is False and the guard scans JSON as if it were prose.
   ``? `` inside a JS ternary is a sentence terminator to it, so the identical
   ``sampleNormal(40,6) : datum.lru=='BETA' ?`` fragment counted as a
   repeated sentence and suppression fired on the third chart.

2. Once suppressed, further text is withheld from the user but still appended
   to ``assistant_text``, the model's own record of its turn. The model's view
   (complete answer) and the user's view (truncated) diverge; the subsequent
   "your previous response was cut off" continuation then contradicted what
   the model could see, and it reported, wrongly, that nothing was missing.

These tests drive the real generator with the real fence tracker and real
content optimizer (the existing repeat-block harness stubs the tracker to a
no-op, which would hide defect 1 entirely).
"""

import json
import os
import re
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.providers.base import (
    StreamEnd,
    TextDelta,
    ToolUseEnd,
    ToolUseInput,
    ToolUseStart,
)
from app.utils.repetition_guard import REPETITION_NOTICE

# The exact shape of the expression from the incident: a JS ternary chain
# whose "? " separators read as sentence ends to the prose guard.
_SHARED_EXPR = (
    "datum.lru=='ALPHA' ? sampleNormal(40,6) : datum.lru=='BETA' ? "
    "(random()<0.5 ? sampleNormal(28,4) : sampleNormal(55,5)) : "
    "15+sampleLogNormal(2.6,0.45)"
)
_TAIL_MARKER = "GALLERY_COMPLETE_MARKER"


def _vega_spec(title: str) -> str:
    return json.dumps({
        "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
        "title": title,
        "data": {"sequence": {"start": 0, "stop": 900, "as": "i"}},
        "transform": [{"calculate": _SHARED_EXPR, "as": "ms"}],
        "mark": "tick",
        "encoding": {"x": {"field": "ms", "type": "quantitative"}},
    })


def _gallery_text(n_charts: int = 4) -> str:
    parts = ["Here are the charts.\n\n"]
    for i in range(1, n_charts + 1):
        parts.append(f"**{i}. Chart number {i}**\n")
        parts.append("```vega-lite\n" + _vega_spec(f"Chart {i}") + "\n```\n\n")
    parts.append(f"All charts are above. {_TAIL_MARKER}\n")
    return "".join(parts)


def _chunks(text: str, size: int = 17):
    """Split like a real token stream: small, boundary-agnostic pieces."""
    return [text[i:i + size] for i in range(0, len(text), size)]


def _make_executor():
    """Real StreamingToolExecutor with __init__ bypassed.

    Deliberately does NOT stub _update_code_block_tracker,
    _normalize_fence_spacing or _content_optimizer: the defect lives in how
    those interact with the repetition guard.
    """
    with patch.dict(os.environ, {"ZIYA_ENDPOINT": "bedrock", "ZIYA_MODEL": "sonnet3.7"}):
        with patch(
            "app.streaming_tool_executor.StreamingToolExecutor.__init__",
            return_value=None,
        ):
            from app.providers.anthropic_direct import AnthropicDirectProvider
            from app.streaming_tool_executor import StreamingToolExecutor

            ex = StreamingToolExecutor.__new__(StreamingToolExecutor)
            ex.model_id = "us.anthropic.claude-3-5-sonnet-20241022-v2:0"
            ex.model_config = {"family": "claude", "max_output_tokens": 8192}
            ex.bedrock = None

            from app.providers.base import LLMProvider as BaseProvider

            builder = MagicMock()
            # build_assistant_message delegates block ordering to this base
            # helper; left as a MagicMock it returns a mock, not content.
            builder._ordered_assistant_content = BaseProvider._ordered_assistant_content
            builder.build_assistant_message = (
                AnthropicDirectProvider.build_assistant_message.__get__(builder)
            )
            builder.build_tool_result_message = (
                AnthropicDirectProvider.build_tool_result_message.__get__(builder)
            )
            ex.provider = builder

            ex._block_opening_buffer = ""
            ex._repetition_suppressed = False
            # End-of-turn wait for late user feedback; 10 s per turn end in
            # production, pure dead time here (~35 s per test without this).
            ex._FEEDBACK_GRACE_SECONDS = 0.0
            ex._sanitize_assistant_text = lambda t: t
            ex._normalize_tool_name = lambda n: n
            ex._build_conversation_from_messages = MagicMock(
                return_value=(
                    [{"role": "user", "content": [{"type": "text", "text": "go"}]}],
                    "system prompt",
                )
            )
            ex._build_provider_config = MagicMock(
                return_value=MagicMock(max_output_tokens=8192)
            )
            ex._handle_usage_event = MagicMock()

            async def _tools(*a, **k):
                return ([], [], set(), set(), set())

            ex._load_and_prepare_tools = _tools
            return ex, builder


class _ScriptedProvider:
    """Plays a fixed list of event scripts, one per provider call, and
    records the conversation each call received, how many events each call
    actually handed out, and whether the stream was closed before its end."""

    provider_name = "mock"

    def __init__(self, scripts, builder):
        self.scripts = list(scripts)
        self.calls = []
        self.yielded = []      # events handed out, per call
        self.exhausted = []    # True if the script ran to its last event
        self.build_assistant_message = builder.build_assistant_message
        self.build_tool_result_message = builder.build_tool_result_message

    def supports_feature(self, feature_name: str) -> bool:
        return False

    async def stream_response(self, conversation, system_content, tools, config):
        self.calls.append(json.loads(json.dumps(conversation, default=str)))
        idx = len(self.calls) - 1
        script = self.scripts[idx] if idx < len(self.scripts) else [
            TextDelta(content="(no further output)"),
            StreamEnd(stop_reason="end_turn"),
        ]
        self.yielded.append(0)
        self.exhausted.append(False)
        for evt in script:
            self.yielded[idx] += 1
            yield evt
        self.exhausted[idx] = True


def _text_script(text: str):
    return [TextDelta(content=c) for c in _chunks(text)] + [StreamEnd(stop_reason="end_turn")]


async def _run(ex, provider, cap=2000):
    mgr = MagicMock()
    mgr.is_initialized = True
    mgr.reset_turn_tool_count = MagicMock()
    ex.provider = provider

    async def _fake_exec(ctx):
        yield {
            "type": "_tool_result",
            "tool_id": ctx.tool_id,
            "tool_name": ctx.tool_name,
            "result": "ok",
        }

    events = []
    with patch.dict(os.environ, {"ZIYA_MAX_TOOL_ITERATIONS": "6"}), \
         patch("app.mcp.manager.get_mcp_manager", return_value=mgr), \
         patch("app.tool_execution.execute_single_tool", _fake_exec):
        async for e in ex.stream_with_tools(
            messages=[{"role": "user", "content": "go"}],
            tools=[], conversation_id="conv-rep-guard",
        ):
            events.append(e)
            if len(events) >= cap:
                break
    return events


def _shown_text(events) -> str:
    """What the user's chat actually receives: the concatenated text events."""
    return "".join(
        e.get("content", "") for e in events
        if e.get("type") == "text" and isinstance(e.get("content"), str)
    )



class TestRepetitionGuardSkipsVizFences:

    @pytest.mark.asyncio
    async def test_repeated_expression_across_viz_fences_is_not_cut(self):
        """The incident: identical JSON expression in 4 vega-lite fences.
        Every fence and the trailing prose must reach the user."""
        ex, builder = _make_executor()
        prov = _ScriptedProvider([_text_script(_gallery_text(4))], builder)
        shown = _shown_text(await _run(ex, prov))

        # Positive: the stream ran and the first chart arrived.
        assert "Chart number 1" in shown
        # The defect: content after the 3rd copy was withheld.
        for i in range(1, 5):
            assert f'"title": "Chart {i}"' in shown, (
                f"chart {i} spec missing from what the user saw "
                f"(repetition guard fired inside a viz fence)"
            )
        assert _TAIL_MARKER in shown
        assert shown.count("```vega-lite") == 4

    @pytest.mark.asyncio
    async def test_prose_degeneration_is_still_suppressed(self):
        """Control: the guard must keep working on real prose loops, or the
        test above would pass by the guard simply being removed."""
        ex, builder = _make_executor()
        loop = "The system is processing your request right now. " * 5
        prov = _ScriptedProvider(
            [_text_script("Starting. " + loop + "UNREACHABLE_AFTER_LOOP.\n")], builder,
        )
        shown = _shown_text(await _run(ex, prov))

        assert "Starting." in shown
        # Detection runs after the delta completing the 3rd copy is already
        # yielded, so the user sees exactly 3 copies and nothing after.
        assert shown.count("processing your request right now") == 3
        assert "UNREACHABLE_AFTER_LOOP" not in shown
        # The cut is announced, not silent.
        assert REPETITION_NOTICE in shown

    @pytest.mark.asyncio
    async def test_refrain_with_new_material_between_is_not_cut(self):
        """A sentence recurring with different content between copies is
        legitimate prose; the old rule cut it on the 3rd copy."""
        ex, builder = _make_executor()
        refrain = "This behaviour is unchanged from the previous release. "
        text = (
            "The parser now accepts trailing commas in arrays. " + refrain
            + "Error messages include the byte offset of the fault. " + refrain
            + "Unicode escapes are validated before decoding begins. " + refrain
            + f"That is the full list. {_TAIL_MARKER}\n"
        )
        prov = _ScriptedProvider([_text_script(text)], builder)
        shown = _shown_text(await _run(ex, prov))

        assert shown.count("unchanged from the previous release") == 3
        assert _TAIL_MARKER in shown
        assert REPETITION_NOTICE not in shown


class TestRepetitionStopsProviderStream:

    @pytest.mark.asyncio
    async def test_loop_closes_stream_and_ends_turn(self):
        """Once a loop is detected the provider stream is closed: the model
        stops generating (and being billed) for text nobody will see. The
        hidden tail and the tool call after it are never even produced, so
        the model's history cannot diverge from what the user saw."""
        ex, builder = _make_executor()
        loop = "The system is processing your request right now. " * 4
        first_call = (
            [TextDelta(content=c) for c in _chunks("Starting. " + loop + "HIDDEN_TAIL_TEXT. ")]
            + [
                ToolUseStart(id="tu_1", name="mcp_run_shell_command", index=0),
                ToolUseInput(partial_json=json.dumps({"command": "true"}), index=0),
                ToolUseEnd(id="tu_1", name="mcp_run_shell_command",
                           input={"command": "true"}, index=0),
                StreamEnd(stop_reason="tool_use"),
            ]
        )
        prov = _ScriptedProvider(
            [first_call, _text_script("Done after tool.\n")], builder,
        )
        events = await _run(ex, prov)
        shown = _shown_text(events)

        # Positive: the stream ran and the loop prefix reached the user.
        assert "Starting." in shown
        assert shown.count("processing your request right now") == 3
        # The provider was not drained: it stopped mid-script.
        assert prov.exhausted == [False], "provider stream ran to completion"
        assert prov.yielded[0] < len(first_call)
        # Nothing after the cut, no tool round-trip, and the turn ended.
        assert "HIDDEN_TAIL_TEXT" not in shown
        assert len(prov.calls) == 1
        assert REPETITION_NOTICE in shown
        assert any(e.get("type") == "stream_end" for e in events)


# --- Every fence type the frontend renders -----------------------------------

_FRONTEND_VIZ_TS = (
    Path(__file__).resolve().parents[1]
    / "frontend" / "src" / "constants" / "visualizationTypes.ts"
)


def _frontend_viz_fence_types():
    """Fence languages the frontend renders as visualizations.

    Read from the frontend's canonical list so a renderer added there is
    covered here automatically, plus the LaTeX profiles it spreads in and the
    renderers MarkdownRenderer routes outside that list.
    """
    src = _FRONTEND_VIZ_TS.read_text()
    body = src.split("VISUALIZATION_TYPES", 1)[1].split("];", 1)[0]
    names = set(re.findall(r"'([a-z][\w-]*)'", body))
    from app.services.latex_profiles import PROFILES
    names |= set(PROFILES)
    names |= {"plotly", "html-mockup", "music"}
    return sorted(names)


_VIZ_FENCE_TYPES = _frontend_viz_fence_types()

# A body line carrying the incident's ternary shape ("? " reads as a sentence
# end to the prose guard), identical in every fence.
_REPEATED_BODY_LINE = (
    "x = datum.lru=='ALPHA' ? sampleNormal(40,6) : datum.lru=='BETA' ? "
    "(random()<0.5 ? sampleNormal(28,4) : sampleNormal(55,5)) : 15;"
)


def _fence_gallery(lang: str, n: int = 4) -> str:
    parts = ["Here they are.\n\n"]
    for i in range(1, n + 1):
        parts.append(f"**Figure {i}**\n")
        parts.append(f"```{lang}\n{_REPEATED_BODY_LINE}\nFENCE_BODY_{i}\n```\n\n")
    parts.append(f"Done. {_TAIL_MARKER}\n")
    return "".join(parts)


def test_frontend_viz_type_list_was_read():
    """Guard for the parametrization below: if the frontend list moves or
    its format changes, fail loudly instead of parametrizing over nothing."""
    assert len(_VIZ_FENCE_TYPES) >= 15
    for expected in ("mermaid", "vega-lite", "wavedrom", "railroad",
                     "flamegraph", "circuitikz", "plotly", "html-mockup"):
        assert expected in _VIZ_FENCE_TYPES


@pytest.mark.asyncio
@pytest.mark.parametrize("lang", _VIZ_FENCE_TYPES)
async def test_repeated_body_inside_any_viz_fence_is_not_cut(lang):
    """Buffered (vega-lite, mermaid, ...) or streamed (wavedrom, railroad,
    TeX, ...), repeated content inside a rendered fence must never trip the
    prose guard. This is the seam the vega-lite incident broke."""
    ex, builder = _make_executor()
    prov = _ScriptedProvider([_text_script(_fence_gallery(lang))], builder)
    shown = _shown_text(await _run(ex, prov))

    assert "Figure 1" in shown
    for i in range(1, 5):
        assert f"FENCE_BODY_{i}" in shown, f"{lang}: fence {i} withheld"
    assert _TAIL_MARKER in shown
    assert REPETITION_NOTICE not in shown
