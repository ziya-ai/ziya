"""Tests for JSON-string argument coercion at the builtin-tool dispatch boundary.

Covers the interface bug where an MCP transport re-stringifies a nested
object/array argument (e.g. task_card_write.root arrives as a JSON string),
which builtin DIRECT tools could not recover from because they bypass the
MCPManager normalize/coerce path.  See app/mcp/tools/base.coerce_json_string_args
and the Layer-3 anyOf fix in MCPManager._coerce_argument_types.
"""
import json
from typing import Any, Dict, List, Optional

import pytest
from pydantic import BaseModel, Field

from app.mcp.tools.base import (
    BaseMCPTool,
    coerce_json_string_args,
    _annotation_accepts_str,
)


# ── _annotation_accepts_str ──────────────────────────────────────────

class TestAnnotationAcceptsStr:
    def test_plain_str_accepts(self):
        assert _annotation_accepts_str(str) is True

    def test_optional_str_accepts(self):
        assert _annotation_accepts_str(Optional[str]) is True

    def test_pep604_str_none_accepts(self):
        assert _annotation_accepts_str(str | None) is True

    def test_optional_dict_does_not_accept(self):
        # The key edge case: Dict[str, Any]'s key type is str, but a bare
        # string is NOT a valid value — must return False so it gets coerced.
        assert _annotation_accepts_str(Optional[Dict[str, Any]]) is False

    def test_required_dict_does_not_accept(self):
        assert _annotation_accepts_str(Dict[str, Any]) is False

    def test_optional_list_does_not_accept(self):
        assert _annotation_accepts_str(Optional[List[str]]) is False

    def test_pep604_dict_none_does_not_accept(self):
        assert _annotation_accepts_str(dict | None) is False


# ── coerce_json_string_args ──────────────────────────────────────────

class _Schema(BaseModel):
    card_id: str = Field(...)
    root: Optional[Dict[str, Any]] = Field(None)
    tags: Optional[List[str]] = Field(None)
    name: Optional[str] = Field(None)


class _Tool(BaseMCPTool):
    name = "fake_tool"
    InputSchema = _Schema

    async def execute(self, **kwargs):  # pragma: no cover - not called here
        return {"success": True}


class TestCoerceJsonStringArgs:
    def setup_method(self):
        self.tool = _Tool()

    def test_stringified_object_is_parsed(self):
        out = coerce_json_string_args(
            self.tool, {"card_id": "abc", "root": '{"block_type": "group", "body": []}'}
        )
        assert out["root"] == {"block_type": "group", "body": []}
        assert isinstance(out["root"], dict)

    def test_stringified_array_is_parsed(self):
        out = coerce_json_string_args(self.tool, {"card_id": "abc", "tags": '["a", "b"]'})
        assert out["tags"] == ["a", "b"]

    def test_real_dict_passes_through_untouched(self):
        payload = {"block_type": "task"}
        out = coerce_json_string_args(self.tool, {"card_id": "abc", "root": payload})
        assert out["root"] is payload

    def test_str_field_never_coerced_even_if_jsonish(self):
        # name accepts str; a value that happens to look like JSON must be left alone.
        out = coerce_json_string_args(self.tool, {"card_id": "abc", "name": '["literal"]'})
        assert out["name"] == '["literal"]'

    def test_required_str_id_never_coerced(self):
        out = coerce_json_string_args(self.tool, {"card_id": "abc"})
        assert out["card_id"] == "abc"

    def test_invalid_json_left_untouched(self):
        # A malformed object string is left as-is so the model's own
        # validation produces the clear error (fail-safe, never raises).
        out = coerce_json_string_args(self.tool, {"card_id": "abc", "root": '{not json}'})
        assert out["root"] == '{not json}'

    def test_non_jsonish_string_left_untouched(self):
        out = coerce_json_string_args(self.tool, {"card_id": "abc", "root": "plain text"})
        assert out["root"] == "plain text"

    def test_missing_inputschema_is_noop(self):
        class _NoSchema(BaseMCPTool):
            name = "no_schema"
            async def execute(self, **kwargs):  # pragma: no cover
                return {}
        args = {"root": '{"a": 1}'}
        out = coerce_json_string_args(_NoSchema(), args)
        assert out == args  # unchanged, no crash

    def test_does_not_mutate_input(self):
        original = {"card_id": "abc", "root": '{"x": 1}'}
        coerce_json_string_args(self.tool, original)
        assert original["root"] == '{"x": 1}'  # caller's dict untouched


# ── task_card_write round-trip (the reported bug) ────────────────────

class TestTaskCardWriteRoundTrip:
    def test_stringified_root_builds_valid_update(self):
        from app.mcp.tools.task_card_tools import TaskCardWriteInput
        from app.models.task_card import TaskCardUpdate

        tool = type("T", (), {"InputSchema": TaskCardWriteInput})()
        raw = {
            "card_id": "f6565433",
            "root": json.dumps({"block_type": "task", "name": "X", "instructions": "do it"}),
        }
        coerced = coerce_json_string_args(tool, raw)
        assert isinstance(coerced["root"], dict)
        # The previously-failing construction now succeeds.
        upd = TaskCardUpdate(root=coerced["root"])
        assert upd.root is not None

    def test_stringified_root_without_coercion_would_fail(self):
        # Negative control: prove the raw string still breaks TaskCardUpdate,
        # i.e. the coercion is load-bearing (guards against a silent no-op).
        from app.models.task_card import TaskCardUpdate
        import pydantic
        with pytest.raises(pydantic.ValidationError):
            TaskCardUpdate(root='{"block_type": "task"}')


# ── Layer 3: MCPManager._coerce_argument_types anyOf recovery ────────

class TestAnyOfCoercion:
    def _mgr(self):
        from app.mcp.manager import MCPManager
        m = MCPManager.__new__(MCPManager)  # bypass __init__ (no I/O)
        m.clients = {}
        return m

    def _schema_lookup(self, mgr, schema):
        # Inject a fake connected client exposing one tool with `schema`.
        class _Tool:
            name = "t"
            inputSchema = schema
        class _Client:
            is_connected = True
            tools = [_Tool()]
        mgr.clients = {"s": _Client()}

    def test_anyof_object_coerced(self):
        mgr = self._mgr()
        self._schema_lookup(mgr, {
            "type": "object",
            "properties": {"root": {"anyOf": [{"type": "object"}, {"type": "null"}]}},
        })
        out = mgr._coerce_argument_types("t", {"root": '{"a": 1}'})
        assert out["root"] == {"a": 1}

    def test_anyof_array_coerced(self):
        mgr = self._mgr()
        self._schema_lookup(mgr, {
            "type": "object",
            "properties": {"tags": {"anyOf": [{"type": "array"}, {"type": "null"}]}},
        })
        out = mgr._coerce_argument_types("t", {"tags": '["x"]'})
        assert out["tags"] == ["x"]

    def test_ambiguous_anyof_left_untouched(self):
        # Mixed member types (object|array) -> can't safely pick; leave as str.
        mgr = self._mgr()
        self._schema_lookup(mgr, {
            "type": "object",
            "properties": {"x": {"anyOf": [{"type": "object"}, {"type": "array"}]}},
        })
        out = mgr._coerce_argument_types("t", {"x": '{"a": 1}'})
        assert out["x"] == '{"a": 1}'
