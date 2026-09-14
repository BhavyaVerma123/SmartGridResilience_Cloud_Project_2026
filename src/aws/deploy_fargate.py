"""
deploy_fargate.py
────────────────────────────────────────────────────────────────────────────
Cloud Resilience Assessment Framework — Smart Powergrid Systems
Course  : BCSE355L — Cloud Architecture Design
Author  : Mohit Gupta (feature/Mohit)

PURPOSE
-------
Builds Docker image, pushes to ECR, and launches on AWS Fargate.

Steps:
  1. Authenticate Docker to ECR
  2. Build Docker image
  3. Tag and push image to ECR
  4. Create/Update ECS Fargate Service
  5. Wait for service to be running
  6. Print public URL

Usage:  python src/aws/deploy_fargate.py
────────────────────────────────────────────────────────────────────────────
"""

import boto3
import json
import subprocess
import time
import sys
from pathlib import Path

REGION       = "us-east-1"
ACCOUNT_ID   = "214086774356"
ECR_REPO     = "smartgrid-backend"
ECR_URI      = f"{ACCOUNT_ID}.dkr.ecr.{REGION}.amazonaws.com/{ECR_REPO}"
ECS_CLUSTER  = "SmartGrid-Cluster"
SERVICE_NAME = "SmartGrid-Backend-Service"
TASK_FAMILY  = "SmartGrid-Backend-Task"
LAB_ROLE_ARN = f"arn:aws:iam::{ACCOUNT_ID}:role/LabRole"

PROJECT_ROOT = Path(__file__).parent.parent.parent

# Load config
CONFIG_PATH = Path(__file__).parent / "config.json"
def load_config():
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH) as f:
            return json.load(f)
    return {}

CFG = load_config()


def section(title):
    print(f"\n{'='*55}")
    print(f"  {title}")
    print(f"{'='*55}")


def run_cmd(cmd, check=True, shell=True):
    """Run a shell command and print output."""
    print(f"  $ {cmd}")
    result = subprocess.run(cmd, shell=shell, capture_output=True, text=True, cwd=str(PROJECT_ROOT))
    if result.stdout.strip():
        for line in result.stdout.strip().split('\n')[:10]:
            print(f"    {line}")
    if result.returncode != 0 and check:
        print(f"  ERROR: {result.stderr.strip()[:200]}")
        if check:
            sys.exit(1)
    return result


# ═══════════════════════════════════════════════════════════════════════════
#  STEP 1: Authenticate Docker to ECR
# ═══════════════════════════════════════════════════════════════════════════

def authenticate_ecr():
    section("STEP 1/5 — Authenticate Docker to ECR")

    ecr = boto3.client("ecr", region_name=REGION)
    token = ecr.get_authorization_token()
    endpoint = token["authorizationData"][0]["proxyEndpoint"]

    # Use AWS CLI to login Docker to ECR
    cmd = f'aws ecr get-login-password --region {REGION} | docker login --username AWS --password-stdin {ACCOUNT_ID}.dkr.ecr.{REGION}.amazonaws.com'
    result = run_cmd(cmd, check=False)

    if result.returncode == 0:
        print("  Docker authenticated to ECR ✅")
    else:
        print("  Trying alternative authentication...")
        # Alternative: pipe the token directly
        import base64
        auth_token = token["authorizationData"][0]["authorizationToken"]
        decoded = base64.b64decode(auth_token).decode("utf-8")
        password = decoded.split(":")[1]
        cmd2 = f'echo {password} | docker login --username AWS --password-stdin {ACCOUNT_ID}.dkr.ecr.{REGION}.amazonaws.com'
        result2 = run_cmd(cmd2, check=True)
        print("  Docker authenticated to ECR ✅")


# ═══════════════════════════════════════════════════════════════════════════
#  STEP 2: Build Docker Image
# ═══════════════════════════════════════════════════════════════════════════

def build_image():
    section("STEP 2/5 — Build Docker Image")

    cmd = f"docker build -t {ECR_REPO}:latest ."
    run_cmd(cmd)
    print(f"  Image '{ECR_REPO}:latest' built ✅")


# ═══════════════════════════════════════════════════════════════════════════
#  STEP 3: Tag and Push to ECR
# ═══════════════════════════════════════════════════════════════════════════

def push_to_ecr():
    section("STEP 3/5 — Push Image to ECR")

    # Tag the image for ECR
    run_cmd(f"docker tag {ECR_REPO}:latest {ECR_URI}:latest")
    print(f"  Tagged: {ECR_URI}:latest")

    # Push to ECR
    run_cmd(f"docker push {ECR_URI}:latest")
    print(f"  Pushed to ECR ✅")


# ═══════════════════════════════════════════════════════════════════════════
#  STEP 4: Create/Update ECS Fargate Service
# ═══════════════════════════════════════════════════════════════════════════

def deploy_service():
    section("STEP 4/5 — Deploy Fargate Service")

    ecs = boto3.client("ecs", region_name=REGION)

    subnet_ids = CFG.get("subnet_ids", [])
    sg_id      = CFG.get("sg_id", "")

    if not subnet_ids or not sg_id:
        print("  ERROR: Missing network config. Run setup_kinesis_fargate.py first.")
        sys.exit(1)

    # Check if service already exists
    try:
        existing = ecs.describe_services(
            cluster  = ECS_CLUSTER,
            services = [SERVICE_NAME]
        )
        active_services = [s for s in existing["services"] if s["status"] == "ACTIVE"]

        if active_services:
            print(f"  Service '{SERVICE_NAME}' exists — updating...")
            ecs.update_service(
                cluster        = ECS_CLUSTER,
                service        = SERVICE_NAME,
                taskDefinition = TASK_FAMILY,
                desiredCount   = 1,
                forceNewDeployment = True,
            )
            print(f"  Service updated with latest task definition ✅")
            return
    except Exception:
        pass

    # Create new service
    print(f"  Creating service '{SERVICE_NAME}'...")
    ecs.create_service(
        cluster        = ECS_CLUSTER,
        serviceName    = SERVICE_NAME,
        taskDefinition = TASK_FAMILY,
        desiredCount   = 1,
        launchType     = "FARGATE",
        networkConfiguration = {
            "awsvpcConfiguration": {
                "subnets"        : subnet_ids,
                "securityGroups" : [sg_id],
                "assignPublicIp" : "ENABLED",
            }
        },
        tags = [
            {"key": "Project", "value": "SmartGridResilience"},
            {"key": "Course",  "value": "BCSE355L"},
        ]
    )
    print(f"  Service '{SERVICE_NAME}' created ✅")


# ═══════════════════════════════════════════════════════════════════════════
#  STEP 5: Wait for Service and Get Public IP
# ═══════════════════════════════════════════════════════════════════════════

def wait_for_service():
    section("STEP 5/5 — Waiting for Fargate Task to Start")

    ecs = boto3.client("ecs", region_name=REGION)
    ec2 = boto3.client("ec2", region_name=REGION)

    print("  Waiting for task to reach RUNNING state...")
    max_wait = 300  # 5 minutes
    start    = time.time()

    while (time.time() - start) < max_wait:
        tasks = ecs.list_tasks(cluster=ECS_CLUSTER, serviceName=SERVICE_NAME)
        task_arns = tasks.get("taskArns", [])

        if task_arns:
            task_details = ecs.describe_tasks(cluster=ECS_CLUSTER, tasks=task_arns)
            for task in task_details["tasks"]:
                status = task.get("lastStatus", "UNKNOWN")
                print(f"  Task status: {status}")

                if status == "RUNNING":
                    # Get the public IP from the ENI
                    attachments = task.get("attachments", [])
                    for att in attachments:
                        for detail in att.get("details", []):
                            if detail["name"] == "networkInterfaceId":
                                eni_id = detail["value"]
                                eni = ec2.describe_network_interfaces(
                                    NetworkInterfaceIds=[eni_id]
                                )
                                public_ip = eni["NetworkInterfaces"][0].get(
                                    "Association", {}
                                ).get("PublicIp", "N/A")

                                print(f"\n  {'='*50}")
                                print(f"  FARGATE SERVICE IS LIVE!")
                                print(f"  {'='*50}")
                                print(f"  Public IP  : {public_ip}")
                                print(f"  API URL    : http://{public_ip}:8000")
                                print(f"  Swagger UI : http://{public_ip}:8000/docs")
                                print(f"  Demo       : http://{public_ip}:8000/demo/run")
                                print(f"  {'='*50}")

                                # Save to config
                                CFG["fargate_public_ip"] = public_ip
                                CFG["fargate_url"] = f"http://{public_ip}:8000"
                                with open(CONFIG_PATH, "w") as f:
                                    json.dump(CFG, f, indent=2)

                                return public_ip

        time.sleep(15)

    print("  Timed out waiting for task. Check ECS console.")
    return None


# ═══════════════════════════════════════════════════════════════════════════
#  MAIN
# ═══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("=" * 55)
    print("  Smart Grid — Fargate Deployment Pipeline")
    print("  Course: BCSE355L | Author: Mohit Gupta")
    print("=" * 55)

    authenticate_ecr()
    build_image()
    push_to_ecr()
    deploy_service()
    wait_for_service()
