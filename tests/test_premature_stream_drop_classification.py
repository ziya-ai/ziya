"""
Regression: urllib3's ``Response ended prematurely`` must classify as a
RETRYABLE transient drop, not UNKNOWN.

Observed live: a 2h33m task-card run (0b34af05) with 4 of 20 Until
iterations already passed was killed outright at iteration 5 by
``Task execution failed: Error: Response ended prematurely``.  The httpx
spelling of the identical fault (``peer closed connection`` /
``incomplete chunked read``) was already handled, so the same physical
socket drop was retriable over one transport and fatal over the other.
"""
import pytest

from app.providers.base import ErrorType
from app.providers.bedrock import BedrockProvider
from app.providers.anthropic_direct import AnthropicDirectProvider
from app.streaming_tool_executor import StreamingToolExecutor

# The exact string recorded on the failed run, plus the bare urllib3
# message and the exception class name that can surface instead.
PREMATURE_VARIANTS = [
    "Task execution failed: Error: Response ended prematurely",
    "Error: Response ended prematurely",
    "Response ended prematurely",
    "ProtocolError('Response ended prematurely')",
]

RETRYABLE = {ErrorType.THROTTLE, ErrorType.READ_TIMEOUT, ErrorType.OVERLOADED}


@pytest.mark.parametrize("msg", PREMATURE_VARIANTS)
def test_bedrock_classifies_premature_drop_as_retryable(msg):
    assert BedrockProvider._classify_error(msg) in RETRYABLE


@pytest.mark.parametrize("msg", PREMATURE_VARIANTS)
def test_anthropic_classifies_premature_drop_as_retryable(msg):
    assert AnthropicDirectProvider._classify_error(msg) in RETRYABLE


@pytest.mark.parametrize("msg", PREMATURE_VARIANTS)
def test_premature_drop_not_unknown(msg):
    """UNKNOWN is the fatal bucket — the specific regression being fixed."""
    assert BedrockProvider._classify_error(msg) is not ErrorType.UNKNOWN
    assert AnthropicDirectProvider._classify_error(msg) is not ErrorType.UNKNOWN


@pytest.mark.parametrize("msg", PREMATURE_VARIANTS)
def test_executor_read_timeout_list_also_matches(msg):
    """The executor keeps its OWN substring list, independent of the
    providers'.  A retryable provider ErrorEvent is re-raised into that
    list, so the phrase must appear in BOTH or the run still dies."""
    import inspect
    src = inspect.getsource(StreamingToolExecutor._classify_and_handle_error)
    start = src.index("is_read_timeout = any(")
    literal = src[start:src.index("])", start)]
    assert "ended prematurely" in literal or "ProtocolError" in literal, (
        "executor is_read_timeout list does not cover the urllib3 "
        "premature-drop phrasing; a retryable provider event will still "
        "be surfaced as fatal"
    )


def test_negative_control_genuinely_fatal_errors_stay_unknown():
    """Guard against over-broadening: the fix must not swallow real
    non-transient failures into the retry path."""
    for fatal in [
        "ValidationException: temperature is deprecated for this model",
        "AccessDeniedException: not authorized to invoke this model",
    ]:
        assert BedrockProvider._classify_error(fatal) is ErrorType.UNKNOWN


def test_httpx_spelling_still_retryable():
    """The pre-existing httpx branch must not regress."""
    httpx_msg = ("peer closed connection without sending complete message "
                 "body (incomplete chunked read)")
    assert BedrockProvider._classify_error(httpx_msg) is ErrorType.READ_TIMEOUT
