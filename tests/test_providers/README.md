# Provider Layer Test Suite

Comprehensive tests for `app/providers/` — the LLM provider abstraction layer.

## Test Coverage Summary

| File | Tests | Status |
|------|-------|--------|
| `test_base.py` | 37 | ✅ Passing |
| `test_bedrock.py` | 41 | ✅ Passing |
| `test_anthropic_direct.py` | 41 | ✅ Passing |
| `test_factory.py` | 7 | ✅ Passing |
| `test_bedrock_mantle_region.py` | 9 | ✅ Passing |
| `test_bedrock_mantle_cache.py` | 13 | ✅ Passing |
| **Total** | **148** | ✅ All Passing |

> `test_anthropic_direct.py` includes 2 tests asserting `temperature` is
> dropped when the model lists it in `unsupported_parameters` (the Fable 5 /
> Mythos-class 400 that surfaced on the inherited Mantle path). Its cache and
> request-building tests are inherited in behavior by `BedrockMantleProvider`;
> `test_bedrock_mantle_cache.py` pins the Mantle-specific parity (stale-marker
> stripping under the Bedrock 4-breakpoint limit, `ZIYA_DISABLE_PROMPT_CACHE`,
> and usage cache-counter surfacing).

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

## Live / real-endpoint testing (footgun)

Unit tests mock the SDK client and never touch the network. When you write a
throwaway harness that hits a **real** endpoint (e.g. verifying Bedrock Mantle
prompt caching returns non-zero `cache_read_input_tokens`), do **not** run it
from outside the repo root:

```bash
# WRONG — sys.path[0] becomes /tmp, so `import app` resolves to the
# INSTALLED site-packages copy, not your edited workspace tree. Your local
# provider fix is silently absent and the harness "reproduces" the old bug.
python3 /tmp/my_live_harness.py

# RIGHT — force the workspace onto sys.path before importing app, or run
# from the repo root so cwd wins.
#   sys.path.insert(0, "/path/to/ziya-<ver>")   # at top of the script
# then verify you loaded the right module:
#   import app.providers.anthropic_direct as m; print(m.__file__)
```

This bit us while validating the Mantle cache-marker stripping fix: the first
live run 400'd with `A maximum of 4 blocks with cache_control` because it
imported the unpatched installed copy. Confirm `m.__file__` points into the
workspace before trusting any live result.

Real-endpoint harnesses also need live AWS creds (the `aws` CLI and `env` are
blocked under the shell policy — probe credentials via `boto3.Session()` in
Python instead) and must set `temperature=None` (or use a model without
`temperature` in `unsupported_parameters`) for Fable 5 / Mythos-class models,
which reject `temperature` with a 400.

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
