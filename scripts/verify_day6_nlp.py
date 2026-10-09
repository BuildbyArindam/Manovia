"""Live Day 6 verification. Start APIs separately; never prints raw server logs.

Run: python scripts/verify_day6_nlp.py --url http://127.0.0.1:8000
Cache should be disabled for these timing measurements (EMOTION_CACHE_SIZE=0).
This measures whichever provider the server uses, not automatically a real model.
"""

import argparse
import math
from statistics import median
from time import perf_counter

import httpx


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--production-url", default="http://127.0.0.1:8002")
    parser.add_argument(
        "--expect-fallback",
        action="store_true",
        help="Assert exact keyword results; omit for real-model accuracy review",
    )
    args = parser.parse_args()
    samples = [
        ("job", "I got the job and I can't stop smiling", "joy"),
        ("alone", "I feel so alone lately", "loneliness"),
        ("Hinglish", "exam kal hai, bahut dar lag raha hai", "fear"),
        ("fine", "I am fine", "neutral"),
        ("empty", "", "neutral"),
        ("10k", "x" * 10000, "neutral"),
    ]
    with httpx.Client(base_url=args.url, timeout=30) as client:
        for name, text, expected in samples:
            response = client.post("/api/v1/dev/analyze", json={"text": text})
            response.raise_for_status()
            result = response.json()
            print(
                f"{name}: HTTP {response.status_code}, "
                f"primary={result['primary']}, valence={result['valence']:.3f}"
            )
            if args.expect_fallback:
                assert result["primary"] == expected
            elif result["primary"] != expected:
                print(f"  Accuracy concern: expected keyword emotion {expected}")
        times = []
        for i in range(20):
            start = perf_counter()
            response = client.post(
                "/api/v1/dev/analyze",
                json={"text": f"I am happy today sample {i}", "lang": "en"},
            )
            response.raise_for_status()
            if args.expect_fallback:
                assert response.json()["primary"] == "joy"
            times.append((perf_counter() - start) * 1000)
        print(
            f"20 sequential uncached HTTP calls: p50={median(times):.3f}ms, "
            f"p95={sorted(times)[math.ceil(0.95 * len(times)) - 1]:.3f}ms"
        )
        response = client.post(
            "/api/v1/dev/analyze", json={"text": "PRIVATE-SENTINEL-LIVE-61903 happy"}
        )
        response.raise_for_status()
        response = client.post("/api/v1/dev/analyze", json={"text": "x" * 10001})
        assert response.status_code == 422
    response = httpx.post(
        f"{args.production_url}/api/v1/dev/analyze", json={"text": "happy"}
    )
    assert response.status_code == 404
    print("Production: HTTP 404; oversized dev input: HTTP 422")


if __name__ == "__main__":
    main()
