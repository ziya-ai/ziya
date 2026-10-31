"""
app.utils.mcp_sdk_compat against REAL SDK models of whichever mcp line is
installed.  Models are built from wire-format (camelCase) JSON, which both
lines accept, so the same assertions hold on 1.x and 2.x.

Run under 2.x as well:  PYTHONPATH=<dir with mcp 2.x> python -m pytest <this file>

The cases that matter are the ones that fail SILENTLY with bare attribute
access on 2.x: ``getattr(r, "isError", False)`` is False, ``mimeType`` falls
back to the default, and ``model_dump()`` yields ``read_only_hint``.
"""

import pytest

from app.utils import mcp_sdk_compat as compat

try:  # 2.x
    import mcp_types as T
except ImportError:  # 1.x
    import mcp.types as T


def test_installed_sdk_is_supported_and_matches_http_library():
    assert compat.unsupported_sdk_message() is None
    assert compat.sdk_major() in compat.SUPPORTED_MAJORS
    expected = "httpx2" if compat.sdk_major() == 2 else "httpx"
    assert compat.http_library_name() == expected
    client = compat.make_http_client(headers={"X": "1"},
                                     timeout=compat.make_timeout(5, read=10))
    assert compat.is_compatible_client(client)
    assert type(client).__module__.split(".")[0] == expected


def test_foreign_client_type_is_rejected():
    assert not compat.is_compatible_client(object())


def test_initialize_result_protocol_version():
    r = T.InitializeResult.model_validate({
        "protocolVersion": "2025-06-18",
        "capabilities": {"tools": {}},
        "serverInfo": {"name": "s", "version": "1"},
    })
    assert compat.protocol_version(r) == "2025-06-18"


def test_tool_schema_and_annotations_keep_wire_keys():
    tool = T.Tool.model_validate({
        "name": "echo",
        "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}},
        "annotations": {"readOnlyHint": True},
    })
    assert compat.tool_input_schema(tool)["properties"] == {"text": {"type": "string"}}
    # The batch runner reads readOnlyHint; read_only_hint would disable it.
    assert compat.tool_annotations(tool) == {"readOnlyHint": True}


def test_tool_without_annotations():
    tool = T.Tool.model_validate({"name": "t", "inputSchema": {"type": "object"}})
    assert compat.tool_annotations(tool) is None


def test_call_tool_result_error_flag_and_content():
    r = T.CallToolResult.model_validate({
        "isError": True,
        "content": [
            {"type": "text", "text": "bad"},
            {"type": "image", "data": "R0lGOD", "mimeType": "image/gif"},
        ],
    })
    assert compat.is_error(r) is True
    assert compat.content_to_dicts(r.content) == [
        {"type": "text", "text": "bad"},
        {"type": "image", "data": "R0lGOD", "mimeType": "image/gif"},
    ]


def test_successful_result_is_not_error():
    r = T.CallToolResult.model_validate({"content": [{"type": "text", "text": "ok"}]})
    assert compat.is_error(r) is False
    assert compat.content_to_dicts(r.content) == [{"type": "text", "text": "ok"}]


def test_audio_is_not_mislabelled_as_image():
    r = T.CallToolResult.model_validate({
        "content": [{"type": "audio", "data": "AAAA", "mimeType": "audio/wav"}],
    })
    [item] = compat.content_to_dicts(r.content)
    assert item["type"] == "text"


def test_unsupported_major_is_reported(monkeypatch):
    monkeypatch.setattr(compat, "sdk_version", lambda: "3.0.0")
    assert "not supported" in compat.unsupported_sdk_message()
    monkeypatch.setattr(compat, "sdk_version", lambda: None)
    assert "not installed" in compat.unsupported_sdk_message()
