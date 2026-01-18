# scheduler.py - FIFO variant

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

app = FastAPI(title="Scheduler (FIFO, long-poll)")

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
# FIFO run state
# ---------------------------
@dataclass
class JobState:
    job_id: str
    service_ms: int  # Estimated execution time from CSV
    arrival_ms: int  # When job arrives in system (from CSV)
    start_ms: Optional[int] = None  # When job starts executing
    finish_ms: Optional[int] = None  # When job finishes
    cpu_percent: Optional[float] = None  # CPU usage during execution
    memory_mb: Optional[float] = None  # Memory usage during execution

@dataclass
class RunState:
    run_id: str
    dataset_file: str
    speedup: float
    start_wall_ms: int
    jobs: Dict[str, JobState]
    ready_q: Deque[str]  # FIFO queue
    pending_jobs: Dict[str, JobState]  # Jobs not yet arrived (arrival_time > current_time)
    total_jobs: int
    current_sim_ms: int = 0  # Current simulated time
    completed: int = 0
    done: bool = False
    jobs_csv: Optional[str] = None
    run_csv: Optional[str] = None
    summary: Optional[Dict[str, Any]] = None

ACTIVE_RUN: Optional[RunState] = None

# One lock + condition to block /next callers until jobs appear
RUN_LOCK = threading.Lock()
RUN_CV = threading.Condition(RUN_LOCK)

# Thread for adding jobs to queue at their arrival time
ARRIVAL_THREAD: Optional[threading.Thread] = None

class StartReq(BaseModel):
    dataset_file: str
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
    started_wall_ms: int
    finished_wall_ms: int
    cpu_percent: Optional[float] = None
    memory_mb: Optional[float] = None

def wall_to_sim(run: RunState, wall_ms: int) -> int:
    return int(round((wall_ms - run.start_wall_ms) * run.speedup))

def load_jobs(dataset_path: str) -> Dict[str, JobState]:
    df = pd.read_csv(dataset_path)
    needed = {"job_id", "service_time_ms"}
    if not needed.issubset(df.columns):
        raise ValueError(f"Dataset must contain at least {sorted(needed)}")
    
    # Check if arrival_time_ms exists, default to 0 if not
    has_arrival = "arrival_time_ms" in df.columns
    
    jobs: Dict[str, JobState] = {}
    for row in df.itertuples(index=False):
        jid = str(getattr(row, "job_id"))
        svc = int(getattr(row, "service_time_ms"))
        arrival = int(getattr(row, "arrival_time_ms")) if has_arrival else 0
        jobs[jid] = JobState(job_id=jid, service_ms=svc, arrival_ms=arrival)
    return jobs

def arrival_thread_func(run: RunState):
    """Thread that adds jobs to ready_q when their arrival_time is reached."""
    while not run.done:
        # Convert current wall time to simulation time
        current_wall_ms = int(time.time() * 1000)
        current_sim = wall_to_sim(run, current_wall_ms)
        
        # Check pending jobs and move them to ready_q if arrival time reached
        jobs_to_add = []
        with RUN_CV:
            for jid, job in list(run.pending_jobs.items()):
                if job.arrival_ms <= current_sim:
                    jobs_to_add.append(jid)
                    del run.pending_jobs[jid]
            
            for jid in jobs_to_add:
                run.ready_q.append(jid)
                RUN_CV.notify_all()
            
            # Update current simulation time
            run.current_sim_ms = max(run.current_sim_ms, current_sim)
        
        if not run.done:
            time.sleep(0.01)  # Check every 10ms wall time

def finalize_run(run: RunState):
    rows = []
    for j in run.jobs.values():
        if j.finish_ms is None or j.start_ms is None:
            continue
        
        waiting_time = j.start_ms - j.arrival_ms
        # execution_time = finish_time - start_time (in simulated time)
        # Since finish_time = start_time + service_time, execution_time = service_time
        execution_time = j.finish_ms - j.start_ms
        response_time = j.finish_ms - j.arrival_ms
        slowdown = response_time / max(1, j.service_ms)
        
        rows.append({
            "job_id": j.job_id,
            "arrival_time_ms": j.arrival_ms,
            "service_time_ms": j.service_ms,
            "start_time_ms": j.start_ms,
            "finish_time_ms": j.finish_ms,
            "waiting_time_ms": waiting_time,
            "execution_time_ms": execution_time,
            "response_time_ms": response_time,
            "cpu_usage_percent": j.cpu_percent if j.cpu_percent is not None else 0.0,
            "memory_usage_mb": j.memory_mb if j.memory_mb is not None else 0.0,
            "slowdown": slowdown,
        })
    
    jobs_df = pd.DataFrame(rows)
    
    if len(jobs_df) == 0:
        return
    
    p50 = float(np.percentile(jobs_df["response_time_ms"], 50))
    p95 = float(np.percentile(jobs_df["response_time_ms"], 95))
    p99 = float(np.percentile(jobs_df["response_time_ms"], 99))
    
    summary = {
        "run_id": run.run_id,
        "dataset_file": run.dataset_file,
        "speedup": run.speedup,
        "jobs": int(len(jobs_df)),
        "mean_response_ms": float(jobs_df["response_time_ms"].mean()),
        "p50_response_ms": p50,
        "p95_response_ms": p95,
        "p99_response_ms": p99,
        "mean_wait_ms": float(jobs_df["waiting_time_ms"].mean()),
        "mean_execution_ms": float(jobs_df["execution_time_ms"].mean()),
        "total_slots_at_end": total_alive_slots(),
    }
    
    os.makedirs(RESULTS_DIR, exist_ok=True)
    jobs_path = os.path.join(RESULTS_DIR, f"results_jobs_{run.run_id}.csv")
    run_path  = os.path.join(RESULTS_DIR, f"results_run_{run.run_id}.csv")
    
    jobs_df.insert(0, "run_id", run.run_id)
    jobs_df.to_csv(jobs_path, index=False)
    pd.DataFrame([summary]).to_csv(run_path, index=False)
    
    run.jobs_csv = jobs_path
    run.run_csv = run_path
    run.summary = summary
    run.done = True

@app.post("/start")
def start(req: StartReq):
    global ACTIVE_RUN, ARRIVAL_THREAD
    
    slots = total_alive_slots()
    if slots < req.min_slots:
        raise HTTPException(status_code=400, detail=f"Not enough slots online ({slots}) < min_slots ({req.min_slots})")
    
    dataset_path = os.path.join(DATA_DIR, req.dataset_file)
    if not os.path.exists(dataset_path):
        raise HTTPException(status_code=400, detail=f"Dataset not found: {dataset_path}")
    
    jobs = load_jobs(dataset_path)
    
    # Separate jobs into ready (arrival_ms=0) and pending (arrival_ms>0)
    ready_q = deque()
    pending_jobs: Dict[str, JobState] = {}
    
    for jid, job in jobs.items():
        if job.arrival_ms <= 0:
            ready_q.append(jid)
        else:
            pending_jobs[jid] = job
    
    run = RunState(
        run_id=uuid.uuid4().hex[:10],
        dataset_file=req.dataset_file,
        speedup=float(req.speedup),
        start_wall_ms=int(time.time() * 1000),
        jobs=jobs,
        ready_q=ready_q,
        pending_jobs=pending_jobs,
        total_jobs=len(jobs),
    )
    
    with RUN_CV:
        ACTIVE_RUN = run
        RUN_CV.notify_all()
    
    # Start arrival thread for progressive job addition
    ARRIVAL_THREAD = threading.Thread(target=arrival_thread_func, args=(run,), daemon=True)
    ARRIVAL_THREAD.start()
    
    return {"run_id": run.run_id}

@app.get("/status")
def status():
    with RUN_LOCK:
        run = ACTIVE_RUN
        if run is None:
            return {"status": "no_run"}
        if run.done:
            return {"status": "done", "run_id": run.run_id, "summary": run.summary, "jobs_csv": run.jobs_csv, "run_csv": run.run_csv}
        return {"status": "running", "run_id": run.run_id, "completed": run.completed, "total": run.total_jobs}

@app.post("/next")
def next_job(req: NextReq):
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
                jid = run.ready_q.popleft()  # FIFO: first come, first served
                job = run.jobs[jid]
                # FIFO: execute entire service_time (no slicing/preemption)
                return {"status": "ok", "job_id": jid, "execution_ms": int(job.service_ms)}
            
            # No work right now: BLOCK until notified or timeout
            remaining = deadline - time.time()
            if remaining <= 0:
                return {"status": "wait"}
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
        
        if job.start_ms is None:
            job.start_ms = started_sim
        
        # finish_time should be start_time + service_time (in simulated time)
        # This is more accurate than using wall time conversion for very fast executions
        job.finish_ms = job.start_ms + job.service_ms
        job.cpu_percent = req.cpu_percent if req.cpu_percent is not None else None
        job.memory_mb = req.memory_mb if req.memory_mb is not None else None
        
        # Update current simulation time (use calculated finish_ms, not converted wall time)
        run.current_sim_ms = max(run.current_sim_ms, job.finish_ms)
        
        run.completed += 1
        
        if run.completed >= run.total_jobs:
            finalize_run(run)
            RUN_CV.notify_all()
        else:
            RUN_CV.notify()  # wake one waiting /next caller
    
    return {"status": "ok"}
