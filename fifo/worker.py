# worker.py - FIFO variant

import os
import time
import queue
import threading
import asyncio
from typing import Any, Dict, Optional
from prometheus_fastapi_instrumentator import Instrumentator

import httpx
from fastapi import FastAPI

import logging
import os
import sys
import json
from datetime import datetime, timezone

try:
    import psutil
    HAS_PSUTIL = True
except ImportError:
    HAS_PSUTIL = False
    print("WARNING: psutil not available, CPU/memory metrics will not be collected", file=sys.stderr)

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

log = setup_json_logging("worker")

def log_info(event: str, **fields):
    log.info(event, extra=fields)

SCHEDULER_URL = os.getenv("SCHEDULER_URL", "http://scheduler-svc:8000")
WORKER_ID = os.getenv("WORKER_ID", os.getenv("HOSTNAME", "worker-unknown"))
CORES = int(os.getenv("CORES", "4"))
SPEEDUP = float(os.getenv("SPEEDUP", "20000.0"))
HEARTBEAT_SEC = float(os.getenv("HEARTBEAT_SEC", "2.0"))

log_info(
    "worker_started",
    worker_id=WORKER_ID,
    cores=CORES,
    scheduler_url=SCHEDULER_URL,
    speedup=SPEEDUP,
    heartbeat_sec=HEARTBEAT_SEC,
)

app = FastAPI(title="Worker (FIFO, non-preemptive)")

# Per-core assignment slot + semaphore
assign_lock = threading.Lock()
assignments: list[Optional[Dict[str, Any]]] = [None] * CORES
assign_sem = [threading.Semaphore(0) for _ in range(CORES)]

# Per-core completion queues (core -> main thread)
done_qs = [queue.Queue() for _ in range(CORES)]

Instrumentator().instrument(app).expose(app, endpoint="/metrics")

def core_thread(core_id: int):
    """One thread = one core. Executes entire job (FIFO non-preemptive)."""
    while True:
        assign_sem[core_id].acquire()  # BLOCKED until main thread assigns work

        with assign_lock:
            a = assignments[core_id]
            assignments[core_id] = None

        if a is None:
            continue  # should not happen, but safe

        job_id = a["job_id"]
        execution_ms = int(a["execution_ms"])

        started_wall_ms = int(time.time() * 1000)
        
        # Collect CPU/memory before execution
        cpu_before = None
        memory_before = None
        if HAS_PSUTIL:
            cpu_before = psutil.cpu_percent(interval=None)
            memory_before = psutil.virtual_memory().used

        log_info(
            "job_started",
            worker_id=WORKER_ID,
            core_id=core_id,
            job_id=job_id,
            execution_ms=execution_ms,
        )
        
        # FIFO: execute entire job (non-preemptive)
        time.sleep(execution_ms / 1000.0 / SPEEDUP)  # simulate running
        
        finished_wall_ms = int(time.time() * 1000)

        # Collect CPU/memory after execution
        cpu_after = None
        memory_after = None
        cpu_avg = None
        memory_max = None
        
        if HAS_PSUTIL:
            cpu_after = psutil.cpu_percent(interval=None)
            memory_after = psutil.virtual_memory().used
            # Average CPU (simple approximation)
            cpu_avg = (cpu_before + cpu_after) / 2.0 if cpu_before is not None else cpu_after
            # Max memory used
            memory_max = max(memory_before or 0, memory_after or 0) / (1024 * 1024)  # Convert to MB

        log_info(
            "job_finished",
            worker_id=WORKER_ID,
            core_id=core_id,
            job_id=job_id,
            execution_ms=execution_ms,
        )

        done_qs[core_id].put({
            "worker_id": WORKER_ID,
            "core_id": core_id,
            "job_id": job_id,
            "started_wall_ms": started_wall_ms,
            "finished_wall_ms": finished_wall_ms,
            "cpu_percent": cpu_avg,
            "memory_mb": memory_max,
        })

def assign_to_core(core_id: int, msg: Dict[str, Any]):
    with assign_lock:
        assignments[core_id] = {
            "job_id": msg["job_id"],
            "execution_ms": msg["execution_ms"],
        }
    assign_sem[core_id].release()  # wake the core thread

async def heartbeat_loop(client: httpx.AsyncClient):
    while True:
        try:
            await client.post(f"{SCHEDULER_URL}/heartbeat", json={"worker_id": WORKER_ID})
        except Exception:
            pass
        await asyncio.sleep(HEARTBEAT_SEC)

async def core_driver(client: httpx.AsyncClient, core_id: int):
    """
    Runs in main thread event loop (async).
    No busy-wait: /next is long-polled and blocks server-side.
    """
    while True:
        # Long-poll for work
        try:
            r = await client.post(
                f"{SCHEDULER_URL}/next",
                json={"worker_id": WORKER_ID, "core_id": core_id, "timeout_ms": 20000},
            )
            r.raise_for_status()
            msg = r.json()
        except Exception:
            await asyncio.sleep(0.2)
            continue

        st = msg.get("status")
        if st == "ok":
            assign_to_core(core_id, msg)

            # Wait (blocked) for this core to finish
            event = await asyncio.to_thread(done_qs[core_id].get)

            # Report completion
            try:
                await client.post(f"{SCHEDULER_URL}/done", json=event)
            except Exception:
                # if scheduler is temporarily down, retry on next loop
                await asyncio.sleep(0.2)
            continue

        if st in ("wait", "no_run", "done"):
            # No spinning; back off a bit (rare because /next long-polls)
            await asyncio.sleep(0.2)
            continue

        await asyncio.sleep(0.2)

async def main_loop():
    async with httpx.AsyncClient(timeout=60.0) as client:
        # Register
        while True:
            try:
                await client.post(f"{SCHEDULER_URL}/register", json={"worker_id": WORKER_ID, "cores": CORES})
                log_info("registered_with_scheduler", worker_id=WORKER_ID, cores=CORES)
                break
            except Exception:
                await asyncio.sleep(0.5)

        asyncio.create_task(heartbeat_loop(client))

        # Start one driver per core
        for core_id in range(CORES):
            asyncio.create_task(core_driver(client, core_id))

        # Keep process alive
        while True:
            await asyncio.sleep(3600)

@app.on_event("startup")
async def on_startup():
    # Start core threads (blocked until assigned)
    for core_id in range(CORES):
        threading.Thread(target=core_thread, args=(core_id,), daemon=True).start()

    # Start main loop (server thread)
    asyncio.create_task(main_loop())

@app.get("/health")
def health():
    return {"ok": True, "worker_id": WORKER_ID, "cores": CORES, "speedup": SPEEDUP, "has_psutil": HAS_PSUTIL}
