"""
AURA-QoS Multi-Flow Traffic Generator
Generates realistic broadband residential traffic patterns:
  1. Interactive Voice/Gaming: 20ms isochronous Opus audio frames (~180 bytes)
  2. Adaptive 4K Video: Periodic DASH/HLS chunk bursts (~1400 bytes)
  3. Bulk ISO Download: MTU-saturated back-to-back TCP stream (~1500 bytes)
"""

import time
import random
from typing import Generator, Dict, Any
from aura_qos.telemetry import FlowKey, PacketMetadata

class RealisticTrafficGenerator:
    """
    Simulates concurrent mixed home broadband traffic lines.
    """
    def __init__(self):
        # Flow 1: Zoom Work Conference Call (UDP)
        self.flow_zoom = FlowKey(
            src_ip="192.168.1.10", dst_ip="203.0.113.10",
            src_port=5004, dst_port=5004, protocol="UDP"
        )
        # Flow 2: 4K Smart TV Stream (TCP HTTPS)
        self.flow_video = FlowKey(
            src_ip="192.168.1.20", dst_ip="203.0.113.20",
            src_port=53210, dst_port=443, protocol="TCP"
        )
        # Flow 3: Saturated Torrent ISO Download (TCP)
        self.flow_iso = FlowKey(
            src_ip="192.168.1.30", dst_ip="203.0.113.30",
            src_port=58900, dst_port=6881, protocol="TCP"
        )

        self.last_zoom_t_ns = 0
        self.last_video_chunk_time = time.time()
        self.video_chunk_active = False

    def generate_packet_stream(
        self,
        duration_sec: float = 10.0,
        enable_iso: bool = True,
        enable_video: bool = True
    ) -> Generator[PacketMetadata, None, None]:
        """
        Yields sequential packet metadata events interleaving all active flows.
        """
        start_wall = time.time()
        curr_t_ns = int(start_wall * 1_000_000_000)
        next_zoom_ns = curr_t_ns

        while (time.time() - start_wall) < duration_sec:
            now_wall = time.time()
            curr_t_ns = int(now_wall * 1_000_000_000)

            # 1. Zoom Call: Fixed 20ms cadence (50 packets per second)
            if curr_t_ns >= next_zoom_ns:
                # 160-220 bytes with microsecond jitter
                jitter_ns = random.randint(-500_000, 500_000)
                pkt_len = random.randint(160, 220)
                yield PacketMetadata(
                    flow_key=self.flow_zoom,
                    pkt_len=pkt_len,
                    timestamp_ns=curr_t_ns + jitter_ns,
                    ip_version=4
                )
                next_zoom_ns = curr_t_ns + 20_000_000 # 20ms interval

            # 2. 4K Video Stream: 2.0s burst-pause cycles
            if enable_video:
                cycle_pos = (now_wall - self.last_video_chunk_time) % 2.5
                if cycle_pos < 0.4: # Active chunk burst for 400ms
                    # Rapid packets during chunk download
                    yield PacketMetadata(
                        flow_key=self.flow_video,
                        pkt_len=random.randint(1380, 1440),
                        timestamp_ns=curr_t_ns + random.randint(50_000, 200_000),
                        ip_version=4
                    )

            # 3. Bulk ISO Torrent: Continuous saturated line rate
            if enable_iso:
                # Saturated 1500B packets back-to-back
                for _ in range(4):
                    yield PacketMetadata(
                        flow_key=self.flow_iso,
                        pkt_len=1500,
                        timestamp_ns=curr_t_ns + random.randint(10_000, 50_000),
                        ip_version=4
                    )

            time.sleep(0.002) # Yield control in generator loop
