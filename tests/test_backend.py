import asyncio
import json
import os
import tempfile
import threading
import time
import unittest
from collections import deque
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pandas as pd
from fastapi import BackgroundTasks

from src.backend import main


class FakeDemoIdempotencyStore:
    def __init__(self):
        self.records = {}
        self.lock = threading.Lock()

    def claim_demo_request(self, request_id, owner_token):
        with self.lock:
            record = self.records.get(request_id)
            if record and record["expires_at"] > time.time():
                return False
            self.records[request_id] = {
                "status": "IN_PROGRESS",
                "owner_token": owner_token,
                "expires_at": time.time() + 7 * 24 * 60 * 60,
            }
            return True

    def get_demo_request(self, request_id):
        with self.lock:
            record = self.records.get(request_id)
            if not record:
                return None
            return {
                "status": record["status"],
                "expires_at": record["expires_at"],
                "result": record.get("result"),
            }

    def finish_demo_request(self, request_id, owner_token, status, result):
        with self.lock:
            record = self.records.get(request_id)
            if not record or record["owner_token"] != owner_token:
                return False
            record.update(
                status=status,
                result=result,
                expires_at=time.time() + 24 * 60 * 60,
            )
            record.pop("owner_token")
            return True

    def log_anomaly_decision(self, _result):
        return True

    def log_stability_decision(self, _result):
        return True


class BackendHelperTests(unittest.TestCase):
    def test_cors_origins_are_explicit_and_validated(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(
                main._get_trusted_origins(),
                ["http://localhost:8000", "http://127.0.0.1:8000"],
            )

        with patch.dict(
            os.environ,
            {"SMARTGRID_CORS_ORIGINS": "https://dashboard.example.com, https://admin.example.com"},
        ):
            self.assertEqual(
                main._get_trusted_origins(),
                ["https://dashboard.example.com", "https://admin.example.com"],
            )

        for invalid_origins in (
            "*",
            "",
            "https://example.com/dashboard",
            "https://*.example.com",
        ):
            with self.subTest(origins=invalid_origins), patch.dict(
                os.environ,
                {"SMARTGRID_CORS_ORIGINS": invalid_origins},
            ):
                with self.assertRaises(RuntimeError):
                    main._get_trusted_origins()

    def test_agent_status_does_not_claim_aws_connectivity(self):
        agent = Mock()
        agent.status.return_value = {"state": "healthy"}
        with (
            patch.object(main, "anomaly_agent", agent),
            patch.object(main, "stability_agent", agent),
        ):
            status = asyncio.run(main.get_agent_status())

        for service_status in status["aws_services"].values():
            self.assertEqual(service_status["configuration"], "configured")
            self.assertEqual(service_status["connectivity"], "not_checked")

    def test_demo_run_is_post_only(self):
        route = next(
            route for route in main.app.routes
            if getattr(route, "path", None) == "/demo/run"
        )
        self.assertEqual(route.methods, {"POST"})

    def test_dynamodb_demo_claims_are_conditional_and_read_consistently(self):
        class ConditionalCheckFailed(Exception):
            pass

        ddb = Mock()
        ddb.exceptions = SimpleNamespace(
            ConditionalCheckFailedException=ConditionalCheckFailed
        )
        logger = main.AWSLogger.__new__(main.AWSLogger)
        logger._ddb = ddb
        logger._table = "test-table"

        self.assertTrue(logger.claim_demo_request("request-1", "owner-1"))
        claim = ddb.put_item.call_args.kwargs
        self.assertEqual(claim["TableName"], "test-table")
        self.assertIn("attribute_not_exists(agent_id)", claim["ConditionExpression"])
        self.assertEqual(claim["Item"]["agent_id"]["S"], "demo-request#request-1")

        ddb.put_item.side_effect = ConditionalCheckFailed
        self.assertFalse(logger.claim_demo_request("request-1", "owner-2"))

        response = {"pipeline": "COMPLETE"}
        ddb.get_item.return_value = {
            "Item": {
                "status": {"S": "COMPLETE"},
                "ttl": {"N": "2000000000"},
                "result": {"S": json.dumps(response)},
            }
        }
        record = logger.get_demo_request("request-1")
        self.assertTrue(ddb.get_item.call_args.kwargs["ConsistentRead"])
        self.assertEqual(record["result"], response)

    def test_processed_dataset_is_cached_without_label_column(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            dataset_path = Path(temp_dir) / "msu_ornl_processed.csv"
            pd.DataFrame({"feature": [1.0, 2.0], "label": [0, 1]}).to_csv(
                dataset_path,
                index=False,
            )
            main._load_processed_dataset.cache_clear()
            try:
                with patch.object(main, "PROC_DIR", Path(temp_dir)):
                    first = main._load_processed_dataset("msu_ornl_processed.csv")
                    second = main._load_processed_dataset("msu_ornl_processed.csv")
            finally:
                main._load_processed_dataset.cache_clear()

        self.assertIs(first, second)
        self.assertEqual(list(first.columns), ["feature"])
        self.assertEqual(len(first), 2)

    def test_event_buffer_is_bounded_and_events_get_monotonic_ids(self):
        with (
            patch.object(main, "live_events", deque(maxlen=50)),
            patch.object(main, "event_counter", 0),
        ):
            for index in range(55):
                main._push_event({"value": index})

            self.assertEqual(len(main.live_events), 50)
            self.assertEqual(main.live_events[0]["event_id"], 6)
            self.assertEqual(main.live_events[-1]["event_id"], 55)


class DemoPipelineTests(unittest.IsolatedAsyncioTestCase):
    async def test_independent_model_inferences_run_concurrently(self):
        barrier = threading.Barrier(2)

        class FakeResult:
            def __init__(self, data):
                self.data = data

            def to_dict(self):
                return self.data

        class FakeAgent:
            def __init__(self, result):
                self.result = result
                self.calls = 0

            def predict(self, _sample):
                self.calls += 1
                barrier.wait(timeout=3)
                return FakeResult(self.result)

        anomaly_data = {
            "agent_id": "anomaly-test",
            "label": "NORMAL",
            "is_anomaly": False,
            "anomaly_score": 0.2,
            "confidence": 80.0,
            "latency_ms": 1.0,
            "timestamp": "2026-10-16T12:00:00",
        }
        stability_data = {
            "agent_id": "stability-test",
            "label": "STABLE",
            "is_stable": True,
            "stable_prob": 0.9,
            "unstable_prob": 0.1,
            "latency_ms": 1.0,
            "timestamp": "2026-10-16T12:00:00",
        }
        frame = pd.DataFrame({"feature": [1.0, 2.0]})
        anomaly_agent = FakeAgent(anomaly_data)
        stability_agent = FakeAgent(stability_data)
        request_token = main.request_id_context.set("demo-test-request")
        shared_store = FakeDemoIdempotencyStore()

        try:
            with (
                patch.object(main, "aws_logger", shared_store),
                patch.object(main, "_load_processed_dataset", return_value=frame),
                patch.object(main, "anomaly_agent", anomaly_agent),
                patch.object(main, "stability_agent", stability_agent),
                patch.object(main, "live_events", deque(maxlen=50)),
                patch.object(main, "event_counter", 0),
            ):
                response, duplicate_response = await asyncio.gather(
                    main.run_demo_pipeline(BackgroundTasks(), scenario="normal"),
                    main.run_demo_pipeline(BackgroundTasks(), scenario="normal"),
                )
                repeated_response = await main.run_demo_pipeline(
                    BackgroundTasks(),
                    scenario="normal",
                )
                self.assertEqual(len(main.live_events), 1)
        finally:
            main.request_id_context.reset(request_token)

        self.assertEqual(response["step1_anomaly"], anomaly_data)
        self.assertEqual(response["step3_stability"], stability_data)
        self.assertEqual(response, duplicate_response)
        self.assertEqual(response, repeated_response)
        self.assertEqual(anomaly_agent.calls, 1)
        self.assertEqual(stability_agent.calls, 1)
        self.assertEqual(response["request_id"], "demo-test-request")
        self.assertGreaterEqual(response["pipeline_latency_ms"], 0)
        self.assertEqual(response["aws_log_status"], "queued")
        self.assertIsNone(response["aws_logged"])

    async def test_cancelled_request_keeps_shared_demo_task(self):
        started = asyncio.Event()
        release = asyncio.Event()
        calls = 0

        async def execute_demo(_background_tasks, _scenario):
            nonlocal calls
            calls += 1
            started.set()
            await release.wait()
            return {"request_id": "cancelled-demo-request"}

        request_token = main.request_id_context.set("cancelled-demo-request")
        shared_store = FakeDemoIdempotencyStore()
        try:
            with (
                patch.object(main, "aws_logger", shared_store),
                patch.object(main, "_execute_demo_pipeline", execute_demo),
            ):
                first_request = asyncio.create_task(
                    main.run_demo_pipeline(BackgroundTasks(), scenario="normal")
                )
                await started.wait()
                first_request.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await first_request

                retry = asyncio.create_task(
                    main.run_demo_pipeline(BackgroundTasks(), scenario="normal")
                )
                release.set()
                result = await retry
        finally:
            main.request_id_context.reset(request_token)

        self.assertEqual(result, {"request_id": "cancelled-demo-request"})
        self.assertEqual(calls, 1)


if __name__ == "__main__":
    unittest.main()
