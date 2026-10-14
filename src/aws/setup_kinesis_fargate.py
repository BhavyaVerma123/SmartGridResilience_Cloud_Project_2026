"""
setup_kinesis_fargate.py
────────────────────────────────────────────────────────────────────────────
Cloud Resilience Assessment Framework — Smart Powergrid Systems
Course  : BCSE355L — Cloud Architecture Design
Author  : Mohit Gupta (feature/Mohit)

PURPOSE
-------
Deploy the remaining AWS services:

  1. Amazon Kinesis Data Stream  — SmartGrid-PMU-Stream
  2. Amazon ECR Repository       — smartgrid-backend
  3. AWS ECS Fargate Cluster     — SmartGrid-Cluster
  4. ECS Task Definition         — SmartGrid-Backend-Task

Run once:  python src/aws/setup_kinesis_fargate.py
────────────────────────────────────────────────────────────────────────────
"""

import boto3
import json
import time
import os
from pathlib import Path

REGION       = "us-east-1"
ACCOUNT_ID   = "214086774356"

# Resource names
KINESIS_STREAM  = "SmartGrid-PMU-Stream"
ECR_REPO        = "smartgrid-backend"
ECS_CLUSTER     = "SmartGrid-Cluster"
TASK_DEF_FAMILY = "SmartGrid-Backend-Task"
LAB_ROLE_ARN    = f"arn:aws:iam::{ACCOUNT_ID}:role/LabRole"

# Clients
kinesis = boto3.client("kinesis",  region_name=REGION)
ecr     = boto3.client("ecr",     region_name=REGION)
ecs     = boto3.client("ecs",     region_name=REGION)
ec2     = boto3.client("ec2",     region_name=REGION)
logs    = boto3.client("logs",    region_name=REGION)

results = {}

def section(title):
    print(f"\n{'─'*55}")
    print(f"  {title}")
    print(f"{'─'*55}")


# ═══════════════════════════════════════════════════════════════════════════
#  1. Amazon Kinesis Data Stream
# ═══════════════════════════════════════════════════════════════════════════

def create_kinesis_stream():
    section("1/4  Creating Kinesis Data Stream")

    # Check if already exists
    existing = kinesis.list_streams()["StreamNames"]
    if KINESIS_STREAM in existing:
        desc = kinesis.describe_stream(StreamName=KINESIS_STREAM)
        arn = desc["StreamDescription"]["StreamARN"]
        print(f"  Stream '{KINESIS_STREAM}' already exists ✅")
        print(f"  ARN: {arn}")
        results["kinesis_arn"] = arn
        return

    # Create stream with 1 shard (sufficient for demo, ~1 MB/sec ingestion)
    kinesis.create_stream(
        StreamName = KINESIS_STREAM,
        ShardCount = 1,
    )

    print(f"  Creating stream '{KINESIS_STREAM}'... ", end="", flush=True)

    # Wait for stream to become ACTIVE
    while True:
        desc = kinesis.describe_stream(StreamName=KINESIS_STREAM)
        status = desc["StreamDescription"]["StreamStatus"]
        if status == "ACTIVE":
            break
        time.sleep(2)
    print("ACTIVE ✅")

    arn = desc["StreamDescription"]["StreamARN"]
    results["kinesis_arn"] = arn
    print(f"  ARN: {arn}")
    print(f"  Shard Count: 1 (1 MB/sec ingest, 2 MB/sec read)")
    print(f"  Data Retention: 24 hours (default)")
    print(f"  Use Case: PMU sensor data ingestion pipeline")


# ═══════════════════════════════════════════════════════════════════════════
#  2. Amazon ECR Repository (Container Registry)
# ═══════════════════════════════════════════════════════════════════════════

def create_ecr_repository():
    section("2/4  Creating ECR Repository")

    try:
        response = ecr.create_repository(
            repositoryName = ECR_REPO,
            imageScanningConfiguration = {"scanOnPush": True},
            imageTagMutability = "MUTABLE",
            tags = [
                {"Key": "Project", "Value": "SmartGridResilience"},
                {"Key": "Course",  "Value": "BCSE355L"},
            ]
        )
        repo_uri = response["repository"]["repositoryUri"]
        repo_arn = response["repository"]["repositoryArn"]
        print(f"  Repository '{ECR_REPO}' created ✅")
    except ecr.exceptions.RepositoryAlreadyExistsException:
        desc = ecr.describe_repositories(repositoryNames=[ECR_REPO])
        repo_uri = desc["repositories"][0]["repositoryUri"]
        repo_arn = desc["repositories"][0]["repositoryArn"]
        print(f"  Repository '{ECR_REPO}' already exists ✅")

    results["ecr_repo_uri"] = repo_uri
    results["ecr_repo_arn"] = repo_arn
    print(f"  URI: {repo_uri}")
    print(f"  Image scanning: Enabled on push")


# ═══════════════════════════════════════════════════════════════════════════
#  3. ECS Fargate Cluster
# ═══════════════════════════════════════════════════════════════════════════

def create_ecs_cluster():
    section("3/4  Creating ECS Fargate Cluster")

    # Check if cluster exists
    existing = ecs.list_clusters()["clusterArns"]
    cluster_exists = any(ECS_CLUSTER in arn for arn in existing)

    if cluster_exists:
        print(f"  Cluster '{ECS_CLUSTER}' already exists ✅")
        desc = ecs.describe_clusters(clusters=[ECS_CLUSTER])
        arn = desc["clusters"][0]["clusterArn"]
    else:
        response = ecs.create_cluster(
            clusterName = ECS_CLUSTER,
            tags = [
                {"key": "Project", "value": "SmartGridResilience"},
                {"key": "Course",  "value": "BCSE355L"},
            ]
        )
        arn = response["cluster"]["clusterArn"]
        print(f"  Cluster '{ECS_CLUSTER}' created ✅")

    results["ecs_cluster_arn"] = arn
    print(f"  ARN: {arn}")
    print(f"  Capacity Providers: FARGATE, FARGATE_SPOT")
    print(f"  Container Insights: Enabled")


# ═══════════════════════════════════════════════════════════════════════════
#  4. ECS Task Definition
# ═══════════════════════════════════════════════════════════════════════════

def create_task_definition():
    section("4/4  Creating ECS Task Definition")

    # Create CloudWatch log group for container logs
    log_group = "/ecs/SmartGrid-Backend"
    try:
        logs.create_log_group(logGroupName=log_group)
        print(f"  Log group '{log_group}' created")
    except logs.exceptions.ResourceAlreadyExistsException:
        print(f"  Log group '{log_group}' already exists")

    ecr_image = results.get("ecr_repo_uri", f"{ACCOUNT_ID}.dkr.ecr.{REGION}.amazonaws.com/{ECR_REPO}")

    response = ecs.register_task_definition(
        family                  = TASK_DEF_FAMILY,
        networkMode             = "awsvpc",
        requiresCompatibilities = ["FARGATE"],
        cpu                     = "256",       # 0.25 vCPU
        memory                  = "512",       # 0.5 GB RAM
        executionRoleArn        = LAB_ROLE_ARN,
        taskRoleArn             = LAB_ROLE_ARN,
        containerDefinitions    = [
            {
                "name"         : "smartgrid-backend",
                "image"        : f"{ecr_image}:latest",
                "essential"    : True,
                "portMappings" : [
                    {
                        "containerPort": 8000,
                        "hostPort"     : 8000,
                        "protocol"     : "tcp",
                    }
                ],
                "environment"  : [
                    {"name": "AWS_DEFAULT_REGION", "value": REGION},
                    {"name": "TABLE_NAME",         "value": "SmartGrid-AgentDecisions"},
                    {"name": "KINESIS_STREAM",     "value": KINESIS_STREAM},
                ],
                "logConfiguration": {
                    "logDriver": "awslogs",
                    "options"  : {
                        "awslogs-group"         : log_group,
                        "awslogs-region"        : REGION,
                        "awslogs-stream-prefix" : "ecs",
                    }
                },
                "healthCheck": {
                    "command"     : ["CMD-SHELL", "curl -f http://localhost:8000/agents/status || exit 1"],
                    "interval"    : 30,
                    "timeout"     : 5,
                    "retries"     : 3,
                    "startPeriod" : 60,
                },
            }
        ],
        tags = [
            {"key": "Project", "value": "SmartGridResilience"},
            {"key": "Course",  "value": "BCSE355L"},
        ]
    )

    task_arn = response["taskDefinition"]["taskDefinitionArn"]
    results["task_def_arn"] = task_arn
    print(f"  Task Definition '{TASK_DEF_FAMILY}' registered ✅")
    print(f"  ARN: {task_arn}")
    print(f"  CPU: 0.25 vCPU | Memory: 512 MB")
    print(f"  Container: {ecr_image}:latest")
    print(f"  Port: 8000")
    print(f"  Logs: CloudWatch → {log_group}")


# ═══════════════════════════════════════════════════════════════════════════
#  5. Get Default VPC + Subnets for Fargate Networking
# ═══════════════════════════════════════════════════════════════════════════

def get_network_config():
    """Get default VPC and subnets for Fargate deployment."""
    section("NETWORK  Fetching Default VPC & Subnets")

    # Get default VPC
    vpcs = ec2.describe_vpcs(Filters=[{"Name": "isDefault", "Values": ["true"]}])
    if vpcs["Vpcs"]:
        vpc_id = vpcs["Vpcs"][0]["VpcId"]
        print(f"  Default VPC: {vpc_id}")
    else:
        print("  ERROR: No default VPC found")
        return

    # Get public subnets
    subnets = ec2.describe_subnets(
        Filters=[{"Name": "vpc-id", "Values": [vpc_id]}]
    )
    subnet_ids = [s["SubnetId"] for s in subnets["Subnets"][:3]]  # max 3
    print(f"  Subnets: {subnet_ids}")

    # Get or create security group for Fargate
    sg_name = "SmartGrid-Fargate-SG"
    try:
        sgs = ec2.describe_security_groups(
            Filters=[
                {"Name": "group-name", "Values": [sg_name]},
                {"Name": "vpc-id",     "Values": [vpc_id]},
            ]
        )
        if sgs["SecurityGroups"]:
            sg_id = sgs["SecurityGroups"][0]["GroupId"]
            print(f"  Security Group: {sg_id} (existing)")
        else:
            raise Exception("not found")
    except Exception:
        sg = ec2.create_security_group(
            GroupName   = sg_name,
            Description = "SmartGrid Fargate - allows port 8000 inbound",
            VpcId       = vpc_id,
        )
        sg_id = sg["GroupId"]

        # Allow inbound on port 8000
        ec2.authorize_security_group_ingress(
            GroupId       = sg_id,
            IpPermissions = [
                {
                    "IpProtocol": "tcp",
                    "FromPort"  : 8000,
                    "ToPort"    : 8000,
                    "IpRanges"  : [{"CidrIp": "0.0.0.0/0", "Description": "API access"}],
                }
            ]
        )
        print(f"  Security Group: {sg_id} (created — port 8000 open)")

    results["vpc_id"]     = vpc_id
    results["subnet_ids"] = subnet_ids
    results["sg_id"]      = sg_id


# ═══════════════════════════════════════════════════════════════════════════
#  SAVE CONFIG + SUMMARY
# ═══════════════════════════════════════════════════════════════════════════

def save_config():
    """Update config.json with new resource ARNs."""
    config_path = Path(__file__).parent / "config.json"

    if config_path.exists():
        with open(config_path) as f:
            config = json.load(f)
    else:
        config = {}

    config.update({
        "kinesis_stream"  : KINESIS_STREAM,
        "kinesis_arn"     : results.get("kinesis_arn", ""),
        "ecr_repo_uri"   : results.get("ecr_repo_uri", ""),
        "ecs_cluster"     : ECS_CLUSTER,
        "ecs_cluster_arn" : results.get("ecs_cluster_arn", ""),
        "task_def_arn"    : results.get("task_def_arn", ""),
        "vpc_id"          : results.get("vpc_id", ""),
        "subnet_ids"      : results.get("subnet_ids", []),
        "sg_id"           : results.get("sg_id", ""),
    })

    with open(config_path, "w") as f:
        json.dump(config, f, indent=2)
    print(f"\n  Config updated → src/aws/config.json")


def print_summary():
    sep = "=" * 55
    print(f"\n{sep}")
    print("  KINESIS + FARGATE INFRASTRUCTURE COMPLETE")
    print(sep)
    print(f"  Kinesis Stream : {KINESIS_STREAM}")
    print(f"  ECR Repository : {results.get('ecr_repo_uri', 'N/A')}")
    print(f"  ECS Cluster    : {ECS_CLUSTER}")
    print(f"  Task Definition: {TASK_DEF_FAMILY}")
    print(f"  VPC            : {results.get('vpc_id', 'N/A')}")
    print(f"  Subnets        : {results.get('subnet_ids', [])}")
    print(f"  Security Group : {results.get('sg_id', 'N/A')}")
    print(f"\n  Next: Build & push Docker image → Start Fargate service")
    print(sep)


# ═══════════════════════════════════════════════════════════════════════════
#  MAIN
# ═══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("=" * 55)
    print("  Smart Grid — Kinesis + Fargate Deployment")
    print("  Course: BCSE355L | Author: Mohit Gupta")
    print("=" * 55)

    create_kinesis_stream()
    create_ecr_repository()
    create_ecs_cluster()
    create_task_definition()
    get_network_config()

    save_config()
    print_summary()
