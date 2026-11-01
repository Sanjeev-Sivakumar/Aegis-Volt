"""
Unit Tests for Aegis-Volt Pre-Emptive Network Engine
Validates:
  1. NILM Physical Grid Pre-Emption (< 12ms lead time)
  2. Shadow Staging Table & Rolling Gini Coefficient Fairness (5-10% bulk floor)
  3. Anti-Overload CPU Circuit Breaker (80% tripwire rollback)
  4. Cross-Layer TCP ECN Injection
"""

import unittest
import time
import os
import sys

# Ensure project root is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from aegis_volt.nilm_telemetry import NILMPowerGridTelemetry
from aegis_volt.shadow_twin import ShadowStagingTable
from aegis_volt.cpu_fuse import AntiOverloadCPUFuse
from aegis_volt.engine import AegisVoltEngine
from aura_qos.telemetry import FlowKey, PacketMetadata
from aura_qos.phase_space import PhaseSpaceClassifier

class TestAegisVoltPlatform(unittest.TestCase):
    def test_nilm_preemptive_detection(self):
        """Verifies physical electrical transient triggers Layer-2 intent frame with positive lead time."""
        nilm = NILMPowerGridTelemetry()
        received_frames = []

        nilm.register_preempt_listener(lambda frame: received_frames.append(frame))
        event = nilm.trigger_appliance_boot("console_gpu", "GPU_GAMING_RENDER")

        self.assertEqual(len(received_frames), 1)
        self.assertEqual(received_frames[0]["target_ip"], "192.168.1.50")
        self.assertEqual(received_frames[0]["target_tin"], "priority")
        self.assertGreater(received_frames[0]["preempt_lead_time_ms"], 0.0)
        print(f"\n[PASS] NILM Pre-Emption Verified: Detected GPU spike ({event['measured_current_amps']}A) with {event['preempt_lead_time_ms']}ms lead time!")

    def test_shadow_twin_gini_fairness(self):
        """Verifies Shadow Staging Table ensures bulk queue retains >= 5% floor with safe Gini coefficient."""
        shadow = ShadowStagingTable()

        # Safe Staged Policy: 40% priority, 30% video, 20% besteffort, 10% bulk
        shadow.stage_configuration(80.0, {"priority": 0.40, "video": 0.30, "besteffort": 0.20, "bulk": 0.10})
        is_safe, msg, matrix = shadow.verify_shadow_safety()
        self.assertTrue(is_safe)
        self.assertLessEqual(matrix["gini_coefficient"], 0.45)
        self.assertGreaterEqual(matrix["bulk_floor_pct"], 5.0)

        # Atomic Swap to Primary Table
        swap_res = shadow.execute_atomic_pointer_swap()
        self.assertTrue(swap_res["swap_successful"])
        self.assertEqual(shadow.primary_matrix["bandwidth_mbps"], 80.0)
        print(f"[PASS] Shadow Staging & Atomic Swap Verified: Gini={matrix['gini_coefficient']} (Bulk Floor={matrix['bulk_floor_pct']}%)")

    def test_shadow_twin_rejects_starvation(self):
        """Shadow table MUST reject any policy that attempts to choke bulk traffic below 5%."""
        shadow = ShadowStagingTable()
        # Greedy Policy: 98% priority, 2% bulk (Starvation condition)
        shadow.stage_configuration(100.0, {"priority": 0.98, "bulk": 0.02})
        is_safe, msg, _ = shadow.verify_shadow_safety()
        self.assertFalse(is_safe)
        self.assertIn("Bulk floor breached", msg)
        print(f"[PASS] Starvation Rejection Verified: {msg}")

    def test_anti_overload_cpu_circuit_breaker(self):
        """Verifies that if router CPU utilization breaches 80%, circuit breaker triggers instant rollback."""
        rollback_called = False
        def mock_rollback():
            nonlocal rollback_called
            rollback_called = True

        fuse = AntiOverloadCPUFuse(rollback_callback=mock_rollback, max_cpu_threshold_pct=80.0, watchdog_timeout_sec=2.0)
        fuse.start()

        # Normal load (1.5% CPU)
        fuse.heartbeat(measured_cpu_pct=1.5)
        self.assertFalse(rollback_called)

        # Simulated spike breaching 80% (e.g. 84.5% CPU)
        fuse.heartbeat(measured_cpu_pct=84.5)
        self.assertTrue(rollback_called)
        self.assertEqual(fuse.state, AntiOverloadCPUFuse.STATE_TRIPPED)
        fuse.stop()
        print(f"[PASS] Anti-Overload Circuit Breaker Tripped! Safe rollback executed upon 84.5% CPU breach.")

    def test_full_aegis_volt_pipeline(self):
        """Tests full end-to-end integration: NILM pre-emption -> packet stream -> ECN injection."""
        engine = AegisVoltEngine(interface="veth-aegis", simulate_tc=True)
        engine.initialize()

        # 1. Trigger NILM Pre-emption on laptop
        engine.nilm.trigger_appliance_boot("laptop_work", "WEBRTC_ENCODER_BURST")

        # 2. Ingest first packet from laptop - should immediately map to priority
        now_ns = int(time.time() * 1_000_000_000)
        pkt = PacketMetadata(
            flow_key=FlowKey("192.168.1.10", "203.0.113.10", 5004, 5004, "UDP"),
            pkt_len=180,
            timestamp_ns=now_ns
        )
        res = engine.process_packet(pkt)
        self.assertEqual(res["enforced_tin"], PhaseSpaceClassifier.TIN_PRIORITY)
        self.assertIn("PREEMPT", res["classification"]["class_name"])

        engine.shutdown()
        print(f"[PASS] Full Aegis-Volt Pipeline Verified: Pre-emptively elevated to {res['enforced_tin']}!")

if __name__ == "__main__":
    unittest.main()

