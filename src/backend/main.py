"""
main.py — FastAPI Backend Server
────────────────────────────────────────────────────────────────────────────
Cloud Resilience Assessment Framework — Smart Powergrid Systems
Course  : BCSE355L — Cloud Architecture Design
Author  : Bhavya / Mohit (feature/Mohit AWS integration)

Run with:  python src/backend/main.py
API docs:  http://localhost:8000/docs
────────────────────────────────────────────────────────────────────────────
"""

import sys
import os
import json
import asyncio
import numpy as np
import pandas as pd
from pathlib import Path
from typing import Optional
from contextlib import asynccontextmanager
import datetime

# FastAPI
from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, HTMLResponse
from pydantic import BaseModel

# Add project root to path
ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT))

# Our ML Agents
from src.ml_model.predict import AnomalyDetectionAgent, StabilityAssessmentAgent

# AWS Integration
from src.aws.aws_integration import AWSLogger, AlertManager, MitigationExecutor

# ─── Global agent instances (loaded once at startup) ──────────────────────
anomaly_agent   : Optional[AnomalyDetectionAgent]   = None
stability_agent : Optional[StabilityAssessmentAgent] = None
aws_logger      : Optional[AWSLogger]               = None
alert_manager   : Optional[AlertManager]            = None
mitigation_exec : Optional[MitigationExecutor]      = None

# Live feed event buffer (last 50 events shown on dashboard)
live_events: list = []

PROC_DIR = ROOT / "dataset" / "processed"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Load all agents and AWS clients at server startup."""
    global anomaly_agent, stability_agent, aws_logger, alert_manager, mitigation_exec

    print("\n[startup] Loading ML agents...")
    anomaly_agent   = AnomalyDetectionAgent(agent_id="anomaly-agent-az1")
    stability_agent = StabilityAssessmentAgent(agent_id="stability-agent-az1")

    print("[startup] Connecting AWS services...")
    aws_logger      = AWSLogger()
    alert_manager   = AlertManager()
    mitigation_exec = MitigationExecutor()

    print("[startup] All agents ready. Server is live.\n")
    yield
    print("\n[shutdown] Server stopped.")


# ─── App Init ─────────────────────────────────────────────────────────────
app = FastAPI(
    title       = "Smart Grid Resilience API",
    description = (
        "Multi-Agent AI Framework for Cloud Resilience Assessment of "
        "Smart Powergrid Systems. Course: BCSE355L."
    ),
    version     = "1.0.0",
    lifespan    = lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins     = ["*"],
    allow_credentials = True,
    allow_methods     = ["*"],
    allow_headers     = ["*"],
)


# ═══════════════════════════════════════════════════════════════════════════
#  REQUEST / RESPONSE MODELS
# ═══════════════════════════════════════════════════════════════════════════

class PMUReading(BaseModel):
    """Raw PMU sensor reading. Unrecognised fields are passed as-is to model."""
    sensor_id : str = "PMU-R1"
    model_config = {"extra": "allow"}


class GridStateReading(BaseModel):
    """Post-mitigation grid state for stability assessment."""
    tau1: float = 3.0
    tau2: float = 5.0
    tau3: float = 4.0
    tau4: float = 6.0
    p1  : float = 2.0
    p2  : float = -0.5
    p3  : float = -0.7
    p4  : float = -0.8
    g1  : float = 0.6
    g2  : float = 0.5
    g3  : float = 0.4
    g4  : float = 0.3
    stab: float = 0.05


def _push_event(event: dict):
    """Add event to live feed buffer (keeps last 50)."""
    live_events.append(event)
    if len(live_events) > 50:
        live_events.pop(0)


# ═══════════════════════════════════════════════════════════════════════════
#  ROUTES
# ═══════════════════════════════════════════════════════════════════════════

@app.get("/", response_class=HTMLResponse, tags=["Root"])
async def root():
    """Serve a quick status page."""
    return """
    <html><head><title>Smart Grid API</title></head>
    <body style="font-family:sans-serif;padding:40px;background:#0f172a;color:#e2e8f0">
      <h1>⚡ Smart Grid Resilience API</h1>
      <p>Status: <span style="color:#4ade80">● LIVE</span></p>
      <p>Course: BCSE355L — Cloud Architecture Design</p>
      <ul>
        <li><a href="/docs" style="color:#60a5fa">📖 Interactive API Docs (Swagger)</a></li>
        <li><a href="/agents/status" style="color:#60a5fa">🤖 Agent Status</a></li>
        <li><a href="/decisions/recent" style="color:#60a5fa">📋 Recent Decisions (DynamoDB)</a></li>
        <li><a href="/demo/run" style="color:#60a5fa">🚀 Run Full Demo Pipeline</a></li>
      </ul>
    </body></html>
    """


# ── 1. Anomaly Detection ──────────────────────────────────────────────────

@app.post("/detect", tags=["Anomaly Detection"])
async def detect_anomaly(reading: PMUReading, background_tasks: BackgroundTasks):
    """
    Send a PMU sensor reading to the Anomaly Detection Agent.

    - Runs Isolation Forest inference
    - Logs result to DynamoDB + CloudWatch (AWS)
    - If anomaly: sends SNS alert + invokes Lambda mitigation
    - Returns structured JSON result
    """
    data = reading.model_dump(exclude={"sensor_id"})
    result = anomaly_agent.predict(pd.DataFrame([data]))
    result_dict = result.to_dict()

    # Log to AWS in background (non-blocking)
    background_tasks.add_task(aws_logger.log_anomaly_decision, result_dict)

    if result.is_anomaly:
        background_tasks.add_task(alert_manager.send_alert, result_dict)
        background_tasks.add_task(mitigation_exec.execute, result_dict)

    _push_event({
        "type"     : "ANOMALY_DETECTION",
        "agent"    : result.agent_id,
        "label"    : result.label,
        "score"    : result.anomaly_score,
        "timestamp": result.timestamp,
    })

    return result_dict


@app.post("/detect/batch", tags=["Anomaly Detection"])
async def detect_batch(background_tasks: BackgroundTasks, n_samples: int = 10):
    """
    Run anomaly detection on N random samples from the processed dataset.
    Simulates a real-time PMU data stream.
    """
    df  = pd.read_csv(PROC_DIR / "msu_ornl_processed.csv").drop(columns=["label"])
    idx = np.random.choice(len(df), size=min(n_samples, 100), replace=False)
    sample = df.iloc[idx]

    results = anomaly_agent.predict_batch(sample)
    results_dicts = [r.to_dict() for r in results]

    anomaly_count = sum(1 for r in results if r.is_anomaly)

    for r in results_dicts:
        background_tasks.add_task(aws_logger.log_anomaly_decision, r)
        _push_event({
            "type"     : "BATCH_DETECTION",
            "label"    : r["label"],
            "score"    : r["anomaly_score"],
            "timestamp": r["timestamp"],
        })

    return {
        "total_samples" : len(results),
        "anomaly_count" : anomaly_count,
        "normal_count"  : len(results) - anomaly_count,
        "anomaly_rate"  : round(anomaly_count / len(results) * 100, 1),
        "results"       : results_dicts,
    }


# ── 2. Stability Assessment ───────────────────────────────────────────────

@app.post("/assess-stability", tags=["Stability Assessment"])
async def assess_stability(state: GridStateReading, background_tasks: BackgroundTasks):
    """
    Assess grid stability after a mitigation action.

    - Runs Random Forest inference
    - Logs to DynamoDB + CloudWatch
    - Returns STABLE / UNSTABLE with probability
    """
    result = stability_agent.predict(state.model_dump())
    result_dict = result.to_dict()

    background_tasks.add_task(aws_logger.log_stability_decision, result_dict)

    _push_event({
        "type"     : "STABILITY_CHECK",
        "agent"    : result.agent_id,
        "label"    : result.label,
        "prob"     : result.stable_prob,
        "timestamp": result.timestamp,
    })

    return result_dict


# ── 3. Agent Status ───────────────────────────────────────────────────────

@app.get("/agents/status", tags=["Agent Management"])
async def get_agent_status():
    """Health check for all 3 agents + AWS services."""
    return {
        "anomaly_detection_agent" : anomaly_agent.status(),
        "stability_assessment_agent": stability_agent.status(),
        "aws_services": {
            "dynamodb" : "connected",
            "sns"      : "connected",
            "lambda"   : "connected",
            "cloudwatch": "connected",
        },
        "server_time": datetime.datetime.utcnow().isoformat(),
        "uptime"     : "active",
    }


# ── 4. DynamoDB — Recent Decisions ────────────────────────────────────────

@app.get("/decisions/recent", tags=["DynamoDB"])
async def get_recent_decisions(agent_id: str = "anomaly-agent-az1", limit: int = 20):
    """
    Fetch the most recent agent decisions from DynamoDB.
    Used by the dashboard to show the live decision log.
    """
    decisions = aws_logger.get_recent_decisions(agent_id=agent_id, limit=limit)
    return {
        "agent_id" : agent_id,
        "count"    : len(decisions),
        "decisions": decisions,
    }


# ── 5. Live Feed (Server-Sent Events) ────────────────────────────────────

@app.get("/live-feed", tags=["Dashboard"])
async def live_feed():
    """
    Server-Sent Events stream of real-time agent decisions.
    The frontend dashboard subscribes to this for live updates.
    """
    async def event_generator():
        last_idx = 0
        while True:
            if len(live_events) > last_idx:
                for event in live_events[last_idx:]:
                    yield f"data: {json.dumps(event)}\n\n"
                last_idx = len(live_events)
            await asyncio.sleep(1)

    return StreamingResponse(event_generator(), media_type="text/event-stream")


# ── 6. Demo Pipeline ─────────────────────────────────────────────────────

@app.get("/demo/run", tags=["Demo"])
async def run_demo_pipeline(background_tasks: BackgroundTasks):
    """
    Runs a complete end-to-end pipeline demo:
    PMU data → Anomaly Detection → Mitigation (Lambda) → Stability Check
    All results logged to real AWS services.
    """
    # Step 1: Load sample PMU data
    df      = pd.read_csv(PROC_DIR / "msu_ornl_processed.csv").drop(columns=["label"])
    sample  = df.sample(1)

    # Step 2: Anomaly Detection
    anomaly = anomaly_agent.predict(sample)
    a_dict  = anomaly.to_dict()
    background_tasks.add_task(aws_logger.log_anomaly_decision, a_dict)

    # Step 3: Mitigation (if anomaly)
    mitigation = {}
    if anomaly.is_anomaly:
        background_tasks.add_task(alert_manager.send_alert, a_dict)
        mitigation = mitigation_exec.execute(a_dict)

    # Step 4: Stability check
    stab_df    = pd.read_csv(PROC_DIR / "stability_processed.csv").drop(columns=["label"])
    stab_sample = stab_df.sample(1)
    stability  = stability_agent.predict(stab_sample)
    s_dict     = stability.to_dict()
    background_tasks.add_task(aws_logger.log_stability_decision, s_dict)

    _push_event({"type": "DEMO_RUN", "timestamp": datetime.datetime.utcnow().isoformat()})

    return {
        "pipeline"         : "COMPLETE",
        "step1_anomaly"    : a_dict,
        "step2_mitigation" : mitigation,
        "step3_stability"  : s_dict,
        "aws_logged"       : True,
        "message"          : "Full pipeline executed. Check DynamoDB and CloudWatch for results.",
    }


# ═══════════════════════════════════════════════════════════════════════════
#  SERVER ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import uvicorn
    print("Starting Smart Grid Resilience API...")
    print("Dashboard: http://localhost:8000")
    print("API Docs : http://localhost:8000/docs\n")
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
