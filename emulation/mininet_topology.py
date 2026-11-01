"""
AURA-QoS Residential Broadband Mininet Topology
Emulates a modern smart home network connected over a constrained WAN link.
Integrates tc-netem for WAN impairment (RTT delay, jitter, dynamic capacity drops).
License: Open Source (BSD)
"""

import sys
import os
import subprocess
import logging

logger = logging.getLogger("AURA_QoS.MininetTopology")

def build_mininet_topology():
    """
    Constructs the Mininet residential network:
    [ h_zoom (192.168.1.10) ]    ──┐
    [ h_video (192.168.1.20) ]   ──┼── [ s_lan ] ── [ r_gateway (AURA-QoS) ] ── (WAN tc-netem) ── [ h_wan (203.0.113.1) ]
    [ h_torrent (192.168.1.30) ] ──┘
    """
    try:
        from mininet.topo import Topo
        from mininet.net import Mininet
        from mininet.node import Node, OVSBridge
        from mininet.link import TCLink
        from mininet.cli import CLI

        class ResidentialGatewayTopo(Topo):
            def build(self):
                # 1. WAN Server (External CDN / Zoom Server / Torrent Peer)
                wan_server = self.addHost("h_wan", ip="203.0.113.1/24", defaultRoute="via 203.0.113.254")

                # 2. Linux Gateway / Home Router (runs AURA-QoS)
                router = self.addHost("r_gateway", ip="192.168.1.1/24")

                # 3. LAN Switch
                lan_switch = self.addSwitch("s_lan", cls=OVSBridge)

                # 4. Residential Home Clients
                h_zoom = self.addHost("h_zoom", ip="192.168.1.10/24", defaultRoute="via 192.168.1.1")
                h_video = self.addHost("h_video", ip="192.168.1.20/24", defaultRoute="via 192.168.1.1")
                h_torrent = self.addHost("h_torrent", ip="192.168.1.30/24", defaultRoute="via 192.168.1.1")

                # Connect LAN hosts to LAN switch
                self.addLink(h_zoom, lan_switch)
                self.addLink(h_video, lan_switch)
                self.addLink(h_torrent, lan_switch)

                # Connect LAN switch to Router internal port
                self.addLink(lan_switch, router, intfName2="r-lan")

                # Connect Router WAN port to WAN server with tc-netem delay & bandwidth limits
                self.addLink(
                    router, wan_server,
                    intfName1="r-wan",
                    bw=100, delay="15ms", max_queue_size=1000
                )

        logger.info("[MININET] Initializing Residential Gateway Topology...")
        topo = ResidentialGatewayTopo()
        net = Mininet(topo=topo, link=TCLink)
        net.start()

        # Configure router routing & forwarding
        r = net.get("r_gateway")
        r.cmd("sysctl -w net.ipv4.ip_forward=1")
        r.cmd("ifconfig r-wan 203.0.113.254/24 up")
        r.cmd("iptables -t nat -A POSTROUTING -o r-wan -j MASQUERADE")

        logger.info("[MININET] Topology active. Nodes: [h_zoom, h_video, h_torrent] -> r_gateway -> h_wan")
        return net

    except ImportError:
        logger.warning("[MININET] Mininet python library not installed in this environment.")
        return None

if __name__ == "__main__":
    net = build_mininet_topology()
    if net:
        try:
            from mininet.cli import CLI
            CLI(net)
        finally:
            net.stop()
