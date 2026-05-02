"""
Tests for app.agents.wrappers.google_direct — the DirectGoogleModel text-only fallback.

Verifies:
  1. No mcp_manager import or call_tool invocation (bug was removed)
  2. astream yields text chunks from Google API
  3. tools= kwarg is ignored with a warning (not dispatched)
  4. Error from API is yielded as error chunk
  5. Thinking config set for supported models
  6. Safety settings always present
  7. ainvoke collects text chunks and returns dict
  8. bind() returns self (compatibility stub)
"""

from __future__ import annotations

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _text_part(text: str) -> MagicMock:
    p = MagicMock()
    p.text = text
    p.function_call = None
    return p


def _fc_part(name: str) -> MagicMock:
    """A function_call part — should never be dispatched by DirectGoogleModel."""
    fc = MagicMock()
    fc.name = name
    fc.args = {"x": 1}
    p = MagicMock()
    p.text = None
    p.function_call = fc
    return p


def _chunk(parts):
    c = MagicMock()
    c.parts = parts
    return c


class _AsyncIter:
    def __init__(self, items):
        self._items = iter(items)

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return next(self._items)
        except StopIteration:
            raise StopAsyncIteration


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def model():
    """DirectGoogleModel with mocked Google client."""
    from google import genai
    mock_client = MagicMock()
    mock_client.aio = MagicMock()
    mock_client.aio.models = MagicMock()
    with patch.object(genai, "Client", return_value=mock_client):
        from app.agents.wrappers.google_direct import DirectGoogleModel
        m = DirectGoogleModel(
            model_name="gemini-2.5-pro",
            temperature=0.3,
            max_output_tokens=4096,
        )
    m.client = mock_client
    return m


@pytest.fixture
def thinking_model():
    from google import genai
    mock_client = MagicMock()
    mock_client.aio = MagicMock()
    mock_client.aio.models = MagicMock()
    with patch.object(genai, "Client", return_value=mock_client):
        from app.agents.wrappers.google_direct import DirectGoogleModel
        m = DirectGoogleModel(
            model_name="gemini-3-thinking",
            temperature=0.3,
            max_output_tokens=4096,
            thinking_level="high",
        )
    m.client = mock_client
    return m


def _wire(model, chunks):
    model.client.aio.models.generate_content_stream = AsyncMock(
        return_value=_AsyncIter(chunks)
    )


# ---------------------------------------------------------------------------
# 1. No mcp_manager in the module
# ---------------------------------------------------------------------------

class TestNoMcpManagerDependency:
    def test_no_mcp_manager_import(self):
        """DirectGoogleModel must not import get_mcp_manager."""
        import app.agents.wrappers.google_direct as mod
        src = open(mod.__file__).read()
        assert "get_mcp_manager" not in src

    def test_no_mcp_manager_attribute(self, model):
        assert not hasattr(model, "mcp_manager")

    def test_no_call_tool_reference(self):
        import app.agents.wrappers.google_direct as mod
        src = open(mod.__file__).read()
        assert "call_tool" not in src


# ---------------------------------------------------------------------------
# 2. Text streaming
# ---------------------------------------------------------------------------

class TestTextStreaming:
    @pytest.mark.asyncio
    async def test_yields_text_chunks(self, model):
        _wire(model, [_chunk([_text_part("Hello "), _text_part("world")])])
        chunks = []
        async for c in model.astream([MagicMock(content="hi")]):
            chunks.append(c)
        texts = [c["content"] for c in chunks if c.get("type") == "text"]
        assert texts == ["Hello ", "world"]

    @pytest.mark.asyncio
    async def test_empty_stream_yields_nothing(self, model):
        _wire(model, [])
        chunks = [c async for c in model.astream([MagicMock(content="hi")])]
        assert chunks == []

    @pytest.mark.asyncio
    async def test_null_parts_skipped(self, model):
        empty = MagicMock()
        empty.parts = None
        _wire(model, [empty])
        chunks = [c async for c in model.astream([MagicMock(content="hi")])]
        assert chunks == []

    @pytest.mark.asyncio
    async def test_function_call_parts_silently_ignored(self, model):
        """Function call parts must not trigger mcp_manager — just no output."""
        _wire(model, [_chunk([_fc_part("file_read")])])
        chunks = [c async for c in model.astream([MagicMock(content="hi")])]
        # No text, no tool_start — nothing dispatched
        assert all(c.get("type") != "tool_start" for c in chunks)
        assert all(c.get("type") != "text" for c in chunks)


# ---------------------------------------------------------------------------
# 3. tools= kwarg warns but does not dispatch
# ---------------------------------------------------------------------------

class TestToolsKwargIgnored:
    @pytest.mark.asyncio
    async def test_warning_logged_when_tools_passed(self, model, monkeypatch):
        import app.agents.wrappers.google_direct as gd_module
        warnings_emitted = []
        monkeypatch.setattr(gd_module.logger, "warning", lambda msg, *a, **kw: warnings_emitted.append(msg))
        _wire(model, [])
        fake_tool = MagicMock()
        fake_tool.name = "run_shell_command"
        async for _ in model.astream([MagicMock(content="hi")], tools=[fake_tool]):
            pass
        assert any("cannot dispatch" in w for w in warnings_emitted)

    @pytest.mark.asyncio
    async def test_no_tool_start_chunk_emitted(self, model):
        _wire(model, [_chunk([_text_part("ok")])])
        fake_tool = MagicMock()
        fake_tool.name = "file_read"
        chunks = [c async for c in model.astream([MagicMock(content="hi")], tools=[fake_tool])]
        assert all(c.get("type") != "tool_start" for c in chunks)


# ---------------------------------------------------------------------------
# 4. Error handling
# ---------------------------------------------------------------------------

class TestErrorHandling:
    @pytest.mark.asyncio
    async def test_api_error_yields_error_chunk(self, model):
        model.client.aio.models.generate_content_stream = AsyncMock(
            side_effect=Exception("503 Service Unavailable")
        )
        chunks = [c async for c in model.astream([MagicMock(content="hi")])]
        assert len(chunks) == 1
        assert chunks[0]["type"] == "error"
        assert "503" in chunks[0]["content"]

    @pytest.mark.asyncio
    async def test_stream_iteration_error_yields_error_chunk(self, model):
        async def _bad_stream():
            yield _chunk([_text_part("partial")])
            raise RuntimeError("stream dropped")

        model.client.aio.models.generate_content_stream = AsyncMock(
            return_value=_bad_stream()
        )
        chunks = [c async for c in model.astream([MagicMock(content="hi")])]
        types_ = [c["type"] for c in chunks]
        assert "error" in types_


# ---------------------------------------------------------------------------
# 5. Generation config
# ---------------------------------------------------------------------------

class TestGenConfig:
    @pytest.mark.asyncio
    async def test_safety_settings_always_passed(self, model):
        _wire(model, [])
        async for _ in model.astream([MagicMock(content="hi")]):
            pass
        call_kwargs = model.client.aio.models.generate_content_stream.call_args
        config = call_kwargs.kwargs.get("config") or call_kwargs.args[0] if call_kwargs.args else None
        if config is None and call_kwargs:
            config = call_kwargs[1].get("config")
        assert config is not None
        assert config.safety_settings is not None
        assert len(config.safety_settings) == 4

    @pytest.mark.asyncio
    async def test_thinking_config_set_for_thinking_model(self, thinking_model):
        _wire(thinking_model, [])
        async for _ in thinking_model.astream([MagicMock(content="hi")]):
            pass
        call_kwargs = thinking_model.client.aio.models.generate_content_stream.call_args
        config = call_kwargs.kwargs.get("config")
        assert config is not None
        assert getattr(config, "thinking_config", None) is not None

    @pytest.mark.asyncio
    async def test_no_tools_in_config(self, model):
        """DirectGoogleModel must never set tools on its gen config."""
        _wire(model, [])
        async for _ in model.astream([MagicMock(content="hi")]):
            pass
        call_kwargs = model.client.aio.models.generate_content_stream.call_args
        config = call_kwargs.kwargs.get("config")
        assert not getattr(config, "tools", None)


# ---------------------------------------------------------------------------
# 6. ainvoke and bind
# ---------------------------------------------------------------------------

class TestAinvokeAndBind:
    @pytest.mark.asyncio
    async def test_ainvoke_returns_text_dict(self, model):
        _wire(model, [_chunk([_text_part("Hello")]), _chunk([_text_part(" world")])])
        result = await model.ainvoke([MagicMock(content="hi")])
        assert isinstance(result, dict)
        assert result["content"] == "Hello world"

    @pytest.mark.asyncio
    async def test_ainvoke_empty_response(self, model):
        _wire(model, [])
        result = await model.ainvoke([MagicMock(content="hi")])
        assert result["content"] == ""

    def test_bind_returns_self(self, model):
        result = model.bind(stop=["END"])
        assert result is model
