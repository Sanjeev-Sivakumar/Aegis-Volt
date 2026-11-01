"""
AURA-QoS Dynamic Intent Arbiter Module
Provides bounded, temporary user service intents (e.g. Work Call Boost, Gaming VIP)
with automatic lease expiry while retaining fairness.
"""

import time
import logging
from typing import Dict, Any, Optional
from .config import DynamicSystemProfile

logger = logging.getLogger("AURA_QoS.IntentArbiter")

class IntentArbiter:
    """
    Manages user/operator intent overrides.
    Ensures that temporary priorities automatically expire back to democratic fairness.
    """
    INTENT_BALANCED = "BALANCED_FAIRNESS"
    INTENT_WORK_BOOST = "WORK_CALL_BOOST"
    INTENT_GAMING_VIP = "GAMING_VIP"
    INTENT_BACKGROUND_THROTTLE = "BACKGROUND_THROTTLE"

    def __init__(self, default_lease_sec: Optional[int] = None, profile: Optional[DynamicSystemProfile] = None):
        self.profile = profile or DynamicSystemProfile()
        self.default_lease_sec = default_lease_sec or self.profile.default_lease_sec
        # Active intents: target_id (IP or device label) -> intent_dict
        self.active_intents: Dict[str, Dict[str, Any]] = {}

    def set_intent(
        self,
        target_ip: str,
        intent_type: str = INTENT_WORK_BOOST,
        duration_sec: Optional[int] = None,
        reason: str = "User initiated intent override"
    ) -> Dict[str, Any]:
        """
        Activates a temporary intent for a specific device IP.
        """
        duration = duration_sec if duration_sec is not None else self.default_lease_sec
        now = time.time()
        expires_at = now + duration

        record = {
            "target_ip": target_ip,
            "intent_type": intent_type,
            "activated_at": now,
            "expires_at": expires_at,
            "duration_sec": duration,
            "reason": reason,
            "status": "ACTIVE"
        }
        self.active_intents[target_ip] = record
        logger.info(f"[INTENT] Activated '{intent_type}' for {target_ip} (Lease: {duration}s)")
        return self.get_intent_state(target_ip)

    def cancel_intent(self, target_ip: str) -> bool:
        """Manually revokes an active intent before lease expiration."""
        if target_ip in self.active_intents:
            del self.active_intents[target_ip]
            logger.info(f"[INTENT] Manually revoked intent for {target_ip}. Returned to {self.INTENT_BALANCED}.")
            return True
        return False

    def evaluate_traffic_intent(self, src_ip: str, dst_ip: str) -> Optional[Dict[str, Any]]:
        """
        Checks if either endpoint has an active, unexpired intent.
        Automatically garbage-collects expired leases.
        """
        now = time.time()
        for ip in (src_ip, dst_ip):
            if ip in self.active_intents:
                intent = self.active_intents[ip]
                if now > intent["expires_at"]:
                    logger.info(f"[INTENT EXPIRED] Lease for {ip} expired. Reverting to balanced fairness.")
                    del self.active_intents[ip]
                    continue
                return intent
        return None

    def get_intent_state(self, target_ip: str) -> Dict[str, Any]:
        """Returns the current intent status for a specific IP."""
        now = time.time()
        if target_ip in self.active_intents:
            intent = self.active_intents[target_ip]
            remaining = max(0.0, intent["expires_at"] - now)
            if remaining <= 0:
                del self.active_intents[target_ip]
                return {"target_ip": target_ip, "intent_type": self.INTENT_BALANCED, "active": False}
            return {
                "target_ip": target_ip,
                "intent_type": intent["intent_type"],
                "active": True,
                "seconds_remaining": round(remaining, 1),
                "reason": intent["reason"]
            }
        return {"target_ip": target_ip, "intent_type": self.INTENT_BALANCED, "active": False}

    def list_all_active_intents(self) -> Dict[str, Any]:
        """Provides full visibility of all active device intents."""
        now = time.time()
        expired = [ip for ip, data in self.active_intents.items() if now > data["expires_at"]]
        for ip in expired:
            del self.active_intents[ip]

        summary = {}
        for ip, data in self.active_intents.items():
            summary[ip] = {
                "intent_type": data["intent_type"],
                "seconds_remaining": round(max(0.0, data["expires_at"] - now), 1),
                "reason": data["reason"]
            }
        return {"total_active_intents": len(summary), "intents": summary}
