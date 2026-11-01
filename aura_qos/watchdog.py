"""
AURA-QoS Bounded Rollback Watchdog Module
Guarantees observable, bounded, and reversible remediation with automated safe-state fallback.
"""

import time
import threading
import logging
from typing import Dict, Any, Optional
from .config import DynamicSystemProfile
from .tc_manager import TCManager

logger = logging.getLogger("AURA_QoS.Watchdog")

class BoundedWatchdog:
    """
    Monitors engine health and telemetry freshness.
    If the engine hangs, freezes, or breaches bounded safety limits,
    the watchdog fires an atomic rollback to safe baseline fq_codel.
    """
    STATE_ARMED = "ARMED_HEALTHY"
    STATE_WARNING = "WARNING_MISSED_HEARTBEATS"
    STATE_TRIPPED = "TRIPPED_SAFE_BASELINE"

    def __init__(
        self,
        tc_manager: TCManager,
        timeout_sec: Optional[float] = None,
        check_interval_sec: float = 0.5,
        profile: Optional[DynamicSystemProfile] = None
    ):
        self.profile = profile or DynamicSystemProfile()
        self.tc_manager = tc_manager
        self.timeout_sec = timeout_sec or self.profile.watchdog_timeout_sec
        self.check_interval_sec = check_interval_sec

        self.last_heartbeat_time = time.time()
        self.state = self.STATE_ARMED
        self.trip_count = 0
        self.running = False
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

        # Consecutive SLA breach counter
        self.consecutive_bloat_breaches = 0

    def start(self):
        """Starts the watchdog background thread."""
        with self._lock:
            if not self.running:
                self.running = True
                self.last_heartbeat_time = time.time()
                self.state = self.STATE_ARMED
                self._thread = threading.Thread(target=self._monitor_loop, daemon=True, name="AURA-Watchdog")
                self._thread.start()
                logger.info(f"[WATCHDOG] Armed with dynamic timeout {self.timeout_sec}s.")

    def stop(self):
        """Gracefully halts the watchdog."""
        with self._lock:
            self.running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)
        logger.info("[WATCHDOG] Disarmed.")

    def heartbeat(self, current_rtt_ms: Optional[float] = None):
        """
        Invoked by the Analytics Engine upon each successful telemetry processing loop.
        Resets countdown timer and inspects runtime SLA boundary conditions.
        """
        with self._lock:
            now = time.time()
            self.last_heartbeat_time = now

            if self.state == self.STATE_WARNING:
                self.state = self.STATE_ARMED

            # Check secondary degradation trap: continuous severe bufferbloat (> 80ms)
            if current_rtt_ms is not None:
                if current_rtt_ms > 80.0:
                    self.consecutive_bloat_breaches += 1
                    if self.consecutive_bloat_breaches >= 6:
                        logger.error("[WATCHDOG] Continuous SLA latency breach! Initiating bounded rollback.")
                        self._trigger_rollback("Continuous SLA latency breach")
                else:
                    self.consecutive_bloat_breaches = 0

    def _trigger_rollback(self, reason: str):
        """Reverts the network stack to a known safe state immediately."""
        self.state = self.STATE_TRIPPED
        self.trip_count += 1
        logger.warning(f"[WATCHDOG ROLLBACK] Reason: {reason}. Reverting to safe baseline.")
        self.tc_manager.rollback_to_baseline()

    def _monitor_loop(self):
        """Main countdown loop."""
        while self.running:
            time.sleep(self.check_interval_sec)
            with self._lock:
                if not self.running:
                    break

                elapsed = time.time() - self.last_heartbeat_time

                if self.state == self.STATE_ARMED and elapsed > (self.timeout_sec * 0.6):
                    self.state = self.STATE_WARNING
                    logger.warning(f"[WATCHDOG] Warning: Heartbeat delayed by {elapsed:.2f}s")

                if elapsed > self.timeout_sec and self.state != self.STATE_TRIPPED:
                    logger.critical(f"[WATCHDOG EXPIRED] No heartbeat for {elapsed:.2f}s! Executing fail-safe rollback.")
                    self._trigger_rollback(f"Heartbeat timeout expired ({elapsed:.2f}s > {self.timeout_sec}s)")

    def get_health(self) -> Dict[str, Any]:
        """Provides full observability for dashboard and audit logging."""
        with self._lock:
            elapsed = time.time() - self.last_heartbeat_time
            time_remaining = max(0.0, self.timeout_sec - elapsed)
            return {
                "watchdog_state": self.state,
                "timeout_configured_sec": self.timeout_sec,
                "seconds_since_last_heartbeat": round(elapsed, 3),
                "seconds_until_trip": round(time_remaining, 3),
                "trip_count": self.trip_count,
                "is_safe_state": (self.state == self.STATE_TRIPPED)
            }
