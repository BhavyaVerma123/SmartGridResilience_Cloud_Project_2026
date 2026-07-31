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
* **AI & Agent Modeling:** Python, Multi-Agent Reinforcement Learning Frameworks
* **Cloud Infrastructure:** AWS (EC2, Lambda, S3, CloudWatch, IAM, Terraform)
* **Backend:** Node.js / Python API Services
* **Frontend:** React / Dashboard UI with charting libraries
* **Database:** PostgreSQL (Relational) / Time-series Database for grid logs

## Dataset Details
The project utilizes simulated smart powergrid datasets alongside cloud infrastructure performance metrics:
* **Grid Data:** Simulated power flow, voltage fluctuations, and load profiles generated via standard IEEE test systems.
* **Cloud Data:** Simulated and real-time AWS CloudWatch logs tracking CPU utilization, network latency, memory usage, and API error codes during injection attacks or service blackouts.
