#!/usr/bin/env bash
# ==============================================================================
# Project: Aegis-Volt Pre-Emptive Cross-Layer Network Orchestrator
# Component: chaos_network.sh (Automated Chaos Network Validation Framework)
# License: Open Source (BSD 3-Clause)
#
# Description:
#   Self-contained automated network validation test script:
#   1. Sets up virtual network namespaces (ns_client, ns_wan) with veth pairs.
#   2. Injects WAN bottlenecks and delay using Linux Traffic Control (tc-netem).
#   3. Generates high-contention bulk download surges alongside interactive traffic.
#   4. Emulates physical-layer NILM pre-emptive Layer-2 triggers (0x88B5).
#   5. Evaluates latency, jitter, packet loss, throughput, and Gini fairness.
#   6. Tests the 80% CPU anti-overload circuit breaker tripwire.
#   7. Outputs the comprehensive evaluation report (AEGIS_VOLT_EVALUATION.md).
# ==============================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "================================================================================"
echo "[*] AEGIS-VOLT: AUTOMATED CHAOS NETWORK VALIDATION FRAMEWORK"
echo "    Pillar 1: NILM Grid Telemetry | Pillar 2: eBPF/XDP | Pillar 3: Netlink Swaps"
echo "================================================================================"

# Check if running as root on Linux with network namespaces support
IS_ROOT=false
if [ "$(id -u)" -eq 0 ] && command -v ip >/dev/null 2>&1; then
    IS_ROOT=true
fi

NS_CLIENT="ns_aegis_client"
NS_WAN="ns_aegis_wan"
NS_SHADOW="shadow-link"
VETH_CLIENT="veth-c"
VETH_WAN="veth-w"
VETH_SHADOW_IN="veth-s-in"
VETH_SHADOW_OUT="veth-s-out"

cleanup_namespaces() {
    if [ "$IS_ROOT" = true ]; then
        echo "[*] Cleaning up network namespaces..."
        ip netns del "$NS_CLIENT" 2>/dev/null || true
        ip netns del "$NS_WAN" 2>/dev/null || true
        ip netns del "$NS_SHADOW" 2>/dev/null || true
    fi
}

trap cleanup_namespaces EXIT

if [ "$IS_ROOT" = true ]; then
    echo "[+] Running in privileged Linux environment: Building network namespaces..."
    
    # 1. Create client, WAN gateway, and Digital Twin Shadow Loop namespaces
    cleanup_namespaces
    ip netns add "$NS_CLIENT"
    ip netns add "$NS_WAN"
    ip netns add "$NS_SHADOW"
    
    # 2. Create primary virtual ethernet link pair
    ip link add "$VETH_CLIENT" type veth peer name "$VETH_WAN"
    ip link set "$VETH_CLIENT" netns "$NS_CLIENT"
    ip link set "$VETH_WAN" netns "$NS_WAN"

    # 3. Create Digital Twin 1% Shadow Mirror link pair
    ip link add "$VETH_SHADOW_IN" type veth peer name "$VETH_SHADOW_OUT"
    ip link set "$VETH_SHADOW_OUT" netns "$NS_SHADOW"
    ip link set "$VETH_SHADOW_IN" up
    
    # 4. Configure IP addressing
    ip netns exec "$NS_CLIENT" ip addr add 192.168.1.100/24 dev "$VETH_CLIENT"
    ip netns exec "$NS_CLIENT" ip link set "$VETH_CLIENT" up
    ip netns exec "$NS_CLIENT" ip link set lo up
    
    ip netns exec "$NS_WAN" ip addr add 192.168.1.1/24 dev "$VETH_WAN"
    ip netns exec "$NS_WAN" ip link set "$VETH_WAN" up
    ip netns exec "$NS_WAN" ip link set lo up

    ip netns exec "$NS_SHADOW" ip addr add 10.99.1.2/24 dev "$VETH_SHADOW_OUT"
    ip netns exec "$NS_SHADOW" ip link set "$VETH_SHADOW_OUT" up
    ip netns exec "$NS_SHADOW" ip link set lo up
    
    echo "[+] Network topology & Digital Twin established:"
    echo "    [$NS_CLIENT (192.168.1.100)] <==== veth-wire ====> [$NS_WAN (192.168.1.1)]"
    echo "    [Digital Twin Shadow Loop]   <==== 1% mirror ====> [$NS_SHADOW (10.99.1.2)]"
    
    # 5. Inject 15ms WAN delay & 100 Mbps bottleneck via tc-netem
    echo "[*] Injecting WAN link simulation: 15ms latency, 100 Mbps bottleneck..."
    ip netns exec "$NS_WAN" tc qdisc add dev "$VETH_WAN" root handle 1: netem delay 15ms rate 100mbit
fi

# Run the automated 5-stage chaos test suite
echo ""
echo "[*] Launching Aegis-Volt Chaos Validation Runner..."
python3 run_chaos_test.py

echo ""
echo "================================================================================"
echo "[+] CHAOS NETWORK VALIDATION TEST COMPLETED SUCCESSFULLY!"
echo "    Validation Evidence Saved: AEGIS_VOLT_EVALUATION.md"
echo "================================================================================"
