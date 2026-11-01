"""
Phase 1 Integration Test: End-to-End AURAEngine Pipeline
Tests the complete flow from packet ingestion, dynamic phase-space classification,
link capacity adaptation, intent override, to watchdog health.
"""

import unittest
import time
from aura_qos.engine import AURAEngine
from aura_qos.telemetry import FlowKey, PacketMetadata
from aura_qos.phase_space import PhaseSpaceClassifier

class TestEngineIntegration(unittest.TestCase):
    def setUp(self):
        self.engine = AURAEngine(interface="veth-test", simulate_tc=True)
        self.engine.initialize()

    def tearDown(self):
        self.engine.shutdown()

    def test_full_pipeline_multi_traffic(self):
        """Streams mixed traffic (Zoom call + Torrent download) through AURA-QoS engine."""
        zoom_flow = FlowKey(src_ip="192.168.1.10", dst_ip="203.0.113.10", src_port=5004, dst_port=5004, protocol="UDP")
        iso_flow = FlowKey(src_ip="192.168.1.20", dst_ip="198.51.100.20", src_port=52000, dst_port=443, protocol="TCP")

        now_ns = int(time.time() * 1_000_000_000)

        # 1. Ingest 5 packets of Zoom Call (steady 20ms intervals, 180 bytes)
        zoom_results = []
        for i in range(5):
            t_ns = now_ns + (i * 20_000_000)  # 20ms steps
            pkt = PacketMetadata(flow_key=zoom_flow, pkt_len=180, timestamp_ns=t_ns, ip_version=4)
            res = self.engine.process_packet(pkt)
            zoom_results.append(res)

        final_zoom = zoom_results[-1]
        self.assertEqual(final_zoom["enforced_tin"], PhaseSpaceClassifier.TIN_PRIORITY)
        print(f"\n[INTEGRATION] Zoom Stream -> Enforced Tin: {final_zoom['enforced_tin']} (Confidence: {final_zoom['classification']['confidence']})")

        # 2. Ingest 5 packets of ISO Download (0.2ms intervals, 1500 bytes MTU)
        iso_results = []
        for i in range(5):
            t_ns = now_ns + 100_000_000 + (i * 200_000)  # 0.2ms steps
            pkt = PacketMetadata(flow_key=iso_flow, pkt_len=1500, timestamp_ns=t_ns, ip_version=4)
            res = self.engine.process_packet(pkt)
            iso_results.append(res)

        final_iso = iso_results[-1]
        self.assertEqual(final_iso["enforced_tin"], PhaseSpaceClassifier.TIN_BULK)
        print(f"[INTEGRATION] Bulk ISO Stream -> Enforced Tin: {final_iso['enforced_tin']} (Confidence: {final_iso['classification']['confidence']})")

        # 3. Test Intent Override ("Work Call Boost" on the ISO IP to verify dynamic prioritization)
        self.engine.intent_arbiter.set_intent("192.168.1.20", duration_sec=60)
        boosted_pkt = PacketMetadata(flow_key=iso_flow, pkt_len=1500, timestamp_ns=now_ns + 200_000_000, ip_version=4)
        boosted_res = self.engine.process_packet(boosted_pkt)
        self.assertEqual(boosted_res["enforced_tin"], PhaseSpaceClassifier.TIN_PRIORITY)
        self.assertTrue(boosted_res["classification"]["is_manual_override"])
        print(f"[INTEGRATION] Intent Override Verified: ISO host dynamically elevated to -> {boosted_res['enforced_tin']}")

        # 4. Inspect full consolidated engine metrics
        metrics = self.engine.get_full_metrics()
        self.assertGreater(metrics["packets_processed"], 10)
        self.assertEqual(metrics["watchdog"]["watchdog_state"], "ARMED_HEALTHY")
        print(f"[INTEGRATION] Consolidated Metrics Verified. Total Packets: {metrics['packets_processed']}, Watchdog: {metrics['watchdog']['watchdog_state']}")

if __name__ == "__main__":
    unittest.main()
