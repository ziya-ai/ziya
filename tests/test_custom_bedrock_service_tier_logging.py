"""
CustomBedrockClient must not log a service-tier rejection as a failure.

Task Card runs request the Flex tier optimistically (app/utils/service_tier.py);
Claude rejects it with ``ValidationException: The provided service tier is
not supported for this model`` and BedrockProvider retries once at the
standard tier and remembers the answer per model.  The wrapper sits between
them and used to log every such rejection at WARNING (``INITIAL_ERROR``) and
twice at ERROR (``Error in custom invoke`` / ``Model ID being used``), so a
card whose blocks use several model tiers printed three alarming lines per
model for a condition that is expected and handled.
"""
import json
from types import SimpleNamespace

import pytest

from app.utils.custom_bedrock import CustomBedrockClient

TIER_REJECT = (
    "An error occurred (ValidationException) when calling the "
    "InvokeModelWithResponseStream operation: The provided service tier is "
    "not supported for this model."
)
OTHER_REJECT = (
    "An error occurred (ValidationException) when calling the "
    "InvokeModelWithResponseStream operation: max_tokens: 99999 > 64000"
)


class _Client:
    def __init__(self, message):
        self.meta = SimpleNamespace(region_name="us-west-2")
        self.message = message
        self.calls = 0

    def invoke_model_with_response_stream(self, **_kw):
        self.calls += 1
        raise Exception(self.message)


def _invoke(client):
    wrapped = CustomBedrockClient(client)
    return wrapped.invoke_model_with_response_stream(
        modelId="global.anthropic.claude-sonnet-5-5",
        body=json.dumps({"messages": [], "max_tokens": 100}),
        serviceTier="flex",
    )


@pytest.fixture
def logged(monkeypatch):
    """(level, message) pairs the module logged.

    ModeAwareLogger does not propagate, so caplog cannot see it; capture
    the module logger's methods directly, as test_bedrock_retention_heal does.
    """
    import app.utils.custom_bedrock as cb
    records = []
    for level in ("debug", "info", "warning", "error"):
        monkeypatch.setattr(cb.logger, level,
                            lambda m, *a, _lvl=level, **k: records.append((_lvl, str(m))))
    return records


def test_tier_rejection_propagates_unchanged_without_warning_or_error_logs(logged):
    client = _Client(TIER_REJECT)
    with pytest.raises(Exception) as excinfo:
        _invoke(client)
    # The provider keys its retry on this exact wording.
    assert "service tier is not supported" in str(excinfo.value)
    assert client.calls == 1  # the wrapper does not retry it itself
    loud = [m for lvl, m in logged if lvl in ("warning", "error")]
    assert loud == [], loud
    # Positive check that the path ran: the rejection is noted at DEBUG.
    assert any(lvl == "debug" and "service tier rejected" in m for lvl, m in logged)


def test_other_validation_error_still_logs_as_before(logged):
    client = _Client(OTHER_REJECT)
    with pytest.raises(Exception):
        _invoke(client)
    loud = [m for lvl, m in logged if lvl in ("warning", "error")]
    assert any("INITIAL_ERROR" in m for m in loud)
    assert any("Error in custom invoke" in m for m in loud)
    assert any("Model ID being used" in m for m in loud)


# ---------------------------------------------------------------------------
# The extended-context retry path.  Its catch-all used to rewrite ANY
# ValidationException into "content is too large even for extended
# context", which would hide the tier wording from BedrockProvider: no
# mark_unsupported, so the tier was re-probed on every call.  Reachable on
# the first call to a model in a task run when the body is large enough
# for the proactive extended-context path.
# ---------------------------------------------------------------------------

EXT_CONFIG = {
    "model_id": "global.anthropic.claude-sonnet-5-5",
    "supports_extended_context": True,
    "extended_context_header": "context-1m-2025-08-07",
    "token_limit": 200_000,
    "extended_context_limit": 1_000_000,
}


def _ext_retry(client, conversation_id):
    wrapped = CustomBedrockClient(client, model_config=EXT_CONFIG)
    return wrapped._retry_with_extended_context(
        {
            "modelId": EXT_CONFIG["model_id"],
            "body": json.dumps({"messages": [], "max_tokens": 100}),
            "serviceTier": "flex",
        },
        "Proactive extended context activation",
        conversation_id,
    )


def test_extended_context_retry_passes_tier_rejection_through_unchanged(logged):
    client = _Client(TIER_REJECT)
    with pytest.raises(Exception) as excinfo:
        _ext_retry(client, "conv-tier-ext-1")
    # Not rewritten: the provider still sees the wording it keys on.
    assert "service tier is not supported" in str(excinfo.value)
    assert "too large" not in str(excinfo.value)
    assert client.calls == 1
    loud = [m for lvl, m in logged if lvl in ("warning", "error")]
    assert loud == [], loud
    assert any(lvl == "debug" and "service tier rejected" in m for lvl, m in logged)


def test_extended_context_retry_still_rewrites_a_real_overflow(logged):
    client = _Client(
        "An error occurred (ValidationException) when calling the "
        "InvokeModelWithResponseStream operation: Input is too long for requested model."
    )
    with pytest.raises(Exception) as excinfo:
        _ext_retry(client, "conv-tier-ext-2")
    assert "too large even for extended context" in str(excinfo.value)
    assert any(lvl == "error" and "Retry failed" in m for lvl, m in logged)
