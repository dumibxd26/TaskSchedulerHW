"""Priority-based scheduler service."""

from __future__ import annotations

import heapq
import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from fastapi import FastAPI, HTTPException
from prometheus_fastapi_instrumentator import Instrumentator
from pydantic import BaseModel

DATA_DIR = os.getenv("DATA_DIR", "/data")
RESULTS_DIR = os.getenv("RESULTS_DIR", "/results")
WORKER_TIMEOUT_SEC = float(os.getenv("WORKER_TIMEOUT_SEC", "10.0"))

app = FastAPI(title="Scheduler (Priority, non-preemptive)")
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
        info = WORKERS.get(req.worker_id)
        if info:
            info["last_seen"] = time.time()
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
                cores = int(info["cores"])
                total_slots += cores
                alive.append({"worker_id": wid, "cores": cores})
        alive.sort(key=lambda x: x["worker_id"])
    return {"worker_count": len(alive), "total_slots": total_slots, "workers": alive}


# ---------------------------
# Priority run state
# ---------------------------
@dataclass
class JobState:
    job_id: str
    service_ms: int
    arrival_ms: int
    priority: int
    start_ms: Optional[int] = None
    finish_ms: Optional[int] = None
    cpu_percent: Optional[float] = None
    memory_mb: Optional[float] = None


@dataclass
class RunState:
    run_id: str
    dataset_file: str
    speedup: float
    start_wall_ms: int
    jobs: Dict[str, JobState]
    ready_heap: List[Tuple[int, int, int, str]] = field(default_factory=list)
    pending_heap: List[Tuple[int, int, str]] = field(default_factory=list)
    next_seq: int = 0
    total_jobs: int = 0
    current_sim_ms: int = 0
    completed: int = 0
    done: bool = False
    jobs_csv: Optional[str] = None
    run_csv: Optional[str] = None
    summary: Optional[Dict[str, Any]] = None


ACTIVE_RUN: Optional[RunState] = None
RUN_LOCK = threading.Lock()
RUN_CV = threading.Condition(RUN_LOCK)
ARRIVAL_THREAD: Optional[threading.Thread] = None


class StartReq(BaseModel):
    dataset_file: str
    speedup: float = 2000.0
    min_slots: int = 1


class NextReq(BaseModel):
    worker_id: str
    core_id: int
    timeout_ms: int = 10000


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
    needed = {"job_id", "service_time_ms", "priority"}
    if not needed.issubset(df.columns):
        raise ValueError(f"Dataset must contain columns: {sorted(needed)}")

    has_arrival = "arrival_time_ms" in df.columns
    jobs: Dict[str, JobState] = {}
    for row in df.itertuples(index=False):
        jid = str(getattr(row, "job_id"))
        service = int(getattr(row, "service_time_ms"))
        arrival = int(getattr(row, "arrival_time_ms")) if has_arrival else 0
        priority = int(getattr(row, "priority"))
        jobs[jid] = JobState(job_id=jid, service_ms=service, arrival_ms=arrival, priority=priority)
    return jobs


def push_ready(run: RunState, job_id: str):
    job = run.jobs[job_id]
    heapq.heappush(run.ready_heap, (job.priority, job.arrival_ms, run.next_seq, job_id))
    run.next_seq += 1


def push_pending(run: RunState, job_id: str):
    job = run.jobs[job_id]
    heapq.heappush(run.pending_heap, (job.arrival_ms, run.next_seq, job_id))
    run.next_seq += 1


def promote_arrivals(run: RunState, upto_sim_ms: int):
    changed = False
    while run.pending_heap and run.pending_heap[0][0] <= upto_sim_ms:
        _, _, jid = heapq.heappop(run.pending_heap)
        push_ready(run, jid)
        changed = True
    if changed:
        RUN_CV.notify_all()


def arrival_thread_func(run: RunState):
    while not run.done:
        now_wall = int(time.time() * 1000)
        current_sim = wall_to_sim(run, now_wall)
        with RUN_CV:
            run.current_sim_ms = max(run.current_sim_ms, current_sim)
            promote_arrivals(run, run.current_sim_ms)
        time.sleep(0.01)


def finalize_run(run: RunState):
    rows = []
    for job in run.jobs.values():
        if job.start_ms is None or job.finish_ms is None:
            continue
        waiting = job.start_ms - job.arrival_ms
        response = job.finish_ms - job.arrival_ms
        execution = job.finish_ms - job.start_ms
        slowdown = response / max(1, job.service_ms)
        rows.append(
            {
                "job_id": job.job_id,
                "arrival_time_ms": job.arrival_ms,
                "service_time_ms": job.service_ms,
                "start_time_ms": job.start_ms,
                "finish_time_ms": job.finish_ms,
                "waiting_time_ms": waiting,
                "execution_time_ms": execution,
                "response_time_ms": response,
                "cpu_usage_percent": job.cpu_percent or 0.0,
                "memory_usage_mb": job.memory_mb or 0.0,
                "slowdown": slowdown,
                "priority": job.priority,
            }
        )

    jobs_df = pd.DataFrame(rows)
    if jobs_df.empty:
        return

    summary = {
        "run_id": run.run_id,
        "dataset_file": run.dataset_file,
        "speedup": run.speedup,
        "jobs": int(len(jobs_df)),
        "mean_response_ms": float(jobs_df["response_time_ms"].mean()),
        "p50_response_ms": float(np.percentile(jobs_df["response_time_ms"], 50)),
        "p95_response_ms": float(np.percentile(jobs_df["response_time_ms"], 95)),
        "p99_response_ms": float(np.percentile(jobs_df["response_time_ms"], 99)),
        "mean_wait_ms": float(jobs_df["waiting_time_ms"].mean()),
        "mean_execution_ms": float(jobs_df["execution_time_ms"].mean()),
        "total_slots_at_end": total_alive_slots(),
    }

    os.makedirs(RESULTS_DIR, exist_ok=True)
    jobs_path = os.path.join(RESULTS_DIR, f"results_jobs_{run.run_id}.csv")
    run_path = os.path.join(RESULTS_DIR, f"results_run_{run.run_id}.csv")

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
    ready_heap: List[Tuple[int, int, int, str]] = []
    pending_heap: List[Tuple[int, int, str]] = []
    seq = 0

    for job_id, job in jobs.items():
        if job.arrival_ms <= 0:
            heapq.heappush(ready_heap, (job.priority, job.arrival_ms, seq, job_id))
        else:
            heapq.heappush(pending_heap, (job.arrival_ms, seq, job_id))
        seq += 1

    run = RunState(
        run_id=uuid.uuid4().hex[:10],
        dataset_file=req.dataset_file,
        speedup=float(req.speedup),
        start_wall_ms=int(time.time() * 1000),
        jobs=jobs,
        ready_heap=ready_heap,
        pending_heap=pending_heap,
        next_seq=seq,
        total_jobs=len(jobs),
    )

    with RUN_CV:
        ACTIVE_RUN = run
        RUN_CV.notify_all()

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
            return {
                "status": "done",
                "run_id": run.run_id,
                "summary": run.summary,
                "jobs_csv": run.jobs_csv,
                "run_csv": run.run_csv,
            }
        return {
            "status": "running",
            "run_id": run.run_id,
            "completed": run.completed,
            "total": run.total_jobs,
            "ready": len(run.ready_heap),
            "pending": len(run.pending_heap),
        }


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

            now_sim = wall_to_sim(run, int(time.time() * 1000))
            run.current_sim_ms = max(run.current_sim_ms, now_sim)
            promote_arrivals(run, run.current_sim_ms)

            if run.ready_heap:
                priority, arrival, _, jid = heapq.heappop(run.ready_heap)
                job = run.jobs[jid]
                return {
                    "status": "ok",
                    "job_id": jid,
                    "execution_ms": int(job.service_ms),
                    "priority": int(priority),
                    "arrival_time_ms": int(arrival),
                }

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
        finished_sim = started_sim + job.service_ms

        if job.start_ms is None:
            job.start_ms = started_sim
        job.finish_ms = finished_sim
        job.cpu_percent = req.cpu_percent
        job.memory_mb = req.memory_mb

        run.current_sim_ms = max(run.current_sim_ms, finished_sim)
        run.completed += 1

        if run.completed >= run.total_jobs:
            finalize_run(run)
            RUN_CV.notify_all()
        else:
            RUN_CV.notify()

    return {"status": "ok"}
