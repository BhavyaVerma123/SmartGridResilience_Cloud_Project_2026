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

import asyncio
import datetime
import json
import logging
import os
import sys
import re
import time
import uuid
from collections import deque
from contextvars import ContextVar
from functools import lru_cache
from pathlib import Path
from typing import Literal, Optional
from contextlib import asynccontextmanager
from urllib.parse import urlsplit

import pandas as pd

# FastAPI
from fastapi import FastAPI, HTTPException, BackgroundTasks, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, HTMLResponse, FileResponse
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

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
live_events: deque[dict] = deque(maxlen=50)
event_counter = 0
request_id_context: ContextVar[str] = ContextVar("request_id", default="")
logger = logging.getLogger(__name__)

PROC_DIR = ROOT / "dataset" / "processed"

def _get_trusted_origins() -> list[str]:
    """Read and validate exact browser origins allowed to call this API."""
    configured_origins = os.getenv("SMARTGRID_CORS_ORIGINS")
    if configured_origins is None:
        return ["http://localhost:8000", "http://127.0.0.1:8000"]

    origins = [
        origin.strip().rstrip("/")
        for origin in configured_origins.split(",")
        if origin.strip()
    ]
    if not origins:
        raise RuntimeError("SMARTGRID_CORS_ORIGINS must contain at least one trusted origin.")

    for origin in origins:
        parsed = urlsplit(origin)
        if (
            "*" in origin
            or parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            raise RuntimeError(
                "SMARTGRID_CORS_ORIGINS must be a comma-separated list of exact "
                "http(s) origins without paths, query strings, fragments, or wildcards."
            )
    return origins


@lru_cache(maxsize=2)
def _load_processed_dataset(filename: str) -> pd.DataFrame:
    """Load a processed dataset once per worker; callers treat it as read-only."""
    if filename not in {"msu_ornl_processed.csv", "stability_processed.csv"}:
        raise ValueError(f"Unsupported processed dataset: {filename}")

    path = PROC_DIR / filename
    if not path.is_file():
        raise HTTPException(
            status_code=503,
            detail=f"Required processed dataset is unavailable: {filename}",
        )

    frame = pd.read_csv(path)
    if "label" not in frame.columns:
        raise HTTPException(
            status_code=503,
            detail=f"Processed dataset is missing its label column: {filename}",
        )
    return frame.drop(columns=["label"])


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
    allow_origins     = _get_trusted_origins(),
    allow_credentials = True,
    allow_methods     = ["*"],
    allow_headers     = ["*"],
)


@app.middleware("http")
async def log_request_timing(request: Request, call_next):
    """Record request latency and a correlation ID without logging payloads."""
    supplied_id = request.headers.get("x-request-id", "")
    request_id = supplied_id if re.fullmatch(r"[A-Za-z0-9._-]{1,128}", supplied_id) else uuid.uuid4().hex
    token = request_id_context.set(request_id)
    started_at = time.perf_counter()
    status_code = 500

    try:
        response = await call_next(request)
        status_code = response.status_code
        response.headers["X-Request-ID"] = request_id
        return response
    finally:
        logger.info(json.dumps({
            "event": "api_request",
            "request_id": request_id,
            "method": request.method,
            "path": request.url.path,
            "status_code": status_code,
            "duration_ms": round((time.perf_counter() - started_at) * 1000, 3),
        }))
        request_id_context.reset(token)


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
    global event_counter
    event_counter += 1
    event["event_id"] = event_counter
    event["request_id"] = request_id_context.get()
    live_events.append(event)


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
        <li><a href="/dashboard" style="color:#60a5fa">🚀 Open Resilience Dashboard</a></li>
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
    result = await run_in_threadpool(anomaly_agent.predict, pd.DataFrame([data]))
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
async def detect_batch(
    background_tasks: BackgroundTasks,
    n_samples: int = Query(default=10, ge=1, le=100),
):
    """
    Run anomaly detection on N random samples from the processed dataset.
    Simulates a real-time PMU data stream.
    """
    df = await run_in_threadpool(_load_processed_dataset, "msu_ornl_processed.csv")
    if df.empty:
        raise HTTPException(status_code=503, detail="The anomaly dataset contains no samples.")
    sample = await run_in_threadpool(
        df.sample,
        n=min(n_samples, len(df)),
        replace=False,
    )

    results = await run_in_threadpool(anomaly_agent.predict_batch, sample)
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
    result = await run_in_threadpool(stability_agent.predict, state.model_dump())
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
    """Report local agent health and separate AWS configuration from connectivity."""
    aws_state = {
        "configuration": "configured",
        "connectivity": "not_checked",
        "message": "Client initialized; remote service connectivity has not been verified.",
    }
    return {
        "anomaly_detection_agent" : anomaly_agent.status(),
        "stability_assessment_agent": stability_agent.status(),
        "aws_services": {
            "dynamodb": aws_state.copy(),
            "sns": aws_state.copy(),
            "lambda": aws_state.copy(),
            "cloudwatch": aws_state.copy(),
        },
        "server_time": datetime.datetime.now(datetime.UTC).isoformat(),
        "uptime"     : "active",
    }


# ── 4. DynamoDB — Recent Decisions ────────────────────────────────────────

@app.get("/decisions/recent", tags=["DynamoDB"])
async def get_recent_decisions(
    agent_id: str = "anomaly-agent-az1",
    limit: int = Query(default=20, ge=1, le=100),
):
    """
    Fetch the most recent agent decisions from DynamoDB.
    Used by the dashboard to show the live decision log.
    """
    decisions = await run_in_threadpool(
        aws_logger.get_recent_decisions,
        agent_id=agent_id,
        limit=limit,
    )
    return {
        "agent_id" : agent_id,
        "count"    : len(decisions),
        "decisions": decisions,
    }


# ── 5. Live Feed (Server-Sent Events) ────────────────────────────────────

@app.get("/live-feed", tags=["Dashboard"])
async def live_feed(request: Request):
    """
    Server-Sent Events stream of real-time agent decisions.
    The frontend dashboard subscribes to this for live updates.
    """
    async def event_generator():
        last_event_id = 0
        supplied_event_id = request.headers.get("last-event-id", "")
        if supplied_event_id.isdigit():
            last_event_id = int(supplied_event_id)
        last_heartbeat = time.monotonic()

        while True:
            if await request.is_disconnected():
                break

            events = list(live_events)
            if events:
                oldest_id = events[0]["event_id"]
                if last_event_id < oldest_id - 1:
                    last_event_id = oldest_id - 1

                for event in events:
                    event_id = event["event_id"]
                    if event_id > last_event_id:
                        yield f"id: {event_id}\ndata: {json.dumps(event)}\n\n"
                        last_event_id = event_id

            if time.monotonic() - last_heartbeat >= 15:
                yield ": keep-alive\n\n"
                last_heartbeat = time.monotonic()
            await asyncio.sleep(0.25)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── 6. Demo Pipeline ─────────────────────────────────────────────────────

@app.post("/demo/run", tags=["Demo"])
async def run_demo_pipeline(
    background_tasks: BackgroundTasks,
    scenario: str = Query(default="random"),
):
    """
    Runs a complete end-to-end pipeline demo:
    PMU data → Anomaly Detection → Mitigation (Lambda) → Stability Check
    Logging and alert work is queued for FastAPI background execution.

    scenario: 'normal', 'overload', 'attack', or 'random'
    """
    if aws_logger is None:
        raise HTTPException(status_code=503, detail="The shared idempotency store is unavailable.")

    s = (scenario or "random").lower().strip()
    if s in ("cyber_attack", "attack"):
        norm_scenario = "attack"
    elif s in ("line_overload", "overload"):
        norm_scenario = "overload"
    elif s == "normal":
        norm_scenario = "normal"
    else:
        norm_scenario = "random"

    request_id = request_id_context.get() or uuid.uuid4().hex
    owner_token = uuid.uuid4().hex
    while True:
        claimed = await run_in_threadpool(
            aws_logger.claim_demo_request,
            request_id,
            owner_token,
        )
        if claimed:
            task = asyncio.create_task(
                _execute_and_store_demo(
                    request_id,
                    owner_token,
                    background_tasks,
                    norm_scenario,
                )
            )
            return await asyncio.shield(task)

        record = await run_in_threadpool(aws_logger.get_demo_request, request_id)
        if record and record["status"] == "COMPLETE":
            return record["result"]
        if record and record["status"] == "FAILED":
            detail = record["result"].get(
                "detail",
                "The assessment failed. Use a new request ID to start another run.",
            )
            raise HTTPException(status_code=503, detail=detail)
        await asyncio.sleep(0.25)


async def _execute_and_store_demo(
    request_id: str,
    owner_token: str,
    background_tasks: BackgroundTasks,
    scenario: str,
) -> dict:
    """Run a claimed assessment and persist its result for other workers."""
    try:
        result = await _execute_demo_pipeline(background_tasks, scenario)
    except Exception:
        if aws_logger is not None:
            try:
                await run_in_threadpool(
                    aws_logger.finish_demo_request,
                    request_id,
                    owner_token,
                    "FAILED",
                    {"detail": "The assessment failed. Use a new request ID to retry."},
                )
            except Exception:
                logger.exception("Could not persist failed demo request %s", request_id)
        raise

    if aws_logger is None or not await run_in_threadpool(
        aws_logger.finish_demo_request,
        request_id,
        owner_token,
        "COMPLETE",
        result,
    ):
        raise HTTPException(
            status_code=503,
            detail="Assessment finished, but its shared result could not be stored.",
        )
    return result


async def _execute_demo_pipeline(
    background_tasks: BackgroundTasks,
    scenario: str,
):
    """Run one deduplicated demo request and queue its AWS logging."""
    pipeline_started = time.perf_counter()
    import random

    s = (scenario or "random").lower().strip()
    if s in ("cyber_attack", "attack"):
        chosen_scenario = "attack"
    elif s in ("line_overload", "overload"):
        chosen_scenario = "overload"
    elif s == "normal":
        chosen_scenario = "normal"
    else:
        chosen_scenario = random.choices(
            ["normal", "overload", "attack"],
            weights=[40, 30, 30],
            k=1,
        )[0]

    now_iso = datetime.datetime.now(datetime.UTC).isoformat()

    if chosen_scenario == "normal":
        # Realistic nominal PMU state (50.0 Hz, 230 kV, balanced load)
        score = round(random.uniform(0.12, 0.26), 4)
        conf = round(random.uniform(88.0, 96.5), 2)
        lat = round(random.uniform(18.0, 32.0), 3)
        a_dict = {
            "label": "NORMAL",
            "is_anomaly": False,
            "anomaly_score": score,
            "confidence": conf,
            "latency_ms": lat,
            "timestamp": now_iso,
            "agent_id": "anomaly-agent-az1",
        }
        mitigation = {
            "mitigation_agent_id": "mitigation-lambda-az1",
            "triggered_by": "anomaly-agent-az1",
            "anomaly_score": score,
            "severity": "LOW",
            "action_taken": "LOG_ONLY",
            "message": "Normal grid operation — voltage and frequency within nominal tolerances (50.0 Hz, 230 kV)",
            "timestamp": now_iso,
            "status": "EXECUTED",
            "latency_ms": 0.0,
        }
        s_prob = round(random.uniform(0.93, 0.985), 4)
        s_dict = {
            "label": "STABLE",
            "is_stable": True,
            "stable_prob": s_prob,
            "unstable_prob": round(1.0 - s_prob, 4),
            "latency_ms": round(random.uniform(35.0, 65.0), 3),
            "timestamp": now_iso,
            "agent_id": "stability-agent-az1",
        }

    elif chosen_scenario == "overload":
        # Line thermal overload (118% rating, slight voltage sag)
        score = round(random.uniform(0.63, 0.74), 4)
        conf = round(random.uniform(74.0, 84.0), 2)
        lat = round(random.uniform(20.0, 38.0), 3)
        a_dict = {
            "label": "ANOMALY",
            "is_anomaly": True,
            "anomaly_score": score,
            "confidence": conf,
            "latency_ms": lat,
            "timestamp": now_iso,
            "agent_id": "anomaly-agent-az1",
        }
        mitigation_started = time.perf_counter()
        mitigation = await run_in_threadpool(mitigation_exec.execute, a_dict)
        mitigation["latency_ms"] = round((time.perf_counter() - mitigation_started) * 1000, 3)
        # Post-mitigation grid stabilized via rerouting
        s_prob = round(random.uniform(0.87, 0.94), 4)
        s_dict = {
            "label": "STABLE",
            "is_stable": True,
            "stable_prob": s_prob,
            "unstable_prob": round(1.0 - s_prob, 4),
            "latency_ms": round(random.uniform(40.0, 75.0), 3),
            "timestamp": now_iso,
            "agent_id": "stability-agent-az1",
        }

    else:  # "attack"
        # Severe FDI / relay manipulation attack
        score = round(random.uniform(0.83, 0.94), 4)
        conf = round(random.uniform(88.0, 97.5), 2)
        lat = round(random.uniform(22.0, 45.0), 3)
        a_dict = {
            "label": "ANOMALY",
            "is_anomaly": True,
            "anomaly_score": score,
            "confidence": conf,
            "latency_ms": lat,
            "timestamp": now_iso,
            "agent_id": "anomaly-agent-az1",
        }
        mitigation_started = time.perf_counter()
        mitigation = await run_in_threadpool(mitigation_exec.execute, a_dict)
        mitigation["latency_ms"] = round((time.perf_counter() - mitigation_started) * 1000, 3)
        # Severe event - grid unstable or initial post-attack assessment
        s_prob = round(random.uniform(0.18, 0.36), 4)
        s_dict = {
            "label": "UNSTABLE",
            "is_stable": False,
            "stable_prob": s_prob,
            "unstable_prob": round(1.0 - s_prob, 4),
            "latency_ms": round(random.uniform(45.0, 85.0), 3),
            "timestamp": now_iso,
            "agent_id": "stability-agent-az1",
        }

    # Asynchronous AWS Logging
    background_tasks.add_task(aws_logger.log_anomaly_decision, a_dict)
    background_tasks.add_task(aws_logger.log_stability_decision, s_dict)
    if a_dict.get("is_anomaly", False):
        background_tasks.add_task(alert_manager.send_alert, a_dict)

    payload = {
        "type"             : "DEMO_RUN",
        "timestamp"        : now_iso,
        "pipeline"         : "COMPLETE",
        "scenario"         : chosen_scenario,
        "step1_anomaly"    : a_dict,
        "step2_mitigation" : mitigation,
        "step3_stability"  : s_dict,
        "pipeline_latency_ms": round((time.perf_counter() - pipeline_started) * 1000, 3),
        "aws_logged"       : None,
        "aws_log_status"   : "queued",
    }
    _push_event(payload)

    return payload



# ── 7. Dashboard ─────────────────────────────────────────────────────────

DASHBOARD_PATH = ROOT / "src" / "frontend" / "dashboard.html"

@app.get("/dashboard", tags=["Dashboard"])
async def serve_dashboard():
    """Serve the interactive resilience dashboard."""
    if DASHBOARD_PATH.exists():
        return FileResponse(str(DASHBOARD_PATH), media_type="text/html")
    return HTMLResponse("<h1>Dashboard not found</h1><p>Place dashboard.html in src/frontend/</p>", status_code=404)


# ═══════════════════════════════════════════════════════════════════════════
#  SERVER ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import uvicorn
    print("Starting Smart Grid Resilience API...")
    print("Dashboard : http://localhost:8000/dashboard")
    print("API Docs  : http://localhost:8000/docs\n")
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
