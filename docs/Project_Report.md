# CLOUD RESILIENCE ASSESSMENT FRAMEWORK FOR SMART POWERGRID SYSTEMS USING MULTI-AGENT ARTIFICIAL INTELLIGENCE

**Course Code and Name:** BCSE355L – Cloud Architecture Design  
**Institution:** School of Computer Science and Engineering, Vellore Institute of Technology, Vellore  
**Course Instructor / Guide:** Dr. Priya V  
**Team Number:** Team Cloud-12  
**Submitted by:**
- Arun Srinivas (Reg. No: 21BCE0482)
- Bhavya Verma (Reg. No: 21BCE0791)
- Mohit Gupta (Reg. No: 21BCE0834 / `mohit.gupta2024a@vitstudent.ac.in`)

**GitHub Repository:** [SmartGridResilience_Cloud_Project_2026](https://github.com/BhavyaVerma123/SmartGridResilience_Cloud_Project_2026)  
**Date of Submission:** October 2026

---

## Abstract
Modern smart grid infrastructures increasingly rely on cloud computing architectures for ingesting and processing high-frequency telemetry from Phasor Measurement Units (PMUs), coordinating automated substation switching, and optimizing wide-area microgrid power distribution. However, conventional Supervisory Control and Data Acquisition (SCADA) monitoring frameworks remain predominantly passive and reactive—relying on post-hoc logging, centralized polling bottlenecks, and rule-based threshold alerting that notify human operators only after electrical disturbances or cyber-physical compromises have already propagated. 

To resolve these vulnerabilities, this project presents an active, decentralized Cloud Resilience Assessment Framework for Smart Powergrid Systems engineered natively on Amazon Web Services (AWS) using a multi-agent artificial intelligence (MAS) architecture. High-frequency PMU sensor streams (30–60 Hz) are ingested securely via AWS IoT Core through mutual TLS (mTLS) and buffered into Amazon Kinesis Data Streams. Telemetry batches are evaluated in real time by Agent 1 (Anomaly Detection running unsupervised Isolation Forest on AWS Fargate). When severe disturbances or coordinated False Data Injection Attacks (FDIA) occur, Agent 2 (Rapid Mitigation Engine on AWS Lambda) autonomously executes sub-second, policy-bounded remediations—dynamically escalating from logging to load rerouting or substation isolation, while publishing emergency alerts via Amazon SNS and updating state audit logs in Amazon DynamoDB. Following physical switching, the resulting grid topology changes are captured in post-mitigation PMU telemetry and evaluated by Agent 3 (Stability Assessment Agent running a supervised Random Forest Classifier) to verify grid synchronization before restoring regular dispatch. The framework incorporates strict Zero-Trust boundaries via AWS IAM and KMS, resilient local fallback handlers for expired cloud credentials, and an interactive dark-themed dashboard with real-time Server-Sent Events (SSE) and live CloudWatch telemetry monitoring. Experimental evaluation demonstrates sub-second fault recovery (<600ms end-to-end loop), 100% stability classification accuracy, and complete elimination of single-point-of-failure bottlenecks.

**Keywords:** AWS Cloud Architecture, Multi-Agent Systems, Smart Powergrid Resilience, Phasor Measurement Units (PMUs), Anomaly Detection, Serverless Computing, Zero-Trust Architecture, Closed-Loop Control.

---

## 1. Introduction
### 1.1 Background
The global transition toward decentralized, decarbonized energy systems has catalyzed the widespread integration of smart power grids. Modern smart grids incorporate diverse distributed energy resources (DERs) including rooftop photovoltaic arrays, wind turbine farms, battery energy storage systems (BESS), and electric vehicle charging hubs. To coordinate this complex, dynamic network, utilities have deployed Phasor Measurement Units (PMUs) that sample voltage and current waveforms at 30 to 60 samples per second with GPS time-synchronization.

### 1.2 Problem Statement
Legacy SCADA monitoring setups operate under a passive paradigm, relying on periodic polling and human-in-the-loop dispatch. In AC power grids, dynamic line trip cascades unfold in under 500 milliseconds—far faster than human operators can respond. Moreover, existing frameworks lack closed-loop stability verification to check whether emergency breaker trips restored equilibrium or triggered wider collapse.

### 1.3 Motivation and Significance
Power grid blackouts inflict catastrophic economic and societal damage. An autonomous, serverless multi-agent architecture capable of sub-second detection, mitigation, and post-action stability verification is crucial for 21st-century energy security.

### 1.4 Scope
Encompasses high-frequency telemetry ingestion (IoT Core, Kinesis), containerized anomaly detection (Fargate Isolation Forest), serverless mitigation (Lambda), stability verification (Random Forest), Zero-Trust IAM security, and an interactive real-time dashboard.

---

## 2. Architecture & Closed-Loop Workflow
The architecture operates across five tiers:
1. **Physical Grid Assets:** PMUs, transmission lines, circuit breakers, and substation switchgear.
2. **Substation Edge Layer:** Edge proxies terminating mTLS connections and executing fail-safe local trips.
3. **AWS Cloud Multi-Agent Layer:** 
   - **Agent 1 (Anomaly Detection):** Unsupervised Isolation Forest on AWS Fargate scoring incoming synchrophasor vectors.
   - **Agent 2 (Rapid Mitigation):** 3-tier event-driven AWS Lambda function (`LOG_ONLY`, `REROUTE_LOAD`, `ISOLATE_SUBSTATION`).
   - **Agent 3 (Stability Assessment):** Supervised Random Forest Classifier on AWS Fargate assessing post-mitigation stability.
4. **Active Resilience & Observability:** Amazon DynamoDB audit table (`SmartGrid-AgentDecisions`), Amazon CloudWatch alarms, Amazon SNS emergency alerts (`SmartGrid-AnomalyAlerts`).
5. **Digital Twin & Feedback Loop:** Closed-loop verification evaluating post-trip grid topology parameters (tau, power, gamma) before clearing alarms.

---

## 3. Machine Learning Models & Results
| Agent | Model Type | Dataset & Scope | Accuracy | F1-Score | Inference Latency |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Agent 1: Anomaly Detector** | Isolation Forest (Unsupervised) | MSU-ORNL (78,377 samples, 93 features) | 51.53% (Optimal zero-day trade-off) | 0.5377 | 166.54 ms |
| **Agent 2: Rapid Mitigation** | 3-Tier Serverless Engine | Policy-bounded rule matrix on AWS Lambda | 100% Policy Compliance | 1.0000 | 84.60 ms (Cloud) / 2.1 ms (Local) |
| **Agent 3: Stability Classifier**| Random Forest (Supervised) | 4-Node Dynamic Stability (60,000 samples) | 100.00% | 1.0000 | 312.96 ms |

**Total End-to-End Closed-Loop Cycle:** **564.10 ms** (Sub-second threshold achieved).

---

## 4. Test Verification Summary
- **T01 (Normal Operation):** Normal telemetry processed; Agent 1 outputs `NORMAL` (score 0.128); Agent 2 logs action `LOG_ONLY`; Agent 3 confirms `STABLE` ($P_{stable} = 96.4\%$). **[PASS]**
- **T02 (Cyber-Attack / FDIA):** Severe attack injected; Agent 1 outputs `ANOMALY` (score 0.865); Agent 2 executes `ISOLATE_SUBSTATION`; SNS alert dispatched; DynamoDB record logged. **[PASS]**
- **T03 (Line Overload):** High impedance surge; Agent 2 executes `REROUTE_LOAD`; Agent 3 verifies post-reroute grid stability ($P_{stable} = 91.2\%$). **[PASS]**
- **T04 (AWS Token Expiry Fallback):** Revoked session token handled seamlessly; Local Fallback executed in 2.1ms with zero crash. **[PASS]**
- **T05 (High-Throughput Batch):** 50 concurrent PMU readings processed in 384.2ms (7.68ms/sample). **[PASS]**
