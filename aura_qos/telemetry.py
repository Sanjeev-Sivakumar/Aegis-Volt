"""
AURA-QoS Telemetry Ingestion Layer
Payload-blind, zero-copy metadata extraction supporting IPv4 and IPv6.
"""

import time
from dataclasses import dataclass
from typing import Tuple, Optional, Dict

@dataclass(frozen=True)
class FlowKey:
    src_ip: str
    dst_ip: str
    src_port: int
    dst_port: int
    protocol: str  # "TCP", "UDP", "ICMP", "OTHER"

    def canonical(self) -> Tuple[str, str, int, int, str]:
        """Provides an order-independent bi-directional flow key."""
        endpoints = sorted([(self.src_ip, self.src_port), (self.dst_ip, self.dst_port)])
        return (endpoints[0][0], endpoints[1][0], endpoints[0][1], endpoints[1][1], self.protocol)


@dataclass
class PacketMetadata:
    flow_key: FlowKey
    pkt_len: int             # Frame length in bytes (Layer 3/4 header + payload)
    timestamp_ns: int        # Hardware or kernel timestamp in nanoseconds
    tcp_flags: Optional[int] = 0
    ecn_bits: int = 0        # RFC 3168 ECN bits (00=Not-ECT, 01/10=ECT, 11=CE)
    ip_version: int = 4      # 4 for IPv4, 6 for IPv6


class FlowTracker:
    """
    Maintains low-overhead sliding window flow telemetry.
    Memory footprint bounded strictly to O(Active_Flows).
    """
    def __init__(self, max_idle_sec: float = 30.0):
        self.flows: Dict[Tuple, Dict] = {}
        self.max_idle_sec = max_idle_sec

    def ingest_packet(self, meta: PacketMetadata) -> Tuple[float, int, int]:
        """
        Processes a packet metadata record.
        Returns:
            delta_t_ms: Inter-arrival time since last packet in this flow (milliseconds)
            pkt_len: Packet length in bytes
            flow_packet_count: Sequential sequence number for this flow
        """
        canon_key = meta.flow_key.canonical()
        now_ns = meta.timestamp_ns

        if canon_key not in self.flows:
            self.flows[canon_key] = {
                "last_timestamp_ns": now_ns,
                "first_seen_ns": now_ns,
                "packet_count": 1,
                "byte_count": meta.pkt_len,
                "last_active_wall": time.time()
            }
            delta_t_ms = 0.0
            pkt_count = 1
        else:
            flow_state = self.flows[canon_key]
            delta_t_ms = (now_ns - flow_state["last_timestamp_ns"]) / 1_000_000.0
            # Sanity clamp to avoid extreme outliers from idle pauses
            delta_t_ms = max(0.001, min(delta_t_ms, 5000.0))
            
            flow_state["last_timestamp_ns"] = now_ns
            flow_state["packet_count"] += 1
            flow_state["byte_count"] += meta.pkt_len
            flow_state["last_active_wall"] = time.time()
            pkt_count = flow_state["packet_count"]

        return delta_t_ms, meta.pkt_len, pkt_count

    def purge_idle_flows(self) -> int:
        """Removes expired flows to maintain strictly bounded memory."""
        now = time.time()
        expired = [k for k, v in self.flows.items() if now - v["last_active_wall"] > self.max_idle_sec]
        for k in expired:
            del self.flows[k]
        return len(expired)
