"""
MCP tools for PCAP analysis and network protocol correlation.

These tools allow the model to analyze network packet captures and correlate
them with protocol implementation source code.
"""

import asyncio
import json
import os
import tempfile
from typing import Dict, List, Optional, Any
from pathlib import Path

from pydantic import BaseModel, Field

from app.utils.logging_utils import logger
from app.mcp.tools.base import BaseMCPTool
from app.utils.pcap_reader import PCAPReader, validate_pcap_file, PCAPAnalysis


class PCAPAnalysisTool(BaseMCPTool):
    """Tool for analyzing PCAP files and extracting network insights."""
    
    name = "analyze_pcap"
    description = """Analyze network packet capture (PCAP) files to extract protocol information, flows, and statistics.
    [BUILTIN] Network Analysis Tool
    This tool can:
    - Parse PCAP/PCAPNG files and extract packet information
    - Identify network protocols and analyze traffic patterns
    - Generate flow statistics and top talker analysis
    - Provide detailed packet-level information for correlation with source code
    
    Use this when:
    - Analyzing network traffic captures
    - Investigating network performance or security issues
    - Correlating network behavior with protocol implementations
    - Understanding network flow patterns and protocols
    """
    
    class InputSchema(BaseModel):
        pcap_path: str = Field(..., description="Path to the PCAP file to analyze")
        max_packets: Optional[int] = Field(None, description="Maximum number of packets to process (default: all packets)")
        include_raw_data: bool = Field(False, description="Include raw packet data in output (increases size significantly)")
        analysis_level: str = Field("standard", description="Analysis detail level: 'basic', 'standard', or 'detailed'")
    
    async def execute(self, **kwargs) -> Dict[str, Any]:
        try:
            # Validate inputs
            input_data = self.InputSchema.model_validate(kwargs)
            
            # Validate PCAP file
            if not os.path.exists(input_data.pcap_path):
                return {
                    "error": True,
                    "message": f"PCAP file not found: {input_data.pcap_path}"
                }
            
            if not validate_pcap_file(input_data.pcap_path):
                return {
                    "error": True,
                    "message": f"Invalid or unreadable PCAP file: {input_data.pcap_path}"
                }
            
            # Check if PCAP dependencies are available
            try:
                from app.mcp.builtin_tools import check_pcap_dependencies
                if not check_pcap_dependencies():
                    return {
                        "error": True,
                        "message": "PCAP analysis dependencies not available. Please install: pip install scapy dpkt netaddr"
                    }
            except ImportError:
                pass
            
            # Initialize PCAP reader
            reader = PCAPReader(use_scapy=True)
            
            # Analyze PCAP file
            logger.info(f"Starting PCAP analysis: {input_data.pcap_path}")
            analysis = reader.read_pcap(
                input_data.pcap_path, 
                max_packets=input_data.max_packets
            )
            
            # Format output based on analysis level
            result = await self._format_analysis_result(analysis, input_data)
            
            logger.info(f"PCAP analysis completed: {analysis.packet_count} packets, {len(analysis.flows)} flows")
            
            return {
                "success": True,
                "analysis": result,
                "metadata": {
                    "tool": self.name,
                    "file_analyzed": analysis.filename,
                    "analysis_level": input_data.analysis_level,
                    "packets_processed": analysis.packet_count
                }
            }
            
        except Exception as e:
            logger.error(f"Error in PCAP analysis: {e}")
            return {
                "error": True,
                "message": f"PCAP analysis failed: {str(e)}"
            }
    
    async def _format_analysis_result(self, analysis: PCAPAnalysis, input_data) -> Dict[str, Any]:
        """Format the analysis result based on the requested detail level."""
        
        base_result = {
            "file_info": {
                "filename": analysis.filename,
                "file_size": analysis.file_size,
                "packet_count": analysis.packet_count,
                "duration": round(analysis.duration, 3),
                "time_range": {
                    "start": analysis.time_range[0],
                    "end": analysis.time_range[1]
                }
            },
            "summary": analysis.summary,
            "protocols": analysis.protocols,
            "unique_ips": analysis.unique_ips[:20],  # Limit to top 20 IPs
            "top_talkers": analysis.top_talkers[:10]  # Top 10 talkers
        }
        
        if input_data.analysis_level in ["standard", "detailed"]:
            # Add flow information
            flows_summary = []
            for flow in analysis.flows[:50]:  # Limit to first 50 flows
                flow_info = {
                    "flow_id": flow.flow_id,
                    "src": f"{flow.src_ip}:{flow.src_port}" if flow.src_port else flow.src_ip,
                    "dst": f"{flow.dst_ip}:{flow.dst_port}" if flow.dst_port else flow.dst_ip,
                    "protocol": flow.protocol,
                    "packets": flow.packet_count,
                    "bytes": flow.total_bytes,
                    "duration": round(flow.duration, 3)
                }
                flows_summary.append(flow_info)
            
            base_result["flows"] = flows_summary
        
        if input_data.analysis_level == "detailed":
            # Add sample packet information
            sample_packets = []
            for i, flow in enumerate(analysis.flows[:10]):  # First 10 flows
                if flow.packets:
                    pkt = flow.packets[0]  # First packet of flow
                    packet_info = {
                        "flow_index": i,
                        "timestamp": pkt.timestamp,
                        "src": f"{pkt.src_ip}:{pkt.src_port}" if pkt.src_port else pkt.src_ip,
                        "dst": f"{pkt.dst_ip}:{pkt.dst_port}" if pkt.dst_port else pkt.dst_ip,
                        "protocol": pkt.protocol,
                        "length": pkt.length,
                        "flags": pkt.flags,
                        "payload_preview": pkt.payload_preview[:100] if pkt.payload_preview else None
                    }
                    if input_data.include_raw_data and pkt.raw_data:
                        packet_info["raw_data"] = pkt.raw_data.hex()[:200]  # First 200 hex chars
                    
                    sample_packets.append(packet_info)
            
            base_result["sample_packets"] = sample_packets
        
        return base_result


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


# Export the tools for registration
__all__ = ["PCAPAnalysisTool", "ListPCAPFilesTool"]
