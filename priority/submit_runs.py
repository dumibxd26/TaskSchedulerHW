"""Utility to submit one or more priority runs to the scheduler."""

from __future__ import annotations

import argparse
import json
import time

import httpx


def parse_float_list(raw: str) -> list[float]:
    values = []
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        values.append(float(item))
    if not values:
        raise argparse.ArgumentTypeError("at least one value required")
    return values


def wait_for_completion(client: httpx.Client, base_url: str, run_id: str, poll_ms: int) -> dict:
    while True:
        resp = client.get(f"{base_url}/status")
        resp.raise_for_status()
        payload = resp.json()
        if payload.get("status") == "done" and payload.get("run_id") == run_id:
            return payload
        time.sleep(poll_ms / 1000.0)


def main():
    parser = argparse.ArgumentParser(description="Submit priority scheduling experiments")
    parser.add_argument("--scheduler", required=True, help="Scheduler base URL, e.g. http://localhost:8000")
    parser.add_argument("--dataset", required=True, help="Dataset filename relative to DATA_DIR")
    parser.add_argument("--speedups", default="20000", help="Comma-separated list of simulation speedups to try")
    parser.add_argument("--min-slots", type=int, default=1, help="Minimum number of alive worker cores required")
    parser.add_argument("--poll-ms", type=int, default=500, help="Status poll interval in milliseconds")
    args = parser.parse_args()

    speedups = parse_float_list(args.speedups)

    timeout = httpx.Timeout(3600.0, connect=30.0)
    with httpx.Client(timeout=timeout) as client:
        for speedup in speedups:
            payload = {
                "dataset_file": args.dataset,
                "speedup": speedup,
                "min_slots": args.min_slots,
            }
            resp = client.post(f"{args.scheduler}/start", json=payload)
            if resp.status_code >= 400:
                print("HTTP", resp.status_code)
                print(resp.text)
                resp.raise_for_status()

            run_id = resp.json()["run_id"]
            print(f"Started run_id={run_id} speedup={speedup}")

            summary = wait_for_completion(client, args.scheduler, run_id, args.poll_ms)
            print(json.dumps(summary["summary"], indent=2))
            print("jobs_csv:", summary.get("jobs_csv"))
            print("run_csv :", summary.get("run_csv"))


if __name__ == "__main__":
    main()
