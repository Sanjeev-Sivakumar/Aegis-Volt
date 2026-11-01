"""
Phase 1 Unit Tests: Dynamic Intent Arbiter & Fairness
Verifies temporary user service intent, automatic lease expiration, and dynamic Jain's Fairness Index.
"""

import unittest
import time
from aura_qos.intent import IntentArbiter
from aura_qos.tc_manager import TCManager

class TestIntentAndFairness(unittest.TestCase):
    def setUp(self):
        self.arbiter = IntentArbiter(default_lease_sec=2)
        self.tc = TCManager(simulate=True)

    def test_work_boost_activation_and_expiry(self):
        """Verifies temporary intent activation and auto-reversion after TTL expires."""
        target_ip = "192.168.1.50"
        self.arbiter.set_intent(target_ip, intent_type=IntentArbiter.INTENT_WORK_BOOST, duration_sec=1)

        intent = self.arbiter.evaluate_traffic_intent(target_ip, "198.51.100.1")
        self.assertIsNotNone(intent)
        self.assertEqual(intent["intent_type"], IntentArbiter.INTENT_WORK_BOOST)

        # Wait for TTL to expire
        time.sleep(1.2)
        intent_expired = self.arbiter.evaluate_traffic_intent(target_ip, "198.51.100.1")
        self.assertIsNone(intent_expired)
        state = self.arbiter.get_intent_state(target_ip)
        self.assertEqual(state["intent_type"], IntentArbiter.INTENT_BALANCED)
        print(f"\n[TEST PASSED] Work Boost Activated and cleanly expired back to: {state['intent_type']}")

    def test_jains_fairness_calculation(self):
        """Verifies Jain's Fairness Index bounds dynamically."""
        # 1. Perfectly equal throughput across 4 flows -> J = 1.0
        j_perfect = self.tc.calculate_jains_fairness_index([25.0, 25.0, 25.0, 25.0])
        self.assertEqual(j_perfect, 1.0)

        # 2. Dynamic multi-flow mixed throughput allocation
        mixed = [5.0, 25.0, 15.0, 55.0]
        j_mixed = self.tc.calculate_jains_fairness_index(mixed)
        # Jain's index must be strictly bounded between 1/n (0.25) and 1.0
        min_bound = 1.0 / len(mixed)
        self.assertGreaterEqual(j_mixed, min_bound)
        self.assertLessEqual(j_mixed, 1.0)
        print(f"[TEST PASSED] Jain's Fairness correctly computed: Perfect={j_perfect}, Mixed={j_mixed} (Min Bound={min_bound})")

if __name__ == "__main__":
    unittest.main()
