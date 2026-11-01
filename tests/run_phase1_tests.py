"""
AURA-QoS Phase 1 Master Test & Benchmark Runner
Executes all core engine unit tests, calculates microsecond processing latency,
and validates mathematical bounds for the engineering evaluation panel.
"""

import unittest
import time
import sys
import os

# Ensure package root is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from tests.test_phase_space import TestPhaseSpaceAttractor
from tests.test_link_estimator import TestLinkCapacityEstimator
from tests.test_watchdog import TestWatchdogRollback
from tests.test_intent_arbiter import TestIntentAndFairness
from tests.test_engine_integration import TestEngineIntegration
from aura_qos.phase_space import PhaseSpaceClassifier

def run_microsecond_benchmark():
    """Measures single-core packet classification latency."""
    print("\n" + "="*70)
    print("AURA-QoS MICROSECOND INFERENCE LATENCY BENCHMARK")
    print("="*70)
    classifier = PhaseSpaceClassifier()
    flow = ("10.0.0.1", "10.0.0.2", 1234, 5678, "UDP")

    # Warmup
    for _ in range(50):
        classifier.observe(flow, 20.0, 160)

    # Benchmark 10,000 iterations
    iterations = 10000
    start = time.perf_counter()
    for i in range(iterations):
        classifier.observe(flow, 20.0 + (i % 2), 160 + (i % 4))
    total_sec = time.perf_counter() - start

    avg_us = (total_sec / iterations) * 1_000_000.0
    packets_per_sec = iterations / total_sec

    print(f"Total Iterations:             {iterations:,}")
    print(f"Total Computation Time:       {total_sec * 1000.0:.2f} ms")
    print(f"Average Latency Per Packet:   {avg_us:.3f} microseconds (us)")
    print(f"Throughput Capacity:          {packets_per_sec:,.0f} packets/second per core")
    print(f"Meets Edge Gateway Target:    {'YES (< 15 us target)' if avg_us < 15.0 else 'NO'}")
    print("="*70)

def main():
    suite = unittest.TestSuite()
    loader = unittest.TestLoader()

    suite.addTests(loader.loadTestsFromTestCase(TestPhaseSpaceAttractor))
    suite.addTests(loader.loadTestsFromTestCase(TestLinkCapacityEstimator))
    suite.addTests(loader.loadTestsFromTestCase(TestWatchdogRollback))
    suite.addTests(loader.loadTestsFromTestCase(TestIntentAndFairness))
    suite.addTests(loader.loadTestsFromTestCase(TestEngineIntegration))

    print("\n" + "#"*70)
    print("# EXECUTING AURA-QoS PHASE 1 VERIFICATION TEST SUITE")
    print("#"*70)

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    run_microsecond_benchmark()

    if result.wasSuccessful():
        print("\n>>> ALL PHASE 1 CORE VERIFICATION TESTS COMPLETED SUCCESSFULLY! <<<\n")
        return 0
    else:
        print("\n>>> PHASE 1 TESTS FAILED. PLEASE CHECK LOGS. <<<\n")
        return 1

if __name__ == "__main__":
    sys.exit(main())
