"""
Regression tests for Google GenAI FunctionResponse wrapping.

The Google GenAI SDK expects each element in a message's `parts` list to be a
`types.Part` whose `function_response` oneof is set. Passing a bare
`types.FunctionResponse` object caused a 400 Bad Request with:

    GenerateContentRequest.contents[N].parts[0].data:
    required oneof field 'data' must have one initialized field

These tests verify that every site in google_direct.py that appends a tool
result to a `parts` list wraps the FunctionResponse in a Part.
"""

import inspect
import re
import pytest

from app.agents.wrappers import google_direct


def _source(func_or_module) -> str:
    return inspect.getsource(func_or_module)


class TestFunctionResponseWrapping:
    def test_module_never_appends_bare_function_response(self):
        """No line should construct a bare types.FunctionResponse without
        being wrapped in types.Part(function_response=...)."""
        src = _source(google_direct)

        # Find every occurrence of types.FunctionResponse(
        matches = list(re.finditer(r"types\.FunctionResponse\(", src))
        assert matches, "expected at least one FunctionResponse construction in google_direct"

        for m in matches:
            # Walk backwards to the nearest non-whitespace token on prior lines;
            # it must be `function_response=` (the kwarg inside types.Part(...)).
            prefix = src[max(0, m.start() - 80):m.start()]
            assert "function_response=" in prefix or "types.Part(" in prefix, (
                f"Bare types.FunctionResponse(...) at offset {m.start()}; "
                f"must be wrapped in types.Part(function_response=...). "
                f"Prefix: {prefix!r}"
            )

    def test_part_wrapper_accepts_function_response(self):
        """Smoke test that the wrapping pattern produces a valid Part with the
        function_response oneof initialized (i.e. what the Google API expects)."""
        from google.genai import types

        part = types.Part(
            function_response=types.FunctionResponse(
                name="ast_get_tree",
                response={"content": "ok"},
            )
        )
        assert part.function_response is not None
        assert part.function_response.name == "ast_get_tree"
        # None of the other oneof fields should leak set
        assert part.function_call is None
        assert part.text is None
