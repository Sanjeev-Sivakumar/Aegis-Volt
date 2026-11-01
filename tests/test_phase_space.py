"""
Phase 1 Unit Tests: Dynamic Phase-Space Attractor Reconstruction
Verifies self-calibrating classification convergence, confidence scoring,
and user misclassification correction.
"""

import unittest
from aura_qos.phase_space import PhaseSpaceClassifier

class TestPhaseSpaceAttractor(unittest.TestCase):
    def setUp(self):
        self.classifier = PhaseSpaceClassifier()
        self.voip_flow = ("192.168.1.100", "203.0.113.5", 50000, 3478, "UDP")
        self.bulk_flow = ("192.168.1.101", "198.51.100.2", 52100, 443, "TCP")
        self.video_flow = ("192.168.1.102", "198.51.100.99", 54321, 443, "TCP")

    def test_voip_attractor_convergence_under_5_packets(self):
        """VoIP traffic (steady 20ms, small ~180B payload) should collapse to tight attractor in <= 4 packets."""
        results = []
        for i in range(5):
            res = self.classifier.observe(self.voip_flow, delta_t_ms=20.0, pkt_len=180)
            results.append(res)

        final = results[-1]
        self.assertEqual(final["class_name"], PhaseSpaceClassifier.CLASS_INTERACTIVE)
        self.assertEqual(final["target_tin"], PhaseSpaceClassifier.TIN_PRIORITY)
        self.assertGreaterEqual(final["confidence"], 0.80)
        self.assertEqual(final["attractor_metrics"]["attractor_type"], "TIGHT_ISOCHRONOUS_ORBIT")
        print(f"\n[TEST PASSED] VoIP Attractor Converged in {final['attractor_metrics']['packet_count']} packets. Confidence: {final['confidence']}")

    def test_bulk_iso_attractor_identification(self):
        """Bulk ISO download (0.15ms pacing, 1500B saturated MTU) should classify as BULK_TRANSFER."""
        results = []
        for i in range(5):
            res = self.classifier.observe(self.bulk_flow, delta_t_ms=0.15, pkt_len=1500)
            results.append(res)

        final = results[-1]
        self.assertEqual(final["class_name"], PhaseSpaceClassifier.CLASS_BULK)
        self.assertEqual(final["target_tin"], PhaseSpaceClassifier.TIN_BULK)
        self.assertGreaterEqual(final["confidence"], 0.85)
        self.assertEqual(final["attractor_metrics"]["attractor_type"], "ASYMPTOTIC_SATURATED_MANIFOLD")
        print(f"[TEST PASSED] Bulk ISO Attractor Identified: {final['class_name']}. Tin: {final['target_tin']}")

    def test_video_streaming_attractor_identification(self):
        """Adaptive Video Streaming (bursty chunks: alternating bursts and pause intervals)."""
        results = []
        # Simulate video chunk download pattern: burst of packets with large CV in inter-arrival times
        dt_pattern = [1.0, 1.2, 55.0, 1.1, 60.0]
        for dt in dt_pattern:
            res = self.classifier.observe(self.video_flow, delta_t_ms=dt, pkt_len=1400)
            results.append(res)

        final = results[-1]
        self.assertEqual(final["class_name"], PhaseSpaceClassifier.CLASS_VIDEO)
        self.assertEqual(final["target_tin"], PhaseSpaceClassifier.TIN_VIDEO)
        self.assertGreaterEqual(final["confidence"], 0.70)
        print(f"[TEST PASSED] Adaptive Video Stream Identified: {final['class_name']}. Tin: {final['target_tin']}")

    def test_misclassification_correction_api(self):
        """Fulfills constraint: allow dynamic correction of misclassification and expose confidence."""
        for _ in range(4):
            self.classifier.observe(self.bulk_flow, delta_t_ms=0.2, pkt_len=1500)

        # Force correction: user declares this is actually interactive work tunnel
        self.classifier.correct_classification(self.bulk_flow, PhaseSpaceClassifier.CLASS_INTERACTIVE, reason="Work secure tunnel")

        res = self.classifier.observe(self.bulk_flow, delta_t_ms=0.2, pkt_len=1500)
        self.assertTrue(res["is_manual_override"])
        self.assertEqual(res["class_name"], PhaseSpaceClassifier.CLASS_INTERACTIVE)
        self.assertEqual(res["target_tin"], PhaseSpaceClassifier.TIN_PRIORITY)
        self.assertEqual(res["confidence"], 1.0)

        # Clear correction
        self.classifier.clear_correction(self.bulk_flow)
        res_after = self.classifier.observe(self.bulk_flow, delta_t_ms=0.2, pkt_len=1500)
        self.assertFalse(res_after["is_manual_override"])
        print("[TEST PASSED] Misclassification Correction API successfully verified.")

if __name__ == "__main__":
    unittest.main()
