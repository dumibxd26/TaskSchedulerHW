import argparse
import time
import json
import httpx

def fmt_time(seconds: float) -> str:
    if seconds == float("inf") or seconds != seconds:
        return "?"
    seconds = int(seconds)
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    if h > 0:
        return f"{h}h{m:02d}m{s:02d}s"
    if m > 0:
        return f"{m}m{s:02d}s"
    return f"{s}s"

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scheduler", required=True, help="http://localhost:8000")
    ap.add_argument("--dataset", required=True)
    ap.add_argument("--quanta", default="2,5,10,20,50")
    ap.add_argument("--speedup", type=float, default=20000.0)
    ap.add_argument("--min-slots", type=int, default=1)
    ap.add_argument("--poll-ms", type=int, default=1000)
    args = ap.parse_args()

    quanta = [int(x) for x in args.quanta.split(",") if x.strip()]

    timeout = httpx.Timeout(3600.0, connect=30.0)
    with httpx.Client(timeout=timeout) as c:
        for q in quanta:
            # start run
            r = c.post(f"{args.scheduler}/start", json={
                "dataset_file": args.dataset,
                "quantum_ms": q,
                "speedup": args.speedup,
                "min_slots": args.min_slots,
            })
            if r.status_code >= 400:
                print("START ERROR", r.status_code, r.text)
                r.raise_for_status()

            run_id = r.json()["run_id"]
            print(f"Started run_id={run_id} quantum_ms={q}")

            t0 = time.time()
            last_completed = None
            last_t = None

            # poll progress
            while True:
                s = c.get(f"{args.scheduler}/status")
                if s.status_code >= 400:
                    print("STATUS ERROR", s.status_code, s.text)
                    s.raise_for_status()

                st = s.json()

                # finished?
                if st.get("status") == "done" and st.get("run_id") == run_id:
                    print(json.dumps(st["summary"], indent=2))
                    print("jobs_csv:", st["jobs_csv"])
                    print("run_csv :", st["run_csv"])
                    break

                if st.get("status") == "running" and st.get("run_id") == run_id:
                    completed = int(st.get("completed", 0))
                    total = int(st.get("total", 0))
                    pct = (completed / total * 100.0) if total else 0.0

                    now = time.time()
                    elapsed = now - t0

                    eta = float("inf")
                    rate = None
                    if last_completed is not None and last_t is not None:
                        dc = completed - last_completed
                        dt = now - last_t
                        if dt > 0 and dc > 0:
                            rate = dc / dt  # jobs/sec
                            remaining = max(0, total - completed)
                            eta = remaining / rate if rate > 0 else float("inf")

                    last_completed = completed
                    last_t = now

                    rate_str = f"{rate:.1f} jobs/s" if rate is not None else "?"
                    print(
                        f"\rProgress: {completed}/{total} ({pct:5.1f}%) | "
                        f"elapsed {fmt_time(elapsed)} | rate {rate_str} | ETA {fmt_time(eta)}",
                        end="",
                        flush=True,
                    )

                time.sleep(args.poll_ms / 1000.0)

            print() 

if __name__ == "__main__":
    main()
