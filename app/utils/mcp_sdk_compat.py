"""
Compatibility layer between Ziya's remote MCP client and the ``mcp`` SDK.

Ziya runs on either major line of the official Python SDK:

* **1.x** — result models use camelCase attribute names (``protocolVersion``,
  ``inputSchema``, ``isError``, ``mimeType``) and the HTTP transports take an
  ``httpx.AsyncClient``.
* **2.x** — types moved to ``mcp_types``; attributes are snake_case
  (``protocol_version``, ``input_schema``, ``is_error``, ``mime_type``) with the
  camelCase name kept only as the wire alias, and the transports take an
  ``httpx2.AsyncClient``.

Only the remote (HTTP/SSE) client path touches the SDK; the stdio client and
the builtin servers speak JSON-RPC directly and are unaffected by the version.

Reading a field under the wrong name does not raise on either line —
``getattr(result, "isError", False)`` on a 2.x result is silently ``False`` —
so every SDK object read goes through the accessors here instead of a bare
attribute.  RemoteAuthProvider plugins build their HTTP client through
:func:`http_module` so they hand the transport the client type it expects.
"""

from __future__ import annotations

import importlib
import importlib.metadata
from typing import Any, Dict, List, Optional

SUPPORTED_MAJORS = (1, 2)

_SDK_HTTP_LIBRARY = {1: "httpx", 2: "httpx2"}


def sdk_version() -> Optional[str]:
    """Installed ``mcp`` distribution version, or None when it is absent."""
    try:
        return importlib.metadata.version("mcp")
    except importlib.metadata.PackageNotFoundError:
        return None


def sdk_major() -> Optional[int]:
    """Major version of the installed ``mcp`` SDK, or None if unknown."""
    v = sdk_version()
    if not v:
        return None
    head = v.split(".", 1)[0]
    return int(head) if head.isdigit() else None


def unsupported_sdk_message() -> Optional[str]:
    """A user-facing reason the installed SDK cannot be used, else None."""
    major = sdk_major()
    if major is None:
        return "The 'mcp' package is not installed in Ziya's environment."
    if major not in SUPPORTED_MAJORS:
        return (
            f"Installed mcp {sdk_version()} is not supported; Ziya supports "
            f"mcp {' and '.join(f'{m}.x' for m in SUPPORTED_MAJORS)}."
        )
    return None


def http_library_name() -> str:
    """Name of the HTTP client module the installed SDK's transports expect."""
    return _SDK_HTTP_LIBRARY.get(sdk_major() or 1, "httpx")


def http_module():
    """The HTTP client module (``httpx`` or ``httpx2``) for the installed SDK."""
    return importlib.import_module(http_library_name())


def make_timeout(connect: float, *, read: float):
    return http_module().Timeout(connect, read=read)


def make_http_client(*, headers: Optional[Dict[str, str]] = None, timeout: Any = None):
    """A plain AsyncClient of the type the installed SDK's transports accept."""
    return http_module().AsyncClient(headers=headers or {}, timeout=timeout)


def is_compatible_client(client: Any) -> bool:
    """True when *client* is the AsyncClient type the installed SDK expects."""
    return isinstance(client, http_module().AsyncClient)


def field(obj: Any, snake: str, camel: str, default: Any = None) -> Any:
    """Read an SDK model field under its 2.x (snake) or 1.x (camel) name."""
    if obj is None:
        return default
    for name in (snake, camel):
        if hasattr(obj, name):
            return getattr(obj, name)
    return default


def protocol_version(init_result: Any) -> Optional[str]:
    return field(init_result, "protocol_version", "protocolVersion")


def tool_input_schema(tool: Any) -> Dict[str, Any]:
    schema = field(tool, "input_schema", "inputSchema")
    return schema if isinstance(schema, dict) else {}


def tool_annotations(tool: Any) -> Optional[Dict[str, Any]]:
    """Tool annotations as a dict with WIRE (camelCase) keys on both lines.

    The per-turn batch runner reads ``readOnlyHint``; a plain 2.x
    ``model_dump()`` would yield ``read_only_hint`` and silently disable it.
    """
    ann = getattr(tool, "annotations", None)
    if ann is None:
        return None
    if isinstance(ann, dict):
        return ann
    return ann.model_dump(by_alias=True, exclude_none=True)


def is_error(result: Any) -> bool:
    return bool(field(result, "is_error", "isError", False))


def content_to_dicts(items: Any) -> List[Dict[str, Any]]:
    """Convert SDK tool-result content blocks to Ziya's dict format."""
    out: List[Dict[str, Any]] = []
    for item in items or []:
        kind = getattr(item, "type", None)
        if kind == "text" or (kind is None and hasattr(item, "text")):
            out.append({"type": "text", "text": item.text})
        elif kind == "image" or (kind is None and hasattr(item, "data")):
            out.append({
                "type": "image",
                "data": item.data,
                "mimeType": field(item, "mime_type", "mimeType", "image/png"),
            })
        else:
            out.append({"type": "text", "text": str(item)})
    return out
