"""
show_document: display a file to the user without the model retyping it.

Thin wrapper; the logic (path validation, section selection, caps, the
model/user split) lives in app/utils/show_document.py.
"""

from typing import Any, Dict, Optional

from pydantic import BaseModel, Field

from app.mcp.tools.base import BaseMCPTool
from app.mcp.tools.fileio import _get_project_root


class ShowDocumentInput(BaseModel):
    path: str = Field(..., description="File to show, relative to the project root (same rules as file_read).")
    heading: Optional[str] = Field(
        None, description="Show only this section: the heading through the next heading of the same or higher level.")
    start_line: Optional[int] = Field(None, description="First line to show (1-based).")
    end_line: Optional[int] = Field(None, description="Last line to show (inclusive).")
    render: str = Field(
        "auto", description="'markdown' renders the text; 'code' shows it as a code block; 'auto' picks by extension.")


class ShowDocumentTool(BaseMCPTool):
    """Show a file to the user, rendered in the chat."""

    name: str = "show_document"
    description: str = (
        "Show a file to the user, rendered in the chat (markdown, tables, diagrams and math "
        "included). Use this whenever the user asks to see, show, open or render a file or "
        "report; never retype file content into your reply. You receive only a summary "
        "(path, lines, size, sha256, headings), not the text. Up to 512 KB per call; for a "
        "larger file pass 'heading' or a line range."
    )
    InputSchema = ShowDocumentInput

    async def execute(self, **kwargs) -> Dict[str, Any]:
        from app.utils.show_document import show
        return show(
            kwargs.get("path", ""), _get_project_root(kwargs),
            heading=kwargs.get("heading") or None,
            start_line=kwargs.get("start_line"),
            end_line=kwargs.get("end_line"),
            render=kwargs.get("render") or "auto",
        )
