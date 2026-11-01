"""
Aegis-Volt: Non-Intrusive Load Monitoring (NILM) Physical Grid Telemetry Engine
Monitors high-frequency current transient harmonics (2-5 kHz) at the power brick/interface.
Pre-emptively detects hardware state changes (GPU rendering loops, WebRTC core scaling)
and fires Layer-2 Ethernet intent frames BEFORE the first network packet reaches the WAN.
License: Open Source (BSD 3-Clause)
"""

import time
import math
import random
from dataclasses import dataclass
from typing import Dict, Any, List, Optional, Callable

# Protocol Identifier for NILM Pre-Emptive Layer-2 Ethernet Frame
ETH_P_NILM_PREEMPT = 0x88B5  # IEEE 802 Local Experimental EtherType

@dataclass
class PowerTransientSignature:
    device_name: str
    target_ip: str
    target_mac: str
    base_current_amps: float
    transient_spike_amps: float
    harmonic_frequency_hz: float
    predicted_intent: str      # e.g., "GPU_GAMING_RENDER", "WEBRTC_ENCODER_BURST", "IDLE"
    target_tin: str            # "priority", "video", "besteffort", "bulk"

class NILMPowerGridTelemetry:
    """
    Simulates / interfaces with smart power brick NILM sampling hardware.
    Analyzes physical electrical draw to overcome DoH / ECH / TLS 1.3 cryptographic blindness.
    """
    def __init__(self, sample_rate_khz: float = 4.0):
        self.sample_rate_khz = sample_rate_khz
        self.active_appliances: Dict[str, Dict[str, Any]] = {
            "console_gpu": {
                "name": "PlayStation / Gaming PC",
                "ip": "192.168.1.50",
                "mac": "02:00:00:00:00:50",
                "idle_amps": 0.45,
                "peak_amps": 2.80,
                "dominant_harmonic_hz": 120.0,
                "state": "IDLE"
            },
            "laptop_work": {
                "name": "Corporate Laptop (WebRTC/Zoom)",
                "ip": "192.168.1.10",
                "mac": "02:00:00:00:00:10",
                "idle_amps": 0.25,
                "peak_amps": 1.45,
                "dominant_harmonic_hz": 60.0,
                "state": "IDLE"
            },
            "nas_cloud": {
                "name": "Home NAS / Cloud Backup Server",
                "ip": "192.168.1.30",
                "mac": "02:00:00:00:00:30",
                "idle_amps": 0.18,
                "peak_amps": 0.95,
                "dominant_harmonic_hz": 180.0,
                "state": "IDLE"
            }
        }
        self.listeners: List[Callable[[Dict[str, Any]], None]] = []
        self.last_sample_time = time.time()

    def register_preempt_listener(self, callback: Callable[[Dict[str, Any]], None]):
        """Registers the eBPF / Router callback to receive Layer-2 Pre-Emptive Intent frames."""
        self.listeners.append(callback)

    def trigger_appliance_boot(self, appliance_key: str, intent_type: str = "WEBRTC_ENCODER_BURST") -> Dict[str, Any]:
        """
        Simulates an appliance drawing power:
        e.g., Laptop starts WebRTC video encoder matrix, causing CPU core spike.
        Fires a raw pre-emptive intent frame BEFORE any WAN packet is transmitted.
        """
        app = self.active_appliances.get(appliance_key)
        if not app:
            return {"status": "UNKNOWN_APPLIANCE"}

        app["state"] = intent_type
        current_spike = app["peak_amps"] + random.uniform(-0.05, 0.08)

        # Build Layer-2 Ethernet Pre-Emptive Intent Frame
        frame_payload = {
            "eth_type": hex(ETH_P_NILM_PREEMPT),
            "source_mac": app["mac"],
            "target_ip": app["ip"],
            "measured_current_amps": round(current_spike, 3),
            "transient_harmonic_hz": app["dominant_harmonic_hz"],
            "predicted_intent": intent_type,
            "target_tin": "priority" if "GPU" in intent_type or "WEBRTC" in intent_type else "bulk",
            "preempt_lead_time_ms": round(random.uniform(4.5, 12.0), 2),
            "timestamp_ns": int(time.time() * 1_000_000_000)
        }

        # Dispatch pre-emptive signal to router NIC
        for listener in self.listeners:
            listener(frame_payload)

        return frame_payload

    def poll_instantaneous_power(self) -> Dict[str, Any]:
        """Returns instantaneous power grid waveform metrics for live dashboard."""
        total_current = 0.0
        devices_status = {}
        for k, v in self.active_appliances.items():
            base = v["idle_amps"] if v["state"] == "IDLE" else v["peak_amps"]
            noise = random.uniform(-0.02, 0.02)
            cur = max(0.05, base + noise)
            total_current += cur
            devices_status[k] = {
                "name": v["name"],
                "ip": v["ip"],
                "current_amps": round(cur, 3),
                "state": v["state"]
            }

        voltage = 230.0 + random.uniform(-1.0, 1.0)
        return {
            "mains_voltage_volts": round(voltage, 1),
            "total_current_amps": round(total_current, 3),
            "real_power_watts": round(voltage * total_current, 1),
            "sample_rate_khz": self.sample_rate_khz,
            "appliances": devices_status
        }

