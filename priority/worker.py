"""Priority worker that executes whole jobs on each core."""

import asyncio
import os
import queue
import threading
import time
from typing import Any, Dict, Optional

import httpx
from fastapi import FastAPI
from prometheus_fastapi_instrumentator import Instrumentator

import logging
import sys
import json
from datetime import datetime, timezone

try:
    import psutil  # type: ignore
    HAS_PSUTIL = True
except Exception:  # pragma: no cover - best effort
    HAS_PSUTIL = False


def setup_json_logging(service_name: str) -> logging.Logger:
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
            for k, v in getattr(record, "__dict__", {}).items():
                if k in (
                    "args",
                    "asctime",
                    "created",
                    "exc_info",
                    "exc_text",
                    "filename",
                    "funcName",
                    "levelname",
                    "levelno",
                    "lineno",
                    "module",
                    "msecs",
                    "message",
                    "msg",
                    "name",
                    "pathname",
                    "process",
                    "processName",
                    "relativeCreated",
                    "stack_info",
                    "thread",
                    "threadName",
                ):
                    continue
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


log = setup_json_logging("priority-worker")


def log_info(event: str, **fields):
    log.info(event, extra=fields)


SCHEDULER_URL = os.getenv("SCHEDULER_URL", "http://scheduler-svc:8000")
WORKER_ID = os.getenv("WORKER_ID", os.getenv("HOSTNAME", "worker-unknown"))
CORES = int(os.getenv("CORES", "4"))
SPEEDUP = float(os.getenv("SPEEDUP", "20000.0"))
HEARTBEAT_SEC = float(os.getenv("HEARTBEAT_SEC", "2.0"))

log_info(
    "worker_boot",
    worker_id=WORKER_ID,
    cores=CORES,
    scheduler_url=SCHEDULER_URL,
    speedup=SPEEDUP,
    heartbeat_sec=HEARTBEAT_SEC,
    psutil=HAS_PSUTIL,
)

app = FastAPI(title="Worker (Priority, non-preemptive)")

assign_lock = threading.Lock()
assignments: list[Optional[Dict[str, Any]]] = [None] * CORES
assign_sem = [threading.Semaphore(0) for _ in range(CORES)]
done_qs = [queue.Queue() for _ in range(CORES)]

Instrumentator().instrument(app).expose(app, endpoint="/metrics")


def core_thread(core_id: int):
    while True:
        assign_sem[core_id].acquire()
        with assign_lock:
            payload = assignments[core_id]
            assignments[core_id] = None

        if payload is None:
            continue

        job_id = payload["job_id"]
        execution_ms = int(payload["execution_ms"])
        priority = payload.get("priority")

        cpu_before = psutil.cpu_percent(interval=None) if HAS_PSUTIL else None
        mem_before = psutil.virtual_memory().used if HAS_PSUTIL else None

        started_wall_ms = int(time.time() * 1000)
        log_info(
            "job_started",
            worker_id=WORKER_ID,
            core_id=core_id,
            job_id=job_id,
            execution_ms=execution_ms,
            priority=priority,
        )

        time.sleep(execution_ms / 1000.0 / SPEEDUP)

        finished_wall_ms = int(time.time() * 1000)

        cpu_after = psutil.cpu_percent(interval=None) if HAS_PSUTIL else None
        mem_after = psutil.virtual_memory().used if HAS_PSUTIL else None

        cpu_avg = None
        mem_peak_mb = None
        if HAS_PSUTIL:
            before = cpu_before or 0.0
            after = cpu_after or 0.0
            cpu_avg = (before + after) / 2.0
            mem_peak_mb = max(mem_before or 0, mem_after or 0) / (1024 * 1024)

        log_info(
            "job_finished",
            worker_id=WORKER_ID,
            core_id=core_id,
            job_id=job_id,
            execution_ms=execution_ms,
            priority=priority,
        )

        done_qs[core_id].put(
            {
                "worker_id": WORKER_ID,
                "core_id": core_id,
                "job_id": job_id,
                "started_wall_ms": started_wall_ms,
                "finished_wall_ms": finished_wall_ms,
                "cpu_percent": cpu_avg,
                "memory_mb": mem_peak_mb,
            }
        )


def assign_to_core(core_id: int, msg: Dict[str, Any]):
    with assign_lock:
        assignments[core_id] = {
            "job_id": msg["job_id"],
            "execution_ms": msg["execution_ms"],
            "priority": msg.get("priority"),
        }
    assign_sem[core_id].release()


async def heartbeat_loop(client: httpx.AsyncClient):
    while True:
        try:
            await client.post(f"{SCHEDULER_URL}/heartbeat", json={"worker_id": WORKER_ID})
        except Exception:
            pass
        await asyncio.sleep(HEARTBEAT_SEC)


async def core_driver(client: httpx.AsyncClient, core_id: int):
    while True:
        try:
            resp = await client.post(
                f"{SCHEDULER_URL}/next",
                json={"worker_id": WORKER_ID, "core_id": core_id, "timeout_ms": 20000},
            )
            resp.raise_for_status()
            msg = resp.json()
        except Exception:
            await asyncio.sleep(0.2)
            continue

        status = msg.get("status")
        if status == "ok":
            assign_to_core(core_id, msg)
            event = await asyncio.to_thread(done_qs[core_id].get)
            try:
                await client.post(f"{SCHEDULER_URL}/done", json=event)
            except Exception:
                await asyncio.sleep(0.2)
            continue

        if status in {"wait", "no_run", "done"}:
            await asyncio.sleep(0.2)
            continue

        await asyncio.sleep(0.2)


async def main_loop():
    async with httpx.AsyncClient(timeout=60.0) as client:
        while True:
            try:
                await client.post(
                    f"{SCHEDULER_URL}/register", json={"worker_id": WORKER_ID, "cores": CORES}
                )
                log_info("registered", worker_id=WORKER_ID, cores=CORES)
                break
            except Exception:
                await asyncio.sleep(0.5)

        asyncio.create_task(heartbeat_loop(client))
        for core_id in range(CORES):
            asyncio.create_task(core_driver(client, core_id))

        while True:
            await asyncio.sleep(3600)


@app.on_event("startup")
async def on_startup():
    for core_id in range(CORES):
        threading.Thread(target=core_thread, args=(core_id,), daemon=True).start()
    asyncio.create_task(main_loop())


@app.get("/health")
def health():
    return {
        "ok": True,
        "worker_id": WORKER_ID,
        "cores": CORES,
        "speedup": SPEEDUP,
        "psutil": HAS_PSUTIL,
    }
