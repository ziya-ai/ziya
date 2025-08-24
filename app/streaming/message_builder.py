#!/usr/bin/env python3
"""
Message Builder - No LangChain Dependencies
Builds messages for streaming without LangChain message types
"""

from typing import Dict, List, Any, Optional
from app.utils.logging_utils import logger


def build_messages_for_streaming(question: str, chat_history: List, files: List, conversation_id: str) -> List[Dict]:
    """
    Build messages for streaming without LangChain dependencies.
    Returns list of dicts with 'role' and 'content' keys.
    """
    
    from app.agents.prompts_manager import get_extended_prompt, get_model_info_from_config
    from app.agents.agent import get_combined_docs_from_files
    from app.config.models_config import TOOL_SENTINEL_OPEN, TOOL_SENTINEL_CLOSE
    
    model_info = get_model_info_from_config()
    
    # Get model_id for MCP guidelines exclusion
    from app.agents.models import ModelManager
    model_id = ModelManager.get_model_id()
    
    # Get MCP context
    mcp_context = {"model_id": model_id}
    try:
        from app.mcp.manager import get_mcp_manager
        mcp_manager = get_mcp_manager()
        if mcp_manager.is_initialized:
            available_tools = [tool.name for tool in mcp_manager.get_all_tools()]
            mcp_context = {
                "mcp_tools_available": len(available_tools) > 0,
                "available_mcp_tools": available_tools
            }
    except Exception as e:
        logger.warning(f"Could not get MCP tools: {e}")
    
    # Get file context
    from app.agents.agent import extract_codebase
    file_context = extract_codebase({"config": {"files": files}, "conversation_id": conversation_id})
    
    # Apply post-instructions to the question once here
    from app.utils.post_instructions import PostInstructionManager
    modified_question = PostInstructionManager.apply_post_instructions(
        query=question,
        model_name=model_info["model_name"],
        model_family=model_info["model_family"],
        endpoint=model_info["endpoint"]
    )
    
    # Get the extended prompt and format it properly
    extended_prompt = get_extended_prompt(
        model_name=model_info["model_name"],
        model_family=model_info["model_family"],
        endpoint=model_info["endpoint"],
        context=mcp_context
    )
    
    # Get available tools for the template
    tools_list = []
    try:
        from app.mcp.manager import get_mcp_manager
        mcp_manager = get_mcp_manager()
        if mcp_manager.is_initialized:
            tools_list = [f"- {tool.name}: {tool.description}" for tool in mcp_manager.get_all_tools()]
    except Exception as e:
        logger.warning(f"Could not get tools for template: {e}")
    
    # Build messages manually to ensure proper conversation history
    messages = []
    
    # Add system message with context
    system_content = extended_prompt.messages[0].prompt.template.format(
        codebase=file_context,
        ast_context="",
        tools="\n".join(tools_list) if tools_list else "No tools available",
        TOOL_SENTINEL_OPEN=TOOL_SENTINEL_OPEN,
        TOOL_SENTINEL_CLOSE=TOOL_SENTINEL_CLOSE
    )
    
    messages.append({"role": "system", "content": system_content})
    
    # Add conversation history
    for item in chat_history:
        if isinstance(item, dict):
            role = item.get('type', item.get('role', 'human'))
            content = item.get('content', '')
        elif isinstance(item, (list, tuple)) and len(item) >= 2:
            role, content = item[0], item[1]
        else:
            continue
            
        if role in ['human', 'user']:
            messages.append({"role": "user", "content": content})
        elif role in ['assistant', 'ai']:
            messages.append({"role": "assistant", "content": content})
    
    # Add current question
    messages.append({"role": "user", "content": modified_question})
    
    return messages


def format_chat_history(chat_history: List) -> str:
    """Format chat history for display without LangChain dependencies."""
    if not chat_history:
        return ""
    
    formatted = []
    for item in chat_history:
        if isinstance(item, dict):
            role = item.get('type', item.get('role', 'human'))
            content = item.get('content', '')
        elif isinstance(item, (list, tuple)) and len(item) >= 2:
            role, content = item[0], item[1]
        else:
            continue
            
        if role in ['human', 'user']:
            formatted.append(f"Human: {content}")
        elif role in ['assistant', 'ai']:
            formatted.append(f"Assistant: {content}")
    
    return "\n".join(formatted)
