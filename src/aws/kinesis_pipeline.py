"""
kinesis_pipeline.py
────────────────────────────────────────────────────────────────────────────
Cloud Resilience Assessment Framework — Smart Powergrid Systems
Course  : BCSE355L — Cloud Architecture Design
Author  : Mohit Gupta (feature/Mohit)

PURPOSE
-------
Kinesis Data Stream integration:

  KinesisProducer — Publishes PMU sensor readings to the stream
  KinesisConsumer — Reads from the stream and invokes ML agents

Architecture:
  PMU Sensor → [KinesisProducer] → Kinesis Stream → [KinesisConsumer]
                                                         ↓
                                               AnomalyDetectionAgent
                                                         ↓
                                               DynamoDB + CloudWatch + SNS
────────────────────────────────────────────────────────────────────────────
"""

import boto3
import json
import time
import uuid
import sys
import os
import numpy as np
import pandas as pd
import datetime
from pathlib import Path

# Load config
CONFIG_PATH = Path(__file__).parent / "config.json"

def load_config() -> dict:
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH) as f:
            return json.load(f)
    return {"region": "us-east-1", "kinesis_stream": "SmartGrid-PMU-Stream"}

CFG    = load_config()
REGION = CFG["region"]
STREAM = CFG.get("kinesis_stream", "SmartGrid-PMU-Stream")

ROOT   = Path(__file__).parent.parent.parent


# ═══════════════════════════════════════════════════════════════════════════
#  KINESIS PRODUCER — Publishes PMU data to the stream
# ═══════════════════════════════════════════════════════════════════════════

class KinesisProducer:
    """
    Simulates a PMU sensor publishing readings to Kinesis.

    In production: Real PMU devices publish directly via the Kinesis SDK.
    In our project: We feed processed dataset samples into the stream.
    """

    def __init__(self, stream_name: str = STREAM):
        self._kinesis = boto3.client("kinesis", region_name=REGION)
        self._stream  = stream_name
        self._count   = 0

    def publish_single(self, pmu_data: dict, sensor_id: str = "PMU-R1") -> dict:
        """Publish a single PMU reading to Kinesis."""
        record = {
            "sensor_id" : sensor_id,
            "timestamp" : datetime.datetime.now(datetime.UTC).isoformat(),
            "data"      : pmu_data,
            "sequence"  : self._count,
        }

        response = self._kinesis.put_record(
            StreamName   = self._stream,
            Data         = json.dumps(record).encode("utf-8"),
            PartitionKey = sensor_id,
        )

        self._count += 1
        return {
            "shard_id"       : response["ShardId"],
            "sequence_number": response["SequenceNumber"],
            "sensor_id"      : sensor_id,
            "record_count"   : self._count,
        }

    def publish_batch(self, records: list, sensor_id: str = "PMU-R1") -> dict:
        """Publish a batch of PMU readings (max 500 per call)."""
        kinesis_records = []
        for i, pmu_data in enumerate(records):
            record = {
                "sensor_id" : sensor_id,
                "timestamp" : datetime.datetime.now(datetime.UTC).isoformat(),
                "data"      : pmu_data,
                "sequence"  : self._count + i,
            }
            kinesis_records.append({
                "Data"        : json.dumps(record).encode("utf-8"),
                "PartitionKey": sensor_id,
            })

        # Kinesis PutRecords max 500 per call
        batch_size    = 500
        total_success = 0
        total_failed  = 0

        for start in range(0, len(kinesis_records), batch_size):
            batch = kinesis_records[start:start+batch_size]
            response = self._kinesis.put_records(
                StreamName = self._stream,
                Records    = batch,
            )
            total_failed  += response.get("FailedRecordCount", 0)
            total_success += len(batch) - response.get("FailedRecordCount", 0)

        self._count += len(records)
        return {
            "total_sent"     : len(records),
            "success_count"  : total_success,
            "failed_count"   : total_failed,
            "stream"         : self._stream,
        }

    def simulate_pmu_stream(self, n_records: int = 20, delay_sec: float = 0.5):
        """
        Simulate a live PMU data stream by publishing processed dataset
        samples at regular intervals.
        """
        # Load processed dataset
        csv_path = ROOT / "dataset" / "processed" / "msu_ornl_processed.csv"
        df = pd.read_csv(csv_path)

        # Remove label column for inference
        feature_cols = [c for c in df.columns if c != "label"]
        df_features  = df[feature_cols]

        # Select random samples
        indices = np.random.choice(len(df_features), size=min(n_records, len(df_features)), replace=False)

        print(f"\n  Simulating PMU stream → {self._stream}")
        print(f"  Publishing {n_records} records (delay: {delay_sec}s between each)")
        print(f"  {'─'*50}")

        for i, idx in enumerate(indices):
            row  = df_features.iloc[idx].to_dict()
            # Convert numpy types to native Python
            row  = {k: float(v) if isinstance(v, (np.floating, np.integer)) else v for k, v in row.items()}
            resp = self.publish_single(row, sensor_id=f"PMU-R{(i % 4) + 1}")
            label = df.iloc[idx].get("label", "unknown")
            print(f"  [{i+1:3d}/{n_records}] → Shard: {resp['shard_id'][-8:]}  "
                  f"Seq: {resp['sequence_number'][-8:]}  "
                  f"Sensor: PMU-R{(i%4)+1}  "
                  f"True Label: {label}")
            time.sleep(delay_sec)

        print(f"  {'─'*50}")
        print(f"  Stream simulation complete: {self._count} records published ✅")


# ═══════════════════════════════════════════════════════════════════════════
#  KINESIS CONSUMER — Reads from stream and runs ML inference
# ═══════════════════════════════════════════════════════════════════════════

class KinesisConsumer:
    """
    Reads PMU data from the Kinesis stream and routes it through
    the ML agent pipeline:

    Stream Record → AnomalyDetectionAgent → DynamoDB/CloudWatch/SNS
    """

    def __init__(self, stream_name: str = STREAM):
        self._kinesis = boto3.client("kinesis", region_name=REGION)
        self._stream  = stream_name

    def consume(self, max_records: int = 50, duration_sec: int = 30):
        """
        Consume records from Kinesis and run ML inference on each.

        Parameters
        ----------
        max_records  : Max records to process before stopping
        duration_sec : Max time to consume before stopping
        """
        # Add project root to path for imports
        sys.path.insert(0, str(ROOT))
        from src.ml_model.predict import AnomalyDetectionAgent
        from src.aws.aws_integration import AWSLogger, AlertManager, MitigationExecutor

        # Initialize agents and AWS services
        agent     = AnomalyDetectionAgent(agent_id="anomaly-kinesis-consumer")
        logger    = AWSLogger()
        alerter   = AlertManager()
        mitigator = MitigationExecutor()

        # Get shard iterator
        desc   = self._kinesis.describe_stream(StreamName=self._stream)
        shards = desc["StreamDescription"]["Shards"]

        if not shards:
            print("  No shards found in stream")
            return

        shard_id = shards[0]["ShardId"]
        iter_resp = self._kinesis.get_shard_iterator(
            StreamName         = self._stream,
            ShardId            = shard_id,
            ShardIteratorType  = "TRIM_HORIZON",
        )
        shard_iter = iter_resp["ShardIterator"]

        print(f"\n  Consuming from '{self._stream}' (shard: {shard_id[-8:]})")
        print(f"  Max records: {max_records} | Timeout: {duration_sec}s")
        print(f"  {'─'*50}")

        processed   = 0
        anomalies   = 0
        start_time  = time.time()

        while processed < max_records and (time.time() - start_time) < duration_sec:
            resp = self._kinesis.get_records(
                ShardIterator = shard_iter,
                Limit         = 25,
            )
            shard_iter = resp["NextShardIterator"]
            records    = resp["Records"]

            if not records:
                time.sleep(1)
                continue

            for rec in records:
                if processed >= max_records:
                    break

                data = json.loads(rec["Data"])
                pmu  = data.get("data", {})

                # Run ML inference
                result      = agent.predict(pd.DataFrame([pmu]))
                result_dict = result.to_dict()

                # Log to DynamoDB + CloudWatch
                logger.log_anomaly_decision(result_dict)

                is_anom = result.is_anomaly
                if is_anom:
                    anomalies += 1
                    alerter.send_alert(result_dict)
                    mitigator.execute(result_dict)

                status = "⚠ ANOMALY" if is_anom else "  normal "
                print(f"  [{processed+1:3d}] {status} | "
                      f"score={result.anomaly_score:.3f} | "
                      f"sensor={data.get('sensor_id','?')} | "
                      f"latency={result.latency_ms:.0f}ms")

                processed += 1

        elapsed = time.time() - start_time
        print(f"  {'─'*50}")
        print(f"  Consumer finished:")
        print(f"    Processed : {processed} records in {elapsed:.1f}s")
        print(f"    Anomalies : {anomalies}")
        print(f"    Normal    : {processed - anomalies}")
        print(f"    All logged to DynamoDB + CloudWatch ✅")


# ═══════════════════════════════════════════════════════════════════════════
#  DEMO — Full Kinesis Pipeline Test
# ═══════════════════════════════════════════════════════════════════════════

def run_kinesis_demo():
    """End-to-end: Produce → Kinesis Stream → Consume → ML Agent → AWS"""

    print("=" * 55)
    print("  Kinesis Pipeline Demo")
    print("  PMU → Kinesis → ML Agent → DynamoDB/CloudWatch")
    print("=" * 55)

    # Step 1: Produce records to Kinesis
    producer = KinesisProducer()
    producer.simulate_pmu_stream(n_records=10, delay_sec=0.3)

    # Give Kinesis a moment to propagate
    print("\n  Waiting 3s for Kinesis propagation...")
    time.sleep(3)

    # Step 2: Consume and run ML inference
    consumer = KinesisConsumer()
    consumer.consume(max_records=10, duration_sec=30)

    print("\n" + "=" * 55)
    print("  Kinesis Pipeline Demo Complete!")
    print("  Check AWS Console → DynamoDB / CloudWatch for results")
    print("=" * 55)


if __name__ == "__main__":
    run_kinesis_demo()
