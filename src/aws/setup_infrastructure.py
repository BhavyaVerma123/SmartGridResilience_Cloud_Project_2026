"""
setup_infrastructure.py
────────────────────────────────────────────────────────────────────────────
Cloud Resilience Assessment Framework — Smart Powergrid Systems
Course  : BCSE355L — Cloud Architecture Design
Author  : Mohit Gupta (feature/Mohit)

PURPOSE
-------
One-time script that creates all AWS resources required by the project:

  1. DynamoDB Table     — SmartGrid-AgentDecisions
  2. SNS Topic          — SmartGrid-AnomalyAlerts
  3. CloudWatch Alarms  — SmartGrid/Agents namespace
  4. Lambda Function    — SmartGrid-MitigationTrigger
  5. IoT Core Thing     — SmartGrid-PMU-Sensor

Run once:  python src/aws/setup_infrastructure.py
────────────────────────────────────────────────────────────────────────────
"""

import boto3
import json
import zipfile
import io
import time
import os

REGION        = "us-east-1"
ACCOUNT_ID    = "214086774356"
TABLE_NAME    = "SmartGrid-AgentDecisions"
SNS_TOPIC     = "SmartGrid-AnomalyAlerts"
LAMBDA_NAME   = "SmartGrid-MitigationTrigger"
IOT_THING     = "SmartGrid-PMU-Sensor"
CW_NAMESPACE  = "SmartGrid/Agents"

# ─── Clients ──────────────────────────────────────────────────────────────
ddb     = boto3.client("dynamodb",    region_name=REGION)
sns     = boto3.client("sns",         region_name=REGION)
cw      = boto3.client("cloudwatch",  region_name=REGION)
lam     = boto3.client("lambda",      region_name=REGION)
iam     = boto3.client("iam",         region_name=REGION)
iot     = boto3.client("iot",         region_name=REGION)

results = {}

def section(title):
    print(f"\n{'─'*55}")
    print(f"  {title}")
    print(f"{'─'*55}")


# ═══════════════════════════════════════════════════════════════════════════
#  1. DynamoDB Table
# ═══════════════════════════════════════════════════════════════════════════

def create_dynamodb_table():
    section("1/5  Creating DynamoDB Table")

    # Check if already exists
    existing = ddb.list_tables()["TableNames"]
    if TABLE_NAME in existing:
        print(f"  Table '{TABLE_NAME}' already exists — skipping")
        desc = ddb.describe_table(TableName=TABLE_NAME)["Table"]
        results["dynamodb_arn"] = desc["TableArn"]
        return

    response = ddb.create_table(
        TableName            = TABLE_NAME,
        AttributeDefinitions = [
            {"AttributeName": "agent_id",  "AttributeType": "S"},
            {"AttributeName": "timestamp", "AttributeType": "S"},
        ],
        KeySchema = [
            {"AttributeName": "agent_id",  "KeyType": "HASH"},
            {"AttributeName": "timestamp", "KeyType": "RANGE"},
        ],
        BillingMode = "PAY_PER_REQUEST",   # On-demand — no fixed cost
        Tags = [
            {"Key": "Project", "Value": "SmartGridResilience"},
            {"Key": "Course",  "Value": "BCSE355L"},
            {"Key": "Author",  "Value": "Mohit"},
        ]
    )

    # Wait for table to be ACTIVE
    print(f"  Creating table '{TABLE_NAME}'... ", end="", flush=True)
    waiter = ddb.get_waiter("table_exists")
    waiter.wait(TableName=TABLE_NAME)
    print("ACTIVE ✅")

    arn = response["TableDescription"]["TableArn"]
    results["dynamodb_arn"] = arn
    print(f"  ARN: {arn}")

    # Enable TTL — auto-delete old records after 90 days
    ddb.update_time_to_live(
        TableName = TABLE_NAME,
        TimeToLiveSpecification = {"Enabled": True, "AttributeName": "ttl"}
    )
    print(f"  TTL enabled (auto-delete after 90 days)")


# ═══════════════════════════════════════════════════════════════════════════
#  2. SNS Topic
# ═══════════════════════════════════════════════════════════════════════════

def create_sns_topic():
    section("2/5  Creating SNS Topic")

    response = sns.create_topic(
        Name = SNS_TOPIC,
        Tags = [
            {"Key": "Project", "Value": "SmartGridResilience"},
        ]
    )
    topic_arn = response["TopicArn"]
    results["sns_topic_arn"] = topic_arn
    print(f"  Topic '{SNS_TOPIC}' ready ✅")
    print(f"  ARN: {topic_arn}")

    # Set display name for SMS alerts
    sns.set_topic_attributes(
        TopicArn      = topic_arn,
        AttributeName = "DisplayName",
        AttributeValue= "SmartGrid Alert"
    )


# ═══════════════════════════════════════════════════════════════════════════
#  3. CloudWatch Alarms
# ═══════════════════════════════════════════════════════════════════════════

def create_cloudwatch_alarms():
    section("3/5  Creating CloudWatch Alarms")

    # Alarm 1: High anomaly rate — triggers if >5 anomalies in 5 minutes
    cw.put_metric_alarm(
        AlarmName          = "SmartGrid-HighAnomalyRate",
        AlarmDescription   = "Triggers when anomaly detection rate exceeds threshold",
        Namespace          = CW_NAMESPACE,
        MetricName         = "AnomalyCount",
        Dimensions         = [{"Name": "AgentType", "Value": "AnomalyDetection"}],
        Period             = 300,    # 5 minutes
        EvaluationPeriods  = 1,
        Threshold          = 5.0,
        ComparisonOperator = "GreaterThanOrEqualToThreshold",
        Statistic          = "Sum",
        AlarmActions       = [results.get("sns_topic_arn", "")],
        TreatMissingData   = "notBreaching",
    )
    print("  Alarm 'SmartGrid-HighAnomalyRate'  ✅  (threshold: 5 anomalies/5min)")

    # Alarm 2: High inference latency
    cw.put_metric_alarm(
        AlarmName          = "SmartGrid-HighLatency",
        AlarmDescription   = "Triggers when agent inference latency exceeds 500ms",
        Namespace          = CW_NAMESPACE,
        MetricName         = "InferenceLatencyMs",
        Dimensions         = [{"Name": "AgentType", "Value": "AnomalyDetection"}],
        Period             = 60,
        EvaluationPeriods  = 3,
        Threshold          = 500.0,
        ComparisonOperator = "GreaterThanThreshold",
        Statistic          = "Average",
        TreatMissingData   = "notBreaching",
    )
    print("  Alarm 'SmartGrid-HighLatency'       ✅  (threshold: 500ms)")

    # Alarm 3: Grid instability
    cw.put_metric_alarm(
        AlarmName          = "SmartGrid-GridInstability",
        AlarmDescription   = "Triggers when grid stability drops — mitigation agent alerted",
        Namespace          = CW_NAMESPACE,
        MetricName         = "UnstableCount",
        Dimensions         = [{"Name": "AgentType", "Value": "StabilityAssessment"}],
        Period             = 300,
        EvaluationPeriods  = 1,
        Threshold          = 3.0,
        ComparisonOperator = "GreaterThanOrEqualToThreshold",
        Statistic          = "Sum",
        AlarmActions       = [results.get("sns_topic_arn", "")],
        TreatMissingData   = "notBreaching",
    )
    print("  Alarm 'SmartGrid-GridInstability'   ✅  (threshold: 3 unstable/5min)")


# ═══════════════════════════════════════════════════════════════════════════
#  4. Lambda Function — Mitigation Trigger
# ═══════════════════════════════════════════════════════════════════════════

LAMBDA_CODE = '''
import json
import boto3
import datetime

REGION     = "us-east-1"
TABLE_NAME = "SmartGrid-AgentDecisions"
SNS_ARN    = None  # injected via environment variable

def lambda_handler(event, context):
    """
    Mitigation Agent — executed when AnomalyDetectionAgent raises an alert.
    
    Decision Logic:
      anomaly_score < 0.6  → LOW    → Log only
      anomaly_score < 0.8  → MEDIUM → Reroute load
      anomaly_score >= 0.8 → HIGH   → Isolate substation + alert
    """
    print("MitigationAgent triggered:", json.dumps(event))
    
    # Parse incoming anomaly event
    body          = event if isinstance(event, dict) else json.loads(event.get("body", "{}"))
    anomaly_score = float(body.get("anomaly_score", 0.5))
    agent_id      = body.get("agent_id", "unknown")
    timestamp     = datetime.datetime.utcnow().isoformat()
    
    # Mitigation decision logic
    if anomaly_score < 0.6:
        action   = "LOG_ONLY"
        severity = "LOW"
        message  = "Anomaly score below threshold — monitoring continued"
    elif anomaly_score < 0.8:
        action   = "REROUTE_LOAD"
        severity = "MEDIUM"
        message  = "Load rerouted to backup substation — grid stabilizing"
    else:
        action   = "ISOLATE_SUBSTATION"
        severity = "HIGH"
        message  = "Substation isolated — backup power activated — SNS alert sent"

    mitigation_result = {
        "mitigation_agent_id" : "mitigation-lambda-az1",
        "triggered_by"        : agent_id,
        "anomaly_score"       : anomaly_score,
        "severity"            : severity,
        "action_taken"        : action,
        "message"             : message,
        "timestamp"           : timestamp,
        "status"              : "EXECUTED"
    }
    
    # Store mitigation record in DynamoDB
    try:
        ddb = boto3.client("dynamodb", region_name=REGION)
        ddb.put_item(
            TableName = TABLE_NAME,
            Item = {
                "agent_id"  : {"S": "mitigation-lambda-az1"},
                "timestamp" : {"S": timestamp},
                "label"     : {"S": action},
                "severity"  : {"S": severity},
                "score"     : {"N": str(anomaly_score)},
                "message"   : {"S": message},
            }
        )
    except Exception as e:
        print("DynamoDB write error:", e)
    
    # Send SNS alert for HIGH severity
    if severity == "HIGH":
        try:
            sns_arn = context.client_context.env.get("SNS_TOPIC_ARN") if context.client_context else None
            if not sns_arn:
                import os
                sns_arn = os.environ.get("SNS_TOPIC_ARN")
            if sns_arn:
                boto3.client("sns", region_name=REGION).publish(
                    TopicArn = sns_arn,
                    Subject  = "[CRITICAL] Smart Grid Anomaly Detected",
                    Message  = json.dumps(mitigation_result, indent=2)
                )
        except Exception as e:
            print("SNS publish error:", e)
    
    print("Mitigation complete:", json.dumps(mitigation_result))
    return {
        "statusCode": 200,
        "body": json.dumps(mitigation_result)
    }
'''


def create_lambda_function():
    section("4/5  Creating Lambda Function (Mitigation Agent)")

    # AWS Academy Learner Lab provides a pre-built 'LabRole' with full permissions
    # We use this instead of creating a new role (IAM role creation is restricted)
    role_arn = f"arn:aws:iam::{ACCOUNT_ID}:role/LabRole"
    print(f"  Using AWS Academy pre-built IAM role: LabRole")
    print(f"  ARN: {role_arn}")

    results["lambda_role_arn"] = role_arn

    # Package Lambda code as ZIP
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("lambda_function.py", LAMBDA_CODE)
    zip_buffer.seek(0)
    zip_bytes = zip_buffer.read()

    # Check if function exists already
    try:
        existing = lam.get_function(FunctionName=LAMBDA_NAME)
        print(f"  Updating existing Lambda function '{LAMBDA_NAME}'...")
        lam.update_function_code(
            FunctionName = LAMBDA_NAME,
            ZipFile      = zip_bytes,
        )
        func_arn = existing["Configuration"]["FunctionArn"]
        print(f"  Lambda updated ✅")
    except lam.exceptions.ResourceNotFoundException:
        print(f"  Creating Lambda function '{LAMBDA_NAME}'...")
        response = lam.create_function(
            FunctionName = LAMBDA_NAME,
            Runtime      = "python3.12",
            Role         = role_arn,
            Handler      = "lambda_function.lambda_handler",
            Code         = {"ZipFile": zip_bytes},
            Description  = "Mitigation Agent — triggered when anomaly detected in smart grid",
            Timeout      = 30,
            MemorySize   = 256,
            Environment  = {
                "Variables": {
                    "SNS_TOPIC_ARN" : results.get("sns_topic_arn", ""),
                    "TABLE_NAME"    : TABLE_NAME,
                    "REGION"        : REGION,
                }
            },
            Tags = {
                "Project": "SmartGridResilience",
                "Course" : "BCSE355L",
                "Author" : "Mohit",
            }
        )
        func_arn = response["FunctionArn"]
        # Wait for function to be active
        print("  Waiting for Lambda to be active...", end="", flush=True)
        waiter = lam.get_waiter("function_active_v2")
        waiter.wait(FunctionName=LAMBDA_NAME)
        print(" ACTIVE ✅")

    results["lambda_arn"] = func_arn
    print(f"  ARN: {func_arn}")


# ═══════════════════════════════════════════════════════════════════════════
#  5. IoT Core — Virtual PMU Sensor Thing
# ═══════════════════════════════════════════════════════════════════════════

def create_iot_thing():
    section("5/5  Creating IoT Core PMU Sensor")

    # Create Thing (max 3 attributes without a ThingType)
    try:
        thing = iot.create_thing(
            thingName       = IOT_THING,
            attributePayload= {
                "attributes": {
                    "type"    : "PMU",
                    "location": "Substation-A",
                    "protocol": "IEEE-C37.118",
                }
            }
        )
        thing_arn = thing["thingArn"]
        print(f"  Thing '{IOT_THING}' created ✅")
    except iot.exceptions.ResourceAlreadyExistsException:
        thing = iot.describe_thing(thingName=IOT_THING)
        thing_arn = thing["thingArn"]
        print(f"  Thing '{IOT_THING}' already exists ✅")

    results["iot_thing_arn"] = thing_arn
    print(f"  ARN: {thing_arn}")

    # Create IoT Policy
    policy_name = "SmartGrid-PMU-Policy"
    policy_doc  = {
        "Version": "2012-10-17",
        "Statement": [{
            "Effect"  : "Allow",
            "Action"  : ["iot:Connect", "iot:Publish", "iot:Subscribe", "iot:Receive"],
            "Resource": "*"
        }]
    }
    try:
        iot.create_policy(
            policyName     = policy_name,
            policyDocument = json.dumps(policy_doc)
        )
        print(f"  IoT Policy '{policy_name}' created ✅")
    except iot.exceptions.ResourceAlreadyExistsException:
        print(f"  IoT Policy '{policy_name}' already exists ✅")

    results["iot_policy"] = policy_name

    # Get IoT endpoint for MQTT publishing
    endpoint = iot.describe_endpoint(endpointType="iot:Data-ATS")
    results["iot_endpoint"] = endpoint["endpointAddress"]
    print(f"  IoT Endpoint: {endpoint['endpointAddress']}")


# ═══════════════════════════════════════════════════════════════════════════
#  SAVE CONFIG + PRINT SUMMARY
# ═══════════════════════════════════════════════════════════════════════════

def save_config():
    """Save all resource ARNs/names to src/aws/config.json for use by agents."""
    config = {
        "region"          : REGION,
        "account_id"      : ACCOUNT_ID,
        "dynamodb_table"  : TABLE_NAME,
        "sns_topic_arn"   : results.get("sns_topic_arn", ""),
        "lambda_function" : LAMBDA_NAME,
        "lambda_arn"      : results.get("lambda_arn", ""),
        "iot_thing"       : IOT_THING,
        "iot_endpoint"    : results.get("iot_endpoint", ""),
        "cw_namespace"    : CW_NAMESPACE,
    }
    config_path = os.path.join(os.path.dirname(__file__), "config.json")
    with open(config_path, "w") as f:
        json.dump(config, f, indent=2)
    print(f"\n  Config saved → src/aws/config.json")
    return config


def print_summary(config):
    sep = "=" * 55
    print(f"\n{sep}")
    print("  AWS INFRASTRUCTURE SETUP COMPLETE")
    print(sep)
    print(f"  Region           : {config['region']}")
    print(f"  Account ID       : {config['account_id']}")
    print(f"  DynamoDB Table   : {config['dynamodb_table']}")
    print(f"  SNS Topic ARN    : {config['sns_topic_arn']}")
    print(f"  Lambda Function  : {config['lambda_function']}")
    print(f"  IoT Thing        : {config['iot_thing']}")
    print(f"  CW Namespace     : {config['cw_namespace']}")
    print(f"\n  Next step → python src/backend/main.py")
    print(sep)


# ═══════════════════════════════════════════════════════════════════════════
#  MAIN
# ═══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("=" * 55)
    print("  Smart Grid Resilience — AWS Infrastructure Setup")
    print("  Course: BCSE355L | Author: Mohit Gupta")
    print("=" * 55)

    create_dynamodb_table()
    create_sns_topic()
    create_cloudwatch_alarms()
    create_lambda_function()
    create_iot_thing()

    config = save_config()
    print_summary(config)
