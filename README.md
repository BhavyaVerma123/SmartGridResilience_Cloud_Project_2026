# Cloud Resilience Assessment Framework for Smart Powergrid Systems using Multi-Agent Artificial Intelligence

## Team Members
* **M Arun Srinivas**
* **Bhavya Verma**
* **Mohit Gupta**

## Problem Statement
Modern smart powergrids are increasingly dependent on cloud infrastructure for real-time monitoring, data analytics, and operational control. However, this dependency introduces vulnerability to cloud service disruptions, cyber-attacks, and latency issues. There is a critical need for an automated, intelligent framework capable of assessing cloud resilience, predicting failures, and executing autonomous mitigation strategies to ensure uninterrupted powergrid stability.

## Objectives
* **Assess Cloud Resilience:** Evaluate the stability and fault tolerance of cloud-hosted powergrid management systems under simulated disruptions.
* **Multi-Agent Coordination:** Deploy an intelligent multi-agent AI framework where distinct agents monitor cloud infrastructure, predict system anomalies, and orchestrate mitigation workflows.
* **Real-time Mitigation:** Implement automated policy execution to dynamically reroute workloads, scale resources, or switch to edge fallbacks during cloud degradation.
* **Visualize Performance:** Build a comprehensive dashboard to monitor powergrid metrics and cloud health KPIs in real time.

## Proposed Architecture / Framework
The framework utilizes a multi-layered architecture:
1. **Powergrid Simulation Layer:** Simulates smart grid operations and load dynamics (e.g., using IEEE test feeders).
2. **Cloud Infrastructure Layer (AWS):** Hosts the grid management services, utilizing EC2, Lambda, S3, and CloudWatch for monitoring.
3. **Multi-Agent AI Layer:** Contains localized monitoring agents, a centralized coordinator agent, and a predictive policy engine.
4. **Frontend Dashboard:** Provides an interface to monitor agent decisions, resilience scores, and real-time powergrid health.

*Note: The detailed visual diagrams can be found in the `architecture/` folder.*

## Technology Stack
* **AI & Agent Modeling:** Python, scikit-learn anomaly detection and stability models
* **Cloud Infrastructure:** AWS SDK integrations for DynamoDB, SNS, Lambda, CloudWatch, Kinesis, and IoT Core
* **Backend:** FastAPI with synchronous model/AWS clients
* **Frontend:** Single-page HTML/CSS/JavaScript dashboard with Chart.js
* **Database:** DynamoDB for agent decisions

## Dataset Details
The project utilizes simulated smart powergrid datasets alongside cloud infrastructure performance metrics:
* **Grid Data:** Simulated power flow, voltage fluctuations, and load profiles generated via standard IEEE test systems.
* **Cloud Data:** Simulated and real-time AWS CloudWatch logs tracking CPU utilization, network latency, memory usage, and API error codes during injection attacks or service blackouts.

## Current Request Flow and Baseline Assessment

The current implementation is a FastAPI service, two scikit-learn model agents, synchronous boto3 service wrappers, and a single-page dashboard. A demo request samples the anomaly and stability datasets, runs both model predictions, invokes Lambda for detected anomalies, queues DynamoDB/CloudWatch/SNS work in FastAPI background tasks, and returns a combined result. The stability sample is drawn from a separate dataset and does not depend on the anomaly or mitigation output.

The repository snapshot contains `stability_processed.csv` and the model artifacts, but it does **not** contain `msu_ornl_processed.csv` or the raw MSU/ORNL input files. Demo and batch anomaly endpoints therefore require that dataset to be supplied before they can run.

The initial code inspection found these latency/correctness issues:

* Async endpoints performed synchronous inference and DynamoDB reads on the event loop.
* Demo and batch requests parsed processed CSV files on every request.
* The demo ran two independent model predictions sequentially.
* The dashboard fabricated results and latency when requests failed, ignored actual batch results, inserted three fake initial rows, and added artificial delays after the server had completed.
* The live event buffer used list offsets, which could stop delivering new events after old items were evicted.
* AWS clients are already reused for the lifetime of the service; no new cache service or AWS infrastructure was warranted.

## Performance and Correctness Changes

* Processed CSVs are now read once per backend worker and reused as read-only data. Restart the service after replacing a processed file to clear this cache.
* Model inference and synchronous DynamoDB access are moved to the framework thread pool so they do not block unrelated async requests. The independent anomaly and stability predictions in the demo run concurrently; Lambda mitigation remains conditional on the anomaly prediction.
* The bounded live event buffer now uses monotonic event IDs and supports `Last-Event-ID`, allowing reconnecting clients to resume without the former index/eviction bug.
* The API logs JSON request timing records containing method, path, status, duration, and a generated or validated `X-Request-ID`. Request bodies and credentials are not logged. Demo responses include measured `pipeline_latency_ms`; AWS persistence is identified as queued, not falsely reported as already complete.
* The dashboard keeps its original dark theme, sections, chart/table structure, and controls. It now shows real API errors and actual batch results, has no synthetic fallback data or artificial completion animation, suppresses duplicate SSE/demo rendering, and displays measured latency only. Keyboard focus, reduced-motion behavior, mobile spacing, and table overflow were also improved.
* AWS indicators now distinguish configured clients from verified connectivity. The service does not probe AWS on each health request, so an initialized boto3 client is not presented as proof that the remote service is reachable.
* Demo scenario names are descriptive labels; model output is no longer overwritten to force a desired score or classification.

This project does not currently contain LLM calls or an orchestration framework with additional independent AI agents. The demo's only independent model work is the anomaly and stability inference described above.

## Run, Test, and Measure

Use Python 3.12 as in the Docker image, or another Python version compatible with the pinned dependencies.

```powershell
python -m pip install -r requirements.txt
python -m uvicorn src.backend.main:app --host 127.0.0.1 --port 8000
```

Open the dashboard at `http://127.0.0.1:8000/dashboard` and API documentation at `http://127.0.0.1:8000/docs`. AWS credentials should be supplied through the standard boto3 credential chain (for example, an AWS profile or an ECS task role); do not put credentials in source or `config.json`.

To run the regression tests:

```powershell
python -m unittest discover -s tests -v
```

For a repeatable sequential API latency sample (warm-up requests excluded), run this from a second terminal after starting the server:

```powershell
python scripts/benchmark_api.py --url http://127.0.0.1:8000/agents/status --warmup 5 --requests 100
```

The script reports p50, p95, p99, maximum, and failed request counts. Use the same endpoint, machine, Python/dependency versions, server configuration, warm-up, request count, and representative input when comparing runs. `/agents/status` measures API overhead only. Measure end-to-end assessment separately with `/demo/run?scenario=random`; that route may invoke AWS Lambda and must only be benchmarked against a safe test environment with the required datasets and AWS permissions. Backend JSON request logs include server-side response timing and correlation IDs for comparing the frontend/network/API request path. Do not treat model latency as end-to-end latency.

### Measurements and Limitations

The `GET /agents/status` API-only check was run against the baseline commit and updated code on the same Windows machine, using Python 3.13.9, the declared requirements, one local Uvicorn worker, 5 warm-up requests, and 100 sequential measured requests. These numbers do **not** represent end-to-end assessment performance:

| Version | Successful / failed | p50 | p95 | p99 | Max |
|---|---:|---:|---:|---:|---:|
| Baseline (`a76eac3`) | 100 / 0 | 15.942 ms | 18.246 ms | 26.825 ms | 29.861 ms |
| Updated | 100 / 0 | 16.028 ms | 19.586 ms | 24.211 ms | 26.346 ms |

The health endpoint shows no measured p50/p95 speedup; the small differences are within the scope of a single sequential local sample, and the updated endpoint also includes request-correlation/timing instrumentation. No end-to-end before/after claim is made. The regression suite passed, the dashboard endpoint returned HTTP 200, and a demo request correctly returned HTTP 503 because `msu_ornl_processed.csv` is absent. The required anomaly dataset and raw MSU/ORNL inputs are not in this checkout, so assessment latency, agent latency under representative data, and AWS/Lambda behavior could not be benchmarked. AWS calls and Lambda behavior were not tested. No additional AWS services or infrastructure costs were introduced by these code changes.
