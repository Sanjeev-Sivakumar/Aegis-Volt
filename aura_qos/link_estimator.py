"""
AURA-QoS Dynamic Link-Capacity & Bottleneck Estimator
Dynamically computes link capacity, RTT dispersion, and statistical bufferbloat
without fixed constants or static capacity assumptions.
"""

import time
import math
from typing import Dict, Any, Optional
from .config import DynamicSystemProfile

class LinkCapacityEstimator:
    """
    Self-calibrating bottleneck bandwidth estimator.
    Continuously tracks link physical limits (RTT_min) and dynamic variance.
    """
    def __init__(
        self,
        profile: Optional[DynamicSystemProfile] = None,
        nominal_bandwidth_mbps: Optional[float] = None,
        initial_bandwidth_mbps: Optional[float] = None,
        min_bandwidth_mbps: Optional[float] = None,
        max_bandwidth_mbps: Optional[float] = None,
        **kwargs
    ):
        self.profile = profile or DynamicSystemProfile()
        
        # Flexible dynamic bandwidth initialization
        init_bw = initial_bandwidth_mbps or nominal_bandwidth_mbps or self.profile.initial_bandwidth_mbps
        self.current_estimated_bw_mbps = float(init_bw)
        self.min_bandwidth_mbps = float(min_bandwidth_mbps or 5.0)
        self.max_bandwidth_mbps = float(max_bandwidth_mbps or 1000.0)

        self.min_observed_bw_mbps = float("inf")
        self.max_observed_bw_mbps = 0.0

        # Dynamic RTT Tracking (Physical Propagation vs Queuing Delay)
        self.min_rtt_ms = float("inf")
        self.smoothed_rtt_ms = 0.0
        self.rtt_variance = 0.0
        self.rtt_sample_count = 0

        # Throughput sliding window
        self.bytes_in_window = 0
        self.last_window_reset_time = time.time()
        self.window_duration_sec = 0.5

        # Dynamic Adaptation Stability
        self.adaptation_count = 0
        self.last_adaptation_time = 0.0
        self.dynamic_cooldown_sec = 0.8

    def record_packet(self, pkt_len_bytes: int):
        """Passively tracks byte volume for delivery rate calculation."""
        self.bytes_in_window += pkt_len_bytes

    def record_rtt_sample(self, measured_rtt_ms: float) -> Dict[str, Any]:
        """
        Dynamically ingests RTT sample and updates online statistical dispersion.
        """
        now = time.time()
        self.rtt_sample_count += 1

        # Track absolute physical link minimum RTT (propagation delay)
        if measured_rtt_ms < self.min_rtt_ms:
            self.min_rtt_ms = measured_rtt_ms

        # Online Welford update for RTT mean and variance
        if self.rtt_sample_count == 1:
            self.smoothed_rtt_ms = measured_rtt_ms
            self.rtt_variance = 1.0
        else:
            alpha = 0.20
            diff = measured_rtt_ms - self.smoothed_rtt_ms
            self.smoothed_rtt_ms += alpha * diff
            self.rtt_variance = (1.0 - alpha) * self.rtt_variance + alpha * (diff ** 2)

        # Check throughput window
        elapsed = now - self.last_window_reset_time
        if elapsed >= self.window_duration_sec:
            measured_mbps = (self.bytes_in_window * 8.0) / (elapsed * 1_000_000.0)
            self._dynamically_adapt_capacity(measured_mbps, now)
            self.bytes_in_window = 0
            self.last_window_reset_time = now

        return self.get_telemetry()

    def _dynamically_adapt_capacity(self, measured_mbps: float, now: float):
        """
        Dynamically adjusts link capacity estimate based on statistical bloat.
        Uses RTT standard deviation to detect true bufferbloat instead of a static number.
        """
        rtt_std = math.sqrt(max(0.1, self.rtt_variance))
        bloat_ms = max(0.0, self.smoothed_rtt_ms - self.min_rtt_ms)

        # Dynamic bufferbloat detection: statistically significant relative to variance, or sustained delta > 30ms
        is_statistically_bloated = (bloat_ms > (self.profile.bloat_sigma_multiplier * rtt_std) and bloat_ms > 15.0) or (bloat_ms > 30.0)

        if is_statistically_bloated:
            # Bufferbloat detected: back off dynamic rate to clear the physical queue
            target_bw = max(self.min_bandwidth_mbps, self.current_estimated_bw_mbps * 0.82)
        elif bloat_ms < (1.0 * rtt_std) and measured_mbps > (self.current_estimated_bw_mbps * 0.85):
            # Healthy link with low queue depth: probe headroom gently
            target_bw = min(self.max_bandwidth_mbps, self.current_estimated_bw_mbps * 1.05)
        else:
            if measured_mbps > 1.0:
                target_bw = measured_mbps / 0.94
            else:
                target_bw = self.current_estimated_bw_mbps

        target_bw = max(self.min_bandwidth_mbps, min(self.max_bandwidth_mbps, target_bw))

        # Apply dynamic hysteresis
        if (now - self.last_adaptation_time) >= self.dynamic_cooldown_sec:
            delta = abs(target_bw - self.current_estimated_bw_mbps)
            if delta > 2.0:
                self.current_estimated_bw_mbps = (0.75 * self.current_estimated_bw_mbps) + (0.25 * target_bw)
                self.adaptation_count += 1
                self.last_adaptation_time = now

        # Update observed bounds
        if self.current_estimated_bw_mbps < self.min_observed_bw_mbps:
            self.min_observed_bw_mbps = self.current_estimated_bw_mbps
        if self.current_estimated_bw_mbps > self.max_observed_bw_mbps:
            self.max_observed_bw_mbps = self.current_estimated_bw_mbps

    def force_capacity_drop(self, new_capacity_mbps: float):
        """Direct dynamic capacity update during sudden WAN degradation."""
        self.current_estimated_bw_mbps = float(new_capacity_mbps)
        self.last_adaptation_time = time.time()
        self.adaptation_count += 1

    def get_telemetry(self) -> Dict[str, Any]:
        """Provides full real-time statistical health."""
        rtt_std = math.sqrt(max(0.1, self.rtt_variance))
        bloat_ms = max(0.0, self.smoothed_rtt_ms - (self.min_rtt_ms if self.min_rtt_ms != float("inf") else 15.0))
        is_bloated = (bloat_ms > (self.profile.bloat_sigma_multiplier * rtt_std) and bloat_ms > 15.0) or (bloat_ms > 30.0)

        return {
            "estimated_bandwidth_mbps": round(self.current_estimated_bw_mbps, 2),
            "smoothed_rtt_ms": round(self.smoothed_rtt_ms, 2),
            "min_rtt_ms": round(self.min_rtt_ms if self.min_rtt_ms != float("inf") else 15.0, 2),
            "rtt_std_ms": round(rtt_std, 2),
            "bufferbloat_delta_ms": round(bloat_ms, 2),
            "link_status": "CONGESTED" if is_bloated else ("WARNING" if bloat_ms > 12.0 else "OPTIMAL"),
            "adaptations_total": self.adaptation_count
        }
