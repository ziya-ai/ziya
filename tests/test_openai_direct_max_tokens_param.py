"""The legacy DirectOpenAIModel sends the model's own max-tokens parameter.

GPT-5.x / 6.x on the OpenAI API reject ``max_tokens`` ("Use
'max_completion_tokens' instead").  The provider path
(app/providers/openai_direct.py) already reads ``max_tokens_param`` from
the model config; the legacy wrapper ModelManager.initialize_model builds
hard-coded ``max_tokens``.  That wrapper is what ``ziya chat`` uses for its
no-tools path (_simple_invoke) and the /quit tangent summary, so on
``--endpoint openai`` those calls failed with a 400 while the main chat
path worked.  Found by test_model_capabilities' real-API runs.
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.models import ModelManager


def _captured_kwargs(model):
    calls = []

    async def create(**kw):
        calls.append(kw)
        raise RuntimeError("stop after request")    # nothing to stream

    model.client = MagicMock()
    model.client.chat.completions.create = AsyncMock(side_effect=create)

    async def run():
        from langchain_core.messages import HumanMessage
        try:
            async for _ in model.astream([HumanMessage(content="hi")]):
                pass
        except RuntimeError as e:
            if "stop after request" not in str(e):
                raise
    # astream resolves the request's project root before building the request.
    with patch("app.context.get_project_root", return_value="/tmp"):
        asyncio.run(run())
    return calls


@pytest.fixture
def openai_env(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("ZIYA_ENDPOINT", "openai")


def test_openai_model_sends_max_completion_tokens(openai_env, monkeypatch):
    monkeypatch.setenv("ZIYA_MODEL", "gpt-5.4-mini")
    cfg = ModelManager.get_model_config("openai", "gpt-5.4-mini")
    assert cfg.get("max_tokens_param") == "max_completion_tokens"   # precondition
    model = ModelManager._initialize_openai_model(cfg)
    [kw] = _captured_kwargs(model)
    assert "max_completion_tokens" in kw and "max_tokens" not in kw


def test_default_is_still_max_tokens():
    from app.agents.wrappers.openai_direct import DirectOpenAIModel
    with patch.dict("os.environ", {"OPENAI_API_KEY": "sk-test"}):
        model = DirectOpenAIModel(model_name="glm-4.6")
    [kw] = _captured_kwargs(model)
    assert "max_tokens" in kw and "max_completion_tokens" not in kw
