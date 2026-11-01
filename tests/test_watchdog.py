"""
Phase 1 Unit Tests: Bounded Rollback Watchdog Module
Verifies bounded, observable, and reversible fail-safe rollback to safe baseline.
"""

import unittest
import time
from aura_qos.tc_manager import TCManager
from aura_qos.watchdog import BoundedWatchdog

class TestWatchdogRollback(unittest.TestCase):
    def setUp(self):
        self.tc = TCManager(simulate=True)
        self.tc.apply_cake_qdisc(bandwidth_mbps=100.0)
        # Configure tight 1.0s timeout for rapid unit test
        self.watchdog = BoundedWatchdog(self.tc, timeout_sec=1.0, check_interval_sec=0.2)

    def tearDown(self):
        self.watchdog.stop()

    def test_watchdog_healthy_with_heartbeats(self):
        """Watchdog remains healthy as long as heartbeats are fed."""
        self.watchdog.start()
        time.sleep(0.3)
        self.watchdog.heartbeat()
        time.sleep(0.3)
        self.watchdog.heartbeat()

        health = self.watchdog.get_health()
        self.assertEqual(health["watchdog_state"], BoundedWatchdog.STATE_ARMED)
        self.assertEqual(self.tc.current_qdisc, "cake")
        print(f"\n[TEST PASSED] Watchdog healthy under active heartbeats. State: {health['watchdog_state']}")

    def test_watchdog_fail_safe_rollback_on_timeout(self):
        """Watchdog must trip and restore safe baseline fq_codel if heartbeats stop."""
        self.watchdog.start()
        # Cease heartbeats intentionally
        time.sleep(1.4)  # Exceeds 1.0s timeout

        health = self.watchdog.get_health()
        self.assertEqual(health["watchdog_state"], BoundedWatchdog.STATE_TRIPPED)
        self.assertTrue(health["is_safe_state"])
        self.assertEqual(self.tc.current_qdisc, "fq_codel")
        self.assertEqual(health["trip_count"], 1)
        print(f"[TEST PASSED] Watchdog tripped safely! Stack reverted to: {self.tc.current_qdisc}")

if __name__ == "__main__":
    unittest.main()
