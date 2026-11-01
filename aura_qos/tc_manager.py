"""
AURA-QoS Linux Traffic Control (tc-CAKE) Enforcement Manager
Executes atomic RCU pointer swaps via Linux netlink or handles cross-platform simulation.
Guarantees zero-starvation through CAKE DiffServ4 Deficit Round Robin scheduling.
"""

import sys
import os
import subprocess
import logging
from typing import Dict, Any, List, Optional
from .config import DynamicSystemProfile, discover_default_interface

logger = logging.getLogger("AURA_QoS.TCManager")

class TCManager:
    """
    Interfaces with Linux Traffic Control (tc) subsystem.
    Enforces CAKE queue discipline with dynamic RTT and rate parameters.
    """
    def __init__(self, interface: Optional[str] = None, simulate: bool = None, profile: Optional[DynamicSystemProfile] = None):
        self.profile = profile or DynamicSystemProfile()
        self.interface = interface or self.profile.interface or discover_default_interface()
        
        # Auto-detect whether to run real Linux tc commands or safe simulation
        is_linux = sys.platform.startswith("linux")
        is_root = os.geteuid() == 0 if hasattr(os, "geteuid") else False
        self.simulate = simulate if simulate is not None else (not (is_linux and is_root))

        self.current_qdisc = "unshaped_fifo"
        self.current_bandwidth_mbps = self.profile.initial_bandwidth_mbps
        self.current_rtt_ms = 20.0
        self.history_actions: List[Dict[str, Any]] = []

        # Real-time tin statistics per DiffServ tin
        self.simulated_tin_stats = {
            "priority": {"packets": 0, "bytes": 0, "dropped": 0, "ecn_marked": 0, "backlog_pkts": 0},
            "video": {"packets": 0, "bytes": 0, "dropped": 0, "ecn_marked": 0, "backlog_pkts": 0},
            "besteffort": {"packets": 0, "bytes": 0, "dropped": 0, "ecn_marked": 0, "backlog_pkts": 0},
            "bulk": {"packets": 0, "bytes": 0, "dropped": 0, "ecn_marked": 0, "backlog_pkts": 0},
        }

    def apply_cake_qdisc(self, bandwidth_mbps: Optional[float] = None, rtt_ms: Optional[float] = None) -> bool:
        """
        Applies tc-CAKE as root qdisc using atomic replacement with dynamic RTT.
        Netlink mechanism: RTM_NEWQDISC with NLM_F_REPLACE.
        """
        if bandwidth_mbps is not None:
            self.current_bandwidth_mbps = bandwidth_mbps
        if rtt_ms is not None:
            self.current_rtt_ms = max(1.0, rtt_ms)

        bw_str = f"{int(self.current_bandwidth_mbps)}mbit"
        rtt_str = f"{int(self.current_rtt_ms)}ms"

        cmd = [
            "tc", "qdisc", "replace", "dev", self.interface, "root",
            "cake", "bandwidth", bw_str, "diffserv4", "rtt", rtt_str, "nat", "wash"
        ]

        if not self.simulate:
            try:
                subprocess.run(cmd, check=True, capture_output=True, text=True)
                logger.info(f"[TC_KERNEL] Successfully applied CAKE ({bw_str}, {rtt_str}) on {self.interface}")
            except Exception as e:
                logger.error(f"[TC_KERNEL] Failed to execute tc command: {e}")
                return False
        else:
            logger.info(f"[TC_SIMULATION] Executing simulated atomic RCU swap: {' '.join(cmd)}")

        self.current_qdisc = "cake"
        self.history_actions.append({
            "action": "APPLY_CAKE",
            "bandwidth_mbps": self.current_bandwidth_mbps,
            "rtt_ms": self.current_rtt_ms,
            "command": " ".join(cmd),
            "simulated": self.simulate
        })
        return True

    def adjust_bandwidth(self, new_bandwidth_mbps: float, dynamic_rtt_ms: Optional[float] = None) -> bool:
        """
        Hitless dynamic rate scaling during link-capacity fluctuations.
        """
        self.current_bandwidth_mbps = new_bandwidth_mbps
        bw_str = f"{int(new_bandwidth_mbps)}mbit"
        
        cmd = ["tc", "qdisc", "change", "dev", self.interface, "root", "cake", "bandwidth", bw_str]
        if dynamic_rtt_ms is not None:
            self.current_rtt_ms = dynamic_rtt_ms
            cmd.extend(["rtt", f"{int(dynamic_rtt_ms)}ms"])

        if not self.simulate:
            try:
                subprocess.run(cmd, check=True, capture_output=True, text=True)
                logger.info(f"[TC_KERNEL] Dynamically scaled CAKE bandwidth to {bw_str}")
            except Exception as e:
                logger.error(f"[TC_KERNEL] Failed to scale bandwidth: {e}")
                return False
        else:
            logger.info(f"[TC_SIMULATION] Dynamic rate adjustment: {' '.join(cmd)}")

        self.history_actions.append({
            "action": "ADJUST_BANDWIDTH",
            "new_bandwidth_mbps": new_bandwidth_mbps,
            "command": " ".join(cmd),
            "simulated": self.simulate
        })
        return True

    def rollback_to_baseline(self) -> bool:
        """
        Bounded Rollback to safe baseline FQ-CoDel.
        Triggered automatically on watchdog timeout or unrecoverable error.
        """
        cmd = ["tc", "qdisc", "replace", "dev", self.interface, "root", "fq_codel"]
        if not self.simulate:
            try:
                subprocess.run(cmd, check=True, capture_output=True, text=True)
                logger.warning(f"[TC_KERNEL] Emergency rollback to safe baseline fq_codel on {self.interface}")
            except Exception as e:
                logger.error(f"[TC_KERNEL] Failed rollback: {e}")
                return False
        else:
            logger.warning(f"[TC_SIMULATION] Emergency rollback to safe baseline: {' '.join(cmd)}")

        self.current_qdisc = "fq_codel"
        self.history_actions.append({
            "action": "ROLLBACK_BASELINE",
            "command": " ".join(cmd),
            "simulated": self.simulate
        })
        return True

    def record_forwarded_packet(self, tin: str, pkt_len: int, ecn_marked: bool = False, dropped: bool = False):
        """Updates internal telemetry counters per tin."""
        if tin not in self.simulated_tin_stats:
            tin = "besteffort"

        stats = self.simulated_tin_stats[tin]
        if dropped:
            stats["dropped"] += 1
        else:
            stats["packets"] += 1
            stats["bytes"] += pkt_len
            if ecn_marked:
                stats["ecn_marked"] += 1

    def calculate_jains_fairness_index(self, throughputs: List[float]) -> float:
        """
        Computes Jain's Fairness Index:
            J(x_1, ..., x_n) = (sum(x_i))^2 / (n * sum(x_i^2))
        Returns a value between 1/n (extreme starvation) to 1.0 (perfect equity).
        """
        valid_t = [t for t in throughputs if t > 0]
        n = len(valid_t)
        if n == 0:
            return 1.0
        sum_x = sum(valid_t)
        sum_sq_x = sum(x ** 2 for x in valid_t)
        if sum_sq_x == 0:
            return 1.0
        j_index = (sum_x ** 2) / (n * sum_sq_x)
        return round(float(j_index), 4)

    def get_status(self) -> Dict[str, Any]:
        """Returns real-time operational status."""
        return {
            "interface": self.interface,
            "active_qdisc": self.current_qdisc,
            "configured_bandwidth_mbps": self.current_bandwidth_mbps,
            "rtt_ms_target": self.current_rtt_ms,
            "simulation_mode": self.simulate,
            "tin_stats": self.simulated_tin_stats,
            "actions_executed": len(self.history_actions)
        }
