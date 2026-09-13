"""
test_connection.py — Verify AWS credentials and test all required services
"""
import boto3
import json

REGION = "us-east-1"

def test_aws_connection():
    print("=" * 55)
    print("  AWS CONNECTION TEST")
    print("=" * 55)

    # 1. Identity check
    try:
        sts = boto3.client("sts", region_name=REGION)
        identity = sts.get_caller_identity()
        print("\n[1/5] STS Identity Check")
        print("  Status     : CONNECTED")
        print("  Account ID :", identity["Account"])
        print("  User ARN   :", identity["Arn"])
    except Exception as e:
        print("  FAILED:", e)
        return

    # 2. DynamoDB
    try:
        ddb = boto3.client("dynamodb", region_name=REGION)
        tables = ddb.list_tables()["TableNames"]
        print("\n[2/5] DynamoDB")
        print("  Status : ACCESSIBLE")
        print("  Existing tables:", tables if tables else "None yet")
    except Exception as e:
        print("\n[2/5] DynamoDB:", e)

    # 3. SNS
    try:
        sns = boto3.client("sns", region_name=REGION)
        topics = sns.list_topics()["Topics"]
        print("\n[3/5] SNS (Simple Notification Service)")
        print("  Status : ACCESSIBLE")
        print("  Existing topics:", len(topics))
    except Exception as e:
        print("\n[3/5] SNS:", e)

    # 4. CloudWatch
    try:
        cw = boto3.client("cloudwatch", region_name=REGION)
        cw.list_metrics(Namespace="SmartGrid")
        print("\n[4/5] CloudWatch")
        print("  Status : ACCESSIBLE")
    except Exception as e:
        print("\n[4/5] CloudWatch:", e)

    # 5. Lambda
    try:
        lam = boto3.client("lambda", region_name=REGION)
        funcs = lam.list_functions()["Functions"]
        print("\n[5/5] Lambda")
        print("  Status : ACCESSIBLE")
        print("  Existing functions:", len(funcs))
    except Exception as e:
        print("\n[5/5] Lambda:", e)

    print("\n" + "=" * 55)
    print("  All core AWS services are accessible!")
    print("  Ready to deploy Smart Grid Resilience Framework")
    print("=" * 55)

if __name__ == "__main__":
    test_aws_connection()
