"""
PCAP file reader and analysis utilities.

This module provides low-level PCAP parsing capabilities with support for
multiple PCAP formats and protocols.
"""

import os
import time
from typing import Dict, List, Optional, Any, Tuple, Iterator
from dataclasses import dataclass, asdict
from pathlib import Path
import logging

try:
    from scapy.all import rdpcap, Packet, IP, TCP, UDP, ICMP, Ether
    from scapy.layers.dns import DNS
    from scapy.layers.http import HTTP
    SCAPY_AVAILABLE = True
except ImportError:
    SCAPY_AVAILABLE = False

try:
    import dpkt
    import socket
    DPKT_AVAILABLE = True
except ImportError:
    DPKT_AVAILABLE = False

logger = logging.getLogger(__name__)


@dataclass
class PacketInfo:
    """Structured representation of a network packet."""
    timestamp: float
    src_ip: str
    dst_ip: str
    src_port: Optional[int]
    dst_port: Optional[int]
    protocol: str
    length: int
    payload_length: int
    flags: Optional[str] = None
    payload_preview: Optional[str] = None
    raw_data: Optional[bytes] = None


@dataclass
class NetworkFlow:
    """Represents a bidirectional network flow."""
    flow_id: str
    src_ip: str
    dst_ip: str
    src_port: Optional[int]
    dst_port: Optional[int]
    protocol: str
    packet_count: int
    total_bytes: int
    start_time: float
    end_time: float
    duration: float
    packets: List[PacketInfo]


@dataclass
class PCAPAnalysis:
    """Complete analysis of a PCAP file."""
    filename: str
    file_size: int
    packet_count: int
    time_range: Tuple[float, float]
    duration: float
    protocols: Dict[str, int]
    unique_ips: List[str]
    flows: List[NetworkFlow]
    top_talkers: List[Tuple[str, int]]  # (IP, packet_count)
    summary: Dict[str, Any]


class PCAPReader:
    """High-performance PCAP file reader with multiple backend support."""
    
    def __init__(self, use_scapy: bool = True):
        """Initialize PCAP reader with preferred backend."""
        self.use_scapy = use_scapy and SCAPY_AVAILABLE
        self.use_dpkt = not use_scapy and DPKT_AVAILABLE
        
        if not (SCAPY_AVAILABLE or DPKT_AVAILABLE):
            raise ImportError("Neither scapy nor dpkt is available. Install at least one PCAP library.")
    
    def read_pcap(self, file_path: str, max_packets: Optional[int] = None) -> PCAPAnalysis:
        """
        Read and analyze a PCAP file.
        
        Args:
            file_path: Path to the PCAP file
            max_packets: Maximum number of packets to process (None for all)
            
        Returns:
            Complete PCAP analysis
        """
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"PCAP file not found: {file_path}")
        
        file_size = os.path.getsize(file_path)
        logger.info(f"Reading PCAP file: {file_path} ({file_size} bytes)")
        
        start_time = time.time()
        
        if self.use_scapy:
            analysis = self._read_with_scapy(file_path, max_packets)
        elif self.use_dpkt:
            analysis = self._read_with_dpkt(file_path, max_packets)
        else:
            raise RuntimeError("No PCAP reading backend available")
        
        analysis.file_size = file_size
        analysis.filename = os.path.basename(file_path)
        
        read_time = time.time() - start_time
        logger.info(f"PCAP analysis completed in {read_time:.2f}s: {analysis.packet_count} packets")
        
        return analysis
    
    def _read_with_scapy(self, file_path: str, max_packets: Optional[int]) -> PCAPAnalysis:
        """Read PCAP using Scapy backend."""
        try:
            packets = rdpcap(file_path, count=max_packets)
        except Exception as e:
            raise ValueError(f"Failed to read PCAP with Scapy: {e}")
        
        packet_infos = []
        protocols = {}
        unique_ips = set()
        
        for i, pkt in enumerate(packets):
            packet_info = self._extract_packet_info_scapy(pkt, i)
            packet_infos.append(packet_info)
            
            # Track protocols
            protocols[packet_info.protocol] = protocols.get(packet_info.protocol, 0) + 1
            
            # Track unique IPs
            if packet_info.src_ip:
                unique_ips.add(packet_info.src_ip)
            if packet_info.dst_ip:
                unique_ips.add(packet_info.dst_ip)
        
        # Generate flows
        flows = self._generate_flows(packet_infos)
        
        # Calculate time range
        if packet_infos:
            time_range = (packet_infos[0].timestamp, packet_infos[-1].timestamp)
            duration = time_range[1] - time_range[0]
        else:
            time_range = (0.0, 0.0)
            duration = 0.0
        
        # Top talkers analysis
        ip_counts = {}
        for pkt in packet_infos:
            if pkt.src_ip:
                ip_counts[pkt.src_ip] = ip_counts.get(pkt.src_ip, 0) + 1
        
        top_talkers = sorted(ip_counts.items(), key=lambda x: x[1], reverse=True)[:10]
        
        return PCAPAnalysis(
            filename="",  # Will be set by caller
            file_size=0,  # Will be set by caller
            packet_count=len(packet_infos),
            time_range=time_range,
            duration=duration,
            protocols=protocols,
            unique_ips=list(unique_ips),
            flows=flows,
            top_talkers=top_talkers,
            summary=self._generate_summary(packet_infos, flows, protocols)
        )
    
    def _extract_packet_info_scapy(self, pkt: Packet, index: int) -> PacketInfo:
        """Extract structured information from a Scapy packet."""
        timestamp = float(pkt.time) if hasattr(pkt, 'time') else time.time()
        
        # Default values
        src_ip = dst_ip = ""
        src_port = dst_port = None
        protocol = "Unknown"
        flags = None
        payload_preview = None
        
        # Extract IP information
        if pkt.haslayer(IP):
            ip_layer = pkt[IP]
            src_ip = ip_layer.src
            dst_ip = ip_layer.dst
            protocol = ip_layer.proto
            
            # Convert protocol number to name
            if protocol == 6:
                protocol = "TCP"
            elif protocol == 17:
                protocol = "UDP"
            elif protocol == 1:
                protocol = "ICMP"
            else:
                protocol = f"IP-{protocol}"
        
        # Extract transport layer information
        if pkt.haslayer(TCP):
            tcp_layer = pkt[TCP]
            src_port = tcp_layer.sport
            dst_port = tcp_layer.dport
            protocol = "TCP"
            
            # TCP flags
            flag_names = []
            if tcp_layer.flags.F: flag_names.append("FIN")
            if tcp_layer.flags.S: flag_names.append("SYN")
            if tcp_layer.flags.R: flag_names.append("RST")
            if tcp_layer.flags.P: flag_names.append("PSH")
            if tcp_layer.flags.A: flag_names.append("ACK")
            if tcp_layer.flags.U: flag_names.append("URG")
            flags = ",".join(flag_names) if flag_names else None
            
        elif pkt.haslayer(UDP):
            udp_layer = pkt[UDP]
            src_port = udp_layer.sport
            dst_port = udp_layer.dport
            protocol = "UDP"
        
        # Application layer detection
        if pkt.haslayer(DNS):
            protocol = "DNS"
        elif pkt.haslayer(HTTP):
            protocol = "HTTP"
        
        # Payload preview (first 50 bytes as hex)
        if hasattr(pkt, 'payload') and pkt.payload:
            payload_bytes = bytes(pkt.payload)[:50]
            payload_preview = payload_bytes.hex()[:100]  # Limit to 100 chars
        
        return PacketInfo(
            timestamp=timestamp,
            src_ip=src_ip,
            dst_ip=dst_ip,
            src_port=src_port,
            dst_port=dst_port,
            protocol=protocol,
            length=len(pkt),
            payload_length=len(pkt.payload) if hasattr(pkt, 'payload') else 0,
            flags=flags,
            payload_preview=payload_preview,
            raw_data=bytes(pkt)
        )
    
    def _generate_flows(self, packets: List[PacketInfo]) -> List[NetworkFlow]:
        """Generate network flows from packets."""
        flows_dict = {}
        
        for pkt in packets:
            # Create flow key (bidirectional)
            if pkt.src_port and pkt.dst_port:
                key1 = f"{pkt.src_ip}:{pkt.src_port}-{pkt.dst_ip}:{pkt.dst_port}-{pkt.protocol}"
                key2 = f"{pkt.dst_ip}:{pkt.dst_port}-{pkt.src_ip}:{pkt.src_port}-{pkt.protocol}"
            else:
                key1 = f"{pkt.src_ip}-{pkt.dst_ip}-{pkt.protocol}"
                key2 = f"{pkt.dst_ip}-{pkt.src_ip}-{pkt.protocol}"
            
            # Use existing flow or create new one
            flow_key = None
            if key1 in flows_dict:
                flow_key = key1
            elif key2 in flows_dict:
                flow_key = key2
            else:
                flow_key = key1
                flows_dict[flow_key] = {
                    'packets': [],
                    'src_ip': pkt.src_ip,
                    'dst_ip': pkt.dst_ip,
                    'src_port': pkt.src_port,
                    'dst_port': pkt.dst_port,
                    'protocol': pkt.protocol,
                    'total_bytes': 0,
                    'start_time': pkt.timestamp,
                    'end_time': pkt.timestamp
                }
            
            # Update flow
            flow = flows_dict[flow_key]
            flow['packets'].append(pkt)
            flow['total_bytes'] += pkt.length
            flow['end_time'] = max(flow['end_time'], pkt.timestamp)
            flow['start_time'] = min(flow['start_time'], pkt.timestamp)
        
        # Convert to NetworkFlow objects
        network_flows = []
        for flow_id, flow_data in flows_dict.items():
            network_flow = NetworkFlow(
                flow_id=flow_id,
                src_ip=flow_data['src_ip'],
                dst_ip=flow_data['dst_ip'],
                src_port=flow_data['src_port'],
                dst_port=flow_data['dst_port'],
                protocol=flow_data['protocol'],
                packet_count=len(flow_data['packets']),
                total_bytes=flow_data['total_bytes'],
                start_time=flow_data['start_time'],
                end_time=flow_data['end_time'],
                duration=flow_data['end_time'] - flow_data['start_time'],
                packets=flow_data['packets']
            )
            network_flows.append(network_flow)
        
        return sorted(network_flows, key=lambda f: f.start_time)
    
    def _read_with_dpkt(self, file_path: str, max_packets: Optional[int]) -> PCAPAnalysis:
        """Read PCAP using dpkt backend (fallback implementation)."""
        # TODO: Implement dpkt-based reading for cases where Scapy isn't available
        raise NotImplementedError("DPKT backend not yet implemented")
    
    def _generate_summary(self, packets: List[PacketInfo], flows: List[NetworkFlow], protocols: Dict[str, int]) -> Dict[str, Any]:
        """Generate analysis summary statistics."""
        if not packets:
            return {}
        
        total_bytes = sum(pkt.length for pkt in packets)
        avg_packet_size = total_bytes / len(packets) if packets else 0
        
        return {
            'total_packets': len(packets),
            'total_flows': len(flows),
            'total_bytes': total_bytes,
            'average_packet_size': round(avg_packet_size, 2),
            'protocols_detected': len(protocols),
            'most_common_protocol': max(protocols.items(), key=lambda x: x[1])[0] if protocols else None,
            'unique_conversations': len(flows),
            'analysis_timestamp': time.time()
        }


def validate_pcap_file(file_path: str) -> bool:
    """
    Validate that a file is a readable PCAP file.
    
    Args:
        file_path: Path to the file to validate
        
    Returns:
        True if file is a valid PCAP, False otherwise
    """
    try:
        # Check file extension
        valid_extensions = {'.pcap', '.pcapng', '.cap', '.dmp'}
        if not any(file_path.lower().endswith(ext) for ext in valid_extensions):
            return False
        
        # Check file exists and is readable
        if not os.path.exists(file_path) or not os.access(file_path, os.R_OK):
            return False
        
        # Try to read first few packets
        if SCAPY_AVAILABLE:
            try:
                rdpcap(file_path, count=1)
                return True
            except (OSError, ValueError, ImportError):
                return False
        
        return False
    except Exception:
        return False
