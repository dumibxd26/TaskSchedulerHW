# submit_runs.py
import argparse
import time
import json
import httpx

import logging
import os
import sys
import json
from datetime import datetime, timezone

def setup_json_logging(service_name: str) -> logging.Logger:
    """
    Emit one JSON object per line to stdout so Alloy/Loki can scrape it.
    """
    level = os.getenv("LOG_LEVEL", "INFO").upper()
    logger = logging.getLogger(service_name)
    logger.setLevel(level)
    logger.handlers.clear()
    logger.propagate = False

    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(level)

    class JsonFormatter(logging.Formatter):
        def format(self, record: logging.LogRecord) -> str:
            payload = {
                "ts": datetime.now(timezone.utc).isoformat(),
                "level": record.levelname,
                "logger": record.name,
                "msg": record.getMessage(),
                "service": service_name,
            }
            # Optional structured extras
            for k, v in getattr(record, "__dict__", {}).items():
                if k in ("args", "asctime", "created", "exc_info", "exc_text", "filename", "funcName",
                         "levelname", "levelno", "lineno", "module", "msecs", "message", "msg",
                         "name", "pathname", "process", "processName", "relativeCreated",
                         "stack_info", "thread", "threadName"):
                    continue
                # only JSON-serializable stuff
                try:
                    json.dumps(v)
                    payload[k] = v
                except Exception:
                    payload[k] = str(v)

            if record.exc_info:
                payload["exc_info"] = self.formatException(record.exc_info)
            return json.dumps(payload, ensure_ascii=False)

    handler.setFormatter(JsonFormatter())
    logger.addHandler(handler)
    return logger

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scheduler", required=True, help="e.g. http://localhost:8000")
    ap.add_argument("--dataset", required=True)
    ap.add_argument("--quanta", default="2,5,10,20,50")
    ap.add_argument("--speedup", type=float, default=20000.0)
    ap.add_argument("--min-slots", type=int, default=1, help="Total cores required across all workers")
    ap.add_argument("--poll-ms", type=int, default=500)
    args = ap.parse_args()

    quanta = [int(x) for x in args.quanta.split(",") if x.strip()]

    timeout = httpx.Timeout(3600.0, connect=30.0)
    with httpx.Client(timeout=timeout) as c:
        for q in quanta:
            start_payload = {
                "dataset_file": args.dataset,
                "quantum_ms": q,
                "speedup": args.speedup,
                "min_slots": args.min_slots,
            }

            r = c.post(f"{args.scheduler}/start", json=start_payload)

            if r.status_code >= 400:
                print("HTTP", r.status_code)
                print(r.text)
                r.raise_for_status()

            run_id = r.json()["run_id"]
            print(f"Started run_id={run_id} quantum_ms={q}")

            # Poll until done
            while True:
                s = c.get(f"{args.scheduler}/status")
                if s.status_code >= 400:
                    print("STATUS HTTP", s.status_code)
                    print(s.text)
                    s.raise_for_status()

                status = s.json()

                if status.get("status") == "done" and status.get("run_id") == run_id:
                    print(json.dumps(status["summary"], indent=2))
                    print("jobs_csv:", status["jobs_csv"])
                    print("run_csv :", status["run_csv"])
                    break

                time.sleep(args.poll_ms / 1000.0)

if __name__ == "__main__":
    main()
