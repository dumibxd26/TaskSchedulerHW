import argparse
import os
import time
import httpx

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scheduler", required=True, help="http://localhost:8000")
    ap.add_argument("--dataset", required=True)
    ap.add_argument("--quantum", type=int, default=10)
    ap.add_argument("--speedup", type=float, default=20000.0)
    ap.add_argument("--min-slots", type=int, default=1)
    ap.add_argument("--poll-ms", type=int, default=500)
    args = ap.parse_args()

    timeout = httpx.Timeout(3600.0, connect=30.0)
    with httpx.Client(timeout=timeout) as c:
        r = c.post(f"{args.scheduler}/start", json={
            "dataset_file": args.dataset,
            "quantum_ms": args.quantum,
            "speedup": args.speedup,
            "min_slots": args.min_slots,
        })
        if r.status_code >= 400:
            print("START ERROR", r.status_code, r.text)
            r.raise_for_status()

        run_id = r.json()["run_id"]
        print("Started run_id:", run_id)

        # poll status until done
        while True:
            s = c.get(f"{args.scheduler}/status")
            if s.status_code >= 400:
                print("STATUS ERROR", s.status_code, s.text)
                s.raise_for_status()

            st = s.json()
            if st.get("status") == "done" and st.get("run_id") == run_id:
                print("DONE summary:", st["summary"])
                print("jobs_csv:", st["jobs_csv"])
                print("run_csv :", st["run_csv"])
                break

            time.sleep(args.poll-ms / 1000.0)

if __name__ == "__main__":
    main()
