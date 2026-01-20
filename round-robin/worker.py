#worker.py
import os
import time
import queue
import threading
import asyncio
from typing import Any, Dict, Optional
# from prometheus_fastapi_instrumentator import Instrumentator

import httpx
from fastapi import FastAPI

SCHEDULER_URL = os.getenv("SCHEDULER_URL", "http://scheduler-svc:8000")
WORKER_ID = os.getenv("WORKER_ID", os.getenv("HOSTNAME", "worker-unknown"))
CORES = int(os.getenv("CORES", "4"))
SPEEDUP = float(os.getenv("SPEEDUP", "20000.0"))
HEARTBEAT_SEC = float(os.getenv("HEARTBEAT_SEC", "2.0"))

app = FastAPI(title="Worker (RR, semaphore-blocking cores)")

# Per-core assignment slot + semaphore
assign_lock = threading.Lock()
assignments: list[Optional[Dict[str, Any]]] = [None] * CORES
assign_sem = [threading.Semaphore(0) for _ in range(CORES)]

# Per-core completion queues (core -> main thread)
done_qs = [queue.Queue() for _ in range(CORES)]

# Instrumentator().instrument(app).expose(app, endpoint="/metrics")

def core_thread(core_id: int):
    """One thread = one core. Blocks on semaphore until assigned."""
    while True:
        assign_sem[core_id].acquire()  # BLOCKED until main thread assigns work

        with assign_lock:
            a = assignments[core_id]
            assignments[core_id] = None

        if a is None:
            continue  # should not happen, but safe

        job_id = a["job_id"]
        slice_ms = int(a["slice_ms"])
        remaining_before = int(a["remaining_before_ms"])

        started_wall_ms = int(time.time() * 1000)
        time.sleep(slice_ms / 1000.0 / SPEEDUP)  # simulate running
        finished_wall_ms = int(time.time() * 1000)

        ran_ms = slice_ms
        remaining_after = max(0, remaining_before - ran_ms)

        done_qs[core_id].put({
            "worker_id": WORKER_ID,
            "core_id": core_id,
            "job_id": job_id,
            "ran_ms": ran_ms,
            "remaining_after_ms": remaining_after,
            "started_wall_ms": started_wall_ms,
            "finished_wall_ms": finished_wall_ms,
        })

def assign_to_core(core_id: int, msg: Dict[str, Any]):
    with assign_lock:
        assignments[core_id] = {
            "job_id": msg["job_id"],
            "slice_ms": msg["slice_ms"],
            "remaining_before_ms": msg["remaining_before_ms"],
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
                # log_info("registered_with_scheduler", worker_id=WORKER_ID, cores=CORES)
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
    return {"ok": True, "worker_id": WORKER_ID, "cores": CORES, "speedup": SPEEDUP}
