"""PenPal #64 contract fix: the PCAP tool advertises max_packets but must
actually honor it (it was previously accepted into the InputSchema and
silently dropped before analyze_pcap_file was called).

Skips cleanly when scapy is not installed (pcap analysis is optional).
"""
import inspect
import pytest


def _scapy_available():
    try:
        import scapy.all  # noqa: F401
        return True
    except Exception:
        return False


class TestMaxPacketsContract:
    def test_analyzer_ctor_accepts_max_packets(self):
        # Signature-level contract: the parameter exists end to end.
        from app.utils.pcap_analyzer import PcapAnalyzer, analyze_pcap_file
        assert "max_packets" in inspect.signature(PcapAnalyzer.__init__).parameters
        assert "max_packets" in inspect.signature(analyze_pcap_file).parameters

    def test_tool_forwards_max_packets(self):
        # The tool's execute() must pass max_packets to analyze_pcap_file,
        # not drop it. Verified by source inspection (the call site) since a
        # full tool run needs scapy + a real capture.
        import app.mcp.tools.pcap_analysis as mod
        src = inspect.getsource(mod)
        assert "max_packets=input_data.max_packets" in src, \
            "tool no longer forwards its advertised max_packets parameter"

    @pytest.mark.skipif(not _scapy_available(), reason="scapy not installed")
    def test_bounded_read_caps_packet_count(self, tmp_path):
        from scapy.all import wrpcap, Ether, IP, UDP
        from app.utils.pcap_analyzer import PcapAnalyzer

        pcap = tmp_path / "t.pcap"
        pkts = [Ether()/IP(dst="10.0.0.1")/UDP()/(b"x" * 4) for _ in range(50)]
        wrpcap(str(pcap), pkts)

        capped = PcapAnalyzer(str(pcap), max_packets=10)
        assert len(capped.packets) == 10

        full = PcapAnalyzer(str(pcap))
        assert len(full.packets) == 50

    @pytest.mark.skipif(not _scapy_available(), reason="scapy not installed")
    def test_max_packets_none_reads_all(self, tmp_path):
        from scapy.all import wrpcap, Ether, IP, UDP
        from app.utils.pcap_analyzer import PcapAnalyzer
        pcap = tmp_path / "t.pcap"
        wrpcap(str(pcap), [Ether()/IP()/UDP() for _ in range(7)])
        assert len(PcapAnalyzer(str(pcap), max_packets=None).packets) == 7

    @pytest.mark.skipif(not _scapy_available(), reason="scapy not installed")
    def test_nonpositive_max_packets_treated_as_unbounded(self, tmp_path):
        from scapy.all import wrpcap, Ether, IP, UDP
        from app.utils.pcap_analyzer import PcapAnalyzer
        pcap = tmp_path / "t.pcap"
        wrpcap(str(pcap), [Ether()/IP()/UDP() for _ in range(5)])
        # 0 / negative must not silently truncate to empty — treated as None.
        assert len(PcapAnalyzer(str(pcap), max_packets=0).packets) == 5
