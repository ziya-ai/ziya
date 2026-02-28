"""
Direct Anthropic API wrapper using the native anthropic Python SDK.
Supports streaming, native tool calling, and multi-modal content.
"""

import json
import os
from typing import List, Dict, Optional, AsyncIterator, Any

from langchain_core.messages import BaseMessage, SystemMessage, HumanMessage, AIMessage, ToolMessage
from langchain_core.tools import BaseTool
from app.utils.logging_utils import logger
from app.mcp.manager import get_mcp_manager


class DirectAnthropicModel:
    """Direct Anthropic model wrapper with streaming and native tool calling."""

    def __init__(
        self,
        model_name: str,
        temperature: float = 0.3,
        max_output_tokens: int = 16384,
        api_key: Optional[str] = None,
    ):
        self.model_name = model_name
        self.temperature = temperature
        self.max_output_tokens = max_output_tokens
        self.mcp_manager = get_mcp_manager()

        try:
            import anthropic
        except ImportError:
            raise ImportError(
                "The 'anthropic' package is required for the Anthropic endpoint. "
                "Install it with: pip install anthropic"
            )

        resolved_key = api_key or os.getenv("ANTHROPIC_API_KEY")
        if not resolved_key:
            raise ValueError("ANTHROPIC_API_KEY not found in environment")

        self.client = anthropic.AsyncAnthropic(api_key=resolved_key)
        logger.info(
            f"DirectAnthropicModel initialized: model={model_name}, "
            f"temp={temperature}, max_output_tokens={max_output_tokens}"
        )

    def bind(self, **kwargs):
        """Compatibility — ignore LangChain bind kwargs for direct mode."""
        return self

    def _convert_tools(self, tools: List[BaseTool]) -> List[Dict]:
        """Convert LangChain tools to Anthropic tool format."""
        if not tools:
            return []
        anthropic_tools = []
        for tool in tools:
            try:
                schema = None
                if hasattr(tool, "metadata") and isinstance(tool.metadata, dict):
                    schema = tool.metadata.get("input_schema")
                if schema is None and tool.args_schema:
                    try:
                        schema = tool.args_schema.schema()
                    except Exception:
                        schema = None
                if schema is None:
                    schema = {"type": "object", "properties": {}}

                func_name = tool.name if tool.name.startswith("mcp_") else f"mcp_{tool.name}"
                anthropic_tools.append({
                    "name": func_name,
                    "description": tool.description or "",
                    "input_schema": schema,
                })
            except Exception as e:
                logger.warning(f"Could not convert tool '{tool.name}': {e}")
        return anthropic_tools

    def _convert_messages(self, messages: List[BaseMessage]):
        """Convert LangChain messages to Anthropic format. Returns (system, messages)."""
        system_content = None
        anthropic_messages = []

        for message in messages:
            content = message.content
            if isinstance(message, SystemMessage):
                # Use list format with cache_control so the system prompt is cached
                text = content if isinstance(content, str) else json.dumps(content)
                system_content = [{
                    "type": "text",
                    "text": text,
                    "cache_control": {"type": "ephemeral"}
                }]
                continue
            elif isinstance(message, HumanMessage):
                role = "user"
            elif isinstance(message, AIMessage):
                role = "assistant"
            elif isinstance(message, ToolMessage):
                anthropic_messages.append({
                    "role": "user",
                    "content": [{
                        "type": "tool_result",
                        "tool_use_id": getattr(message, "tool_call_id", message.name),
                        "content": content if isinstance(content, str) else json.dumps(content),
                    }]
                })
                continue
            else:
                continue

            if isinstance(content, list):
                anthropic_messages.append({"role": role, "content": content})
            else:
                anthropic_messages.append({"role": role, "content": content})

        # Also cache the last user message before the current turn (conversation history)
        # This means on multi-turn conversations, prior turns are cached too
        if len(anthropic_messages) >= 3:
            boundary = anthropic_messages[-2]  # second-to-last = last assistant or prior user
            bc = boundary.get("content")
            if isinstance(bc, str):
                boundary["content"] = [{"type": "text", "text": bc, "cache_control": {"type": "ephemeral"}}]
            elif isinstance(bc, list) and bc:
                last_block = bc[-1]
                if isinstance(last_block, dict) and "cache_control" not in last_block:
                    last_block["cache_control"] = {"type": "ephemeral"}

        return system_content, anthropic_messages

    async def astream(self, messages: List[BaseMessage], **kwargs) -> AsyncIterator[Dict]:
        """Stream responses with native tool calling."""
        from app.context import get_project_root
        self._project_root = get_project_root()

        tools = kwargs.get("tools", [])
        anthropic_tools = self._convert_tools(tools)
        system_content, history = self._convert_messages(messages)

        logger.info(
            f"Anthropic astream: received {len(tools)} tools, "
            f"converted to {len(anthropic_tools)} Anthropic tools"
        )
        if anthropic_tools:
            logger.info(f"Anthropic tools: {[t['name'] for t in anthropic_tools[:10]]}")

        max_rounds = 25

        for _round in range(max_rounds):
            request_kwargs = {
                "model": self.model_name,
                "max_tokens": self.max_output_tokens,
                "messages": history,
            }
            if system_content:
                request_kwargs["system"] = system_content
            if self.temperature is not None:
                request_kwargs["temperature"] = self.temperature
            if anthropic_tools:
                request_kwargs["tools"] = anthropic_tools

            logger.info(
                f"Anthropic stream request: model={self.model_name}, "
                f"msgs={len(history)}, max_tokens={self.max_output_tokens}"
            )

            try:
                collected_text = ""
                tool_uses = []

                async with self.client.messages.stream(**request_kwargs) as stream:
                    async for event in stream:
                        if event.type == "message_start" and hasattr(event, "message"):
                            usage = getattr(event.message, "usage", None)
                            if usage:
                                yield {
                                    "type": "usage",
                                    "input_tokens": getattr(usage, "input_tokens", 0),
                                    "output_tokens": 0,
                                    "cache_read_tokens": getattr(usage, "cache_read_input_tokens", 0),
                                    "cache_write_tokens": getattr(usage, "cache_creation_input_tokens", 0),
                                }
                        elif event.type == "message_delta":
                            usage = getattr(event, "usage", None)
                            if usage:
                                yield {
                                    "type": "usage",
                                    "output_tokens": getattr(usage, "output_tokens", 0),
                                }
                        elif event.type == "content_block_delta":
                            if event.delta.type == "text_delta":
                                collected_text += event.delta.text
                                yield {"type": "text", "content": event.delta.text}
                            elif event.delta.type == "input_json_delta":
                                # Accumulate tool input JSON
                                if tool_uses:
                                    tool_uses[-1]["_partial_json"] += event.delta.partial_json
                        elif event.type == "content_block_start":
                            if event.content_block.type == "tool_use":
                                tool_uses.append({
                                    "id": event.content_block.id,
                                    "name": event.content_block.name,
                                    "_partial_json": "",
                                })
                        elif event.type == "message_stop":
                            pass

                # If no tool calls, we're done
                if not tool_uses:
                    yield {"type": "stream_end"}
                    return

                # Build assistant message with text + tool_use blocks
                assistant_content = []
                if collected_text:
                    assistant_content.append({"type": "text", "text": collected_text})
                for tu in tool_uses:
                    try:
                        args = json.loads(tu["_partial_json"]) if tu["_partial_json"] else {}
                    except json.JSONDecodeError:
                        args = {}
                    assistant_content.append({
                        "type": "tool_use",
                        "id": tu["id"],
                        "name": tu["name"],
                        "input": args,
                    })
                history.append({"role": "assistant", "content": assistant_content})

                # Execute tools
                tool_results = []
                for tu in tool_uses:
                    try:
                        args = json.loads(tu["_partial_json"]) if tu["_partial_json"] else {}
                    except json.JSONDecodeError:
                        args = {}

                    if self._project_root:
                        args["_workspace_path"] = self._project_root

                    tool_name = tu["name"]
                    display_name = tool_name.replace("mcp_", "", 1) if tool_name.startswith("mcp_") else tool_name

                    yield {"type": "tool_start", "tool_name": display_name, "tool_id": tu["id"], "input": args}

                    try:
                        result = await self.mcp_manager.call_tool(display_name, args)
                        result_text = str(result) if result else "Tool executed successfully"
                    except Exception as e:
                        result_text = f"Error: {e}"

                    yield {"type": "tool_display", "tool_name": display_name, "tool_id": tu["id"], "result": result_text}

                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": tu["id"],
                        "content": result_text,
                    })

                history.append({"role": "user", "content": tool_results})

            except Exception as e:
                error_msg = f"Anthropic API Error ({type(e).__name__}): {e}"
                logger.error(error_msg, exc_info=True)
                yield {"type": "error", "content": error_msg}
                return

        yield {"type": "stream_end"}
