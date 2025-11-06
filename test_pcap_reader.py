#!/usr/bin/env python3
"""
Test script for PCAP reader functionality.

This script tests the PCAP analysis tools with sample data.
"""

import asyncio
import json
from pathlib import Path

from app.utils.pcap_reader import PCAPReader, validate_pcap_file
from app.mcp.tools.pcap_analysis import PCAPAnalysisTool, ListPCAPFilesTool


async def test_pcap_analysis():
    """Test PCAP analysis functionality."""
    print("Testing PCAP Analysis Tools")
    print("=" * 50)
    
    # Test file listing
    list_tool = ListPCAPFilesTool()
    print("\n1. Testing PCAP file discovery...")
    result = await list_tool.execute(search_path=".", recursive=True)
    
    if result.get("success"):
        files = result.get("files", [])
        print(f"Found {len(files)} PCAP files:")
        for file_info in files[:5]:  # Show first 5 files
            print(f"  - {file_info['name']} ({file_info['size']} bytes) - Valid: {file_info['is_valid']}")
        
        # Test analysis on first valid file
        valid_files = [f for f in files if f['is_valid']]
        if valid_files:
            test_file = valid_files[0]['path']
            print(f"\n2. Testing PCAP analysis on: {test_file}")
            
            analysis_tool = PCAPAnalysisTool()
            analysis_result = await analysis_tool.execute(
                pcap_path=test_file,
                analysis_level="detailed",
                max_packets=100
            )
            
            if analysis_result.get("success"):
                analysis = analysis_result["analysis"]
                print(f"Analysis completed:")
                print(f"  - Packets: {analysis['file_info']['packet_count']}")
                print(f"  - Duration: {analysis['file_info']['duration']}s")
                print(f"  - Protocols: {list(analysis['protocols'].keys())}")
                print(f"  - Flows: {len(analysis.get('flows', []))}")
            else:
                print(f"Analysis failed: {analysis_result.get('message')}")
        else:
            print("No valid PCAP files found for testing")
    else:
        print(f"File listing failed: {result.get('message')}")


if __name__ == "__main__":
    asyncio.run(test_pcap_analysis())
