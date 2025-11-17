"""
PCAP Analysis Utilities
 
Provides packet capture file reading and analysis capabilities
without requiring MCP protocol overhead.
"""
 
import os
from typing import Dict, List, Optional, Any, Tuple
from pathlib import Path
import logging
 
logger = logging.getLogger(__name__)

# Try to import scapy, but don't fail if it's not available
try:
    from scapy.all import (rdpcap, IP, IPv6, TCP, UDP, ICMP, 
                          ARP, DNS, DNSQR, DNSRR, GRE, Raw, Ether)
    from scapy.contrib.geneve import GENEVE
    from scapy.packet import Packet
    SCAPY_AVAILABLE = True
except ImportError:
    SCAPY_AVAILABLE = False
    logger.warning("Scapy not available - pcap analysis will be disabled")
 
 
class PcapAnalyzer:
    """Analyzer for packet capture files"""
    
    def __init__(self, pcap_path: str):
        """
        Initialize analyzer with a pcap file path
        
        Args:
            pcap_path: Path to the pcap file
        """
        if not SCAPY_AVAILABLE:
            raise ImportError("Scapy is not installed. Install with: pip install scapy")
        
        self.pcap_path = Path(pcap_path)
        if not self.pcap_path.exists():
            raise FileNotFoundError(f"PCAP file not found: {pcap_path}")
        
        self.packets = None
        self._load_packets()
    
    def _load_packets(self):
        """Load packets from the pcap file"""
        try:
            self.packets = rdpcap(str(self.pcap_path))
            logger.info(f"Loaded {len(self.packets)} packets from {self.pcap_path}")
        except Exception as e:
            logger.error(f"Failed to load pcap file: {e}")
            raise
    
    def get_summary(self) -> Dict[str, Any]:
        """
        Get a high-level summary of the pcap file
        
        Returns:
            Dictionary with summary statistics
        """
        if not self.packets:
            return {"error": "No packets loaded"}
        
        summary = {
            "total_packets": len(self.packets),
            "file_path": str(self.pcap_path),
            "file_size_bytes": self.pcap_path.stat().st_size,
            "protocols": self._count_protocols(),
            "unique_ips": self._count_unique_ips(),
            "time_range": self._get_time_range(),
            "top_talkers": self._get_top_talkers(limit=5),
            "tunneling_protocols": self._count_tunneling_protocols(),
            "ipv6_stats": self._get_ipv6_stats(),
            "dscp_distribution": self._get_dscp_distribution()
        }
        
        return summary
    
    def _count_tunneling_protocols(self) -> Dict[str, int]:
        """Count packets by tunneling protocol"""
        tunneling = {}
        
        for packet in self.packets:
            if GRE in packet:
                tunneling['GRE'] = tunneling.get('GRE', 0) + 1
            if GENEVE in packet:
                tunneling['GENEVE'] = tunneling.get('GENEVE', 0) + 1
            # VXLAN uses UDP port 4789
            if UDP in packet and packet[UDP].dport == 4789:
                tunneling['VXLAN'] = tunneling.get('VXLAN', 0) + 1
            # ERSPAN uses GRE protocol 0x88BE
            if GRE in packet and packet[GRE].proto == 0x88be:
                tunneling['ERSPAN'] = tunneling.get('ERSPAN', 0) + 1
        
        return tunneling
    
    def _count_protocols(self) -> Dict[str, int]:
        """Count packets by protocol"""
        protocols = {}
        
        for packet in self.packets:
            if IP in packet:
                proto = packet[IP].proto
                proto_name = {1: 'ICMP', 6: 'TCP', 17: 'UDP'}.get(proto, f'Other({proto})')
            elif ARP in packet:
                proto_name = 'ARP'
            elif IPv6 in packet:
                # IPv6 next header field
                nh = packet[IPv6].nh
                proto_name = {
                    1: 'ICMPv6',
                    6: 'TCP',
                    17: 'UDP',
                    43: 'IPv6-Route',
                    44: 'IPv6-Frag',
                    58: 'ICMPv6',
                    59: 'IPv6-NoNxt',
                    60: 'IPv6-Opts'
                }.get(nh, f'IPv6-Other({nh})')
            elif GRE in packet:
                proto_name = 'GRE'
            else:
                proto_name = 'Other'
            
            protocols[proto_name] = protocols.get(proto_name, 0) + 1
        
        return protocols
    
    def _count_unique_ips(self) -> Dict[str, int]:
        """Count unique source and destination IPs"""
        src_ips = set()
        dst_ips = set()
        
        for packet in self.packets:
            if IP in packet:
                src_ips.add(packet[IP].src)
                dst_ips.add(packet[IP].dst)
            if IPv6 in packet:
                src_ips.add(packet[IPv6].src)
                dst_ips.add(packet[IPv6].dst)
        
        return {
            "unique_sources": len(src_ips),
            "unique_destinations": len(dst_ips),
            "unique_total": len(src_ips | dst_ips)
        }
    
    def _get_ipv6_stats(self) -> Dict[str, Any]:
        """Get IPv6-specific statistics"""
        ipv6_count = 0
        extension_headers = {}
        hop_by_hop_count = 0
        dest_opts_count = 0
        
        for packet in self.packets:
            if IPv6 in packet:
                ipv6_count += 1
                
                # Check for extension headers
                # Hop-by-Hop Options (next header = 0)
                if packet[IPv6].nh == 0:
                    hop_by_hop_count += 1
                    extension_headers['HopByHop'] = extension_headers.get('HopByHop', 0) + 1
                
                # Destination Options (next header = 60)
                if packet[IPv6].nh == 60:
                    dest_opts_count += 1
                    extension_headers['DestOpts'] = extension_headers.get('DestOpts', 0) + 1
                
                # Routing Header (next header = 43)
                if packet[IPv6].nh == 43:
                    extension_headers['Routing'] = extension_headers.get('Routing', 0) + 1
                
                # Fragment Header (next header = 44)
                if packet[IPv6].nh == 44:
                    extension_headers['Fragment'] = extension_headers.get('Fragment', 0) + 1
        
        return {
            "ipv6_packets": ipv6_count,
            "extension_headers": extension_headers,
            "hop_by_hop_options": hop_by_hop_count,
            "destination_options": dest_opts_count
        }
    
    def _get_dscp_distribution(self) -> Dict[str, int]:
        """Get DSCP (Differentiated Services Code Point) distribution"""
        dscp_counts = {}
        
        for packet in self.packets:
            dscp_value = None
            
            if IP in packet:
                # DSCP is the upper 6 bits of the TOS field
                tos = packet[IP].tos
                dscp_value = (tos >> 2) & 0x3F
            elif IPv6 in packet:
                # DSCP is the upper 6 bits of the traffic class
                tc = packet[IPv6].tc
                dscp_value = (tc >> 2) & 0x3F
            
            if dscp_value is not None:
                # Common DSCP names
                dscp_name = {
                    0: 'BE (Best Effort)',
                    8: 'CS1',
                    10: 'AF11',
                    12: 'AF12',
                    14: 'AF13',
                    16: 'CS2',
                    18: 'AF21',
                    20: 'AF22',
                    22: 'AF23',
                    24: 'CS3',
                    26: 'AF31',
                    28: 'AF32',
                    30: 'AF33',
                    32: 'CS4',
                    34: 'AF41',
                    36: 'AF42',
                    38: 'AF43',
                    40: 'CS5',
                    46: 'EF (Expedited Forwarding)',
                    48: 'CS6',
                    56: 'CS7'
                }.get(dscp_value, f'DSCP-{dscp_value}')
                
                dscp_counts[dscp_name] = dscp_counts.get(dscp_name, 0) + 1
        
        # Sort by count
        return dict(sorted(dscp_counts.items(), key=lambda x: x[1], reverse=True))
    
    def _get_time_range(self) -> Optional[Dict[str, float]]:
        """Get the time range of captured packets"""
        if not self.packets:
            return None
        
        timestamps = [float(p.time) for p in self.packets if hasattr(p, 'time')]
        if not timestamps:
            return None
        
        return {
            "start": min(timestamps),
            "end": max(timestamps),
            "duration_seconds": max(timestamps) - min(timestamps)
        }
    
    def _get_top_talkers(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Get the most active IP addresses"""
        ip_stats = {}
        
        for packet in self.packets:
            if IP in packet:
                src = packet[IP].src
                dst = packet[IP].dst
                
            elif IPv6 in packet:
                src = packet[IPv6].src
                dst = packet[IPv6].dst
                
                if src not in ip_stats:
                    ip_stats[src] = {"sent": 0, "received": 0, "bytes_sent": 0, "bytes_received": 0}
                if dst not in ip_stats:
                    ip_stats[dst] = {"sent": 0, "received": 0, "bytes_sent": 0, "bytes_received": 0}
                
                packet_size = len(packet)
                ip_stats[src]["sent"] += 1
                ip_stats[src]["bytes_sent"] += packet_size
                ip_stats[dst]["received"] += 1
                ip_stats[dst]["bytes_received"] += packet_size
            else:
                continue
            
            if src not in ip_stats:
                ip_stats[src] = {"sent": 0, "received": 0, "bytes_sent": 0, "bytes_received": 0}
            if dst not in ip_stats:
                ip_stats[dst] = {"sent": 0, "received": 0, "bytes_sent": 0, "bytes_received": 0}
                packet_size = len(packet)
                ip_stats[src]["sent"] += 1
                ip_stats[src]["bytes_sent"] += packet_size
                ip_stats[dst]["received"] += 1
                ip_stats[dst]["bytes_received"] += packet_size
        
        # Sort by total packets
        sorted_ips = sorted(
            ip_stats.items(),
            key=lambda x: x[1]["sent"] + x[1]["received"],
            reverse=True
        )[:limit]
        
        return [
            {
                "ip": ip,
                "packets_sent": stats["sent"],
                "packets_received": stats["received"],
                "bytes_sent": stats["bytes_sent"],
                "bytes_received": stats["bytes_received"],
                "total_packets": stats["sent"] + stats["received"]
            }
            for ip, stats in sorted_ips
        ]
    
    def get_tcp_health_analysis(self) -> Dict[str, Any]:
        """
        Analyze TCP health metrics to identify communication issues.
        
        Returns:
            Dictionary with per-IP health metrics including:
            - Retransmissions (duplicate sequence numbers)
            - Reset packets
            - Zero window conditions
            - Out-of-order packets
            - Connection establishment issues
        """
        if not self.packets:
            return {"error": "No packets loaded"}
        
        # Track per-IP health metrics
        ip_health = {}
        
        # Track sequence numbers per flow to detect retransmissions
        flow_sequences = {}
        flow_acks = {}
        
        # Track connection attempts
        syn_packets = {}
        
        for i, packet in enumerate(self.packets):
            if TCP not in packet:
                continue
            
            # Get IP addresses
            src_ip = None
            dst_ip = None
            if IP in packet:
                src_ip = packet[IP].src
                dst_ip = packet[IP].dst
            elif IPv6 in packet:
                src_ip = packet[IPv6].src
                dst_ip = packet[IPv6].dst
            else:
                continue
            
            # Initialize health tracking for this IP
            if src_ip not in ip_health:
                ip_health[src_ip] = {
                    'total_packets': 0,
                    'retransmissions': 0,
                    'resets': 0,
                    'fins': 0,
                    'zero_windows': 0,
                    'out_of_order': 0,
                    'connection_attempts': 0,
                    'failed_connections': 0,
                    'duplicate_acks': 0,
                    'issues': []
                }
            
            ip_health[src_ip]['total_packets'] += 1
            
            # Get TCP details
            tcp_layer = packet[TCP]
            flags = str(tcp_layer.flags)
            seq = tcp_layer.seq
            ack = tcp_layer.ack
            window = tcp_layer.window if hasattr(tcp_layer, 'window') else None
            
            # Create flow identifier
            flow_id = (src_ip, tcp_layer.sport, dst_ip, tcp_layer.dport)
            
            # Initialize flow tracking
            if flow_id not in flow_sequences:
                flow_sequences[flow_id] = {'sequences': {}, 'last_seq': None, 'expected_seq': None}
                flow_acks[flow_id] = {'last_ack': None, 'ack_count': {}}
            
            flow_track = flow_sequences[flow_id]
            ack_track = flow_acks[flow_id]
            
            # Check for TCP flags indicating issues
            if 'R' in flags:  # Reset
                ip_health[src_ip]['resets'] += 1
                ip_health[src_ip]['issues'].append({
                    'type': 'reset',
                    'packet_index': i,
                    'timestamp': float(packet.time),
                    'dst_ip': dst_ip,
                    'port': f"{tcp_layer.sport}->{tcp_layer.dport}"
                })
            
            if 'F' in flags:  # Fin (normal close)
                ip_health[src_ip]['fins'] += 1
            
            # Track SYN packets for connection analysis
            if 'S' in flags and 'A' not in flags:  # SYN only
                ip_health[src_ip]['connection_attempts'] += 1
                syn_packets[flow_id] = {
                    'packet_index': i,
                    'timestamp': float(packet.time),
                    'responded': False
                }
            
            # Mark SYN as responded if we see SYN-ACK
            if 'S' in flags and 'A' in flags:  # SYN-ACK
                reverse_flow = (dst_ip, tcp_layer.dport, src_ip, tcp_layer.sport)
                if reverse_flow in syn_packets:
                    syn_packets[reverse_flow]['responded'] = True
            
            # Check for zero window (flow control issue)
            if window == 0:
                ip_health[src_ip]['zero_windows'] += 1
                ip_health[src_ip]['issues'].append({
                    'type': 'zero_window',
                    'packet_index': i,
                    'timestamp': float(packet.time),
                    'dst_ip': dst_ip,
                    'port': f"{tcp_layer.sport}->{tcp_layer.dport}"
                })
            
            # Detect retransmissions (same seq with payload seen before)
            payload_len = len(tcp_layer.payload) if hasattr(tcp_layer, 'payload') else 0
            
            if payload_len > 0:  # Only check packets with data
                if seq in flow_track['sequences']:
                    # This is a retransmission
                    ip_health[src_ip]['retransmissions'] += 1
                    ip_health[src_ip]['issues'].append({
                        'type': 'retransmission',
                        'packet_index': i,
                        'timestamp': float(packet.time),
                        'seq': seq,
                        'dst_ip': dst_ip,
                        'port': f"{tcp_layer.sport}->{tcp_layer.dport}"
                    })
                else:
                    flow_track['sequences'][seq] = i
                
                # Track expected sequence for out-of-order detection
                if flow_track['last_seq'] is not None:
                    expected_next = flow_track['last_seq'] + payload_len
                    if seq < expected_next and seq != flow_track['last_seq']:
                        # Out of order packet
                        ip_health[src_ip]['out_of_order'] += 1
                
                flow_track['last_seq'] = seq
            
            # Detect duplicate ACKs (sign of packet loss)
            if 'A' in flags and payload_len == 0:  # Pure ACK
                if ack == ack_track['last_ack']:
                    ack_count = ack_track['ack_count'].get(ack, 0) + 1
                    ack_track['ack_count'][ack] = ack_count
                    
                    if ack_count >= 3:  # 3 duplicate ACKs = fast retransmit trigger
                        ip_health[src_ip]['duplicate_acks'] += 1
                
                ack_track['last_ack'] = ack
        
        # Check for failed connection attempts
        for flow_id, syn_info in syn_packets.items():
            if not syn_info['responded']:
                src_ip = flow_id[0]
                if src_ip in ip_health:
                    ip_health[src_ip]['failed_connections'] += 1
                    ip_health[src_ip]['issues'].append({
                        'type': 'failed_connection',
                        'packet_index': syn_info['packet_index'],
                        'timestamp': syn_info['timestamp'],
                        'dst_ip': flow_id[2],
                        'port': f"{flow_id[1]}->{flow_id[3]}"
                    })
        
        # Calculate health scores and identify problematic IPs
        problematic_ips = []
        
        for ip, metrics in ip_health.items():
            # Calculate issue rate
            total_issues = (metrics['retransmissions'] + 
                          metrics['resets'] + 
                          metrics['zero_windows'] + 
                          metrics['failed_connections'])
            
            issue_rate = total_issues / metrics['total_packets'] if metrics['total_packets'] > 0 else 0
            metrics['issue_rate'] = round(issue_rate * 100, 2)  # Percentage
            
            # Mark as problematic if issue rate > 5% or has resets/failed connections
            if issue_rate > 0.05 or metrics['resets'] > 0 or metrics['failed_connections'] > 0:
                problematic_ips.append(ip)
                metrics['health_status'] = 'poor'
            elif issue_rate > 0.01:
                metrics['health_status'] = 'fair'
            else:
                metrics['health_status'] = 'good'
            
            # Sort issues by timestamp
            metrics['issues'].sort(key=lambda x: x['timestamp'])
            
            # Limit issues list to most recent 10
            if len(metrics['issues']) > 10:
                metrics['issues'] = metrics['issues'][-10:]
        
        return {
            'per_ip_health': ip_health,
            'problematic_ips': problematic_ips,
            'summary': {
                'total_ips_analyzed': len(ip_health),
                'ips_with_issues': len(problematic_ips),
                'total_retransmissions': sum(m['retransmissions'] for m in ip_health.values()),
                'total_resets': sum(m['resets'] for m in ip_health.values()),
                'total_zero_windows': sum(m['zero_windows'] for m in ip_health.values()),
                'total_failed_connections': sum(m['failed_connections'] for m in ip_health.values())
            }
        }
    
    def get_flow_statistics(self, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """
        Get detailed statistics for each network flow (conversation).
        Now properly handles IPv6 and includes timing analysis.
        
        Returns:
            List of flow dictionaries with detailed metrics
        """
        if not self.packets:
            return []
        
        flows = {}
        
        for packet in self.packets:
            # Get IP addresses and ports
            src_ip = None
            dst_ip = None
            src_port = None
            dst_port = None
            protocol = None
            
            # Extract IP layer
            if IP in packet:
                src_ip = packet[IP].src
                dst_ip = packet[IP].dst
            elif IPv6 in packet:
                src_ip = packet[IPv6].src
                dst_ip = packet[IPv6].dst
            
            # Extract transport layer
            if TCP in packet:
                src_port = packet[TCP].sport
                dst_port = packet[TCP].dport
                protocol = "TCP"
            elif UDP in packet:
                src_port = packet[UDP].sport
                dst_port = packet[UDP].dport
                protocol = "UDP"
            
            # Skip if we don't have complete 5-tuple
            if not (src_ip and dst_ip and src_port and dst_port and protocol):
                continue
            
            # Create bidirectional flow key
            flow_key = tuple(sorted([
                (src_ip, src_port),
                (dst_ip, dst_port)
            ]))
            
            # Initialize flow if new
            if flow_key not in flows:
                flows[flow_key] = {
                    'endpoints': [
                        {'ip': flow_key[0][0], 'port': flow_key[0][1]},
                        {'ip': flow_key[1][0], 'port': flow_key[1][1]}
                    ],
                    'protocol': protocol,
                    'packet_count': 0,
                    'bytes_sent': {flow_key[0][0]: 0, flow_key[1][0]: 0},
                    'packets_sent': {flow_key[0][0]: 0, flow_key[1][0]: 0},
                    'first_seen': float(packet.time),
                    'last_seen': float(packet.time),
                    'timestamps': []
                }
            
            flow = flows[flow_key]
            flow['packet_count'] += 1
            flow['last_seen'] = float(packet.time)
            flow['timestamps'].append(float(packet.time))
            
            # Track directional statistics
            flow['bytes_sent'][src_ip] += len(packet)
            flow['packets_sent'][src_ip] += 1
        
        # Calculate derived metrics for each flow
        flow_list = []
        for flow_key, flow in flows.items():
            duration = flow['last_seen'] - flow['first_seen']
            flow['duration_seconds'] = round(duration, 3)
            
            # Calculate inter-packet timing statistics
            if len(flow['timestamps']) > 1:
                intervals = [flow['timestamps'][i+1] - flow['timestamps'][i] 
                           for i in range(len(flow['timestamps'])-1)]
                flow['avg_interval_ms'] = round(sum(intervals) / len(intervals) * 1000, 2)
                flow['max_interval_ms'] = round(max(intervals) * 1000, 2)
                flow['min_interval_ms'] = round(min(intervals) * 1000, 2)
            else:
                flow['avg_interval_ms'] = 0
                flow['max_interval_ms'] = 0
                flow['min_interval_ms'] = 0
            
            # Calculate throughput
            if duration > 0:
                total_bytes = sum(flow['bytes_sent'].values())
                flow['throughput_bps'] = int(total_bytes * 8 / duration)
            else:
                flow['throughput_bps'] = 0
            
            # Remove raw timestamps to reduce output size
            del flow['timestamps']
            
            flow_list.append(flow)
        
        # Sort by packet count
        flow_list.sort(key=lambda x: x['packet_count'], reverse=True)
        
        if limit:
            flow_list = flow_list[:limit]
        
        return flow_list
    
    def get_connectivity_map(self) -> Dict[str, Any]:
        """
        Generate a connectivity map showing all IP-to-IP communications.
        Optimized for visualization and flow analysis.
        
        Returns:
            Dictionary with nodes (IPs) and edges (flows) suitable for graphing
        """
        if not self.packets:
            return {"error": "No packets loaded"}
        
        # Track all unique IPs
        nodes = {}
        
        # Track edges (flows between IPs)
        edges = {}
        
        for packet in self.packets:
            src_ip = None
            dst_ip = None
            
            # Get IP addresses
            if IP in packet:
                src_ip = packet[IP].src
                dst_ip = packet[IP].dst
            elif IPv6 in packet:
                src_ip = packet[IPv6].src
                dst_ip = packet[IPv6].dst
            else:
                continue
            
            # Track nodes
            if src_ip not in nodes:
                nodes[src_ip] = {
                    'ip': src_ip,
                    'packets_sent': 0,
                    'packets_received': 0,
                    'bytes_sent': 0,
                    'bytes_received': 0,
                    'connections': set()
                }
            
            if dst_ip not in nodes:
                nodes[dst_ip] = {
                    'ip': dst_ip,
                    'packets_sent': 0,
                    'packets_received': 0,
                    'bytes_sent': 0,
                    'bytes_received': 0,
                    'connections': set()
                }
            
            # Update node statistics
            packet_size = len(packet)
            nodes[src_ip]['packets_sent'] += 1
            nodes[src_ip]['bytes_sent'] += packet_size
            nodes[dst_ip]['packets_received'] += 1
            nodes[dst_ip]['bytes_received'] += packet_size
            nodes[src_ip]['connections'].add(dst_ip)
            
            # Track directional edge
            edge_key = (src_ip, dst_ip)
            
            if edge_key not in edges:
                edges[edge_key] = {
                    'source': src_ip,
                    'destination': dst_ip,
                    'packets': 0,
                    'bytes': 0,
                    'protocols': set(),
                    'ports': set()
                }
            
            edges[edge_key]['packets'] += 1
            edges[edge_key]['bytes'] += packet_size
            
            # Track protocols and ports
            if TCP in packet:
                edges[edge_key]['protocols'].add('TCP')
                edges[edge_key]['ports'].add(f"{packet[TCP].sport}->{packet[TCP].dport}")
            elif UDP in packet:
                edges[edge_key]['protocols'].add('UDP')
                edges[edge_key]['ports'].add(f"{packet[UDP].sport}->{packet[UDP].dport}")
        
        # Convert sets to lists for JSON serialization
        for node in nodes.values():
            node['connections'] = list(node['connections'])
        
        for edge in edges.values():
            edge['protocols'] = list(edge['protocols'])
            edge['ports'] = list(edge['ports'])[:5]  # Limit to top 5 ports
        
        # Calculate node importance (for sizing in visualizations)
        max_packets = max((n['packets_sent'] + n['packets_received']) for n in nodes.values()) if nodes else 1
        
        for node in nodes.values():
            total_packets = node['packets_sent'] + node['packets_received']
            node['importance'] = round(total_packets / max_packets, 3)
            node['role'] = self._classify_node_role(node)
        
        # Sort edges by packet count
        edge_list = sorted(edges.values(), key=lambda x: x['packets'], reverse=True)
        
        return {
            'nodes': list(nodes.values()),
            'edges': edge_list,
            'summary': {
                'total_nodes': len(nodes),
                'total_edges': len(edges),
                'total_packets': sum(e['packets'] for e in edges.values()),
                'total_bytes': sum(e['bytes'] for e in edges.values())
            }
        }
    
    def _classify_node_role(self, node: Dict[str, Any]) -> str:
        """Classify a node's role based on traffic patterns."""
        sent = node['packets_sent']
        received = node['packets_received']
        total = sent + received
        
        if total == 0:
            return 'inactive'
        
        send_ratio = sent / total
        
        if send_ratio > 0.7:
            return 'server'  # Mostly sending
        elif send_ratio < 0.3:
            return 'client'  # Mostly receiving
        else:
            return 'peer'    # Balanced traffic
    
    def get_flow_health_summary(self) -> Dict[str, Any]:
        """
        Combine flow statistics with TCP health analysis for a complete picture.
        
        Returns:
            Dictionary with flows annotated with health metrics
        """
        flows = self.get_flow_statistics()
        health = self.get_tcp_health_analysis()
        
        # Annotate flows with health information
        for flow in flows:
            ep1_ip = flow['endpoints'][0]['ip']
            ep2_ip = flow['endpoints'][1]['ip']
            
            flow['health'] = {}
            
            # Get health metrics for both endpoints
            if ep1_ip in health['per_ip_health']:
                flow['health'][ep1_ip] = {
                    'status': health['per_ip_health'][ep1_ip]['health_status'],
                    'retransmissions': health['per_ip_health'][ep1_ip]['retransmissions'],
                    'resets': health['per_ip_health'][ep1_ip]['resets'],
                    'zero_windows': health['per_ip_health'][ep1_ip]['zero_windows']
                }
            
            if ep2_ip in health['per_ip_health']:
                flow['health'][ep2_ip] = {
                    'status': health['per_ip_health'][ep2_ip]['health_status'],
                    'retransmissions': health['per_ip_health'][ep2_ip]['retransmissions'],
                    'resets': health['per_ip_health'][ep2_ip]['resets'],
                    'zero_windows': health['per_ip_health'][ep2_ip]['zero_windows']
                }
            
            # Determine overall flow health
            has_issues = any(
                ep.get('retransmissions', 0) > 0 or 
                ep.get('resets', 0) > 0 or 
                ep.get('zero_windows', 0) > 0
                for ep in flow['health'].values()
            )
            
            flow['flow_status'] = 'unhealthy' if has_issues else 'healthy'
        
        return {
            'flows': flows,
            'health_summary': health['summary'],
            'problematic_ips': health['problematic_ips']
        }
    
    def get_tunneling_info(self, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """
        Extract detailed tunneling protocol information.
        
        Returns:
            List of packets using tunneling protocols with details
        """
        tunneling_packets = []
        
        for i, packet in enumerate(self.packets):
            tunnel_info = None
            
            if GRE in packet:
                tunnel_info = {
                    'packet_index': i,
                    'timestamp': float(packet.time),
                    'protocol': 'GRE',
                    'gre_protocol': packet[GRE].proto if hasattr(packet[GRE], 'proto') else None
                }
                
                if IP in packet:
                    tunnel_info['outer_src'] = packet[IP].src
                    tunnel_info['outer_dst'] = packet[IP].dst
            
            elif GENEVE in packet:
                tunnel_info = {
                    'packet_index': i,
                    'timestamp': float(packet.time),
                    'protocol': 'GENEVE',
                    'vni': packet[GENEVE].vni if hasattr(packet[GENEVE], 'vni') else None
                }
            
            elif UDP in packet and packet[UDP].dport == 4789:  # VXLAN
                tunnel_info = {
                    'packet_index': i,
                    'timestamp': float(packet.time),
                    'protocol': 'VXLAN',
                    'udp_port': 4789
                }
            
            if tunnel_info:
                tunneling_packets.append(tunnel_info)
                
                if limit and len(tunneling_packets) >= limit:
                    break
        
        return tunneling_packets
    
    def get_ipv6_extension_headers(self, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """
        Extract IPv6 extension header information.
        
        Returns:
            List of packets with IPv6 extension headers
        """
        extension_packets = []
        
        for i, packet in enumerate(self.packets):
            if IPv6 not in packet:
                continue
            
            extensions = []
            next_header = packet[IPv6].nh
            
            # Check for various extension headers
            if next_header == 0:
                extensions.append('Hop-by-Hop Options')
            if next_header == 43:
                extensions.append('Routing Header')
            if next_header == 44:
                extensions.append('Fragment Header')
            if next_header == 60:
                extensions.append('Destination Options')
            
            if extensions:
                extension_packets.append({
                    'packet_index': i,
                    'timestamp': float(packet.time),
                    'src_ip': packet[IPv6].src,
                    'dst_ip': packet[IPv6].dst,
                    'extensions': extensions,
                    'next_header': next_header
                })
                
                if limit and len(extension_packets) >= limit:
                    break
        
        return extension_packets
    
    def get_tls_info(self, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """
        Extract TLS/SSL connection information.
        
        Returns:
            List of packets that appear to be TLS traffic
        """
        tls_packets = []
        
        # Common TLS ports
        tls_ports = {443, 8443, 4443}
        
        for i, packet in enumerate(self.packets):
            if TCP not in packet:
                continue
            
            src_port = packet[TCP].sport
            dst_port = packet[TCP].dport
            
            # Check if this is likely TLS traffic
            is_tls_port = src_port in tls_ports or dst_port in tls_ports
            
            # Check for TLS handshake signatures in payload
            if hasattr(packet[TCP], 'payload') and len(packet[TCP].payload) > 0:
                payload = bytes(packet[TCP].payload)
                # TLS handshake starts with 0x16 (handshake) or 0x17 (application data)
                is_tls_handshake = len(payload) > 0 and payload[0] in [0x16, 0x17]
            else:
                is_tls_handshake = False
            
            if is_tls_port or is_tls_handshake:
                info = {
                    'packet_index': i,
                    'timestamp': float(packet.time),
                    'src_port': src_port,
                    'dst_port': dst_port,
                    'is_tls_port': is_tls_port,
                    'is_tls_handshake': is_tls_handshake
                }
                
                if IP in packet:
                    info['src_ip'] = packet[IP].src
                    info['dst_ip'] = packet[IP].dst
                elif IPv6 in packet:
                    info['src_ip'] = packet[IPv6].src
                    info['dst_ip'] = packet[IPv6].dst
                
                tls_packets.append(info)
                
                if limit and len(tls_packets) >= limit:
                    break
        
        return tls_packets
    
    def get_conversations(self, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """
        Extract TCP/UDP conversations from the capture
        
        Args:
            limit: Maximum number of conversations to return
        
        Returns:
            List of conversation dictionaries
        """
        conversations = {}
        
        for packet in self.packets:
            if IP in packet and (TCP in packet or UDP in packet):
                src_ip = packet[IP].src
                dst_ip = packet[IP].dst
            elif IPv6 in packet and (TCP in packet or UDP in packet):
                src_ip = packet[IPv6].src
                dst_ip = packet[IPv6].dst
            else:
                continue
                
                if TCP in packet:
                    src_port = packet[TCP].sport
                    dst_port = packet[TCP].dport
                    proto = "TCP"
                else:  # UDP
                    src_port = packet[UDP].sport
                    dst_port = packet[UDP].dport
                    proto = "UDP"
                
                # Create conversation key (bidirectional)
                conv_key = tuple(sorted([
                    (src_ip, src_port),
                    (dst_ip, dst_port)
                ]))
                
                if conv_key not in conversations:
                    conversations[conv_key] = {
                        "endpoints": [
                            {"ip": conv_key[0][0], "port": conv_key[0][1]},
                            {"ip": conv_key[1][0], "port": conv_key[1][1]}
                        ],
                        "protocol": proto,
                        "packet_count": 0,
                        "total_bytes": 0,
                        "first_seen": float(packet.time),
                        "last_seen": float(packet.time)
                    }
                
                conv = conversations[conv_key]
                conv["packet_count"] += 1
                conv["total_bytes"] += len(packet)
                conv["last_seen"] = float(packet.time)
        
        # Convert to list and sort by packet count
        conv_list = sorted(
            conversations.values(),
            key=lambda x: x["packet_count"],
            reverse=True
        )
        
        if limit:
            conv_list = conv_list[:limit]
        
        return conv_list
    
    def search_packets_advanced(
        self,
        src_ip: Optional[str] = None,
        dst_ip: Optional[str] = None,
        protocol: Optional[str] = None,
        port: Optional[int] = None,
        tcp_flags: Optional[str] = None,
        min_size: Optional[int] = None,
        max_size: Optional[int] = None,
        limit: Optional[int] = 100
    ) -> List[Dict[str, Any]]:
        """
        Advanced packet filtering with multiple criteria.
        
        Args:
            src_ip: Source IP filter
            dst_ip: Destination IP filter
            protocol: Protocol filter (TCP, UDP, ICMP)
            port: Port number filter
            tcp_flags: TCP flags to match (e.g., 'R', 'S', 'F', 'PA')
            min_size: Minimum packet size
            max_size: Maximum packet size
            limit: Maximum results
        
        Returns:
            List of matching packets
        """
        matches = []
        
        for packet in self.packets:
            # Apply IP filters
            if IP in packet or IPv6 in packet:
                pkt_src = packet[IP].src if IP in packet else packet[IPv6].src
                pkt_dst = packet[IP].dst if IP in packet else packet[IPv6].dst
                
                if src_ip and pkt_src != src_ip:
                    continue
                if dst_ip and pkt_dst != dst_ip:
                    continue
            
            # Apply protocol filter
            if protocol:
                proto_upper = protocol.upper()
                if proto_upper == 'TCP' and TCP not in packet:
                    continue
                if proto_upper == 'UDP' and UDP not in packet:
                    continue
                if proto_upper == 'ICMP' and ICMP not in packet:
                    continue
            
            # Apply TCP flags filter
            if tcp_flags and TCP in packet:
                if tcp_flags not in str(packet[TCP].flags):
                    continue
            
            # Apply port filter
            if port:
                has_port = False
                if TCP in packet and (packet[TCP].sport == port or packet[TCP].dport == port):
                    has_port = True
                if UDP in packet and (packet[UDP].sport == port or packet[UDP].dport == port):
                    has_port = True
                if not has_port:
                    continue
            
            # Apply size filters
            pkt_size = len(packet)
            if min_size and pkt_size < min_size:
                continue
            if max_size and pkt_size > max_size:
                continue
            
            matches.append(self._packet_to_dict(packet))
            
            if limit and len(matches) >= limit:
                break
        
        return matches
    
    def get_dns_queries(self, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """
        Extract DNS queries from the capture
        
        Args:
            limit: Maximum number of queries to return
        
        Returns:
            List of DNS query dictionaries
        """
        dns_queries = []
        
        for packet in self.packets:
            if DNS in packet and packet[DNS].qr == 0:  # Query (not response)
                if DNSQR in packet:
                    query = {
                        "query_name": packet[DNSQR].qname.decode('utf-8', errors='ignore'),
                        "query_type": packet[DNSQR].qtype,
                        "timestamp": float(packet.time)
                    }
                    
                    if IP in packet:
                        query["source_ip"] = packet[IP].src
                        query["dest_ip"] = packet[IP].dst
                    
                    dns_queries.append(query)
        
        if limit:
            dns_queries = dns_queries[:limit]
        
        return dns_queries
    
    def get_dns_responses(self, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """
        Extract DNS responses from the capture
        
        Args:
            limit: Maximum number of responses to return
        
        Returns:
            List of DNS response dictionaries
        """
        dns_responses = []
        
        for packet in self.packets:
            if DNS in packet and packet[DNS].qr == 1:  # Response
                response = {
                    "timestamp": float(packet.time),
                    "answers": []
                }
                
                if DNSQR in packet:
                    response["query_name"] = packet[DNSQR].qname.decode('utf-8', errors='ignore')
                
                if IP in packet:
                    response["source_ip"] = packet[IP].src
                    response["dest_ip"] = packet[IP].dst
                
                # Extract answers
                if packet[DNS].an:
                    for i in range(packet[DNS].ancount):
                        answer = packet[DNS].an[i]
                        if hasattr(answer, 'rdata'):
                            response["answers"].append({
                                "name": answer.rrname.decode('utf-8', errors='ignore'),
                                "type": answer.type,
                                "data": str(answer.rdata)
                            })
                
                dns_responses.append(response)
        
        if limit:
            dns_responses = dns_responses[:limit]
        
        return dns_responses
    
    def filter_packets(
        self,
        src_ip: Optional[str] = None,
        dst_ip: Optional[str] = None,
        protocol: Optional[str] = None,
        port: Optional[int] = None,
        limit: Optional[int] = 100
    ) -> List[Dict[str, Any]]:
        """
        Filter packets based on criteria
        
        Args:
            src_ip: Source IP address
            dst_ip: Destination IP address
            protocol: Protocol name (TCP, UDP, ICMP, etc.)
            port: Port number (source or destination)
            limit: Maximum number of packets to return
        
        Returns:
            List of filtered packet summaries
        """
        filtered = []
        
        for packet in self.packets:
            # Check IP filter
            if IP in packet:
                if src_ip and packet[IP].src != src_ip:
                    continue
                if dst_ip and packet[IP].dst != dst_ip:
                    continue
            
            # Check protocol filter
            if protocol:
                proto_upper = protocol.upper()
                if proto_upper == 'TCP' and TCP not in packet:
                    continue
                if proto_upper == 'UDP' and UDP not in packet:
                    continue
                if proto_upper == 'ICMP' and ICMP not in packet:
                    continue
            
            # Check port filter
            if port:
                has_port = False
                if TCP in packet and (packet[TCP].sport == port or packet[TCP].dport == port):
                    has_port = True
                if UDP in packet and (packet[UDP].sport == port or packet[UDP].dport == port):
                    has_port = True
                if not has_port:
                    continue
            
            # Add to filtered list
            filtered.append(self._packet_to_dict(packet))
            
            if limit and len(filtered) >= limit:
                break
        
        return filtered
    
    def _packet_to_dict(self, packet: 'Packet') -> Dict[str, Any]:
        """Convert a packet to a dictionary representation"""
        info = {
            "timestamp": float(packet.time),
            "length": len(packet),
            "layers": []
        }
        
        # Extract IP layer info
        if IP in packet:
            info["ip"] = {
                "src": packet[IP].src,
                "dst": packet[IP].dst,
                "protocol": packet[IP].proto
            }
            info["layers"].append("IP")
        
        # Extract TCP layer info
        if TCP in packet:
            info["tcp"] = {
                "src_port": packet[TCP].sport,
                "dst_port": packet[TCP].dport,
                "flags": str(packet[TCP].flags),
                "seq": packet[TCP].seq,
                "ack": packet[TCP].ack
            }
            info["layers"].append("TCP")
        
        # Extract UDP layer info
        if UDP in packet:
            info["udp"] = {
                "src_port": packet[UDP].sport,
                "dst_port": packet[UDP].dport,
                "length": packet[UDP].len
            }
            info["layers"].append("UDP")
        
        # Extract ICMP layer info
        if ICMP in packet:
            info["icmp"] = {
                "type": packet[ICMP].type,
                "code": packet[ICMP].code
            }
            info["layers"].append("ICMP")
        
        # Extract DNS layer info
        if DNS in packet:
            info["dns"] = {
                "is_query": packet[DNS].qr == 0,
                "questions": [],
                "answers": []
            }
            
            if DNSQR in packet:
                info["dns"]["questions"].append({
                    "name": packet[DNSQR].qname.decode('utf-8', errors='ignore'),
                    "type": packet[DNSQR].qtype
                })
            
            info["layers"].append("DNS")
        
        # Extract ARP layer info
        if ARP in packet:
            info["arp"] = {
                "operation": packet[ARP].op,
                "src_ip": packet[ARP].psrc,
                "dst_ip": packet[ARP].pdst,
                "src_mac": packet[ARP].hwsrc,
                "dst_mac": packet[ARP].hwdst
            }
            info["layers"].append("ARP")
        
        return info
    
    def search_packets(
        self,
        pattern: str,
        limit: Optional[int] = 100
    ) -> List[Dict[str, Any]]:
        """
        Search for packets containing a specific pattern in payload
        
        Args:
            pattern: String pattern to search for
            limit: Maximum number of results
        
        Returns:
            List of matching packet summaries
        """
        matches = []
        pattern_bytes = pattern.encode('utf-8', errors='ignore')
        
        for packet in self.packets:
            # Get raw payload
            if hasattr(packet, 'load'):
                if pattern_bytes in bytes(packet.load):
                    matches.append(self._packet_to_dict(packet))
                    
                    if limit and len(matches) >= limit:
                        break
        
        return matches
    
    def get_http_requests(self, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """
        Extract HTTP requests from the capture
        
        Args:
            limit: Maximum number of requests to return
        
        Returns:
            List of HTTP request dictionaries
        """
        http_requests = []
        
        for packet in self.packets:
            if TCP in packet and hasattr(packet, 'load'):
                payload = bytes(packet.load)
                
                # Check if this looks like an HTTP request
                if payload.startswith(b'GET ') or payload.startswith(b'POST ') or \
                   payload.startswith(b'PUT ') or payload.startswith(b'DELETE '):
                    
                    try:
                        # Try to parse the HTTP request
                        payload_str = payload.decode('utf-8', errors='ignore')
                        lines = payload_str.split('\r\n')
                        
                        if lines:
                            request_line = lines[0].split(' ')
                            if len(request_line) >= 2:
                                request = {
                                    "method": request_line[0],
                                    "path": request_line[1],
                                    "timestamp": float(packet.time),
                                    "headers": {}
                                }
                                
                                if IP in packet:
                                    request["src_ip"] = packet[IP].src
                                    request["dst_ip"] = packet[IP].dst
                                elif IPv6 in packet:
                                    request["src_ip"] = packet[IPv6].src
                                    request["dst_ip"] = packet[IPv6].dst
                                
                                if TCP in packet:
                                    request["src_port"] = packet[TCP].sport
                                    request["dst_port"] = packet[TCP].dport
                                
                                # Parse headers
                                for line in lines[1:]:
                                    if ':' in line:
                                        key, value = line.split(':', 1)
                                        request["headers"][key.strip()] = value.strip()
                                
                                http_requests.append(request)
                    except Exception as e:
                        logger.debug(f"Failed to parse HTTP packet: {e}")
                        continue
                    
                    if limit and len(http_requests) >= limit:
                        break
        
        return http_requests
    
    def get_icmp_packets(
        self,
        icmp_type: Optional[int] = None,
        limit: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """
        Extract ICMP/ICMPv6 packets
        
        Args:
            icmp_type: Specific ICMP type to filter (e.g., 8 for echo request)
            limit: Maximum number of packets to return
        
        Returns:
            List of ICMP packet information
        """
        icmp_packets = []
        
        for i, packet in enumerate(self.packets):
            icmp_info = None
            
            # ICMPv4
            if ICMP in packet:
                if icmp_type is None or packet[ICMP].type == icmp_type:
                    icmp_info = {
                        "packet_index": i,
                        "timestamp": float(packet.time),
                        "version": "ICMPv4",
                        "type": packet[ICMP].type,
                        "code": packet[ICMP].code,
                        "type_name": {
                            0: "Echo Reply",
                            3: "Destination Unreachable",
                            8: "Echo Request",
                            11: "Time Exceeded",
                            12: "Parameter Problem"
                        }.get(packet[ICMP].type, f"Type {packet[ICMP].type}")
                    }
                    
                    if IP in packet:
                        icmp_info["src_ip"] = packet[IP].src
                        icmp_info["dst_ip"] = packet[IP].dst
            
            # ICMPv6
            elif IPv6 in packet and packet[IPv6].nh == 58:
                # ICMPv6 is next header 58
                if Raw in packet:
                    payload = bytes(packet[Raw].load)
                    if len(payload) >= 2:
                        icmpv6_type = payload[0]
                        icmpv6_code = payload[1]
                        
                        if icmp_type is None or icmpv6_type == icmp_type:
                            icmp_info = {
                                "packet_index": i,
                                "timestamp": float(packet.time),
                                "version": "ICMPv6",
                                "type": icmpv6_type,
                                "code": icmpv6_code,
                                "src_ip": packet[IPv6].src,
                                "dst_ip": packet[IPv6].dst
                            }
            
            if icmp_info:
                icmp_packets.append(icmp_info)
                
                if limit and len(icmp_packets) >= limit:
                    break
        
        return icmp_packets
    
    def get_packet_details(self, packet_index: int) -> Optional[Dict[str, Any]]:
        """
        Get detailed information about a specific packet
        
        Args:
            packet_index: Index of the packet (0-based)
        
        Returns:
            Detailed packet information or None if index is invalid
        """
        if not self.packets or packet_index < 0 or packet_index >= len(self.packets):
            return None
        
        packet = self.packets[packet_index]
        details = self._packet_to_dict(packet)
        
        # Add DSCP information
        if "ip" in details:
            tos = details["ip"].get("tos", 0)
            dscp = (tos >> 2) & 0x3F
            ecn = tos & 0x03
            details["ip"]["dscp"] = dscp
            details["ip"]["dscp_name"] = self._get_dscp_name(dscp)
            details["ip"]["ecn"] = ecn
        
        # Add IPv6 specific details
        if "ipv6" in details:
            tc = details["ipv6"].get("tc", 0)
            dscp = (tc >> 2) & 0x3F
            ecn = tc & 0x03
            details["ipv6"]["dscp"] = dscp
            details["ipv6"]["dscp_name"] = self._get_dscp_name(dscp)
            details["ipv6"]["ecn"] = ecn
        
        # Add hex dump of payload if available
        if hasattr(packet, 'load'):
            payload = bytes(packet.load)
            details["payload_hex"] = payload.hex()
            details["payload_ascii"] = payload.decode('utf-8', errors='ignore')
            details["payload_length"] = len(payload)
        
        # Add tunneling info if present
        if GRE in packet or GENEVE in packet:
            details["is_tunneled"] = True
            tunnel_info = self.get_tunneling_info(limit=None)
            matching = [t for t in tunnel_info if t["packet_index"] == packet_index]
            if matching:
                details["tunnel_info"] = matching[0]
        
        # Add full packet summary
        details["packet_summary"] = packet.summary()
        
        return details
    
    def _get_dscp_name(self, dscp_value: int) -> str:
        """Get human-readable name for DSCP value"""
        dscp_names = {
            0: 'BE (Best Effort)',
            8: 'CS1', 10: 'AF11', 12: 'AF12', 14: 'AF13',
            16: 'CS2', 18: 'AF21', 20: 'AF22', 22: 'AF23',
            24: 'CS3', 26: 'AF31', 28: 'AF32', 30: 'AF33',
            32: 'CS4', 34: 'AF41', 36: 'AF42', 38: 'AF43',
            40: 'CS5', 46: 'EF (Expedited Forwarding)',
            48: 'CS6', 56: 'CS7'
        }
        return dscp_names.get(dscp_value, f'DSCP-{dscp_value}')
 
 
def analyze_pcap_file(
    file_path: str,
    operation: str = "summary",
    **kwargs
) -> Dict[str, Any]:
    """
    Main entry point for pcap analysis
    
    Args:
        file_path: Path to the pcap file
        operation: Type of analysis to perform
            - "summary": Get overall statistics
            - "conversations": Extract TCP/UDP conversations
            - "dns_queries": Extract DNS queries
            - "dns_responses": Extract DNS responses
            - "filter": Filter packets by criteria
            - "search": Search for pattern in payloads
            - "tcp_health": Analyze TCP health metrics and errors
            - "flow_stats": Get detailed flow statistics
            - "connectivity_map": Get connectivity map for visualization
            - "flow_health": Combined flow statistics with health metrics
            - "search_advanced": Advanced packet filtering
            - "http": Extract HTTP requests
            - "packet_details": Get details for specific packet
        **kwargs: Additional arguments based on operation
    
    Returns:
        Analysis results dictionary
    """
    if not SCAPY_AVAILABLE:
        return {
            "error": "Scapy not installed",
            "message": "Install scapy with: pip install scapy"
        }
    
    try:
        analyzer = PcapAnalyzer(file_path)
        
        if operation == "summary":
            return analyzer.get_summary()
        
        elif operation == "conversations":
            limit = kwargs.get("limit")
            return {
                "conversations": analyzer.get_conversations(limit=limit),
                "total_conversations": len(analyzer.get_conversations())
            }
        
        elif operation == "dns_queries":
            limit = kwargs.get("limit")
            return {
                "queries": analyzer.get_dns_queries(limit=limit)
            }
        
        elif operation == "dns_responses":
            limit = kwargs.get("limit")
            return {
                "responses": analyzer.get_dns_responses(limit=limit)
            }
        
        elif operation == "filter":
            return {
                "filtered_packets": analyzer.filter_packets(
                    src_ip=kwargs.get("src_ip"),
                    dst_ip=kwargs.get("dst_ip"),
                    protocol=kwargs.get("protocol"),
                    port=kwargs.get("port"),
                    limit=kwargs.get("limit", 100)
                )
            }
        
        elif operation == "tcp_health":
            return analyzer.get_tcp_health_analysis()
        
        elif operation == "flow_stats":
            limit = kwargs.get("limit")
            return {
                "flows": analyzer.get_flow_statistics(limit=limit)
            }
        
        elif operation == "connectivity_map":
            return analyzer.get_connectivity_map()
        
        elif operation == "flow_health":
            return analyzer.get_flow_health_summary()
        
        elif operation == "search_advanced":
            return {
                "matches": analyzer.search_packets_advanced(
                    src_ip=kwargs.get("src_ip"),
                    dst_ip=kwargs.get("dst_ip"),
                    protocol=kwargs.get("protocol"),
                    port=kwargs.get("port"),
                    tcp_flags=kwargs.get("tcp_flags"),
                    min_size=kwargs.get("min_size"),
                    max_size=kwargs.get("max_size"),
                    limit=kwargs.get("limit", 100)
                )
            }
        
        elif operation == "search":
            pattern = kwargs.get("pattern")
            if not pattern:
                return {"error": "Pattern is required for search operation"}
            
            return {
                "matches": analyzer.search_packets(
                    pattern=pattern,
                    limit=kwargs.get("limit", 100)
                )
            }
        
        elif operation == "http":
            limit = kwargs.get("limit")
            return {
                "http_requests": analyzer.get_http_requests(limit=limit)
            }
        
        elif operation == "tunneling":
            limit = kwargs.get("limit")
            return {
                "tunneling_packets": analyzer.get_tunneling_info(limit=limit)
            }
        
        elif operation == "ipv6_extensions":
            limit = kwargs.get("limit")
            return {
                "ipv6_extension_headers": analyzer.get_ipv6_extension_headers(limit=limit)
            }
        
        elif operation == "tls":
            limit = kwargs.get("limit")
            return {
                "tls_packets": analyzer.get_tls_info(limit=limit)
            }
        
        elif operation == "icmp":
            icmp_type = kwargs.get("icmp_type")
            limit = kwargs.get("limit")
            return {
                "icmp_packets": analyzer.get_icmp_packets(
                    icmp_type=icmp_type,
                    limit=limit
                )
            }
        
        elif operation == "packet_details":
            packet_index = kwargs.get("packet_index")
            if packet_index is None:
                return {"error": "packet_index is required for packet_details operation"}
            
            details = analyzer.get_packet_details(packet_index)
            if details:
                return details
            else:
                return {"error": f"Invalid packet index: {packet_index}"}
        
        else:
            return {"error": f"Unknown operation: {operation}"}
    
    except FileNotFoundError as e:
        return {"error": "file_not_found", "message": str(e)}
    except ImportError as e:
        return {"error": "import_error", "message": str(e)}
    except Exception as e:
        logger.error(f"Error analyzing pcap: {e}", exc_info=True)
        return {"error": "analysis_failed", "message": str(e)}
 
 
def is_pcap_supported() -> bool:
    """Check if pcap analysis is supported (scapy installed)"""
    return SCAPY_AVAILABLE
