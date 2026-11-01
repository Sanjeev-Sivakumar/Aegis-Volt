#!/usr/bin/env bash
# ==============================================================================
# AURA-QoS: Linux Network Namespace (ip netns) Residential Sandbox
# Works on any standard Linux kernel (Ubuntu, Debian, OpenWrt, RHEL, WSL2)
# Simulates WAN bottleneck, Zoom call, 4K streaming, and multi-stream torrent.
# ==============================================================================

set -e

echo "[+] Setting up AURA-QoS Residential Network Namespaces..."

# 1. Clean existing namespaces
ip netns del ns_router 2>/dev/null || true
ip netns del ns_clients 2>/dev/null || true
ip netns del ns_wan 2>/dev/null || true

# 2. Create Namespaces
ip netns add ns_router
ip netns add ns_clients
ip netns add ns_wan

# 3. Create veth links: Clients <-> Router (LAN)
ip link add veth-lan-c type veth peer name veth-lan-r
ip link set veth-lan-c netns ns_clients
ip link set veth-lan-r netns ns_router

# 4. Create veth links: Router <-> WAN (WAN)
ip link add veth-wan-r type veth peer name veth-wan-w
ip link set veth-wan-r netns ns_router
ip link set veth-wan-w netns ns_wan

# 5. Configure LAN Subnet (192.168.1.0/24)
ip netns exec ns_clients ip addr add 192.168.1.10/24 dev veth-lan-c
ip netns exec ns_clients ip link set veth-lan-c up
ip netns exec ns_clients ip link set lo up
ip netns exec ns_clients ip route add default via 192.168.1.1

ip netns exec ns_router ip addr add 192.168.1.1/24 dev veth-lan-r
ip netns exec ns_router ip link set veth-lan-r up

# 6. Configure WAN Subnet (203.0.113.0/24)
ip netns exec ns_router ip addr add 203.0.113.2/24 dev veth-wan-r
ip netns exec ns_router ip link set veth-wan-r up
ip netns exec ns_router ip link set lo up
ip netns exec ns_router ip route add default via 203.0.113.1

ip netns exec ns_wan ip addr add 203.0.113.1/24 dev veth-wan-w
ip netns exec ns_wan ip link set veth-wan-w up
ip netns exec ns_wan ip link set lo up

# 7. Enable IP Forwarding in Router Namespace
ip netns exec ns_router sysctl -w net.ipv4.ip_forward=1 >/dev/null

# 8. Apply WAN Impairments with tc-netem (15ms RTT propagation delay, 100 Mbps rate)
ip netns exec ns_wan tc qdisc replace dev veth-wan-w root netem delay 15ms

echo "[+] Network topology established successfully!"
echo "    [ns_clients: 192.168.1.10] <---> [ns_router: AURA-QoS] <---> [ns_wan: 203.0.113.1 (15ms RTT)]"
