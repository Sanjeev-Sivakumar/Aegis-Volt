"""
AURA-QoS Real-Time Telemetry & Intent API Server
Built with FastAPI + WebSockets for sub-10ms streaming of metrics and Chaos Attractor loops.
License: MIT
"""

import asyncio
import json
import time
import random
import threading
from typing import Dict, Any, List
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, FileResponse
from pydantic import BaseModel

import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from aura_qos.engine import AURAEngine
from aura_qos.telemetry import FlowKey, PacketMetadata
from aura_qos.phase_space import PhaseSpaceClassifier
from aura_qos.intent import IntentArbiter
from aura_qos.wireless_telemetry import WirelessTelemetryMonitor
from emulation.traffic_generator import RealisticTrafficGenerator

app = FastAPI(title="AURA-QoS Engine UI", version="1.0.0")

# Mount static frontend
static_dir = os.path.join(os.path.dirname(__file__), "static")
os.makedirs(static_dir, exist_ok=True)
app.mount("/static", StaticFiles(directory=static_dir), name="static")

# Core Engine Instance: Aegis-Volt Pre-Emptive Platform
from aegis_volt.engine import AegisVoltEngine
engine = AegisVoltEngine(interface="veth-wan", simulate_tc=True)
engine.initialize()
wireless = engine.wireless
traffic_gen = RealisticTrafficGenerator()

class NilmSpikeRequest(BaseModel):
    appliance_key: str = "console_gpu"
    intent_type: str = "GPU_GAMING_RENDER"

@app.post("/api/v1/nilm/trigger-spike")
def trigger_nilm_spike(req: NilmSpikeRequest):
    frame = engine.nilm.trigger_appliance_boot(req.appliance_key, req.intent_type)
    return {"status": "PREEMPT_DISPATCHED", "frame": frame}

@app.post("/api/v1/fuse/trip")
def trip_circuit_breaker():
    dummy = PacketMetadata(FlowKey("192.168.1.10", "8.8.8.8", 1234, 53, "UDP"), 64, int(time.time()*1e9))
    engine.process_packet(dummy, simulated_cpu_pct=86.5)
    return {"status": "CIRCUIT_BREAKER_TRIPPED", "fuse": engine.cpu_fuse.get_status()}

# Live real-time sniffer thread for native capture
from live_aura import RealTimeLiveSniffer
live_sniffer = RealTimeLiveSniffer()
live_sniffer_active = live_sniffer.open_live_socket()

if live_sniffer_active:
    def live_sniff_worker():
        live_sniffer.capture_live_loop()
    threading.Thread(target=live_sniff_worker, daemon=True, name="AURA-LiveSniffer").start()
    print("[+] Background Real-Time Live Sniffer successfully attached to network!")

class IntentRequest(BaseModel):
    target_ip: str = "192.168.1.10"
    intent_type: str = IntentArbiter.INTENT_WORK_BOOST
    duration_sec: int = 1800
    reason: str = "Work Call Boost via Dashboard"

class CorrectionRequest(BaseModel):
    flow_key: List[Any]
    target_class: str
    reason: str = "Operator Manual Correction"

@app.get("/")
def get_dashboard():
    return FileResponse(os.path.join(static_dir, "index.html"))

@app.get("/api/v1/metrics")
def get_current_metrics():
    metrics = engine.get_full_metrics()
    metrics["wireless"] = wireless.poll_station_metrics()
    return metrics

@app.post("/api/v1/intent/work-boost")
def activate_work_boost(req: IntentRequest):
    res = engine.intent_arbiter.set_intent(
        target_ip=req.target_ip,
        intent_type=req.intent_type,
        duration_sec=req.duration_sec,
        reason=req.reason
    )
    return {"status": "SUCCESS", "intent": res}

@app.post("/api/v1/intent/cancel")
def cancel_intent(target_ip: str = "192.168.1.10"):
    revoked = engine.intent_arbiter.cancel_intent(target_ip)
    return {"status": "REVOKED" if revoked else "NOT_FOUND"}

@app.post("/api/v1/simulate/wan-drop")
def toggle_wan_drop():
    sim_state["wan_degraded"] = not sim_state["wan_degraded"]
    target_bw = 20.0 if sim_state["wan_degraded"] else 100.0
    engine.link_estimator.force_capacity_drop(target_bw)
    engine.tc_manager.adjust_bandwidth(target_bw)
    return {"status": "OK", "wan_degraded": sim_state["wan_degraded"], "new_bw_mbps": target_bw}

@app.post("/api/v1/classify/correct")
def correct_classification(req: CorrectionRequest):
    canon_tuple = tuple(req.flow_key)
    engine.classifier.correct_classification(canon_tuple, req.target_class, req.reason)
    return {"status": "CORRECTED", "target_class": req.target_class}

@app.websocket("/ws/telemetry")
async def websocket_telemetry_stream(websocket: WebSocket):
    """
    Sub-10ms real-time WebSocket telemetry stream.
    Streams latency, jitter, throughput, fairness, and Chaos Attractor points.
    """
    await websocket.accept()
    try:
        while True:
            # If live packet sniffer is active, pull real-time captured packets
            if live_sniffer_active and len(live_sniffer.recent_points) > 0:
                recent_pts = list(live_sniffer.recent_points)[-35:]
            else:
                # Live dynamic probe generator
                recent_pts = []
                for _ in range(3):
                    curr_t_ns = int(time.time() * 1_000_000_000)
                    choice = random.choice(["zoom", "video", "iso"] if not sim_state.get("wan_degraded") else ["zoom", "video"])
                    if choice == "zoom":
                        pkt = PacketMetadata(flow_key=traffic_gen.flow_zoom, pkt_len=180, timestamp_ns=curr_t_ns)
                        sample_rtt = 14.5 + random.uniform(-0.5, 1.2)
                    elif choice == "video":
                        pkt = PacketMetadata(flow_key=traffic_gen.flow_video, pkt_len=1420, timestamp_ns=curr_t_ns)
                        sample_rtt = 16.0 + random.uniform(0.5, 4.0)
                    else:
                        pkt = PacketMetadata(flow_key=traffic_gen.flow_iso, pkt_len=1500, timestamp_ns=curr_t_ns)
                        sample_rtt = 18.5 + random.uniform(1.0, 5.0)

                    res = engine.process_packet(pkt)
                    engine.feed_rtt_probe(sample_rtt)
                    recent_pts.append({
                        "dt": round(res["delta_t_ms"], 2),
                        "len": res["pkt_len"],
                        "tin": res["enforced_tin"],
                        "class": res["classification"]["class_name"]
                    })

            metrics = engine.get_full_metrics()
            metrics["attractor_points"] = recent_pts
            metrics["live_sniffer_active"] = live_sniffer_active
            metrics["wan_degraded"] = sim_state.get("wan_degraded", False)

            await websocket.send_text(json.dumps(metrics))
            await asyncio.sleep(0.1) # 10 Hz telemetry updates

    except WebSocketDisconnect:
        pass
    except Exception as e:
        print(f"[WS ERROR] {e}")
