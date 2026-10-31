"""
Regression tests for Google Gemini tool-schema sanitization.

Gemini's FunctionDeclaration validator is stricter than Bedrock/Anthropic/
OpenAI. Two malformed-but-tolerated-elsewhere schema shapes produced a live
400 Bad Request from the GUI Google endpoint:

    GenerateContentRequest.tools[0].functionDeclarations[86]
        .parameters.properties[priority].enum[3]: cannot be empty
    GenerateContentRequest.tools[0].functionDeclarations[90]
        .parameters.required[1]: property is not defined

Both are now handled inside app.providers.google_direct._sanitize_schema_for_gemini:
  - an enum containing an empty-string member has that member stripped
    (and, if that empties the enum, the enum key is dropped);
  - a `required` list is reconciled against the actual properties, pruning any
    entry naming a property that is not defined.

The unit tests below reproduce the exact two offending schema shapes. The live
test (gated on GOOGLE_API_KEY) sends the sanitized tools to the real Gemini API
and asserts the request is accepted — i.e. the 400 is gone end to end.
"""

import os
import pytest

from app.providers.google_direct import _sanitize_schema_for_gemini


# Schema shape behind functionDeclarations[86]: an enum whose last member is "".
# (mcp_TaskeiUpdateTask.priority declares enum: ["High","Medium","Low",""].)
_ENUM_WITH_EMPTY = {
    "type": "object",
    "properties": {
        "priority": {"type": "string", "enum": ["High", "Medium", "Low", ""]},
    },
}

# Schema shape behind functionDeclarations[90]: a required entry naming a
# property that is not present in `properties`.
_REQUIRED_ORPHAN = {
    "type": "object",
    "properties": {
        "id": {"type": "string"},
    },
    "required": ["id", "ghost"],
}


class TestEnumSanitization:
    def test_empty_enum_member_is_stripped(self):
        out = _sanitize_schema_for_gemini(_ENUM_WITH_EMPTY)
        enum = out["properties"]["priority"]["enum"]
        assert "" not in enum, "empty-string enum member must be removed for Gemini"
        assert enum == ["High", "Medium", "Low"]

    def test_enum_of_only_empty_strings_drops_enum_key(self):
        schema = {"type": "object", "properties": {
            "x": {"type": "string", "enum": ["", None]},
        }}
        out = _sanitize_schema_for_gemini(schema)
        # Nothing valid left -> enum key removed entirely rather than left empty.
        assert "enum" not in out["properties"]["x"]

    def test_nonempty_enum_is_preserved_and_stringified(self):
        schema = {"type": "integer", "enum": [1, 2, 3]}
        out = _sanitize_schema_for_gemini(schema)
        assert out["enum"] == ["1", "2", "3"]
        assert out["type"] == "string"


class TestRequiredReconciliation:
    def test_orphan_required_entry_is_pruned(self):
        out = _sanitize_schema_for_gemini(_REQUIRED_ORPHAN)
        assert out["required"] == ["id"], "required must only name defined properties"

    def test_required_emptied_to_nothing_drops_the_key(self):
        schema = {"type": "object", "properties": {}, "required": ["gone"]}
        out = _sanitize_schema_for_gemini(schema)
        assert "required" not in out

    def test_well_formed_required_is_untouched(self):
        schema = {"type": "object",
                  "properties": {"a": {"type": "string"}, "b": {"type": "string"}},
                  "required": ["a", "b"]}
        out = _sanitize_schema_for_gemini(schema)
        assert out["required"] == ["a", "b"]


# --------------------------------------------------------------------------
# Live Gemini integration test — proves the 400 is gone against the real API.
# --------------------------------------------------------------------------

_LIVE_MODEL = os.environ.get("ZIYA_TEST_GEMINI_MODEL", "gemini-3.8-flash")


@pytest.mark.integration
@pytest.mark.skipif(
    not os.environ.get("GOOGLE_API_KEY"),
    reason="GOOGLE_API_KEY not set (source ~/.env to run the live Gemini test)",
)
def test_sanitized_tools_accepted_by_live_gemini():
    """The two malformed schema shapes, once sanitized, must be accepted by the
    real Gemini generate_content endpoint (no INVALID_ARGUMENT 400)."""
    from google.genai import types, Client

    client = Client(api_key=os.environ["GOOGLE_API_KEY"])

    # Build FunctionDeclarations exactly as GoogleDirectProvider._convert_tools
    # does: sanitize the raw schema, then wrap in a FunctionDeclaration.
    declarations = [
        types.FunctionDeclaration(
            name="update_task",
            description="Update a task; priority enum includes an empty member.",
            parameters=_sanitize_schema_for_gemini(_ENUM_WITH_EMPTY),
        ),
        types.FunctionDeclaration(
            name="get_thing",
            description="Fetch a thing; required over-declares a missing prop.",
            parameters=_sanitize_schema_for_gemini(_REQUIRED_ORPHAN),
        ),
    ]
    tool = types.Tool(function_declarations=declarations)

    # A 400 from schema validation raises here; success returns a response.
    resp = client.models.generate_content(
        model=_LIVE_MODEL,
        contents="Reply with the single word: ok",
        config=types.GenerateContentConfig(tools=[tool]),
    )
    assert resp is not None


@pytest.mark.integration
@pytest.mark.skipif(
    not os.environ.get("GOOGLE_API_KEY"),
    reason="GOOGLE_API_KEY not set (source ~/.env to run the live Gemini test)",
)
def test_unsanitized_empty_enum_is_rejected_by_live_gemini():
    """Negative control: the raw (unsanitized) empty-enum schema should still be
    rejected by Gemini, proving the sanitizer is what makes the request valid.

    Tolerant of where the rejection surfaces: the SDK may reject client-side
    before the HTTP call, or the API returns a 400 — either is a rejection.
    If a future SDK/API silently accepts it, we xfail rather than fail hard,
    since that would mean the upstream bug was fixed, not that ours regressed.
    """
    from google.genai import types, Client

    client = Client(api_key=os.environ["GOOGLE_API_KEY"])
    tool = types.Tool(function_declarations=[
        types.FunctionDeclaration(
            name="update_task",
            description="raw empty-enum schema",
            parameters=_ENUM_WITH_EMPTY,  # NOT sanitized
        ),
    ])
    try:
        client.models.generate_content(
            model=_LIVE_MODEL,
            contents="Reply with the single word: ok",
            config=types.GenerateContentConfig(tools=[tool]),
        )
    except Exception:
        return  # expected: rejected somewhere in the stack
    pytest.xfail("live Gemini accepted an empty enum member; upstream validation changed")
