"""
MCP tools for PCAP analysis and network protocol correlation.

These tools allow the model to analyze network packet captures and correlate
them with protocol implementation source code.

Note: This module has optional dependencies (scapy). If not installed, the tools
will be registered but will return an error message when executed.
"""

import json
import os
from typing import Dict, List, Optional, Any
from pathlib import Path

from pydantic import BaseModel, Field

from app.utils.logging_utils import logger
from app.mcp.tools.base import BaseMCPTool

# Conditional import - don't fail at module load time
try:
    from app.utils.pcap_analyzer import analyze_pcap_file, is_pcap_supported
    PCAP_AVAILABLE = True
except ImportError as e:
    logger.debug(f"PCAP analysis dependencies not available: {e}")
    PCAP_AVAILABLE = False
    # Provide stub functions
    def is_pcap_supported(): return False
    def analyze_pcap_file(*args, **kwargs): return {"error": True, "message": "PCAP dependencies not installed"}

class PCAPAnalysisTool(BaseMCPTool):
    """Tool for analyzing PCAP files and extracting network insights."""
    
    name = "analyze_pcap"
    description = """Analyze network packet capture (PCAP) files to extract protocol information, flows, and statistics.
    [BUILTIN] Network Analysis Tool
    This tool can:
    - Parse PCAP/PCAPNG files and extract packet information
    - Identify network protocols and analyze traffic patterns
    - Generate flow statistics and top talker analysis
    - Analyze TCP health: retransmissions, resets, zero windows, connection failures
    - Detect communication errors and network issues
    - Create connectivity maps showing source/destination relationships
    - Provide flow-level health metrics and error indicators
    - Provide detailed packet-level information for correlation with source code
    
    Use this when:
    - Analyzing network traffic captures
    - Investigating network performance or security issues
    - Troubleshooting TCP connection problems and retransmissions
    - Understanding flow health and error patterns
    - Correlating network behavior with protocol implementations
    - Understanding network flow patterns and protocols
    """
    
    class InputSchema(BaseModel):
        pcap_path: str = Field(..., description="Path to the PCAP file to analyze")
        max_packets: Optional[int] = Field(None, description="Maximum number of packets to process (default: all packets)")
        operation: str = Field("summary", description="Analysis operation: 'summary', 'conversations', 'dns_queries', 'dns_responses', 'filter', 'search', 'tcp_health', 'flow_stats', 'connectivity_map', 'flow_health', 'search_advanced', 'http', 'packet_details', 'tunneling', 'ipv6_extensions', 'tls', 'icmp'")
    
    async def execute(self, **kwargs) -> Dict[str, Any]:
        try:
            # Check dependencies first
            if not PCAP_AVAILABLE or not is_pcap_supported():
                return {
                    "error": True,
                    "message": "PCAP analysis dependencies not available. Install with: pip install scapy"
                }
            
            # Validate inputs
            input_data = self.InputSchema.model_validate(kwargs)
            
            # Validate PCAP file
            if not os.path.exists(input_data.pcap_path):
                return {
                    "error": True,
                    "message": f"PCAP file not found: {input_data.pcap_path}"
                }
            
            # Use the built-in pcap analyzer
            logger.info(f"Analyzing PCAP file: {input_data.pcap_path}")
            result = analyze_pcap_file(
                input_data.pcap_path,
                operation=input_data.operation
            )
            
            if "error" in result:
                return {
                    "error": True,
                    "message": result.get("message", "Analysis failed")
                }
            
            # Return the full analysis result as structured JSON for the model to analyze
            return {
                "content": [{"type": "text", "text": json.dumps(result, indent=2)}]
            }
        except Exception as e:
            return {
                "error": True,
                "message": f"PCAP analysis failed: {str(e)}"
            }


class ListPCAPFilesTool(BaseMCPTool):
    """Tool for finding PCAP files in the current workspace."""
    
    name = "list_pcap_files"
    description = """Find PCAP files in the current workspace or specified directory.
    [BUILTIN] Network Analysis Tool
    This tool helps locate network capture files for analysis by searching for
    common PCAP file extensions (.pcap, .pcapng, .cap, .dmp).
    
    Use this when:
    - Looking for PCAP files to analyze
    - Exploring available network captures
    - Preparing for PCAP analysis workflow
    """
    
    class InputSchema(BaseModel):
        search_path: Optional[str] = Field(".", description="Directory to search for PCAP files (default: current directory)")
        recursive: bool = Field(True, description="Search subdirectories recursively")
        max_results: int = Field(100, description="Maximum number of files to return")
    
    async def execute(self, **kwargs) -> Dict[str, Any]:
        try:
            # Check dependencies first
            if not PCAP_AVAILABLE or not is_pcap_supported():
                return {
                    "error": True,
                    "message": "PCAP analysis dependencies not available. Install with: pip install scapy"
                }
            
            input_data = self.InputSchema.model_validate(kwargs)
            
            search_path = Path(input_data.search_path)
            if not search_path.exists():
                return {
                    "error": True,
                    "message": f"Search path does not exist: {input_data.search_path}"
                }
            
            pcap_extensions = {'.pcap', '.pcapng', '.cap', '.dmp'}
            pcap_files = []
            
            # Search for PCAP files
            if input_data.recursive:
                pattern = "**/*"
            else:
                pattern = "*"
            
            for file_path in search_path.glob(pattern):
                if (file_path.is_file() and 
                    file_path.suffix.lower() in pcap_extensions and
                    len(pcap_files) < input_data.max_results):
                    
                    file_info = {
                        "path": str(file_path),
                        "name": file_path.name,
                        "size": file_path.stat().st_size,
                        "modified": file_path.stat().st_mtime,
                        "extension": file_path.suffix.lower(),
                        "is_valid": validate_pcap_file(str(file_path))
                    }
                    pcap_files.append(file_info)
            
            # Sort by modification time (newest first)
            pcap_files.sort(key=lambda x: x["modified"], reverse=True)
            
            return {
                "success": True,
                "files": pcap_files,
                "summary": {
                    "total_found": len(pcap_files),
                    "search_path": str(search_path),
                    "recursive": input_data.recursive,
                    "valid_files": sum(1 for f in pcap_files if f["is_valid"])
                }
            }
            
        except Exception as e:
            logger.error(f"Error listing PCAP files: {e}")
            return {
                "error": True,
                "message": f"Failed to list PCAP files: {str(e)}"
            }


def validate_pcap_file(file_path: str) -> bool:
    """Quick validation that a file is a valid PCAP."""
    if not PCAP_AVAILABLE:
        return False
    try:
        # Basic file check - just verify it exists and has reasonable size
        return os.path.exists(file_path) and os.path.getsize(file_path) > 24
    except:
        return False


# Export the tools for registration
__all__ = ["PCAPAnalysisTool", "ListPCAPFilesTool"]
