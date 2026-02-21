"""
Nova Web Search builtin tool.

Exposes Nova Web Grounding as a tool that the primary model (Claude)
can call whenever it needs current web information.  Functionally
equivalent to brave_web_search but powered by Amazon's internal web
index — no external MCP server or API key required.

The tool uses the Bedrock Converse API with the nova_grounding
systemTool under the hood.  Results include inline citations.
"""

from typing import Any, Optional

from pydantic import BaseModel, Field

from app.mcp.tools.base import BaseMCPTool
from app.utils.logging_utils import logger


class NovaWebSearchInput(BaseModel):
    """Input schema for nova_web_search."""

    query: str = Field(
        ...,
        description=(
            "The search query or question to look up on the web. "
            "Be specific — include relevant keywords, dates, or "
            "entity names to get the best results."
        ),
    )
    system_prompt: Optional[str] = Field(
        None,
        description=(
            "Optional system instruction to guide the search model. "
            "For example: 'Focus on official AWS documentation' or "
            "'Summarize in bullet points with dates'."
        ),
    )


class NovaWebSearchTool(BaseMCPTool):
    """
    Search the web for current information using Amazon Nova.

    Returns text grounded in real-time web results with source
    citations.  Use this when you need up-to-date information
    that may not be in your training data.
    """

    name: str = "nova_web_search"
    description: str = (
        "Search the web for current information using Amazon Nova Web Grounding. "
        "Returns text with source citations. Use when you need up-to-date "
        "information beyond your training data — news, documentation, recent "
        "events, package versions, API changes, etc. "
        "Input: a search query string. "
        "Output: grounded text with numbered source references."
    )

    # Pydantic schema for input validation
    InputSchema = NovaWebSearchInput

    async def execute(self, **kwargs) -> Any:
        """Execute a web search via Nova grounding."""
        # Pop workspace path injected by the streaming executor
        kwargs.pop("_workspace_path", None)

        query = kwargs.get("query", "")
        system_prompt = kwargs.get("system_prompt")

        if not query or not query.strip():
            return {"content": [{"type": "text", "text": "Error: query must not be empty"}]}

        logger.info(f"🌐 NovaWebSearch: Searching for: {query[:100]}")

        try:
            from app.services.grounding import get_grounding_service

            service = get_grounding_service()
            result = service.query(query.strip(), system_prompt=system_prompt)

            formatted = result.format_for_tool_result()

            logger.info(
                f"🌐 NovaWebSearch: Got {len(result.citations)} citations, "
                f"{result.latency_ms}ms, "
                f"{result.input_tokens}+{result.output_tokens} tokens"
            )

            return {"content": [{"type": "text", "text": formatted}]}

        except Exception as e:
            error_msg = f"Web search failed: {e}"
            logger.error(f"🌐 NovaWebSearch: {error_msg}")
            return {"content": [{"type": "text", "text": error_msg}]}
