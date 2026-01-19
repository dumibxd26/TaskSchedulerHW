import argparse
import time
import httpx


def main():
    parser = argparse.ArgumentParser(description="Kick off a single priority scheduling run")
    parser.add_argument("--scheduler", required=True, help="Base URL, e.g. http://localhost:8000")
    parser.add_argument("--dataset", required=True, help="Dataset file relative to DATA_DIR")
    parser.add_argument("--speedup", type=float, default=20000.0)
    parser.add_argument("--min-slots", type=int, default=1)
    parser.add_argument("--poll-ms", type=int, default=500)
    args = parser.parse_args()

    timeout = httpx.Timeout(3600.0, connect=30.0)
    with httpx.Client(timeout=timeout) as client:
        start_resp = client.post(
            f"{args.scheduler}/start",
            json={
                "dataset_file": args.dataset,
                "speedup": args.speedup,
                "min_slots": args.min_slots,
            },
        )
        start_resp.raise_for_status()
        run_id = start_resp.json()["run_id"]
        print("Started run_id:", run_id)

        while True:
            status = client.get(f"{args.scheduler}/status")
            status.raise_for_status()
            payload = status.json()
            if payload.get("status") == "done" and payload.get("run_id") == run_id:
                print("Summary:", payload["summary"])
                print("jobs_csv:", payload.get("jobs_csv"))
                print("run_csv :", payload.get("run_csv"))
                break
            time.sleep(args.poll_ms / 1000.0)


if __name__ == "__main__":
    main()
