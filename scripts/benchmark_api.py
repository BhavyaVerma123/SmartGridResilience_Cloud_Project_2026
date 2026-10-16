"""Measure HTTP request latency using only the Python standard library."""

import argparse
import math
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def percentile(samples: list[float], percentile_value: int) -> float:
    ordered = sorted(samples)
    index = max(0, math.ceil(percentile_value / 100 * len(ordered)) - 1)
    return ordered[index]


def request_latency(url: str, timeout: float) -> float:
    request = Request(url, headers={"Accept": "application/json"})
    started_at = time.perf_counter()
    with urlopen(request, timeout=timeout) as response:
        response.read()
        if response.status >= 400:
            raise RuntimeError(f"HTTP {response.status}")
    return (time.perf_counter() - started_at) * 1000


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8000/agents/status")
    parser.add_argument("--requests", type=int, default=100)
    parser.add_argument("--warmup", type=int, default=5)
    parser.add_argument("--timeout", type=float, default=30)
    args = parser.parse_args()

    if args.requests < 1 or args.warmup < 0 or args.timeout <= 0:
        parser.error("--requests and --timeout must be positive; --warmup cannot be negative")

    try:
        for _ in range(args.warmup):
            request_latency(args.url, args.timeout)
    except (HTTPError, URLError, TimeoutError, RuntimeError) as error:
        print(f"Warm-up request failed: {error}")
        return 1

    samples = []
    failures = 0
    for _ in range(args.requests):
        try:
            samples.append(request_latency(args.url, args.timeout))
        except (HTTPError, URLError, TimeoutError, RuntimeError) as error:
            failures += 1
            print(f"Request failed: {error}")

    print(f"URL: {args.url}")
    print(f"Successful requests: {len(samples)} / {args.requests}")
    print(f"Failed requests: {failures}")
    if not samples:
        return 1

    print(f"p50: {percentile(samples, 50):.3f} ms")
    print(f"p95: {percentile(samples, 95):.3f} ms")
    print(f"p99: {percentile(samples, 99):.3f} ms")
    print(f"max: {max(samples):.3f} ms")
    return int(failures > 0)


if __name__ == "__main__":
    raise SystemExit(main())
