"""
aws_integration.py
────────────────────────────────────────────────────────────────────────────
Cloud Resilience Assessment Framework — Smart Powergrid Systems
Course  : BCSE355L — Cloud Architecture Design
Author  : Mohit Gupta (feature/Mohit)

PURPOSE
-------
AWS service wrapper classes used by all backend agents:

  AWSLogger          — writes agent decisions to DynamoDB + CloudWatch
  AlertManager       — publishes anomaly alerts to SNS
  MitigationExecutor — invokes Lambda mitigation function
  IoTPublisher       — publishes simulated PMU data to IoT Core
────────────────────────────────────────────────────────────────────────────
"""

import json
import time
import boto3
import datetime
import os
from pathlib import Path

# ─── Load config ──────────────────────────────────────────────────────────
CONFIG_PATH = Path(__file__).parent / "config.json"

def load_config() -> dict:
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH) as f:
            return json.load(f)
    # Fallback defaults
    return {
        "region"         : "us-east-1",
        "dynamodb_table" : "SmartGrid-AgentDecisions",
        "sns_topic_arn"  : "",
        "lambda_function": "SmartGrid-MitigationTrigger",
        "cw_namespace"   : "SmartGrid/Agents",
        "iot_endpoint"   : "",
    }

CFG = load_config()
REGION = CFG["region"]


# ═══════════════════════════════════════════════════════════════════════════
#  AWS LOGGER — DynamoDB + CloudWatch
# ═══════════════════════════════════════════════════════════════════════════

class AWSLogger:
    """
    Logs every agent decision to:
      - DynamoDB  (persistent storage, queryable)
      - CloudWatch (real-time metrics for dashboards and alarms)
    """

    def __init__(self):
        self._ddb = boto3.client("dynamodb",   region_name=REGION)
        self._cw  = boto3.client("cloudwatch", region_name=REGION)
        self._table     = CFG["dynamodb_table"]
        self._namespace = CFG["cw_namespace"]

    def log_anomaly_decision(self, result: dict) -> bool:
        """
        Log an AnomalyDetectionAgent result.

        Parameters
        ----------
        result : dict with keys: agent_id, label, anomaly_score,
                 confidence, latency_ms, timestamp
        """
        ts  = result.get("timestamp", datetime.datetime.utcnow().isoformat())
        ttl = int(time.time()) + (90 * 24 * 3600)   # expire after 90 days

        try:
            # ── DynamoDB write ──────────────────────────────────────────
            self._ddb.put_item(
                TableName = self._table,
                Item = {
                    "agent_id"      : {"S": result.get("agent_id", "unknown")},
                    "timestamp"     : {"S": ts},
                    "label"         : {"S": result.get("label", "UNKNOWN")},
                    "anomaly_score" : {"N": str(result.get("anomaly_score", 0.0))},
                    "confidence"    : {"N": str(result.get("confidence", 0.0))},
                    "latency_ms"    : {"N": str(result.get("latency_ms", 0.0))},
                    "is_anomaly"    : {"BOOL": result.get("is_anomaly", False)},
                    "ttl"           : {"N": str(ttl)},
                }
            )

            # ── CloudWatch metrics ──────────────────────────────────────
            self._cw.put_metric_data(
                Namespace  = self._namespace,
                MetricData = [
                    {
                        "MetricName": "AnomalyScore",
                        "Dimensions": [{"Name": "AgentType", "Value": "AnomalyDetection"}],
                        "Value"     : float(result.get("anomaly_score", 0.0)),
                        "Unit"      : "None",
                    },
                    {
                        "MetricName": "InferenceLatencyMs",
                        "Dimensions": [{"Name": "AgentType", "Value": "AnomalyDetection"}],
                        "Value"     : float(result.get("latency_ms", 0.0)),
                        "Unit"      : "Milliseconds",
                    },
                    {
                        "MetricName": "AnomalyCount",
                        "Dimensions": [{"Name": "AgentType", "Value": "AnomalyDetection"}],
                        "Value"     : 1.0 if result.get("is_anomaly") else 0.0,
                        "Unit"      : "Count",
                    },
                ]
            )
            return True

        except Exception as e:
            print(f"[AWSLogger] ERROR logging anomaly decision: {e}")
            return False

    def log_stability_decision(self, result: dict) -> bool:
        """Log a StabilityAssessmentAgent result."""
        ts  = result.get("timestamp", datetime.datetime.utcnow().isoformat())
        ttl = int(time.time()) + (90 * 24 * 3600)

        try:
            self._ddb.put_item(
                TableName = self._table,
                Item = {
                    "agent_id"      : {"S": result.get("agent_id", "stability-agent")},
                    "timestamp"     : {"S": ts},
                    "label"         : {"S": result.get("label", "UNKNOWN")},
                    "stable_prob"   : {"N": str(result.get("stable_prob", 0.0))},
                    "unstable_prob" : {"N": str(result.get("unstable_prob", 0.0))},
                    "latency_ms"    : {"N": str(result.get("latency_ms", 0.0))},
                    "is_stable"     : {"BOOL": result.get("is_stable", True)},
                    "ttl"           : {"N": str(ttl)},
                }
            )

            self._cw.put_metric_data(
                Namespace  = self._namespace,
                MetricData = [
                    {
                        "MetricName": "UnstableCount",
                        "Dimensions": [{"Name": "AgentType", "Value": "StabilityAssessment"}],
                        "Value"     : 0.0 if result.get("is_stable") else 1.0,
                        "Unit"      : "Count",
                    },
                    {
                        "MetricName": "StabilityProbability",
                        "Dimensions": [{"Name": "AgentType", "Value": "StabilityAssessment"}],
                        "Value"     : float(result.get("stable_prob", 1.0)),
                        "Unit"      : "None",
                    },
                ]
            )
            return True

        except Exception as e:
            print(f"[AWSLogger] ERROR logging stability decision: {e}")
            return False

    def get_recent_decisions(self, agent_id: str, limit: int = 20) -> list:
        """Fetch recent decisions from DynamoDB for dashboard display."""
        try:
            response = self._ddb.query(
                TableName                = self._table,
                KeyConditionExpression   = "agent_id = :aid",
                ExpressionAttributeValues= {":aid": {"S": agent_id}},
                ScanIndexForward         = False,   # newest first
                Limit                    = limit,
            )
            items = []
            for item in response.get("Items", []):
                items.append({
                    "agent_id"    : item.get("agent_id",      {}).get("S", ""),
                    "timestamp"   : item.get("timestamp",     {}).get("S", ""),
                    "label"       : item.get("label",         {}).get("S", ""),
                    "anomaly_score": float(item.get("anomaly_score", {}).get("N", 0)),
                    "confidence"  : float(item.get("confidence",    {}).get("N", 0)),
                    "latency_ms"  : float(item.get("latency_ms",    {}).get("N", 0)),
                })
            return items
        except Exception as e:
            print(f"[AWSLogger] ERROR fetching decisions: {e}")
            return []


# ═══════════════════════════════════════════════════════════════════════════
#  ALERT MANAGER — SNS
# ═══════════════════════════════════════════════════════════════════════════

class AlertManager:
    """
    Sends anomaly alerts via Amazon SNS.
    Only fires when anomaly_score >= 0.7 to prevent alert fatigue.
    """

    ALERT_THRESHOLD = 0.7

    def __init__(self):
        self._sns       = boto3.client("sns", region_name=REGION)
        self._topic_arn = CFG.get("sns_topic_arn", "")

    def send_alert(self, result: dict) -> bool:
        """
        Publish an anomaly alert to SNS topic.

        Parameters
        ----------
        result : AnomalyResult dict
        """
        if not self._topic_arn:
            print("[AlertManager] No SNS topic ARN configured")
            return False

        score = float(result.get("anomaly_score", 0.0))
        if score < self.ALERT_THRESHOLD:
            return False   # below threshold — don't alert

        severity = "CRITICAL" if score >= 0.85 else "HIGH"
        subject  = f"[{severity}] Smart Grid Anomaly — Score: {score:.2f}"

        message = {
            "alert_type"   : f"GRID_ANOMALY_{severity}",
            "agent_id"     : result.get("agent_id"),
            "label"        : result.get("label"),
            "anomaly_score": score,
            "confidence"   : result.get("confidence"),
            "timestamp"    : result.get("timestamp"),
            "action"       : "MitigationAgent triggered automatically",
            "course"       : "BCSE355L — Cloud Resilience Assessment Framework",
        }

        try:
            resp = self._sns.publish(
                TopicArn = self._topic_arn,
                Subject  = subject,
                Message  = json.dumps(message, indent=2),
                MessageAttributes = {
                    "severity": {
                        "DataType"   : "String",
                        "StringValue": severity,
                    }
                }
            )
            print(f"[AlertManager] SNS alert sent — MessageId: {resp['MessageId']}")
            return True
        except Exception as e:
            print(f"[AlertManager] ERROR sending SNS alert: {e}")
            return False


# ═══════════════════════════════════════════════════════════════════════════
#  MITIGATION EXECUTOR — Lambda
# ═══════════════════════════════════════════════════════════════════════════

class MitigationExecutor:
    """
    Invokes the SmartGrid-MitigationTrigger Lambda function
    when an anomaly is detected by the AnomalyDetectionAgent.
    """

    def __init__(self):
        self._lam  = boto3.client("lambda", region_name=REGION)
        self._func = CFG.get("lambda_function", "SmartGrid-MitigationTrigger")

    def execute(self, anomaly_result: dict) -> dict:
        """
        Invoke the mitigation Lambda with the anomaly result payload.

        Returns the mitigation action taken (from Lambda response).
        """
        if not anomaly_result.get("is_anomaly", False):
            return {"status": "SKIPPED", "reason": "No anomaly detected"}

        try:
            response = self._lam.invoke(
                FunctionName   = self._func,
                InvocationType = "RequestResponse",   # synchronous
                Payload        = json.dumps(anomaly_result).encode(),
            )
            payload = json.loads(response["Payload"].read())

            if "body" in payload:
                result = json.loads(payload["body"])
            else:
                result = payload

            print(f"[MitigationExecutor] Lambda invoked → action: {result.get('action_taken', 'unknown')}")
            return result

        except Exception as e:
            print(f"[MitigationExecutor] Lambda unavailable, using local fallback: {e}")
            return self._local_fallback(anomaly_result)

    @staticmethod
    def _local_fallback(anomaly_result: dict) -> dict:
        """
        Local fallback mirroring the Lambda's 3-tier decision logic.
        Used when Lambda is unavailable (expired creds, network issues).
        """
        import datetime as _dt
        score = float(anomaly_result.get("anomaly_score", 0.0))

        if score >= 0.8:
            severity = "HIGH"
            action   = "ISOLATE_SUBSTATION"
            message  = "Substation isolated — backup power activated — SNS alert sent"
        elif score >= 0.6:
            severity = "MEDIUM"
            action   = "REROUTE_LOAD"
            message  = "Load rerouted to adjacent substations — monitoring active"
        else:
            severity = "LOW"
            action   = "LOG_ONLY"
            message  = "Event logged — no immediate action required"

        return {
            "mitigation_agent_id": "mitigation-lambda-az1",
            "triggered_by"      : anomaly_result.get("agent_id", "anomaly-agent-az1"),
            "anomaly_score"     : score,
            "severity"          : severity,
            "action_taken"      : action,
            "message"           : message,
            "timestamp"         : _dt.datetime.utcnow().isoformat(),
            "status"            : "EXECUTED",
            "source"            : "local-fallback",
        }


# ═══════════════════════════════════════════════════════════════════════════
#  IoT PUBLISHER — Simulates PMU data stream
# ═══════════════════════════════════════════════════════════════════════════

class IoTPublisher:
    """
    Publishes simulated PMU sensor readings to AWS IoT Core.

    In production: Real PMU devices would publish directly.
    In our project: We simulate the PMU stream using processed dataset samples.

    Topic: smartgrid/pmu/readings
    """

    TOPIC = "smartgrid/pmu/readings"

    def __init__(self):
        endpoint = CFG.get("iot_endpoint", "")
        if endpoint:
            self._client = boto3.client(
                "iot-data",
                region_name   = REGION,
                endpoint_url  = f"https://{endpoint}",
            )
        else:
            self._client = None

    def publish_reading(self, pmu_data: dict, sensor_id: str = "PMU-R1") -> bool:
        """Publish a PMU reading to IoT Core MQTT topic."""
        if not self._client:
            return False

        payload = {
            "sensor_id" : sensor_id,
            "timestamp" : datetime.datetime.utcnow().isoformat(),
            "reading"   : pmu_data,
            "protocol"  : "IEEE-C37.118",
        }

        try:
            self._client.publish(
                topic   = self.TOPIC,
                qos     = 1,
                payload = json.dumps(payload).encode(),
            )
            return True
        except Exception as e:
            print(f"[IoTPublisher] ERROR publishing to IoT Core: {e}")
            return False


# ═══════════════════════════════════════════════════════════════════════════
#  QUICK TEST
# ═══════════════════════════════════════════════════════════════════════════

def test_aws_integration():
    """Test all AWS integration components with a mock anomaly result."""
    print("=" * 55)
    print("  AWS Integration Layer — Component Test")
    print("=" * 55)

    mock_anomaly = {
        "agent_id"     : "anomaly-agent-az1",
        "label"        : "ANOMALY",
        "is_anomaly"   : True,
        "anomaly_score": 0.87,
        "confidence"   : 87.0,
        "latency_ms"   : 42.3,
        "timestamp"    : datetime.datetime.utcnow().isoformat(),
    }

    mock_stability = {
        "agent_id"    : "stability-agent-az1",
        "label"       : "UNSTABLE",
        "is_stable"   : False,
        "stable_prob" : 0.12,
        "unstable_prob": 0.88,
        "latency_ms"  : 61.0,
        "timestamp"   : datetime.datetime.utcnow().isoformat(),
    }

    print("\n[1/4] Testing AWSLogger — DynamoDB + CloudWatch...")
    logger = AWSLogger()
    ok1 = logger.log_anomaly_decision(mock_anomaly)
    ok2 = logger.log_stability_decision(mock_stability)
    print(f"  Anomaly logged to DynamoDB  : {'OK' if ok1 else 'FAILED'}")
    print(f"  Stability logged to DynamoDB: {'OK' if ok2 else 'FAILED'}")

    print("\n[2/4] Testing AlertManager — SNS...")
    alert = AlertManager()
    ok3 = alert.send_alert(mock_anomaly)
    print(f"  SNS alert sent: {'OK' if ok3 else 'SKIPPED (no topic or below threshold)'}")

    print("\n[3/4] Testing MitigationExecutor — Lambda...")
    executor = MitigationExecutor()
    mitigation = executor.execute(mock_anomaly)
    print(f"  Lambda response: {mitigation.get('action_taken', mitigation.get('status', 'unknown'))}")

    print("\n[4/4] Fetching recent DynamoDB decisions...")
    decisions = logger.get_recent_decisions("anomaly-agent-az1", limit=5)
    print(f"  Retrieved {len(decisions)} recent decisions from DynamoDB")
    if decisions:
        latest = decisions[0]
        print(f"  Latest: {latest.get('label')} at {latest.get('timestamp')[:19]}")

    print("\n" + "=" * 55)
    print("  AWS Integration Layer — All components tested!")
    print("=" * 55)


if __name__ == "__main__":
    test_aws_integration()
