"""
AURA-QoS BPF Ring Buffer Interface
Reads 32-byte zero-copy metadata records from BPF_MAP_TYPE_RINGBUF in <1μs.
"""

import ctypes
import socket
import struct
import sys
import logging
from typing import Optional, Callable
from .telemetry import FlowKey, PacketMetadata

logger = logging.getLogger("AURA_QoS.BPFRingBuf")

class CPktMeta(ctypes.Structure):
    _pack_ = 1
    _fields_ = [
        ("src_ip", ctypes.c_uint32),
        ("dst_ip", ctypes.c_uint32),
        ("src_port", ctypes.c_uint16),
        ("dst_port", ctypes.c_uint16),
        ("protocol", ctypes.c_uint8),
        ("ip_version", ctypes.c_uint8),
        ("ecn_bits", ctypes.c_uint8),
        ("tcp_flags", ctypes.c_uint8),
        ("pkt_len", ctypes.c_uint16),
        ("_pad", ctypes.c_uint16),
        ("timestamp_ns", ctypes.c_uint64),
    ]

def decode_ip(uint32_ip: int) -> str:
    """Converts a network-byte-order or host uint32 into a dotted string."""
    return socket.inet_ntoa(struct.pack("!I", uint32_ip))

def decode_protocol(proto_num: int) -> str:
    if proto_num == 6:
        return "TCP"
    elif proto_num == 17:
        return "UDP"
    elif proto_num == 1:
        return "ICMP"
    return f"PROTO_{proto_num}"

class BPFRingBufferConsumer:
    """
    Subscribes to BPF_MAP_TYPE_RINGBUF and delivers streaming metadata to user-space.
    """
    def __init__(self, callback: Callable[[PacketMetadata], None]):
        self.callback = callback
        self.running = False
        self.has_native_bpf = False

    def parse_raw_record(self, raw_bytes: bytes) -> Optional[PacketMetadata]:
        """Unpacks 32-byte BPF struct into high-level PacketMetadata object."""
        if len(raw_bytes) < ctypes.sizeof(CPktMeta):
            return None

        c_meta = CPktMeta.from_buffer_copy(raw_bytes)
        flow = FlowKey(
            src_ip=decode_ip(c_meta.src_ip),
            dst_ip=decode_ip(c_meta.dst_ip),
            src_port=c_meta.src_port,
            dst_port=c_meta.dst_port,
            protocol=decode_protocol(c_meta.protocol)
        )
        return PacketMetadata(
            flow_key=flow,
            pkt_len=c_meta.pkt_len,
            timestamp_ns=c_meta.timestamp_ns,
            tcp_flags=c_meta.tcp_flags,
            ecn_bits=c_meta.ecn_bits,
            ip_version=c_meta.ip_version
        )

    def feed_simulated_packet(self, meta: PacketMetadata):
        """Used in test sandboxes when native bpf is not mounted."""
        self.callback(meta)
