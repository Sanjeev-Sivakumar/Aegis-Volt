"""
AURA-QoS Pyroute2 Netlink Enforcer
Directly manipulates Linux CAKE qdiscs via low-level Netlink sockets (rtnetlink).
Eliminates CLI subprocess fork/exec overhead for sub-millisecond atomic swaps.
License: LGPL v2.1+ / GPL v2
"""

import sys
import logging
from typing import Dict, Any, Optional

logger = logging.getLogger("AURA_QoS.NetlinkEnforcer")

class NetlinkTCEnforcer:
    """
    Interfaces directly with Linux kernel rtnetlink using pyroute2.IPRoute.
    Sends RTM_NEWQDISC with NLM_F_REPLACE / NLM_F_CREATE.
    """
    def __init__(self, interface: str = "veth-wan"):
        self.interface = interface
        self.has_pyroute2 = False
        self.ipr = None

        if sys.platform.startswith("linux"):
            try:
                from pyroute2 import IPRoute
                self.ipr = IPRoute()
                self.has_pyroute2 = True
                logger.info(f"[NETLINK] Successfully bound to Linux rtnetlink socket for {self.interface}")
            except Exception as e:
                logger.warning(f"[NETLINK] Could not initialize pyroute2 socket ({e}). Operating in simulation.")
        else:
            logger.info("[NETLINK] Non-Linux platform detected. Operating in simulation mode.")

    def get_interface_index(self) -> int:
        """Resolves interface name to kernel ifindex."""
        if self.has_pyroute2 and self.ipr:
            try:
                devs = self.ipr.link_lookup(ifname=self.interface)
                if devs:
                    return devs[0]
            except Exception:
                pass
        return 1

    def apply_cake_netlink(self, bandwidth_mbps: float, rtt_ms: float) -> bool:
        """
        Issues an atomic RCU qdisc replacement via Netlink socket:
        Equivalent to: tc qdisc replace dev <iface> root cake bandwidth <BW> diffserv4 rtt <RTT>
        """
        if self.has_pyroute2 and self.ipr:
            try:
                ifindex = self.get_interface_index()
                # Pyroute2 qdisc replace message
                self.ipr.tc(
                    "replace",
                    "cake",
                    ifindex,
                    handle=0x10000,
                    parent=0,
                    bandwidth=f"{int(bandwidth_mbps)}mbit",
                    diffserv="diffserv4",
                    rtt=f"{int(rtt_ms)}ms"
                )
                logger.info(f"[NETLINK_SOCKET] Atomic CAKE configured via Netlink: {bandwidth_mbps} Mbps, {rtt_ms}ms")
                return True
            except Exception as e:
                logger.warning(f"[NETLINK_SOCKET] Netlink call returned error ({e}). Falling back.")
                return False
        return True

    def rollback_netlink(self) -> bool:
        """Emergency rollback to baseline fq_codel over Netlink socket."""
        if self.has_pyroute2 and self.ipr:
            try:
                ifindex = self.get_interface_index()
                self.ipr.tc("replace", "fq_codel", ifindex, handle=0x10000, parent=0)
                logger.info("[NETLINK_SOCKET] Reverted root qdisc to baseline fq_codel.")
                return True
            except Exception as e:
                logger.error(f"[NETLINK_SOCKET] Rollback error: {e}")
                return False
        return True

    def close(self):
        if self.has_pyroute2 and self.ipr:
            try:
                self.ipr.close()
            except Exception:
                pass
