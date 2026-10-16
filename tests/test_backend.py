import asyncio
import tempfile
import threading
import unittest
from collections import deque
from pathlib import Path
from unittest.mock import patch

import pandas as pd
from fastapi import BackgroundTasks

from src.backend import main


class BackendHelperTests(unittest.TestCase):
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

        try:
            with (
                patch.object(main, "_load_processed_dataset", return_value=frame),
                patch.object(main, "anomaly_agent", anomaly_agent),
                patch.object(main, "stability_agent", stability_agent),
                patch.object(main, "aws_logger"),
                patch.object(main, "live_events", deque(maxlen=50)),
                patch.object(main, "event_counter", 0),
                patch.object(main, "demo_request_tasks", {}),
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
        self.assertIs(response, duplicate_response)
        self.assertIs(response, repeated_response)
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
        try:
            with (
                patch.object(main, "demo_request_tasks", {}),
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
