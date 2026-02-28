# Provider Refactoring: Phases 0–2 Complete ✅

## Executive Summary

Successfully completed the foundational work for the provider abstraction layer:
- ✅ **Phase 0**: Provider interface defined (`app/providers/base.py`)
- ✅ **Phase 1**: Bedrock provider extracted (`app/providers/bedrock.py`)
- ✅ **Phase 2**: Anthropic direct provider created (`app/providers/anthropic_direct.py`)
- ✅ **Tests**: Base interface tested (37/37 passing)
- 📝 **Remaining**: Provider-specific tests + Phase 3 (orchestrator integration)

---

## What Was Built

### 1. Provider Interface (`app/providers/base.py` — 220 lines)

**StreamEvent Hierarchy** (9 types):
```python
@dataclass(frozen=True, slots=True)
class StreamEvent: ...

class TextDelta(StreamEvent):
    content: str

class ToolUseStart(StreamEvent):
    id: str
    name: str
    index: int = 0

class ToolUseInput(StreamEvent):
    partial_json: str
    index: int = 0

class ToolUseEnd(StreamEvent):
    id: str
    name: str
    input: Dict[str, Any]
    index: int = 0

class UsageEvent(StreamEvent):
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

class ThinkingDelta(StreamEvent):
    content: str

class ErrorEvent(StreamEvent):
    message: str
    error_type: ErrorType = ErrorType.UNKNOWN
    retryable: bool = False
    status_code: Optional[int] = None

class StreamEnd(StreamEvent):
    stop_reason: str = "end_turn"
```

**Why dataclasses?**
- Frozen → immutable (no accidental state corruption)
- Slotted → 30-40% memory savings vs dicts
- Pattern matching via `isinstance()` is clean and fast

**LLMProvider ABC**:
```python
class LLMProvider(ABC):
    @abstractmethod
    async def stream_response(
        messages, system_content, tools, config
    ) -> AsyncGenerator[StreamEvent, None]: ...
    
    @abstractmethod
    def build_assistant_message(text, tool_uses) -> Dict: ...
    
    @abstractmethod
    def build_tool_result_message(tool_results) -> Dict: ...
    
    def prepare_cache_control(messages, iteration) -> List[Dict]:
        return messages  # default no-op
    
    def supports_feature(feature_name: str) -> bool:
        return False  # default
    
    @property
    def provider_name(self) -> str:
        return self.__class__.__name__
```

**ProviderConfig** — the orchestrator → provider contract:
```python
@dataclass
class ProviderConfig:
    max_output_tokens: int = 16384
    temperature: Optional[float] = 0.3
    thinking: Optional[ThinkingConfig] = None
    enable_cache: bool = True
    use_extended_context: bool = False
    suppress_tools: bool = False
    model_config: Dict[str, Any] = field(default_factory=dict)
    iteration: int = 0
```

### 2. Bedrock Provider (`app/providers/bedrock.py` — 389 lines)

**Extracted from StreamingToolExecutor**:
- boto3 client initialization
- `invoke_model_with_response_stream` + retry loop
- Bedrock body building (`anthropic_version: bedrock-2023-05-31`)
- Stream parsing (boto3 chunks → `StreamEvent` objects)
- 4-block cache control strategy
- Extended context negotiation
- Error classification (throttle, context limit, timeout, overloaded)

**Key methods**:
```python
async def stream_response(messages, system, tools, config):
    # Build Bedrock request body
    body = self._build_request_body(...)
    
    # Retry loop (4 retries, exponential backoff)
    for retry_attempt in range(max_retries + 1):
        try:
            response = self.bedrock.invoke_model_with_response_stream(...)
            break
        except Exception as e:
            # Classify error, retry if appropriate
            ...
    
    # Parse boto3 stream → normalized events
    async for event in self._parse_stream(response, config):
        yield event
```

**Features**:
- ✅ Adaptive thinking (`thinking: {type: "adaptive"}` + effort)
- ✅ Standard thinking (`thinking: {type: "enabled", budget_tokens: N}`)
- ✅ Extended context (adds `anthropic_beta` header on context limit)
- ✅ Prompt caching (4-block limit enforcement)
- ✅ Tool suppression (loop-breaking mechanism)
- ✅ Retry coordination (delegates to orchestrator for cross-cutting concerns)

### 3. Anthropic Direct Provider (`app/providers/anthropic_direct.py` — 244 lines)

**Native Anthropic SDK**:
- `anthropic.AsyncAnthropic` client
- `client.messages.stream()` for streaming
- Same tool format as Bedrock (it's the same API underneath)
- No 4-block cache limit (Anthropic native has no such restriction)

**Key differences from Bedrock**:
- System prompt always cached (no size threshold)
- Cache boundary at second-to-last message (simpler strategy)
- No extended context header (200k context native)
- Simpler event parsing (SDK already normalizes somewhat)

### 4. Provider Factory (`app/providers/factory.py` — 77 lines)

```python
def create_provider(
    endpoint: str,
    model_id: str,
    model_config: Dict,
    *,
    aws_profile: Optional[str] = None,
    region: Optional[str] = None,
    api_key: Optional[str] = None,
) -> LLMProvider:
    if endpoint == "bedrock":
        return BedrockProvider(model_id, model_config, aws_profile, region)
    if endpoint == "anthropic":
        return AnthropicDirectProvider(model_id, model_config, api_key)
    raise ValueError(f"No LLMProvider for endpoint '{endpoint}'")
```

---

## Test Coverage

### ✅ Completed: `test_base.py` (37/37 passing)
- StreamEvent dataclass properties (frozen, slotted)
- Event dispatch via isinstance()
- ProviderConfig defaults and overrides
- ThinkingConfig wiring
- ErrorType enum coverage
- LLMProvider ABC enforcement
- Default method implementations

### 📝 Remaining Test Files

**`test_bedrock.py` (35 tests)**:
- Request body building (8 tests)
- Cache control strategy (4 tests)
- Stream parsing (4 tests)
- Retry logic (4 tests)
- Message formatting (4 tests)
- Feature support (6 tests)
- Error classification (5 tests)

**`test_anthropic_direct.py` (29 tests)**:
- Request building (6 tests)
- Cache control (4 tests)
- Stream parsing (3 tests)
- Retry logic (2 tests)
- Message formatting (3 tests)
- Feature support (6 tests)
- Error classification (5 tests)

**`test_factory.py` (6 tests)**:
- Provider creation for each endpoint
- Credential passthrough
- Model config passthrough
- Unsupported endpoint handling

**Grand Total: 107 tests** (37 passing, 70 to be written)

---

## What Happens Next: Phase 3

**Refactor StreamingToolExecutor to use `self.provider`**:

```python
# Old (current):
response = self.bedrock.invoke_model_with_response_stream(**api_params)
for chunk in stream_body:
    chunk_dict = json.loads(chunk_bytes)
    if chunk_dict['type'] == 'content_block_delta':
        # ... lots of parsing logic

# New (after Phase 3):
config = ProviderConfig(max_output_tokens=8192, iteration=iteration, ...)
async for event in self.provider.stream_response(messages, system, tools, config):
    if isinstance(event, TextDelta):
        # ... orchestration logic (repetition detection, etc.)
    elif isinstance(event, ToolUseEnd):
        # ... orchestration logic (validation, execution, etc.)
```

The orchestrator's 3,361 lines shrink by ~600 lines (the Bedrock-specific code moves to `BedrockProvider`).
The Anthropic direct path gains all 14 features from the gap analysis automatically.

---

**Status**: ✅ Phases 0–3 complete. Provider abstraction fully integrated.

## Phase 3 Integration (Completed)

The orchestrator (`StreamingToolExecutor.stream_with_tools`) now routes all
LLM calls through `self.provider.stream_response()`:

- **Main streaming loop** — dispatches on `isinstance(event, TextDelta)` etc.
- **Post-loop feedback** — uses `self.provider.stream_response()` with `suppress_tools=True`
- **Code block continuation** — uses `self.provider.stream_response()` with continuation config
- **Throttle backoff** — reduced tokens stored in `throttle_state['max_output_tokens_override']`
  and applied via `ProviderConfig.max_output_tokens` on the next iteration

### Bugs Found and Fixed During Phase 3

| Bug | Severity | Fix |
|-----|----------|-----|
| `original_max_tokens` / `body` undefined in throttle handler | 🔴 Crash | Use `provider_config.max_output_tokens` |
| `self.bedrock` None for non-Bedrock endpoints (feedback + continuation) | 🔴 Crash | Route through `self.provider.stream_response()` |
| `iteration_usages` list never populated | 🟠 Silent failure | Added `iteration_usages.append(iteration_usage)` |
| `self.provider` None guard missing | 🔴 Crash | Early return with error event |
| Baseline calibration crash on non-Bedrock | 🟡 Cosmetic | Guard `if self.bedrock is None` |
| Throttle token reduction dead code | 🟠 Functional | Added `throttle_state['max_output_tokens_override']` wiring |
| Frontend `removeStreamingConversation` broadcast loop | 🔴 Infinite loop | `removedStreamingIds` ref guard |

### Test Suite: 119 tests, all passing

| File | Tests |
|------|-------|
| `test_base.py` | 37 |
| `test_bedrock.py` | 41 |
| `test_anthropic_direct.py` | 34 |
| `test_factory.py` | 7 |

### Remaining `self.bedrock` Usage

Only the **baseline calibration** path (token count measurement) still uses `self.bedrock`
directly via `invoke_model` (non-streaming, sync).  This is intentional — baseline measurement
needs the raw client to get exact token counts without provider wrapper overhead.  It is
guarded with `if self.bedrock is None: skip`.
