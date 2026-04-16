"""
Miscellaneous routes (PCAP, dynamic tools, stream control).

Extracted from server.py during Phase 3b refactoring.
"""
import os
import logging
import asyncio
import json

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from starlette.responses import StreamingResponse
from pydantic import BaseModel
from typing import Optional

from app.utils.logging_utils import logger
from app.utils.pcap_analyzer import analyze_pcap_file, is_pcap_supported

router = APIRouter(tags=["misc"])

class PcapAnalyzeRequest(BaseModel):
    model_config = {"extra": "allow"}
    file_path: str
    operation: str = "summary"
    src_ip: Optional[str] = None
    dst_ip: Optional[str] = None
    protocol: Optional[str] = None
    port: Optional[int] = None
    tcp_flags: Optional[str] = None
    min_size: Optional[int] = None
    max_size: Optional[int] = None
    icmp_type: Optional[int] = None
    pattern: Optional[str] = None
    packet_index: Optional[int] = None
    limit: Optional[int] = None


@router.post("/api/dynamic-tools/update")
async def update_dynamic_tools(request: Request):
    """
    Update dynamically loaded tools based on file selection.
    Called by frontend when file selection changes.
    """
    try:
        from starlette.requests import ClientDisconnect
        body = await request.json()
        files = body.get('files', [])

        logger.debug(f"Dynamic tools update requested for {len(files)} files")

        from app.mcp.dynamic_tools import get_dynamic_loader
        from app.mcp.manager import get_mcp_manager

        # Get the dynamic loader
        loader = get_dynamic_loader()

        # Load appropriate tools based on files
        newly_loaded = loader.load_tools_for_files(files)

        # Invalidate MCP manager's tools cache so new tools appear
        mcp_manager = get_mcp_manager()
        if mcp_manager.is_initialized:
            mcp_manager.invalidate_tools_cache()

        # Get currently active dynamic tools
        active_tools = loader.get_active_tools()

        return JSONResponse({
            "success": True,
            "newly_loaded": list(newly_loaded.keys()),
            "active_tools": list(active_tools.keys()),
            "message": f"Loaded {len(newly_loaded)} new tools, {len(active_tools)} total active"
        })

    except ClientDisconnect:
        # Browser cancelled the request during page navigation — harmless.
        logger.debug("Dynamic tools update: client disconnected before body was read")
        return JSONResponse({"message": "Client disconnected"}, status_code=499)
    except Exception as e:
        logger.error(f"Error updating dynamic tools: {e}", exc_info=True)
        return JSONResponse({"error": str(e)}, status_code=500)


@router.post("/api/tools/pcap/analyze")
async def analyze_pcap(request: PcapAnalyzeRequest):
    """
    Analyze a pcap file with various operations
    
    Operations:
    - summary: Get overall statistics
    - conversations: Extract TCP/UDP conversations
    - dns_queries: Extract DNS queries
    - dns_responses: Extract DNS responses
    - filter: Filter packets by IP/protocol/port
    - search: Search for pattern in payloads
    - tcp_health: Analyze TCP health metrics (retransmissions, resets, errors)
    - flow_stats: Get detailed flow-level statistics with timing
    - connectivity_map: Get connectivity map for visualization
    - flow_health: Combined flow statistics with health analysis
    - search_advanced: Advanced filtering with TCP flags, size, etc.
    - http: Extract HTTP requests
    - packet_details: Get details for specific packet
    - tunneling: Get tunneling protocol information
    - ipv6_extensions: Get IPv6 extension header details
    - tls: Get TLS/SSL connection information
    - icmp: Get ICMP/ICMPv6 packet information
    """
    if not is_pcap_supported():
        return JSONResponse(
            status_code=501,
            content={
                "error": "pcap_not_supported",
                "message": "Scapy is not installed. Install with: pip install scapy"
            }
        )
    
    try:
        result = analyze_pcap_file(
            file_path=request.file_path,
            operation=request.operation,
            src_ip=request.src_ip,
            dst_ip=request.dst_ip,
            protocol=request.protocol,
            port=request.port,
            pattern=request.pattern,
            packet_index=request.packet_index,
            limit=request.limit
        )
        
        # Check if result contains an error
        if isinstance(result, dict) and "error" in result:
            status_code = 400
            if result["error"] == "file_not_found":
                status_code = 404
            elif result["error"] == "import_error":
                status_code = 501
            
            return JSONResponse(
                status_code=status_code,
                content=result
            )
        
        return result
        
    except Exception as e:
        logger.error(f"Error in pcap analysis endpoint: {e}")
        return JSONResponse(
            status_code=500,
            content={"error": "analysis_failed", "message": str(e)}
        )


@router.get("/api/tools/pcap/status")
async def pcap_status():
    """Check if pcap analysis is available"""
    return {
        "available": is_pcap_supported(),
        "message": "Scapy is installed and ready" if is_pcap_supported() else "Scapy is not installed. Install with: pip install scapy"
    }


@router.post('/api/abort-stream')
async def abort_stream(request: Request):
    """Explicitly abort a streaming response from the client side."""
    try:
        body = await request.json()
        conversation_id = body.get("conversation_id") or body.get("conversationId")
        
        if not conversation_id:
            return JSONResponse(
                status_code=400,
                content={"error": "conversation_id is required"}
            )
            
        from app.server import cleanup_stream

        logger.info(f"Explicitly aborting stream for conversation: {conversation_id}")
        await cleanup_stream(conversation_id)
        return JSONResponse(content={"status": "success", "message": "Stream aborted"})
    except Exception as e:
        logger.error(f"Error aborting stream: {str(e)}")
        return JSONResponse(status_code=500, content={"error": str(e)})


@router.post('/api/retry-throttled-request')
async def retry_throttled_request(request: Request):
    """Retry a request that was throttled, with fresh retry attempts."""
    try:
        body = await request.json()
        
        if not body.get("conversation_id"):
            return JSONResponse(status_code=400, content={"error": "conversation_id is required"})
            
        logger.info(f"User retry requested for conversation: {body.get('conversation_id')}")
        
        # Forward to the main streaming endpoint with fresh retry attempts
        from app.server import _keepalive_wrapper, stream_chunks

        return StreamingResponse(
            _keepalive_wrapper(stream_chunks(body)),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "POST, OPTIONS",
                "Access-Control-Allow-Headers": "Content-Type"
            }
        )
    except Exception as e:
        logger.error(f"Error retrying throttled request: {str(e)}")
        return JSONResponse(status_code=500, content={"error": str(e)})

