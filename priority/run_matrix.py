import argparse
import itertools
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import IO, Iterable, List, Sequence, Tuple

import httpx


PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_DATASET = "dataset_priority_linux_1k.csv"
DEFAULT_RESULTS_DIR = PROJECT_ROOT / "results"
DEFAULT_TEMP_DIR = DEFAULT_RESULTS_DIR / ".tmp"
SCHEDULER_PORT = 18080
WORKER_BASE_PORT = 19000


def parse_int_list(raw: str) -> List[int]:
    values: List[int] = []
    for chunk in raw.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        values.append(int(chunk))
    if not values:
        raise argparse.ArgumentTypeError("expected at least one integer")
    return values


def spawn_scheduler(results_dir: Path) -> Tuple[subprocess.Popen, IO[str]]:
    log_path = results_dir / "scheduler.log"
    log_file = log_path.open("w")
    env = os.environ.copy()
    env["DATA_DIR"] = str(PROJECT_ROOT)
    env["RESULTS_DIR"] = str(results_dir)
    env.setdefault("WORKER_TIMEOUT_SEC", "60")
    cmd = [
        sys.executable,
        "-m",
        "uvicorn",
        "priority.scheduler:app",
        "--host",
        "127.0.0.1",
        "--port",
        str(SCHEDULER_PORT),
    ]
    proc = subprocess.Popen(cmd, env=env, stdout=log_file, stderr=log_file)
    return proc, log_file


def spawn_worker(idx: int, cores: int, speedup: float, run_dir: Path) -> Tuple[subprocess.Popen, IO[str]]:
    port = WORKER_BASE_PORT + idx
    log_path = run_dir / f"worker_{idx}.log"
    log_file = log_path.open("w")
    env = os.environ.copy()
    env["SCHEDULER_URL"] = f"http://127.0.0.1:{SCHEDULER_PORT}"
    env["CORES"] = str(cores)
    env["WORKER_ID"] = f"worker-{idx}"
    env["SPEEDUP"] = str(speedup)
    cmd = [
        sys.executable,
        "-m",
        "uvicorn",
        "priority.worker:app",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
    ]
    proc = subprocess.Popen(cmd, env=env, stdout=log_file, stderr=log_file)
    return proc, log_file


def wait_for_scheduler(timeout_sec: float = 30.0) -> None:
    base_url = f"http://127.0.0.1:{SCHEDULER_PORT}"
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        try:
            resp = httpx.get(f"{base_url}/status", timeout=2.0)
            if resp.status_code < 500:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.5)
    raise RuntimeError("scheduler did not become ready")


def trigger_run(dataset: str, speedup: float, min_slots: int, poll_ms: int) -> dict:
    base_url = f"http://127.0.0.1:{SCHEDULER_PORT}"
    with httpx.Client(timeout=httpx.Timeout(3600.0, connect=10.0)) as client:
        resp = client.post(
            f"{base_url}/start",
            json={
                "dataset_file": dataset,
                "speedup": speedup,
                "min_slots": min_slots,
            },
        )
        resp.raise_for_status()
        run_id = resp.json()["run_id"]
        while True:
            status = client.get(f"{base_url}/status")
            status.raise_for_status()
            payload = status.json()
            if payload.get("status") == "done" and payload.get("run_id") == run_id:
                return payload
            time.sleep(poll_ms / 1000.0)


def wait_for_slots(expected_slots: int, timeout_sec: float = 30.0, poll_sec: float = 0.5) -> None:
    base_url = f"http://127.0.0.1:{SCHEDULER_PORT}"
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        try:
            resp = httpx.get(f"{base_url}/workers", timeout=2.0)
            resp.raise_for_status()
            if resp.json().get("total_slots", 0) >= expected_slots:
                return
        except httpx.HTTPError:
            pass
        time.sleep(poll_sec)
    raise RuntimeError(f"workers never reached {expected_slots} slots")


def stop_process(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    proc.send_signal(signal.SIGTERM)
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()


def cleanup_processes(procs: Iterable[Tuple[subprocess.Popen, IO[str]]]) -> None:
    for proc, handle in procs:
        stop_process(proc)
        handle.close()


def copy_results(payload: dict, target_dir: Path, tag: str) -> Path:
    target_dir.mkdir(parents=True, exist_ok=True)
    jobs_src = Path(payload["jobs_csv"])
    run_src = Path(payload["run_csv"])
    jobs_dst = target_dir / f"{tag}_jobs.csv"
    run_dst = target_dir / f"{tag}_run.csv"
    shutil.copy2(jobs_src, jobs_dst)
    shutil.copy2(run_src, run_dst)
    return run_dst


def build_matrix(replicas: Sequence[int], cores: Sequence[int], force_grid: bool) -> List[Tuple[int, int]]:
    if force_grid or len(replicas) == 1 or len(cores) == 1:
        return list(itertools.product(replicas, cores))
    if len(replicas) != len(cores):
        raise ValueError("replicas and cores must have same length unless --grid is used")
    return list(zip(replicas, cores))


def main() -> None:
    parser = argparse.ArgumentParser(description="Run local priority scheduler experiments")
    parser.add_argument("--dataset", default=DEFAULT_DATASET, help="Dataset relative to priority folder")
    parser.add_argument("--speedup", type=float, default=20000.0, help="Simulation speedup factor")
    parser.add_argument("--replicas", default="2,4,8,16", help="Comma-separated replica counts")
    parser.add_argument("--cores", default="2,4,8,16", help="Comma-separated core counts")
    parser.add_argument("--poll-ms", type=int, default=500, help="Status poll interval")
    parser.add_argument("--grid", action="store_true", help="Evaluate full cartesian product of replicas x cores")
    parser.add_argument("--results-dir", default=str(DEFAULT_RESULTS_DIR), help="Where to copy final CSV files")
    parser.add_argument("--keep-temp", action="store_true", help="Keep raw run directories for debugging")
    args = parser.parse_args()

    replicas = parse_int_list(args.replicas)
    cores = parse_int_list(args.cores)
    combos = build_matrix(replicas, cores, args.grid)

    results_dir = Path(args.results_dir)
    temp_root = DEFAULT_TEMP_DIR
    temp_root.mkdir(parents=True, exist_ok=True)
    summaries = []

    for idx, (replica_count, core_count) in enumerate(combos, start=1):
        tag = f"replicas_{replica_count}_cores_{core_count}"
        print(f"[{idx}/{len(combos)}] Running {tag}")
        run_results_dir = temp_root / tag
        run_results_dir.mkdir(parents=True, exist_ok=True)
        scheduler_proc, scheduler_log = spawn_scheduler(run_results_dir)
        workers: List[Tuple[subprocess.Popen, IO[str]]] = []
        try:
            wait_for_scheduler()
            for worker_idx in range(replica_count):
                proc, handle = spawn_worker(worker_idx, core_count, args.speedup, run_results_dir)
                workers.append((proc, handle))
            time.sleep(2.0)
            wait_for_slots(replica_count * core_count)
            payload = trigger_run(
                dataset=args.dataset,
                speedup=args.speedup,
                min_slots=replica_count * core_count,
                poll_ms=args.poll_ms,
            )
            copy_results(payload, results_dir, tag)
            summary = payload.get("summary", {}).copy()
            summary["tag"] = tag
            summaries.append(summary)
            print(f"  -> completed run_id={summary.get('run_id')}")
        finally:
            cleanup_processes(workers)
            stop_process(scheduler_proc)
            scheduler_log.close()
            if not args.keep_temp:
                shutil.rmtree(run_results_dir, ignore_errors=True)

    if summaries:
        import pandas as pd

        df = pd.DataFrame(summaries)
        df.to_csv(results_dir / "summary.csv", index=False)
        print(f"Wrote summary for {len(summaries)} configurations to {results_dir / 'summary.csv'}")


if __name__ == "__main__":
    main()
