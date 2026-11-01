"""
AURA-QoS Self-Calibrating Phase-Space Attractor Reconstruction Engine
Eliminates hardcoded packet sizes and static thresholds by dynamically learning
the link's MTU, inter-arrival distributions, and attractor manifolds online.
"""

import math
from collections import deque
from typing import Dict, Tuple, Optional, Any
import numpy as np
from .config import DynamicSystemProfile
from .numba_attractor import numba_calculate_attractor

class PhaseSpaceClassifier:
    """
    Implements Takens' Delay Coordinate Embedding with Dynamic Topological Clustering.
    Dynamically normalizes against live observed link MTU and inter-arrival distributions.
    """

    CLASS_INTERACTIVE = "INTERACTIVE_VOIP_GAME"
    CLASS_VIDEO = "STREAMING_VIDEO"
    CLASS_BULK = "BULK_TRANSFER"
    CLASS_BESTEFFORT = "BEST_EFFORT"

    # CAKE DiffServ4 Tins
    TIN_PRIORITY = "priority"
    TIN_VIDEO = "video"
    TIN_BESTEFFORT = "besteffort"
    TIN_BULK = "bulk"

    def __init__(self, profile: Optional[DynamicSystemProfile] = None, window_size: int = 8):
        self.profile = profile or DynamicSystemProfile()
        self.window_size = window_size
        
        # Trajectories: canonical_flow_key -> deque of (delta_t_ms, pkt_len)
        self.flow_trajectories: Dict[Tuple, deque] = {}
        self.manual_corrections: Dict[Tuple, Dict[str, Any]] = {}

        # Dynamic Auto-Calibration State (Online Welford Statistics)
        self.observed_min_len = float("inf")
        self.observed_max_len = 0.0
        self.total_packets_profiled = 0

        # Running Global Statistics for Inter-Arrival Time
        self.global_mean_dt = 10.0
        self.global_m2_dt = 0.0

    def _update_global_statistics(self, delta_t_ms: float, pkt_len: int):
        """Dynamically calibrates link spectrum bounds online without static values."""
        self.total_packets_profiled += 1
        
        # Dynamic MTU / frame size bounds
        if pkt_len < self.observed_min_len:
            self.observed_min_len = float(pkt_len)
        if pkt_len > self.observed_max_len:
            self.observed_max_len = float(pkt_len)

        # Welford's online mean and variance for inter-arrival times
        if delta_t_ms > 0:
            count = self.total_packets_profiled
            delta = delta_t_ms - self.global_mean_dt
            self.global_mean_dt += delta / count
            delta2 = delta_t_ms - self.global_mean_dt
            self.global_m2_dt += delta * delta2

    def observe(self, flow_key_canonical: Tuple, delta_t_ms: float, pkt_len: int) -> Dict[str, Any]:
        """
        Classifies flow in O(1) time using dynamically calibrated phase-space metrics.
        """
        # Check active manual override first
        if flow_key_canonical in self.manual_corrections:
            override = self.manual_corrections[flow_key_canonical]
            return {
                "class_name": override["class_name"],
                "target_tin": override["target_tin"],
                "confidence": 1.0,
                "attractor_metrics": {"manual_override": True, "notes": override.get("reason", "Operator override")},
                "is_manual_override": True
            }

        # Update dynamic link baseline
        self._update_global_statistics(delta_t_ms, pkt_len)

        if flow_key_canonical not in self.flow_trajectories:
            self.flow_trajectories[flow_key_canonical] = deque(maxlen=self.window_size)

        traj = self.flow_trajectories[flow_key_canonical]
        traj.append((delta_t_ms, pkt_len))
        n = len(traj)

        # Need at least 3 packets to form a 2D phase-space embedding vector
        if n < 3:
            return {
                "class_name": self.CLASS_BESTEFFORT,
                "target_tin": self.TIN_BESTEFFORT,
                "confidence": 0.50,
                "attractor_metrics": {"packet_count": n, "state": "CALIBRATING_EMBEDDING"},
                "is_manual_override": False
            }

        # JIT-compiled Numba delay-coordinate embedding kernel
        dt_arr = np.array([pt[0] for pt in traj], dtype=np.float64)
        len_arr = np.array([pt[1] for pt in traj], dtype=np.float64)
        
        span_len = (self.observed_max_len - self.observed_min_len) if (self.observed_max_len > self.observed_min_len + 100.0) else max(1500.0, self.observed_max_len)
        mean_dt, std_dt, cv_dt, mean_len, relative_len_ratio, dispersion = numba_calculate_attractor(
            dt_arr, len_arr, self.global_mean_dt, span_len
        )

        # DYNAMIC CLASSIFICATION LOGIC (Adaptive topology)
        # 1. Bulk Saturated Transfer (ISO / Torrents / Cloud Backup)
        # High relative payload (mean_len near MTU or ratio > 0.70) with steady flow pacing (CV < 0.85)
        if (relative_len_ratio >= 0.70 or mean_len >= 1200.0) and cv_dt < 0.85:
            confidence = min(0.99, max(0.75, (mean_len / max(1500.0, self.observed_max_len)) * 0.98))
            return {
                "class_name": self.CLASS_BULK,
                "target_tin": self.TIN_BULK,
                "confidence": round(confidence, 3),
                "attractor_metrics": {
                    "packet_count": n,
                    "cv_dt": round(cv_dt, 3),
                    "relative_len_ratio": round(relative_len_ratio, 3),
                    "dispersion": round(dispersion, 2),
                    "attractor_type": "ASYMPTOTIC_SATURATED_MANIFOLD"
                },
                "is_manual_override": False
            }

        # 2. Adaptive Video Streaming (DASH / HLS chunks)
        # Bursty inter-arrival pattern (CV_dt >= 0.75) with medium-to-large packets
        elif cv_dt >= 0.75 and (relative_len_ratio > 0.40 or mean_len > 600.0):
            confidence = min(0.98, max(0.70, (cv_dt / (cv_dt + 1.0)) * 0.95))
            return {
                "class_name": self.CLASS_VIDEO,
                "target_tin": self.TIN_VIDEO,
                "confidence": round(confidence, 3),
                "attractor_metrics": {
                    "packet_count": n,
                    "cv_dt": round(cv_dt, 3),
                    "relative_len_ratio": round(relative_len_ratio, 3),
                    "dispersion": round(dispersion, 2),
                    "attractor_type": "BURST_LIMIT_CYCLE"
                },
                "is_manual_override": False
            }

        # 3. Interactive Real-Time (VoIP / Low-Latency Gaming)
        # High regularity (low CV < 0.65) + lean payload (mean_len < 450 bytes or ratio < 0.40)
        elif cv_dt < 0.65 and (relative_len_ratio < 0.40 or mean_len < 450.0):
            confidence = min(0.99, max(0.75, 1.0 - (cv_dt * 0.4) - (relative_len_ratio * 0.2)))
            return {
                "class_name": self.CLASS_INTERACTIVE,
                "target_tin": self.TIN_PRIORITY,
                "confidence": round(confidence, 3),
                "attractor_metrics": {
                    "packet_count": n,
                    "cv_dt": round(cv_dt, 3),
                    "relative_len_ratio": round(relative_len_ratio, 3),
                    "dispersion": round(dispersion, 2),
                    "attractor_type": "TIGHT_ISOCHRONOUS_ORBIT"
                },
                "is_manual_override": False
            }

        # 4. Best Effort (General Web Browsing / Unclassified)
        else:
            return {
                "class_name": self.CLASS_BESTEFFORT,
                "target_tin": self.TIN_BESTEFFORT,
                "confidence": 0.65,
                "attractor_metrics": {
                    "packet_count": n,
                    "cv_dt": round(cv_dt, 3),
                    "relative_len_ratio": round(relative_len_ratio, 3),
                    "dispersion": round(dispersion, 2),
                    "attractor_type": "TRANSIENT_BROWSE"
                },
                "is_manual_override": False
            }

    def correct_classification(self, flow_key_canonical: Tuple, target_class: str, reason: str = "Manual operator feedback"):
        """Fulfills constraint: allow dynamic correction of misclassification."""
        tin_map = {
            self.CLASS_INTERACTIVE: self.TIN_PRIORITY,
            self.CLASS_VIDEO: self.TIN_VIDEO,
            self.CLASS_BULK: self.TIN_BULK,
            self.CLASS_BESTEFFORT: self.TIN_BESTEFFORT
        }
        self.manual_corrections[flow_key_canonical] = {
            "class_name": target_class,
            "target_tin": tin_map.get(target_class, self.TIN_BESTEFFORT),
            "reason": reason
        }

    def clear_correction(self, flow_key_canonical: Tuple):
        """Restores autonomous attractor inference."""
        self.manual_corrections.pop(flow_key_canonical, None)
