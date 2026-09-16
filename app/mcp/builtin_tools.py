"""
Registry for builtin MCP tools that run directly without external servers.

This module provides a centralized registry for optional builtin tools
that can be enabled/disabled by users without requiring external MCP servers.
"""

import os
from typing import Dict, List, Type, Optional
from app.utils.logging_utils import logger
from app.mcp.tools.base import BaseMCPTool


# Registry of available builtin tool categories
BUILTIN_TOOL_CATEGORIES: Dict[str, Dict[str, any]] = {
    "pcap_analysis": {
        "name": "PCAP Analysis",
        "description": "Network packet capture analysis and protocol correlation tools",
        "enabled_by_default": False,
        "requires_dependencies": ["scapy", "dpkt"],
        "tools": [],  # Will be populated dynamically
        "hidden": True  # Hidden for release - not ready yet
    },
    "architecture_shapes": {
        "name": "Architecture Shapes",
        "description": "Architecture diagram shape catalog for DrawIO, Mermaid, and Graphviz",
        "enabled_by_default": True,
        "requires_dependencies": [],
        "tools": [],
    },
    "fileio": {
        "name": "File I/O",
        "description": "Read, write, and list files for agentic state tracking and design doc maintenance",
        "enabled_by_default": True,
        "requires_dependencies": [],
        "tools": [],
    },
    "pdf_rag": {
        "name": "PDF RAG",
        "description": "On-demand access to large PDF reference documents — outline, page range, BM25 search",
        "enabled_by_default": True,
        "requires_dependencies": [],
        "tools": [],
    },
    "ast": {
        "name": "AST Code Intelligence",
        "description": "Search and inspect the AST-indexed codebase — symbols, references, dependencies, file structure",
        "enabled_by_default": True,
        "requires_dependencies": [],
        "tools": [],
    },
    "nova_grounding": {
        "name": "Nova Web Search",
        "description": "Web search via Amazon Nova grounding — no external MCP server needed",
        "enabled_by_default": True,
        "requires_dependencies": [],
        "tools": [],
    },
    "skills": {
        "name": "Skill Discovery",
        "description": "Model-driven skill discovery — load specialized instructions on demand",
        "enabled_by_default": True,
        "requires_dependencies": [],
        "tools": [],
    },
    "memory": {
        "name": "Structured Memory",
        "description": "Persistent memory across sessions — search, save, and propose memories",
        "enabled_by_default": True,
        "requires_dependencies": [],
        "tools": [],
    },
    "context_management": {
        "name": "Context Management",
        "description": "Lets the model add/remove/list files in the current conversation's persistent context",
        "enabled_by_default": True,
        "requires_dependencies": [],
        "tools": [],
    },
    "chat_history": {
        "name": "Chat History",
        "description": "Lets the model search, list and read past conversation transcripts (read-only)",
        "enabled_by_default": True,
        "requires_dependencies": [],
        "tools": [],
    },
    "diagram_render": {
        "name": "Diagram Render",
        "description": "Render diagrams to images for visual inspection and iterative refinement",
        "enabled_by_default": True,
        "requires_dependencies": [],
        "tools": [],
    },
    "beads": {
        "name": "Conversation Beads",
        "description": "Silent task-tree tracking — subtask forking, parking, and completion",
        "enabled_by_default": True,
        "requires_dependencies": [],
        "tools": [],
    },
    "task_cards": {
        "name": "Task Card Editing",
        "description": "Author, stage, read and edit Task Cards (block trees, instructions, loop conditions)",
        "enabled_by_default": True,
        "requires_dependencies": [],
        "tools": [],
    },
    "shadow": {
        "name": "Shadow Sessions",
        "description": "Read and annotate live `ziya shadow` terminal sessions (observe-only; no exec)",
        "enabled_by_default": True,
        "requires_dependencies": [],
        "tools": [],
    },
    "task_artifacts": {
        "name": "Task Artifact Emission",
        "description": "Declare durable task outputs (emit_artifact) — collected into the run's Artifact and shown in the run tile's artifact viewer",
        "enabled_by_default": True,
        "requires_dependencies": [],
        "tools": [],
    },
}


def check_pcap_dependencies() -> bool:
    """Check if PCAP analysis dependencies are available."""
    try:
        import scapy.all
        return True
    except ImportError:
        logger.debug("PCAP analysis dependencies not available (scapy not installed)")
        return False


def get_pcap_analysis_tools() -> List[Type[BaseMCPTool]]:
    """Get PCAP analysis tools if dependencies are available."""
    if not check_pcap_dependencies():
        return []
    
    try:
        from app.mcp.tools.pcap_analysis import PCAPAnalysisTool, ListPCAPFilesTool
        return [PCAPAnalysisTool, ListPCAPFilesTool]
    except ImportError as e:
        logger.warning(f"Could not import PCAP analysis tools: {e}")
        return []


def get_architecture_shapes_tools() -> List[Type[BaseMCPTool]]:
    """Get architecture shapes catalog tools."""
    try:
        from app.mcp.tools.architecture_shapes.tools import (
            ListShapeCategoriesTool, SearchShapesTool, GetDiagramTemplateTool
        )
        return [ListShapeCategoriesTool, SearchShapesTool, GetDiagramTemplateTool]
    except ImportError as e:
        logger.warning(f"Could not import architecture shapes tools: {e}")
        return []


def get_fileio_tools() -> List[Type[BaseMCPTool]]:
    """Get file I/O tools for agentic state tracking."""
    try:
        from app.mcp.tools.fileio import (
            FileReadTool, FileWriteTool, FileListTool
        )
        return [FileReadTool, FileWriteTool, FileListTool]
    except ImportError as e:
        logger.warning(f"Could not import fileio tools: {e}")
        return []


def get_pdf_rag_tools() -> List[Type[BaseMCPTool]]:
    """Get PDF RAG tools for on-demand large-PDF access."""
    try:
        from app.mcp.tools.pdf_tools import (
            PdfOutlineTool, PdfReadPagesTool, PdfSearchTool
        )
        return [PdfOutlineTool, PdfReadPagesTool, PdfSearchTool]
    except ImportError as e:
        logger.warning(f"Could not import PDF RAG tools: {e}")
        return []


def get_ast_tools() -> List[Type[BaseMCPTool]]:
    """Get AST code intelligence tools."""
    try:
        from app.mcp.tools.ast_tools import (
            ASTGetTreeTool, ASTSearchTool, ASTReferencesTool
        )
        return [ASTGetTreeTool, ASTSearchTool, ASTReferencesTool]
    except ImportError as e:
        logger.warning(f"Could not import AST tools: {e}")
        return []


def get_nova_grounding_tools() -> List[Type[BaseMCPTool]]:
    """Get Nova Web Search grounding tools."""
    try:
        from app.mcp.tools.nova_grounding import NovaWebSearchTool
        return [NovaWebSearchTool]
    except ImportError as e:
        logger.warning(f"Could not import Nova grounding tools: {e}")
        return []


def get_diagram_render_tools() -> List[Type[BaseMCPTool]]:
    """Get diagram rendering tools, if Playwright AND its Chromium are installed.

    The Chromium build is a post-install step pip cannot run, so it is absent
    on a fresh ``pip install ziya``.  Offering the tool anyway meant a new
    user's first diagram request was spent on the model calling render_diagram
    and reading an install error -- on every diagram, every session.
    Same gate as the PCAP tools on scapy: register nothing when the dependency
    is missing and tell the operator, once, in the log.
    """
    from app.services import diagram_renderer
    if not diagram_renderer._check_playwright():
        from app.utils import optional_features
        logger.info(
            f"render_diagram / recall_image not registered: missing "
            f"{optional_features.playwright_missing_description()}. "
            f"{optional_features.browser_hint()} (then restart Ziya)"
        )
        return []
    try:
        from app.mcp.tools.diagram_render import (
            RecallImageTool, RenderDiagramTool, ViewImageTool,
        )
        # recall_image and view_image ship with render_diagram: one is only
        # reachable via a handle a render produced, the other reads back
        # what render_diagram(save_path=...) or the golden tier wrote.
        return [RenderDiagramTool, RecallImageTool, ViewImageTool]
    except ImportError as e:
        logger.warning(f"Could not import diagram render tools: {e}")
        return []


def get_skill_tools() -> List[Type[BaseMCPTool]]:
    """Get skill discovery tools."""
    try:
        from app.mcp.tools.skill_tools import GetSkillDetailsTool
        return [GetSkillDetailsTool]
    except ImportError as e:
        logger.warning(f"Could not import skill tools: {e}")
        return []


def get_memory_tools() -> List[Type[BaseMCPTool]]:
    """Get structured memory tools."""
    try:
        from app.mcp.tools.memory_tools import (
            MemorySearchTool, MemorySaveTool, MemoryProposeTool,
            MemoryContextTool, MemoryExpandTool, MemoryRetractProposalTool
        )
        return [MemorySearchTool, MemorySaveTool, MemoryProposeTool, MemoryContextTool, MemoryExpandTool, MemoryRetractProposalTool]
    except ImportError as e:
        logger.warning(f"Could not import memory tools: {e}")
        return []


def get_bead_tools() -> List[Type[BaseMCPTool]]:
    """Get bead task-tree tracking tools."""
    try:
        from app.mcp.tools.bead_tools import (
            BeadCreateTool, BeadCompleteTool, BeadStatusTool
        )
        return [BeadCreateTool, BeadCompleteTool, BeadStatusTool]
    except ImportError as e:
        logger.warning(f"Could not import bead tools: {e}")
        return []


def get_context_management_tools() -> List[Type[BaseMCPTool]]:
    """Get model-driven context-management tools."""
    try:
        from app.mcp.tools.context_management import (
            ContextAddFileTool, ContextRemoveFileTool, ContextListFilesTool
        )
        return [ContextAddFileTool, ContextRemoveFileTool, ContextListFilesTool]
    except ImportError as e:
        logger.warning(f"Could not import context management tools: {e}")
        return []


def get_chat_history_tools() -> List[Type[BaseMCPTool]]:
    """Get read-only chat-history tools (search / read / list past transcripts)."""
    try:
        from app.mcp.tools.chat_history_tools import (
            ChatSearchTool, ChatReadTool, ChatListTool
        )
        return [ChatSearchTool, ChatReadTool, ChatListTool]
    except ImportError as e:
        logger.warning(f"Could not import chat history tools: {e}")
        return []


def get_task_card_tools() -> List[Type[BaseMCPTool]]:
    """Get task-card read/write tools."""
    try:
        from app.mcp.tools.task_card_tools import (
            TaskCardListTool, TaskCardReadTool, TaskCardWriteTool,
            TaskCardValidateTool,
        )
        from app.mcp.tools.task_card_stage import TaskCardStageTool
        from app.mcp.tools.task_card_launch import TaskCardLaunchTool
        return [TaskCardStageTool, TaskCardListTool, TaskCardReadTool,
                TaskCardWriteTool, TaskCardValidateTool, TaskCardLaunchTool]
    except ImportError as e:
        logger.warning(f"Could not import task card tools: {e}")
        return []


def get_task_artifact_tools() -> List[Type[BaseMCPTool]]:
    """Get task artifact emission tools."""
    try:
        from app.mcp.tools.emit_artifact import EmitArtifactTool
        from app.mcp.tools.list_run_artifacts import ListRunArtifactsTool
        return [EmitArtifactTool, ListRunArtifactsTool]
    except ImportError as e:
        logger.warning(f"Could not import emit_artifact tool: {e}")
        return []


def get_shadow_tools() -> List[Type[BaseMCPTool]]:
    """Get shadow-session observation tools (Docs/design/shadow-sessions.md §9)."""
    try:
        from app.mcp.tools.shadow_tools import (
            ShadowListTool, ShadowReadTool, ShadowCommentTool, ShadowSetMetaTool,
            ShadowAttachTool, ShadowDetachTool,
            ShadowControlTool, ShadowSendTool, ShadowReleaseTool,
            ShadowSpawnTool, ShadowKillTool,
        )
        return [ShadowListTool, ShadowReadTool, ShadowCommentTool, ShadowSetMetaTool,
                ShadowAttachTool, ShadowDetachTool,
                ShadowControlTool, ShadowSendTool, ShadowReleaseTool,
                ShadowSpawnTool, ShadowKillTool]
    except ImportError as e:
        logger.warning(f"Could not import shadow tools: {e}")
        return []


def get_builtin_tools_for_category(category: str) -> List[Type[BaseMCPTool]]:
    """Get builtin tools for a specific category."""
    tool_getters = {
        "pcap_analysis": get_pcap_analysis_tools,
        "architecture_shapes": get_architecture_shapes_tools,
        "fileio": get_fileio_tools,
        "pdf_rag": get_pdf_rag_tools,
        "ast": get_ast_tools,
        "nova_grounding": get_nova_grounding_tools,
        "diagram_render": get_diagram_render_tools,
        "skills": get_skill_tools,
        "memory": get_memory_tools,
        "context_management": get_context_management_tools,
        "chat_history": get_chat_history_tools,
        "beads": get_bead_tools,
        "task_cards": get_task_card_tools,
        "task_artifacts": get_task_artifact_tools,
        "shadow": get_shadow_tools,
    }

    getter = tool_getters.get(category)
    if getter:
        return getter()
    return []


# Per-process cache for category enabled checks.  Populated on first
# call per category, cleared by invalidate_category_cache().
_category_enabled_cache: dict[str, bool] = {}


def is_builtin_category_enabled(category: str) -> bool:
    """Check if a builtin tool category is enabled."""
    if category not in BUILTIN_TOOL_CATEGORIES:
        return False

    cached = _category_enabled_cache.get(category)
    if cached is not None:
        return cached

    # Check environment variable first
    env_var = f"ZIYA_ENABLE_{category.upper()}"
    env_value = os.environ.get(env_var)
    if env_value is not None:
        result = env_value.lower() in ("true", "1", "yes")
        _category_enabled_cache[category] = result
        return result

    # Check if a service model plugin has enabled this category
    try:
        from app.plugins import get_enabled_service_tool_categories
        plugin_enabled = get_enabled_service_tool_categories()
        if category in plugin_enabled:
            _category_enabled_cache[category] = True
            return True
    except Exception:
        pass

    # Fall back to default setting
    result = BUILTIN_TOOL_CATEGORIES[category]["enabled_by_default"]
    _category_enabled_cache[category] = result
    return result


def invalidate_category_cache() -> None:
    """Clear the category-enabled cache after runtime config changes."""
    _category_enabled_cache.clear()


def get_enabled_builtin_tools() -> List[BaseMCPTool]:
    """Get all enabled builtin tools as instances."""
    enabled_tools = []
    
    for category, config in BUILTIN_TOOL_CATEGORIES.items():
        if is_builtin_category_enabled(category):
            tool_classes = get_builtin_tools_for_category(category)
            for tool_class in tool_classes:
                try:
                    enabled_tools.append(tool_class())
                    logger.debug(f"Enabled builtin tool: {tool_class().name}")
                except Exception as e:
                    logger.warning(f"Failed to initialize builtin tool {tool_class}: {e}")
    
    if enabled_tools:
        logger.debug(f"Loaded {len(enabled_tools)} enabled builtin tools")
    
    return enabled_tools
