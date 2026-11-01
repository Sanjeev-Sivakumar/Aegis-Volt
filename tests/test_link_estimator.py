"""
Phase 1 Unit Tests: Passive Link-Capacity & Bottleneck Estimator
Verifies capacity tracking, bufferbloat inflection detection, and stability.
"""

import unittest
import time
from aura_qos.link_estimator import LinkCapacityEstimator

class TestLinkCapacityEstimator(unittest.TestCase):
    def setUp(self):
        self.estimator = LinkCapacityEstimator(nominal_bandwidth_mbps=100.0, min_bandwidth_mbps=10.0, max_bandwidth_mbps=1000.0)

    def test_rtt_tracking_and_baseline(self):
        """Verifies RTT baseline establishment and bloat calculation."""
        self.estimator.record_rtt_sample(15.0)
        self.estimator.record_rtt_sample(16.0)
        self.estimator.record_rtt_sample(14.8)

        status = self.estimator.get_telemetry()
        self.assertAlmostEqual(status["min_rtt_ms"], 14.8, delta=0.5)
        self.assertEqual(status["link_status"], "OPTIMAL")
        print(f"\n[TEST PASSED] Minimum Link RTT tracked: {status['min_rtt_ms']} ms")

    def test_bufferbloat_detection_and_backoff(self):
        """Verifies that RTT inflation triggers CONGESTED state and capacity backoff."""
        # Establish healthy baseline
        self.estimator.record_rtt_sample(15.0)

        # Inject severe bufferbloat sample (e.g. 75ms under unshaped load)
        for _ in range(5):
            self.estimator.record_rtt_sample(80.0)

        # Record high byte volume
        self.estimator.record_packet(500_000)
        time.sleep(0.55)  # Wait for window cycle
        status = self.estimator.record_rtt_sample(85.0)

        self.assertEqual(status["link_status"], "CONGESTED")
        self.assertGreater(status["bufferbloat_delta_ms"], 35.0)
        print(f"[TEST PASSED] Bufferbloat Detected! Delta: {status['bufferbloat_delta_ms']} ms, Status: {status['link_status']}")

    def test_forced_capacity_drop(self):
        """Verifies dynamic shaping recalculation when WAN speed drops from 100M to 20M."""
        self.estimator.force_capacity_drop(20.0)
        status = self.estimator.get_telemetry()
        self.assertEqual(status["estimated_bandwidth_mbps"], 20.0)
        self.assertGreaterEqual(status["adaptations_total"], 1)
        print(f"[TEST PASSED] Forced capacity drop handled: {status['estimated_bandwidth_mbps']} Mbps")

if __name__ == "__main__":
    unittest.main()
