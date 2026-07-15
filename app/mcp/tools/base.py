"""Base class for MCP tools."""
import json
import types
import typing
from abc import ABC, abstractmethod
from typing import Any, Dict


class BaseMCPTool(ABC):
    """Base class for all MCP tools."""
    
    @property
    @abstractmethod
    def name(self) -> str:
        """Tool name."""
        pass
    
    @property
    def is_internal(self) -> bool:
        """Whether tool output should be hidden from user (default: False)."""
        return False
    
    @abstractmethod
    async def execute(self, **kwargs) -> Any:
        """Execute the tool."""
        pass


def _annotation_accepts_str(annotation: Any) -> bool:
    """True if a plain ``str`` is a valid value for this field annotation.

    Only ``str`` itself and ``Union``/``Optional`` members are inspected —
    we deliberately do NOT descend into container type args, so
    ``Dict[str, Any]`` (whose key type is ``str``) is correctly reported as
    NOT accepting a bare string value.
    """
    if annotation is str:
        return True
    origin = typing.get_origin(annotation)
    union_types = (typing.Union, getattr(types, "UnionType", None))
    if origin in union_types:
        return any(arg is str for arg in typing.get_args(annotation))
    return False


def coerce_json_string_args(tool_instance: Any, kwargs: Dict[str, Any]) -> Dict[str, Any]:
    """Parse JSON-string values for builtin-tool params that don't accept str.

    Some MCP transports re-stringify nested object/array arguments — e.g.
    ``task_card_write.root`` (typed ``Optional[Dict]``) arrives as the JSON
    *string* ``'{"block_type": ...}'`` rather than a parsed object, and
    Pydantic will not coerce ``str`` -> ``dict`` (raising "Input should be a
    valid dictionary or instance of Block").  Builtin DIRECT tools also
    bypass MCPManager._normalize_tool_parameters / _coerce_argument_types
    entirely, so nothing upstream fixes this for them.

    This normalizes at the builtin-dispatch boundary using the tool's own
    ``InputSchema`` (a Pydantic model): a string value is JSON-parsed only
    when (a) the field's annotation does NOT accept a plain ``str`` (so
    genuine string params like ``name``/``card_id`` are never touched) and
    (b) the value looks like a JSON object/array.  Parse failures leave the
    value untouched so the model's own validation still produces a clear
    error.  Never raises.
    """
    schema = getattr(tool_instance, "InputSchema", None)
    fields = getattr(schema, "model_fields", None)
    if not isinstance(kwargs, dict) or not fields:
        return kwargs
    out: Dict[str, Any] = dict(kwargs)
    for field_name, field_info in fields.items():
        value = out.get(field_name)
        if not isinstance(value, str):
            continue
        if _annotation_accepts_str(getattr(field_info, "annotation", None)):
            continue
        stripped = value.strip()
        if stripped[:1] not in ("{", "["):
            continue
        try:
            out[field_name] = json.loads(stripped)
        except (json.JSONDecodeError, ValueError):
            pass  # leave as-is; the model's validation will report it
    return out