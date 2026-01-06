# scheduler.py

import os
import time
import uuid
import threading
from dataclasses import dataclass
from collections import deque
from typing import Any, Deque, Dict, Optional
from prometheus_fastapi_instrumentator import Instrumentator

import numpy as np
import pandas as pd
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

import logging
import os
import sys
import json
from datetime import datetime, timezone

DATA_DIR = os.getenv("DATA_DIR", "/data")
RESULTS_DIR = os.getenv("RESULTS_DIR", "/results")
WORKER_TIMEOUT_SEC = float(os.getenv("WORKER_TIMEOUT_SEC", "10.0"))

app = FastAPI(title="Scheduler (RR only, long-poll)")

Instrumentator().instrument(app).expose(app, endpoint="/metrics")

# ---------------------------
# Worker registry
# ---------------------------
WORKERS: Dict[str, Dict[str, Any]] = {}
REGISTRY_LOCK = threading.Lock()

class RegisterReq(BaseModel):
    worker_id: str
    cores: int

class HeartbeatReq(BaseModel):
    worker_id: str

@app.post("/register")
def register(req: RegisterReq):
    with REGISTRY_LOCK:
        WORKERS[req.worker_id] = {"cores": int(req.cores), "last_seen": time.time()}
    return {"ok": True}

@app.post("/heartbeat")
def heartbeat(req: HeartbeatReq):
    with REGISTRY_LOCK:
        if req.worker_id in WORKERS:
            WORKERS[req.worker_id]["last_seen"] = time.time()
            return {"ok": True}
    return {"ok": False}

def is_alive_worker_core(worker_id: str, core_id: int) -> bool:
    now = time.time()
    with REGISTRY_LOCK:
        info = WORKERS.get(worker_id)
        if not info:
            return False
        if now - info["last_seen"] > WORKER_TIMEOUT_SEC:
            return False
        return 0 <= core_id < int(info["cores"])

def total_alive_slots() -> int:
    now = time.time()
    with REGISTRY_LOCK:
        return sum(
            int(info["cores"])
            for info in WORKERS.values()
            if now - info["last_seen"] <= WORKER_TIMEOUT_SEC
        )

@app.get("/workers")
def workers():
    now = time.time()
    with REGISTRY_LOCK:
        alive = []
        total_slots = 0
        for wid, info in WORKERS.items():
            if now - info["last_seen"] <= WORKER_TIMEOUT_SEC:
                c = int(info["cores"])
                alive.append({"worker_id": wid, "cores": c})
                total_slots += c
        alive.sort(key=lambda x: x["worker_id"])
    return {"worker_count": len(alive), "total_slots": total_slots, "workers": alive}

# ---------------------------
# RR run state
# ---------------------------
@dataclass
class JobState:
    job_id: str
    service_ms: int
    remaining_ms: int
    arrival_ms: int = 0
    first_start_ms: Optional[int] = None
    finish_ms: Optional[int] = None
    slices: int = 0
    preemptions: int = 0

@dataclass
class RunState:
    run_id: str
    dataset_file: str
    quantum_ms: int
    speedup: float
    start_wall_ms: int
    jobs: Dict[str, JobState]
    ready_q: Deque[str]
    total_jobs: int
    completed: int = 0
    done: bool = False
    jobs_csv: Optional[str] = None
    run_csv: Optional[str] = None
    summary: Optional[Dict[str, Any]] = None

ACTIVE_RUN: Optional[RunState] = None

# One lock + condition to block /next callers until jobs appear
RUN_LOCK = threading.Lock()
RUN_CV = threading.Condition(RUN_LOCK)

class StartReq(BaseModel):
    dataset_file: str
    quantum_ms: int = 10
    speedup: float = 2000.0
    min_slots: int = 1

class NextReq(BaseModel):
    worker_id: str
    core_id: int
    timeout_ms: int = 10000  # long-poll duration

class DoneReq(BaseModel):
    worker_id: str
    core_id: int
    job_id: str
    ran_ms: int
    remaining_after_ms: int
    started_wall_ms: int
    finished_wall_ms: int

def wall_to_sim(run: RunState, wall_ms: int) -> int:
    return int(round((wall_ms - run.start_wall_ms) * run.speedup))

def load_jobs(dataset_path: str) -> Dict[str, JobState]:
    df = pd.read_csv(dataset_path)
    needed = {"job_id", "service_time_ms"}
    if not needed.issubset(df.columns):
        raise ValueError(f"Dataset must contain at least {sorted(needed)}")

    jobs: Dict[str, JobState] = {}
    for row in df.itertuples(index=False):
        jid = str(getattr(row, "job_id"))
        svc = int(getattr(row, "service_time_ms"))
        jobs[jid] = JobState(job_id=jid, service_ms=svc, remaining_ms=svc, arrival_ms=0)
    return jobs

def finalize_run(run: RunState):
    rows = []
    for j in run.jobs.values():
        if j.finish_ms is None:
            continue
        response = j.finish_ms - j.arrival_ms
        waiting = response - j.service_ms
        slowdown = response / max(1, j.service_ms)
        rows.append({
            "job_id": j.job_id,
            "service_time_ms": j.service_ms,
            "arrival_time_ms": j.arrival_ms,
            "first_start_time_ms": j.first_start_ms if j.first_start_ms is not None else 0,
            "finish_time_ms": j.finish_ms,
            "response_time_ms": response,
            "waiting_time_ms": waiting,
            "slices": j.slices,
            "preemptions": j.preemptions,
            "slowdown": slowdown,
        })
    jobs_df = pd.DataFrame(rows)

    p50 = float(np.percentile(jobs_df["response_time_ms"], 50))
    p95 = float(np.percentile(jobs_df["response_time_ms"], 95))
    p99 = float(np.percentile(jobs_df["response_time_ms"], 99))

    summary = {
        "run_id": run.run_id,
        "dataset_file": run.dataset_file,
        "quantum_ms": run.quantum_ms,
        "speedup": run.speedup,
        "jobs": int(len(jobs_df)),
        "mean_response_ms": float(jobs_df["response_time_ms"].mean()),
        "p50_response_ms": p50,
        "p95_response_ms": p95,
        "p99_response_ms": p99,
        "mean_wait_ms": float(jobs_df["waiting_time_ms"].mean()),
        "avg_slices_per_job": float(jobs_df["slices"].mean()),
        "total_slots_at_end": total_alive_slots(),
    }

    os.makedirs(RESULTS_DIR, exist_ok=True)
    jobs_path = os.path.join(RESULTS_DIR, f"results_jobs_{run.run_id}.csv")
    run_path  = os.path.join(RESULTS_DIR, f"results_run_{run.run_id}.csv")

    jobs_df.insert(0, "run_id", run.run_id)
    jobs_df.insert(1, "quantum_ms", run.quantum_ms)
    jobs_df.to_csv(jobs_path, index=False)
    pd.DataFrame([summary]).to_csv(run_path, index=False)

    run.jobs_csv = jobs_path
    run.run_csv = run_path
    run.summary = summary
    run.done = True

@app.post("/start")
def start(req: StartReq):
    global ACTIVE_RUN

    slots = total_alive_slots()
    if slots < req.min_slots:
        raise HTTPException(status_code=400, detail=f"Not enough slots online ({slots}) < min_slots ({req.min_slots})")

    dataset_path = os.path.join(DATA_DIR, req.dataset_file)
    if not os.path.exists(dataset_path):
        raise HTTPException(status_code=400, detail=f"Dataset not found: {dataset_path}")

    jobs = load_jobs(dataset_path)
    run = RunState(
        run_id=uuid.uuid4().hex[:10],
        dataset_file=req.dataset_file,
        quantum_ms=int(req.quantum_ms),
        speedup=float(req.speedup),
        start_wall_ms=int(time.time() * 1000),
        jobs=jobs,
        ready_q=deque(jobs.keys()),
        total_jobs=len(jobs),
    )

    with RUN_CV:
        ACTIVE_RUN = run
        RUN_CV.notify_all()  # wake cores waiting in /next

    return {"run_id": run.run_id}

@app.get("/status")
def status():
    with RUN_LOCK:
        run = ACTIVE_RUN
        if run is None:
            return {"status": "no_run"}
        if run.done:
            return {"status": "done", "run_id": run.run_id, "summary": run.summary, "jobs_csv": run.jobs_csv, "run_csv": run.run_csv}
        return {"status": "running", "run_id": run.run_id, "completed": run.completed, "total": run.total_jobs, "quantum_ms": run.quantum_ms}

@app.post("/next")
def next_slice(req: NextReq):
    if not is_alive_worker_core(req.worker_id, req.core_id):
        raise HTTPException(status_code=400, detail="worker/core not alive or invalid core_id")

    deadline = time.time() + (req.timeout_ms / 1000.0)

    with RUN_CV:
        while True:
            run = ACTIVE_RUN
            if run is None:
                return {"status": "no_run"}
            if run.done or run.completed >= run.total_jobs:
                return {"status": "done"}

            if run.ready_q:
                jid = run.ready_q.popleft()
                job = run.jobs[jid]
                slice_ms = min(run.quantum_ms, job.remaining_ms)
                return {"status": "ok", "job_id": jid, "slice_ms": int(slice_ms), "remaining_before_ms": int(job.remaining_ms)}

            # No work right now: BLOCK until notified or timeout
            remaining = deadline - time.time()
            if remaining <= 0:
                return {"status": "wait"}  # long-poll timed out
            RUN_CV.wait(timeout=remaining)

@app.post("/done")
def done(req: DoneReq):
    if not is_alive_worker_core(req.worker_id, req.core_id):
        raise HTTPException(status_code=400, detail="worker/core not alive or invalid core_id")

    with RUN_CV:
        run = ACTIVE_RUN
        if run is None:
            return {"status": "no_run"}
        if run.done:
            return {"status": "done"}

        job = run.jobs.get(req.job_id)
        if job is None:
            raise HTTPException(status_code=400, detail="unknown job_id")

        started_sim = wall_to_sim(run, int(req.started_wall_ms))
        finished_sim = wall_to_sim(run, int(req.finished_wall_ms))

        if job.first_start_ms is None:
            job.first_start_ms = started_sim

        job.slices += 1
        job.remaining_ms = max(0, int(req.remaining_after_ms))

        if job.remaining_ms == 0:
            job.finish_ms = finished_sim
            run.completed += 1
        else:
            job.preemptions += 1
            run.ready_q.append(job.job_id)  # RR: back to tail
            RUN_CV.notify()                 # wake one waiting /next caller

        if run.completed >= run.total_jobs:
            finalize_run(run)
            RUN_CV.notify_all()  # wake everyone so they can see "done"

    return {"status": "ok"}
