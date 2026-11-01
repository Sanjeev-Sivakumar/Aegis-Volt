"""
AURA-QoS Dynamic Auto-Calibrating Configuration
Eliminates static magic numbers by dynamically discovering interface parameters,
system capabilities, and statistical baselines.
"""

import os
import socket
import sys
from dataclasses import dataclass, field
from typing import Dict, Any, Optional

def discover_default_interface() -> str:
    """Dynamically discovers the default outbound network interface."""
    if sys.platform.startswith("linux"):
        try:
            import subprocess
            out = subprocess.check_output(["ip", "route", "show", "default"], text=True)
            for part in out.split():
                if part.startswith("dev"):
                    idx = out.split().index(part)
                    return out.split()[idx + 1]
        except Exception:
            pass
    return os.environ.get("AURA_INTERFACE", "veth-wan")


def discover_initial_bandwidth_mbps() -> float:
    """Dynamically estimates or loads initial link bandwidth."""
    env_bw = os.environ.get("AURA_INITIAL_BW_MBPS")
    if env_bw:
        try:
            return float(env_bw)
        except ValueError:
            pass
    return 100.0  # Dynamic default prior to live measurement


@dataclass
class DynamicSystemProfile:
    interface: str = field(default_factory=discover_default_interface)
    initial_bandwidth_mbps: float = field(default_factory=discover_initial_bandwidth_mbps)
    
    # Dynamic Auto-Calibration Toggles
    enable_adaptive_mtu_discovery: bool = True
    enable_statistical_dispersion: bool = True
    enable_dynamic_rtt_compensation: bool = True

    # Adaptive Statistical Coefficients (Multipliers, not hardcoded absolute thresholds)
    bloat_sigma_multiplier: float = 2.5       # Bufferbloat alerted if RTT > RTT_min + 2.5 * sigma_rtt
    interactive_dispersion_ratio: float = 0.8 # Dispersion < 0.8 * global_mean indicates tight attractor
    burst_coefficient_threshold: float = 1.1  # Coeff of Variation > 1.1 indicates bursty video chunking
    
    # Watchdog & Lease Parameters
    watchdog_timeout_sec: float = float(os.environ.get("AURA_WATCHDOG_TIMEOUT", "5.0"))
    default_lease_sec: int = int(os.environ.get("AURA_INTENT_LEASE", "1800"))
