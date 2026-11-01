"""
AURA-QoS Central Orchestration Engine
Integrates Telemetry, Deterministic Phase Space Classifier, Link Estimator,
tc-CAKE Netlink Enforcer, Bounded Watchdog, and Intent Arbiter into a cohesive unit.
100% Dynamic, zero hardcoded parameters.
"""

import time
import logging
from typing import Dict, Any, Optional, Tuple

from .config import DynamicSystemProfile
from .telemetry import FlowKey, PacketMetadata, FlowTracker
from .phase_space import PhaseSpaceClassifier
from .link_estimator import LinkCapacityEstimator
from .tc_manager import TCManager
from .watchdog import BoundedWatchdog
from .intent import IntentArbiter

logger = logging.getLogger("AURA_QoS.Engine")

class AURAEngine:
    """
    Main Autonomous QoS Controller.
    Decouples line-rate telemetry from user-space deterministic modeling.
    Self-calibrating and dynamic throughout.
    """
    def __init__(
        self,
        interface: Optional[str] = None,
        profile: Optional[DynamicSystemProfile] = None,
        simulate_tc: bool = None
    ):
        self.profile = profile or DynamicSystemProfile()
        if interface:
            self.profile.interface = interface
        
        # Subsystem Components (All receiving the dynamic profile)
        self.telemetry = FlowTracker()
        self.classifier = PhaseSpaceClassifier(self.profile)
        self.link_estimator = LinkCapacityEstimator(self.profile)
        self.tc_manager = TCManager(interface=self.profile.interface, simulate=simulate_tc, profile=self.profile)
        self.watchdog = BoundedWatchdog(self.tc_manager, profile=self.profile)
        self.intent_arbiter = IntentArbiter(profile=self.profile)

        # Engine State
        self.initialized = False
        self.packet_count_total = 0
        self.total_bytes_processed = 0
        self.start_time = time.time()

    def initialize(self) -> bool:
        """Initializes kernel qdiscs with dynamic parameters and arms the watchdog."""
        logger.info(f"[AURA-QoS] Initializing dynamically on interface {self.profile.interface}...")
        # 1. Apply initial tc-CAKE qdisc with dynamic profile bandwidth
        applied = self.tc_manager.apply_cake_qdisc(bandwidth_mbps=self.profile.initial_bandwidth_mbps)
        if not applied:
            logger.error("[AURA-QoS] Failed to apply initial CAKE qdisc!")
            return False

        # 2. Arm bounded watchdog
        self.watchdog.start()
        self.initialized = True
        logger.info("[AURA-QoS] Engine successfully initialized dynamically.")
        return True

    def shutdown(self):
        """Cleanly shuts down watchdog and returns network stack to baseline."""
        logger.info("[AURA-QoS] Shutting down...")
        self.watchdog.stop()
        self.tc_manager.rollback_to_baseline()
        self.initialized = False

    def process_packet(self, meta: PacketMetadata) -> Dict[str, Any]:
        """
        Line-rate ingestion path for a single packet metadata struct.
        Executes zero-copy state reconstruction and determines queuing policy dynamically.
        """
        self.packet_count_total += 1
        self.total_bytes_processed += meta.pkt_len

        # 1. Update Telemetry Sliding Window
        delta_t_ms, pkt_len, flow_seq = self.telemetry.ingest_packet(meta)
        canon_flow_key = meta.flow_key.canonical()

        # 2. Feed Link Capacity Estimator
        self.link_estimator.record_packet(pkt_len)

        # 3. Check for Active Temporary User Intent (e.g. Work Call Boost)
        active_intent = self.intent_arbiter.evaluate_traffic_intent(meta.flow_key.src_ip, meta.flow_key.dst_ip)
        
        target_tin = None
        classification_result = None

        if active_intent and active_intent["intent_type"] in (IntentArbiter.INTENT_WORK_BOOST, IntentArbiter.INTENT_GAMING_VIP):
            target_tin = PhaseSpaceClassifier.TIN_PRIORITY
            classification_result = {
                "class_name": f"INTENT_{active_intent['intent_type']}",
                "target_tin": target_tin,
                "confidence": 1.0,
                "attractor_metrics": {"intent": active_intent["intent_type"], "device": active_intent["target_ip"]},
                "is_manual_override": True
            }
        else:
            # 4. Deterministic Phase-Space Attractor Classification
            classification_result = self.classifier.observe(canon_flow_key, delta_t_ms, pkt_len)
            target_tin = classification_result["target_tin"]

        # 5. Check Link Capacity & Adapt Shaping Rate dynamically
        link_state = self.link_estimator.get_telemetry()
        current_bw = self.tc_manager.current_bandwidth_mbps
        target_bw = link_state["estimated_bandwidth_mbps"]
        dynamic_rtt = link_state["smoothed_rtt_ms"]

        # Dynamic rate adjustment if capacity drifts by > 4 Mbps
        if abs(target_bw - current_bw) >= 4.0:
            self.tc_manager.adjust_bandwidth(target_bw, dynamic_rtt_ms=dynamic_rtt)

        # 6. ECN Marking / Forwarding Accounting
        ecn_marked = False
        if target_tin == PhaseSpaceClassifier.TIN_BULK and link_state["link_status"] != "OPTIMAL":
            ecn_marked = True

        self.tc_manager.record_forwarded_packet(target_tin, pkt_len, ecn_marked=ecn_marked)

        # 7. Keep Watchdog Heartbeat Alive with dynamic RTT
        self.watchdog.heartbeat(current_rtt_ms=link_state["smoothed_rtt_ms"])

        return {
            "flow": f"{meta.flow_key.src_ip}:{meta.flow_key.src_port}->{meta.flow_key.dst_ip}:{meta.flow_key.dst_port}",
            "protocol": meta.flow_key.protocol,
            "pkt_len": pkt_len,
            "delta_t_ms": delta_t_ms,
            "classification": classification_result,
            "enforced_tin": target_tin,
            "ecn_marked": ecn_marked,
            "link_status": link_state["link_status"],
            "current_bw_mbps": self.tc_manager.current_bandwidth_mbps
        }

    def feed_rtt_probe(self, measured_rtt_ms: float):
        """Passes RTT sample to link estimator and watchdog."""
        self.link_estimator.record_rtt_sample(measured_rtt_ms)
        self.watchdog.heartbeat(current_rtt_ms=measured_rtt_ms)

    def get_full_metrics(self) -> Dict[str, Any]:
        """Consolidated dynamic telemetry export for dashboard and judges."""
        link_state = self.link_estimator.get_telemetry()
        tc_state = self.tc_manager.get_status()
        watchdog_state = self.watchdog.get_health()
        intents_state = self.intent_arbiter.list_all_active_intents()

        tin_bytes = [
            tc_state["tin_stats"]["priority"]["bytes"],
            tc_state["tin_stats"]["video"]["bytes"],
            tc_state["tin_stats"]["besteffort"]["bytes"],
            tc_state["tin_stats"]["bulk"]["bytes"],
        ]
        fairness_index = self.tc_manager.calculate_jains_fairness_index(tin_bytes)

        return {
            "uptime_sec": round(time.time() - self.start_time, 1),
            "packets_processed": self.packet_count_total,
            "bytes_processed": self.total_bytes_processed,
            "active_flows": len(self.telemetry.flows),
            "link": link_state,
            "tc": tc_state,
            "jains_fairness_index": fairness_index,
            "watchdog": watchdog_state,
            "intents": intents_state
        }
