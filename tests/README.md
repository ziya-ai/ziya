# LLM Interaction Regression Test Suite

This directory contains a comprehensive test suite for LLM interactions in Ziya, focusing on edge cases, error handling, and model-specific behaviors.

## Test Suite Structure

The test suite is organized into several files:

1. **test_ziya_string.py**: Tests for the ZiyaString class that preserves attributes in strings.
2. **test_agent_string_handling.py**: Tests for string handling in the agent.
3. **test_nova_wrapper_syntax.py**: Basic syntax check for the Nova wrapper.
4. **test_nova_wrapper.py**: Comprehensive tests for the Nova wrapper.
5. **test_ziya_string_integration.py**: Integration tests for ZiyaString with model wrappers.
6. **test_llm_interaction_regression.py**: Core regression tests for string handling, attribute preservation, and error handling.
7. **test_llm_interaction_regression_async.py**: Tests for async streaming, error handling, and combining streams.
8. **test_llm_interaction_edge_cases.py**: Tests for unusual inputs, error conditions, and boundary cases.
9. **test_llm_interaction_model_specific.py**: Tests for specific LLM models and their unique response formats.

## Key Areas Tested

### String Handling and Attribute Preservation

- Conversion between string and object types
- Preservation of attributes during string conversion
- Handling of ZiyaString objects
- Wrapping strings in AIMessageChunk objects

### Error Handling

- Handling of error responses from LLMs
- Creation of error Generation objects
- Parsing error messages
- Retry mechanisms

### Special Cases

- Invisible Unicode characters
- Escape sequences
- Empty and whitespace-only responses
- Large responses
- Malformed JSON

### Async Streaming

- Handling string chunks in streams
- Combining and interleaving streams
- Error handling in streams
- Retry mechanisms for streams

### Model-Specific Behaviors

- Claude response parsing and message formatting
- Nova response parsing and message formatting
- Titan response parsing and message formatting
- Mistral response parsing and message formatting

## Running the Tests

To run the entire test suite:

```bash
python -m pytest tests/ -v
```

To run a specific test file:

```bash
python -m pytest tests/test_ziya_string.py -v
```

To run a specific test:

```bash
python -m pytest tests/test_ziya_string.py::test_ziya_string_creation -v
```

## Adding New Tests

When adding new tests, follow these guidelines:

1. Place the test in the appropriate file based on its category.
2. Use descriptive test names that clearly indicate what is being tested.
3. Include assertions that verify the expected behavior.
4. Mock external dependencies to avoid actual API calls.
5. Handle async tests properly with pytest.mark.asyncio.

## Common Issues and Solutions

### 'str' object has no attribute 'id'

This error occurs when a Generation object is converted to a string and then code tries to access the 'id' attribute on that string. To fix this:

1. Use ZiyaString to create strings that preserve attributes.
2. Check the type of objects before accessing attributes.
3. Wrap string chunks in AIMessageChunk objects with proper attributes.
4. Use object.__setattr__ to add attributes to objects that don't support them directly.

### Async Test Failures

For async tests, make sure to:

1. Use pytest.mark.asyncio decorator.
2. Properly mock async methods with AsyncMock.
3. Use await for all async calls.
4. Handle StopAsyncIteration properly.

### Model-Specific Issues

Different models have different response formats. Make sure to:

1. Use the appropriate wrapper for each model.
2. Parse responses according to the model's format.
3. Format messages according to the model's requirements.
4. Handle model-specific error responses.
