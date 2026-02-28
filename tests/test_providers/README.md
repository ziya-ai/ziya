# Provider Layer Test Suite

Comprehensive tests for `app/providers/` — the LLM provider abstraction layer.

## Test Coverage Summary

| File | Tests | Status |
|------|-------|--------|
| `test_base.py` | 37 | ✅ Passing |
| `test_bedrock.py` | 41 | ✅ Passing |
| `test_anthropic_direct.py` | 34 | ✅ Passing |
| `test_factory.py` | 7 | ✅ Passing |
| **Total** | **119** | ✅ All Passing |

## Test Details

### `test_base.py` (37 tests)
Tests the provider interface, event types, and configuration:
- **StreamEvent dataclasses** (frozen, slotted, dispatchable)
- **Event dispatch** via isinstance() pattern
- **ProviderConfig** defaults and overrides
- **ThinkingConfig** wiring
- **ErrorType** enum coverage
- **LLMProvider ABC** enforcement
- **Default method implementations**

### `test_bedrock.py` (41 tests)
Tests the BedrockProvider implementation:

- **Request Building** (8 tests)
  - Basic body with anthropic_version, max_tokens, temperature
  - System prompt caching (>1024 chars gets cache_control)
  - Tools included/suppressed
  - Adaptive thinking + standard thinking

- **Cache Control** (4 tests)
  - No cache on first iteration or short conversations
  - Cache marker at boundary (len-4)
  - Strips existing markers

- **Message Formatting** (5 tests)
  - build_assistant_message with text only
  - build_assistant_message with tools (strips mcp_ prefix)
  - build_tool_result_message

- **Feature Support** (7 tests)
  - supports_feature() for thinking, adaptive_thinking, extended_context
  - cache_control always true
  - assistant_prefill default true
  - provider_name == "bedrock"

- **Error Classification** (11 tests)
  - Throttle detection (ThrottlingException, Too many tokens, rate limit)
  - Context limit detection
  - Timeout detection
  - Overloaded detection
  - Unknown error fallback

- **Stream Parsing** (6 tests)
  - Text deltas
  - Tool use flow (start → input → input → end)
  - Usage events from amazon-bedrock-invocationMetrics
  - Thinking deltas
  - Empty stream handling
  - Chunks without 'chunk' key skipped

### `test_anthropic_direct.py` (34 tests)
Tests the AnthropicDirectProvider implementation:

- **Request Building** (7 tests)
  - Basic request with model, max_tokens, temperature
  - System prompt always cached (no size threshold)
  - Tools included/suppressed
  - Adaptive thinking + standard thinking

- **Cache Control** (4 tests)
  - No cache on first iteration or short conversations
  - Cache at second-to-last message
  - Multiblock content handling

- **Message Formatting** (5 tests)
  - build_assistant_message (keeps mcp_ prefix, unlike Bedrock)
  - build_tool_result_message

- **Feature Support** (7 tests)
  - supports_feature() checks
  - No extended_context (200k native)
  - provider_name == "anthropic"

- **Error Classification** (8 tests)
  - Throttle, overloaded, timeout, context limit, unknown

- **Initialization** (3 tests)
  - Requires API key
  - API key from parameter
  - API key from environment

### `test_factory.py` (7 tests)
Tests the provider factory:

- create_provider("bedrock") → BedrockProvider
- create_provider("anthropic") → AnthropicDirectProvider
- Unsupported endpoint → ValueError
- Empty model_config defaults
- API key passthrough (anthropic)
- Default profile/region (bedrock)
- Model config passthrough

## Running Tests

```bash
# All provider tests
pytest tests/test_providers/ -v

# Just the base interface
pytest tests/test_providers/test_base.py -v

# Specific provider
pytest tests/test_providers/test_bedrock.py -v
pytest tests/test_providers/test_anthropic_direct.py -v

# With coverage
pytest tests/test_providers/ --cov=app.providers --cov-report=html
```

## Architecture

```
app/providers/
├── __init__.py          # Re-exports key types
├── base.py              # StreamEvent hierarchy + LLMProvider ABC
├── bedrock.py           # AWS Bedrock implementation
├── anthropic_direct.py  # Native Anthropic API implementation
└── factory.py           # create_provider() factory function
```

The orchestrator (`StreamingToolExecutor`) uses these providers through the
`LLMProvider` interface, dispatching on `StreamEvent` subclasses via `isinstance()`.
