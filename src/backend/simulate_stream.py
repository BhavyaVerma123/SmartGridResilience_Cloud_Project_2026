"""
simulate_stream.py — Continuous Live Data Stream Simulator
─────────────────────────────────────────────────────────────
Sends varied PMU readings to the backend API every 3-5 seconds.
Mixes NORMAL, MEDIUM (overload), and HIGH (cyber attack) scenarios
to demonstrate all 3 mitigation tiers on the dashboard.

Also publishes data to Kinesis and IoT Core for full AWS integration.
"""

import time
import random
import json
import sys
import os
import numpy as np
import boto3
import datetime
from pathlib import Path

# Add project root
ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT))

try:
    import requests
except ImportError:
    import urllib.request
    requests = None

API_URL = "http://localhost:8000"

# ── AWS Clients for Kinesis + IoT Core ─────────────────────────────────────
REGION = "us-east-1"
try:
    kinesis = boto3.client("kinesis", region_name=REGION)
    KINESIS_STREAM = "SmartGrid-PMU-Stream"
    print(f"[✓] Kinesis client ready → {KINESIS_STREAM}")
except Exception as e:
    kinesis = None
    print(f"[!] Kinesis not available: {e}")

try:
    # Load IoT endpoint from config
    config_path = ROOT / "src" / "aws" / "config.json"
    iot_endpoint = ""
    if config_path.exists():
        with open(config_path) as f:
            cfg = json.load(f)
            iot_endpoint = cfg.get("iot_endpoint", "")
    
    if iot_endpoint:
        iot_client = boto3.client("iot-data", region_name=REGION, 
                                   endpoint_url=f"https://{iot_endpoint}")
        print(f"[✓] IoT Core client ready → {iot_endpoint}")
    else:
        iot_client = None
        print("[!] IoT Core endpoint not configured")
except Exception as e:
    iot_client = None
    print(f"[!] IoT Core not available: {e}")

# ── CloudWatch for custom stream metrics ──────────────────────────────────
try:
    cloudwatch = boto3.client("cloudwatch", region_name=REGION)
    print("[✓] CloudWatch client ready")
except Exception as e:
    cloudwatch = None
    print(f"[!] CloudWatch not available: {e}")


def generate_pmu_reading(scenario: str) -> dict:
    """Generate a simulated PMU sensor reading based on scenario."""
    base_freq = 50.0    # Hz
    base_volt = 230.0   # kV
    
    if scenario == "cyber_attack":
        # Wild fluctuations indicating a cyber intrusion
        reading = {
            "frequency_hz": base_freq + random.uniform(-5.0, 5.0),
            "voltage_kv": base_volt + random.uniform(-50.0, 50.0),
            "phase_angle_deg": random.uniform(-180, 180),
            "power_factor": random.uniform(0.3, 0.65),
            "active_power_mw": random.uniform(10, 500),
            "reactive_power_mvar": random.uniform(-200, 200),
        }
    elif scenario == "line_overload":
        # Moderate stress on the grid
        reading = {
            "frequency_hz": base_freq + random.uniform(-1.5, 1.5),
            "voltage_kv": base_volt + random.uniform(-20.0, 20.0),
            "phase_angle_deg": random.uniform(-45, 45),
            "power_factor": random.uniform(0.65, 0.80),
            "active_power_mw": random.uniform(200, 400),
            "reactive_power_mvar": random.uniform(-50, 50),
        }
    else:  # normal
        reading = {
            "frequency_hz": base_freq + random.uniform(-0.2, 0.2),
            "voltage_kv": base_volt + random.uniform(-5.0, 5.0),
            "phase_angle_deg": random.uniform(-10, 10),
            "power_factor": random.uniform(0.90, 0.99),
            "active_power_mw": random.uniform(100, 250),
            "reactive_power_mvar": random.uniform(-15, 15),
        }
    
    reading["sensor_id"] = random.choice(["PMU-R1", "PMU-R2", "PMU-R3", "PMU-R4"])
    reading["timestamp"] = datetime.datetime.utcnow().isoformat()
    reading["scenario"] = scenario
    return reading


def send_to_kinesis(reading: dict):
    """Push PMU reading to Kinesis Data Stream."""
    if not kinesis:
        return
    try:
        kinesis.put_record(
            StreamName=KINESIS_STREAM,
            Data=json.dumps(reading).encode("utf-8"),
            PartitionKey=reading.get("sensor_id", "PMU-R1"),
        )
        print(f"  ↳ Kinesis: record pushed to {KINESIS_STREAM}")
    except Exception as e:
        print(f"  ↳ Kinesis ERROR: {e}")


def send_to_iot(reading: dict):
    """Publish PMU reading to IoT Core MQTT topic."""
    if not iot_client:
        return
    try:
        iot_client.publish(
            topic="smartgrid/pmu/readings",
            qos=1,
            payload=json.dumps(reading).encode("utf-8"),
        )
        print(f"  ↳ IoT Core: published to smartgrid/pmu/readings")
    except Exception as e:
        print(f"  ↳ IoT Core ERROR: {e}")


def send_to_cloudwatch(scenario: str, reading: dict):
    """Publish custom stream metrics to CloudWatch."""
    if not cloudwatch:
        return
    try:
        cloudwatch.put_metric_data(
            Namespace="SmartGrid/PMUStream",
            MetricData=[
                {
                    "MetricName": "PMUReadingsPerMinute",
                    "Dimensions": [{"Name": "SensorId", "Value": reading.get("sensor_id", "PMU-R1")}],
                    "Value": 1.0,
                    "Unit": "Count",
                },
                {
                    "MetricName": "GridFrequency",
                    "Dimensions": [{"Name": "Scenario", "Value": scenario}],
                    "Value": reading.get("frequency_hz", 50.0),
                    "Unit": "None",
                },
                {
                    "MetricName": "GridVoltage",
                    "Dimensions": [{"Name": "Scenario", "Value": scenario}],
                    "Value": reading.get("voltage_kv", 230.0),
                    "Unit": "None",
                },
            ]
        )
    except Exception as e:
        pass  # Don't spam logs for CW errors


def call_demo_api():
    """Call the /demo/run endpoint and return the result."""
    try:
        if requests:
            resp = requests.get(f"{API_URL}/demo/run", timeout=15)
            return resp.json()
        else:
            req = urllib.request.urlopen(f"{API_URL}/demo/run", timeout=15)
            return json.loads(req.read().decode("utf-8"))
    except Exception as e:
        print(f"  [!] API error: {e}")
        return None


# ═══════════════════════════════════════════════════════════════════════════
#  MAIN LOOP
# ═══════════════════════════════════════════════════════════════════════════

print("\n" + "=" * 60)
print("  ⚡ Smart Grid PMU Simulator — Live Data Stream")
print("=" * 60)
print(f"  API Target  : {API_URL}/demo/run")
print(f"  Kinesis     : {'✓ ' + KINESIS_STREAM if kinesis else '✗ not available'}")
print(f"  IoT Core    : {'✓ connected' if iot_client else '✗ not available'}")
print(f"  CloudWatch  : {'✓ connected' if cloudwatch else '✗ not available'}")
print(f"  Interval    : 3-5 seconds (randomized)")
print(f"  Scenarios   : 40% Normal | 30% Line Overload | 30% Cyber Attack")
print("=" * 60 + "\n")

event_count = 0

while True:
    try:
        # Pick a weighted random scenario
        scenario = random.choices(
            ["normal", "line_overload", "cyber_attack"],
            weights=[40, 30, 30],
            k=1
        )[0]
        
        event_count += 1
        emoji = {"normal": "🟢", "line_overload": "🟡", "cyber_attack": "🔴"}[scenario]
        
        print(f"\n[{event_count}] {emoji} Scenario: {scenario.upper().replace('_', ' ')}")
        
        # Generate PMU reading
        reading = generate_pmu_reading(scenario)
        
        # 1. Push to Kinesis Data Stream
        send_to_kinesis(reading)
        
        # 2. Publish to IoT Core
        send_to_iot(reading)
        
        # 3. Push metrics to CloudWatch
        send_to_cloudwatch(scenario, reading)
        
        # 4. Call the ML pipeline API
        result = call_demo_api()
        if result:
            score = result.get("step1_anomaly", {}).get("anomaly_score", "?")
            label = result.get("step1_anomaly", {}).get("label", "?")
            action = result.get("step2_mitigation", {}).get("action_taken", "N/A")
            stability = result.get("step3_stability", {}).get("label", "?")
            print(f"  → ML Result: {label} (score={score}) | Action: {action} | Grid: {stability}")
        
        # Randomized sleep for organic feel
        sleep_time = random.uniform(3.0, 5.0)
        time.sleep(sleep_time)
        
    except KeyboardInterrupt:
        print("\n\n[✓] Simulator stopped by user.")
        break
    except Exception as e:
        print(f"[!] Unexpected error: {e}")
        time.sleep(5)
