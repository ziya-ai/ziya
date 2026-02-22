"""
Direct OpenAI API wrapper that uses the native openai Python SDK
to support proper conversation history and native tool calling.

Mirrors the pattern established by google_direct.py for Gemini.
"""

import json
import asyncio
import re
import os
from typing import List, Dict, Optional, AsyncIterator, Any, Tuple

from langchain_core.messages import BaseMessage, SystemMessage, HumanMessage, AIMessage, ToolMessage
from langchain_core.tools import BaseTool
from app.utils.logging_utils import logger
from app.mcp.manager import get_mcp_manager


class DirectOpenAIModel:
    """
    Direct OpenAI model wrapper that uses the native openai SDK
    to support proper conversation history and native tool calling.
    """

    def __init__(
        self,
        model_name: str,
        temperature: float = 0.3,
        max_output_tokens: int = 16384,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
    ):
        self.model_name = model_name
        self.temperature = temperature
        self.max_output_tokens = max_output_tokens
        self.mcp_manager = get_mcp_manager()

        try:
            from openai import AsyncOpenAI
        except ImportError:
            raise ImportError(
                "The 'openai' package is required for the OpenAI endpoint. "
                "Install it with: pip install openai"
            )

        resolved_key = api_key or os.getenv("OPENAI_API_KEY")
        resolved_base = base_url or os.getenv("OPENAI_BASE_URL")

        client_kwargs: Dict[str, Any] = {}
        if resolved_key:
            client_kwargs["api_key"] = resolved_key
        if resolved_base:
            client_kwargs["base_url"] = resolved_base

        self.client = AsyncOpenAI(**client_kwargs)
        logger.info(
            f"DirectOpenAIModel initialized: model={model_name}, "
            f"temp={temperature}, max_output_tokens={max_output_tokens}, "
            f"base_url={resolved_base or 'default'}"
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _extract_text_from_mcp_result(self, result: Any) -> str:
        """Extracts the text content from a structured MCP tool result."""
        if not isinstance(result, dict) or "content" not in result:
            return str(result)
        content = result["content"]
        if isinstance(content, list) and content:
            first = content[0]
            if isinstance(first, dict) and "text" in first:
                return first["text"]
        return str(result)

    def _convert_langchain_tools_to_openai(self, tools: List[BaseTool]) -> List[Dict]:
        """Convert LangChain BaseTool list to OpenAI function-calling format."""
        if not tools:
            return []
        openai_tools = []
        for tool in tools:
            try:
                # SecureMCPTool stores its schema in metadata["input_schema"].
                # Fall back to args_schema for other LangChain tool types.
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

                # System prompt instructs the model to call tools with the mcp_ prefix.
                # Register under that same name so OpenAI accepts the function call.
                func_name = tool.name if tool.name.startswith("mcp_") else f"mcp_{tool.name}"

                openai_tools.append({
                    "type": "function",
                    "function": {
                        "name": func_name,
                        "description": tool.description or "",
                        "parameters": schema,
                    },
                })
            except Exception as e:
                logger.warning(f"Could not convert tool '{tool.name}': {e}")
        return openai_tools

    def _convert_messages_to_openai_format(
        self, messages: List[BaseMessage]
    ) -> List[Dict[str, Any]]:
        """Convert LangChain messages to the OpenAI chat-completion format."""
        openai_messages: List[Dict[str, Any]] = []
        for message in messages:
            content = message.content
            # Convert multi-modal content blocks from Bedrock format to OpenAI format
            if isinstance(content, list):
                converted = []
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "image":
                        # Bedrock: {"type":"image","source":{"type":"base64","media_type":"...","data":"..."}}
                        src = block.get("source", {})
                        media = src.get("media_type", "image/png")
                        data = src.get("data", "")
                        converted.append({
                            "type": "image_url",
                            "image_url": {"url": f"data:{media};base64,{data}"}
                        })
                    elif isinstance(block, dict) and block.get("type") == "text":
                        converted.append(block)
                    else:
                        converted.append(block)
                content = converted

            if isinstance(message, SystemMessage):
                openai_messages.append({"role": "system", "content": content})
            elif isinstance(message, HumanMessage):
                openai_messages.append({"role": "user", "content": content})
            elif isinstance(message, AIMessage):
                openai_messages.append({"role": "assistant", "content": content})
            elif isinstance(message, ToolMessage):
                openai_messages.append({
                    "role": "tool",
                    "tool_call_id": getattr(message, "tool_call_id", message.name),
                    "content": content,
                })
        return openai_messages

    # ------------------------------------------------------------------
    # Streaming
    # ------------------------------------------------------------------

    async def astream(
        self, messages: List[BaseMessage], **kwargs
    ) -> AsyncIterator[Dict]:
        """
        Stream responses from the OpenAI API, handling native tool calls.
        Yields dicts with keys: type, content, tool_name, input, result, error.
        """
        tools = kwargs.get("tools", [])
        openai_tools = self._convert_langchain_tools_to_openai(tools)
        logger.info(f"OpenAI astream: received {len(tools)} LangChain tools, converted to {len(openai_tools)} OpenAI tools")
        if openai_tools:
            logger.info(f"OpenAI tools: {[t['function']['name'] for t in openai_tools[:10]]}")
        history = self._convert_messages_to_openai_format(messages)

        max_rounds = 25  # guard against infinite tool loops

        for _round in range(max_rounds):
            # Estimate input tokens and clamp max_tokens to stay within limits.
            # OpenAI orgs often have low TPM quotas; input+output must fit.
            estimated_input = sum(
                len(json.dumps(m).encode()) // 4 for m in history  # ~4 bytes/token rough estimate
            )
            effective_max = self.max_output_tokens
            # If we can detect a hard ceiling (e.g. from a previous 429), respect it
            tpm_limit = getattr(self, '_tpm_limit', None)
            if tpm_limit and estimated_input + effective_max > tpm_limit:
                effective_max = max(1024, tpm_limit - estimated_input - 512)  # 512 token safety margin
                logger.info(f"OpenAI: Clamped max_tokens {self.max_output_tokens} → {effective_max} (est input={estimated_input}, TPM={tpm_limit})")

            request_kwargs: Dict[str, Any] = {
                "model": self.model_name,
                "messages": history,
                "temperature": self.temperature,
                "max_tokens": effective_max,
                "stream": True,
            }
            if openai_tools:
                request_kwargs["tools"] = openai_tools
                request_kwargs["tool_choice"] = "auto"

            logger.info(f"OpenAI stream request: model={self.model_name}, msgs={len(history)}, max_tokens={effective_max}, est_input={estimated_input}")

            try:
                stream = await self.client.chat.completions.create(**request_kwargs)
            except Exception as e:
                error_str = str(e)
                # Parse TPM limit from 429 error and retry once with clamped tokens
                if 'rate_limit_exceeded' in error_str and 'Limit' in error_str and not getattr(self, '_retried_tpm', False):
                    import re as _re
                    limit_match = _re.search(r'Limit\s+(\d+)', error_str)
                    requested_match = _re.search(r'Requested\s+(\d+)', error_str)
                    if limit_match:
                        self._tpm_limit = int(limit_match.group(1))
                        clamped = max(1024, self._tpm_limit - estimated_input - 512)
                        logger.warning(f"OpenAI 429: TPM limit={self._tpm_limit}, retrying with max_tokens={clamped}")
                        request_kwargs["max_tokens"] = clamped
                        self._retried_tpm = True
                        try:
                            stream = await self.client.chat.completions.create(**request_kwargs)
                        except Exception as retry_e:
                            self._retried_tpm = False
                            yield {"type": "error", "content": f"OpenAI API Error after retry: {retry_e}"}
                            return
                        self._retried_tpm = False
                    else:
                        yield {"type": "error", "content": f"OpenAI rate limit exceeded: {e}"}
                        return
                else:
                    error_msg = f"OpenAI API Error ({type(e).__name__}): {e}"
                    logger.error(error_msg, exc_info=True)
                    yield {"type": "error", "content": error_msg}
                    return

            # Accumulate streamed deltas
            collected_text = ""
            tool_calls_by_index: Dict[int, Dict[str, Any]] = {}
            finish_reason = None

            async for chunk in stream:
                delta = chunk.choices[0].delta if chunk.choices else None
                if delta is None:
                    continue

                finish_reason = chunk.choices[0].finish_reason

                # Text content
                if delta.content:
                    collected_text += delta.content
                    yield {"type": "text", "content": delta.content}

                # Tool call deltas
                if delta.tool_calls:
                    for tc_delta in delta.tool_calls:
                        idx = tc_delta.index
                        if idx not in tool_calls_by_index:
                            tool_calls_by_index[idx] = {
                                "id": tc_delta.id or "",
                                "name": "",
                                "arguments": "",
                            }
                        entry = tool_calls_by_index[idx]
                        if tc_delta.id:
                            entry["id"] = tc_delta.id
                        if tc_delta.function:
                            if tc_delta.function.name:
                                entry["name"] += tc_delta.function.name
                            if tc_delta.function.arguments:
                                entry["arguments"] += tc_delta.function.arguments

            logger.info(
                f"Stream ended. Tool calls: {len(tool_calls_by_index)}, "
                f"finish_reason: {finish_reason}"
            )

            if not tool_calls_by_index:
                # No tool calls — we're done
                return

            # Build the assistant message with tool_calls for the history
            assistant_tool_calls = []
            for idx in sorted(tool_calls_by_index):
                tc = tool_calls_by_index[idx]
                assistant_tool_calls.append({
                    "id": tc["id"],
                    "type": "function",
                    "function": {"name": tc["name"], "arguments": tc["arguments"]},
                })

            history.append({
                "role": "assistant",
                "content": collected_text or None,
                "tool_calls": assistant_tool_calls,
            })

            # Execute each tool call
            for tc_info in assistant_tool_calls:
                tool_name = tc_info["function"]["name"]
                raw_args = tc_info["function"]["arguments"]
                call_id = tc_info["id"]

                # Strip mcp_ prefix for internal lookup
                internal_name = tool_name[4:] if tool_name.startswith("mcp_") else tool_name

                try:
                    tool_args = json.loads(raw_args) if raw_args else {}
                except json.JSONDecodeError:
                    tool_args = {}

                yield {"type": "tool_start", "tool_name": internal_name, "input": tool_args}

                try:
                    tool_result_obj = await self.mcp_manager.call_tool(internal_name, tool_args)

                    # Sign and verify (same as Bedrock/Google paths)
                    try:
                        from app.mcp.signing import sign_tool_result, verify_tool_result, strip_signature_metadata
                        tool_result_obj = sign_tool_result(internal_name, tool_args, tool_result_obj)
                        is_valid, err_msg = verify_tool_result(tool_result_obj, internal_name, tool_args)
                        if not is_valid:
                            logger.error(f"Tool verification failed for {internal_name}: {err_msg}")
                            yield {"type": "error", "content": f"Tool verification failed: {err_msg}"}
                            history.append({"role": "tool", "tool_call_id": call_id, "content": f"Verification failed: {err_msg}"})
                            continue
                        tool_result_obj = strip_signature_metadata(tool_result_obj)
                    except ImportError:
                        logger.warning("Tool signing module not available, proceeding without verification")

                    tool_result_str = self._extract_text_from_mcp_result(tool_result_obj)
                    yield {"type": "tool_display", "tool_name": internal_name, "result": tool_result_str}
                    history.append({"role": "tool", "tool_call_id": call_id, "content": tool_result_str})

                except Exception as e:
                    error_msg = f"Error executing tool {internal_name}: {e}"
                    logger.error(error_msg)
                    yield {"type": "error", "content": error_msg}
                    history.append({"role": "tool", "tool_call_id": call_id, "content": error_msg})

            # Loop back to let the model respond to tool results

        logger.warning("OpenAI tool loop hit max rounds, stopping")

    # ------------------------------------------------------------------
    # Non-streaming & compatibility
    # ------------------------------------------------------------------

    async def ainvoke(self, messages: List[BaseMessage], **kwargs) -> Dict[str, Any]:
        """Non-streaming invocation. Collects the full response."""
        content = ""
        async for chunk in self.astream(messages, **kwargs):
            if chunk.get("type") == "text":
                content += chunk.get("content", "")
        return {"content": content}

    def invoke(self, messages: List[BaseMessage], **kwargs) -> Dict[str, Any]:
        """Synchronous invocation."""
        return asyncio.run(self.ainvoke(messages, **kwargs))

    def bind(self, **kwargs):
        """Compatibility — ignore stop sequences for OpenAI direct mode."""
        return self
