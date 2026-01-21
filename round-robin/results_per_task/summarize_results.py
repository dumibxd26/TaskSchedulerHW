import os
import re
import json
import math
from pathlib import Path
from typing import Dict, Any, Optional, Tuple, List

import pandas as pd


JOBS_RE = re.compile(r"^results_jobs_([a-fA-F0-9]+)\.csv$")
RUN_RE  = re.compile(r"^results_run_([a-fA-F0-9]+)\.csv$")


def gini_coefficient(x: pd.Series) -> float:
    """Gini for non-negative values. 0=perfect equality, 1=max inequality."""
    x = pd.to_numeric(x, errors="coerce").dropna()
    if x.empty:
        return 0.0
    x = x.clip(lower=0)
    n = len(x)
    if n == 0:
        return 0.0
    mean = float(x.mean())
    if mean == 0:
        return 0.0
    # O(n log n) formula using sorted values
    xs = x.sort_values().to_numpy()
    cum = 0.0
    for i, v in enumerate(xs, start=1):
        cum += i * float(v)
    g = (2.0 * cum) / (n * xs.sum()) - (n + 1) / n
    return float(g)


def jain_index(x: pd.Series) -> float:
    """Jain's fairness index. 1=perfectly fair (all equal)."""
    x = pd.to_numeric(x, errors="coerce").dropna()
    if x.empty:
        return 1.0
    s1 = float(x.sum())
    s2 = float((x * x).sum())
    n = len(x)
    if s2 == 0:
        return 1.0
    return float((s1 * s1) / (n * s2))


def pct(x: pd.Series, p: float) -> float:
    x = pd.to_numeric(x, errors="coerce").dropna()
    if x.empty:
        return 0.0
    return float(x.quantile(p))


def cv(x: pd.Series) -> float:
    x = pd.to_numeric(x, errors="coerce").dropna()
    if x.empty:
        return 0.0
    mu = float(x.mean())
    if mu == 0:
        return 0.0
    return float(x.std(ddof=0) / mu)


def load_run_speedup(run_csv: Path) -> Optional[float]:
    try:
        df = pd.read_csv(run_csv)
        if df.empty or "speedup" not in df.columns:
            return None
        return float(df.loc[0, "speedup"])
    except Exception:
        return None


def summarize_jobs(jobs_csv: Path, run_csv: Optional[Path]) -> Dict[str, Any]:
    df = pd.read_csv(jobs_csv)

    required = {"arrival_time_ms", "finish_time_ms", "service_time_ms", "response_time_ms", "waiting_time_ms"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"{jobs_csv} missing columns: {sorted(missing)}")

    # Basic time metrics
    max_arrival = float(pd.to_numeric(df["arrival_time_ms"], errors="coerce").max())
    min_arrival = float(pd.to_numeric(df["arrival_time_ms"], errors="coerce").min())
    max_finish  = float(pd.to_numeric(df["finish_time_ms"], errors="coerce").max())
    sim_makespan_ms = max_finish - min_arrival  # for batch (arrival=0) => max_finish

    speedup = load_run_speedup(run_csv) if run_csv else None
    estimated_wall_seconds = None
    if speedup and speedup > 0:
        estimated_wall_seconds = sim_makespan_ms / (speedup * 1000.0)

    # Fairness-related metrics
    service = pd.to_numeric(df["service_time_ms"], errors="coerce").clip(lower=1)
    response = pd.to_numeric(df["response_time_ms"], errors="coerce")
    waiting = pd.to_numeric(df["waiting_time_ms"], errors="coerce").clip(lower=0)

    slowdown = (response / service).replace([math.inf, -math.inf], pd.NA).dropna()

    fairness = {
        "jobs": int(len(df)),
        "max_arrival_time_ms": max_arrival,

        # makespan
        "sim_makespan_ms": sim_makespan_ms,
        "speedup": speedup,
        "estimated_wall_seconds": estimated_wall_seconds,

        # waiting distribution
        "waiting_p50_ms": pct(waiting, 0.50),
        "waiting_p95_ms": pct(waiting, 0.95),
        "waiting_p99_ms": pct(waiting, 0.99),
        "waiting_max_ms": float(waiting.max()) if not waiting.empty else 0.0,
        "waiting_cv": cv(waiting),
        "waiting_gini": gini_coefficient(waiting),

        # slowdown distribution (fairness proxy)
        "slowdown_p50": pct(slowdown, 0.50),
        "slowdown_p95": pct(slowdown, 0.95),
        "slowdown_p99": pct(slowdown, 0.99),
        "slowdown_max": float(slowdown.max()) if not slowdown.empty else 0.0,
        "slowdown_cv": cv(slowdown),
        "slowdown_gini": gini_coefficient(slowdown),
        "slowdown_jain": jain_index(slowdown),
    }

    return fairness


def pick_better(existing: Dict[str, Any], candidate: Dict[str, Any]) -> bool:
    """
    Return True if candidate should replace existing.
    Prefer entries that have speedup (i.e. run_csv found), then newest mtime.
    """
    ex_has_speedup = existing.get("fairness", {}).get("speedup") is not None
    ca_has_speedup = candidate.get("fairness", {}).get("speedup") is not None
    if ca_has_speedup and not ex_has_speedup:
        return True
    if ex_has_speedup and not ca_has_speedup:
        return False
    # otherwise pick newest jobs file
    return candidate.get("jobs_mtime", 0) > existing.get("jobs_mtime", 0)


def main(base_dir: str) -> None:
    base = Path(base_dir).resolve()
    if not base.exists():
        raise SystemExit(f"Base directory not found: {base}")

    # Index run_csv files by id so we can match quickly
    run_map: Dict[str, Path] = {}
    for path in base.rglob("results_run_*.csv"):
        m = RUN_RE.match(path.name)
        if m:
            run_map[m.group(1)] = path

    # Gather jobs summaries
    by_id: Dict[str, Dict[str, Any]] = {}

    for jobs_path in base.rglob("results_jobs_*.csv"):
        m = JOBS_RE.match(jobs_path.name)
        if not m:
            continue
        run_id = m.group(1)

        run_csv = run_map.get(run_id)
        try:
            fairness = summarize_jobs(jobs_path, run_csv)
        except Exception as e:
            print(f"[WARN] Skipping {jobs_path} because: {e}")
            continue

        candidate = {
            "id": run_id,
            "total_time_to_run": {
                "sim_makespan_ms": fairness["sim_makespan_ms"],
                "estimated_wall_seconds": fairness["estimated_wall_seconds"],
            },
            "fairness": fairness,
            "sources": {
                "jobs_csv": str(jobs_path),
                "run_csv": str(run_csv) if run_csv else None,
                "directory": str(jobs_path.parent),
            },
            "jobs_mtime": jobs_path.stat().st_mtime,
        }

        if run_id not in by_id:
            by_id[run_id] = candidate
        else:
            if pick_better(by_id[run_id], candidate):
                by_id[run_id] = candidate

    # Output array of objects (as requested)
    out: List[Dict[str, Any]] = []
    for _, obj in sorted(by_id.items(), key=lambda kv: kv[0]):
        # remove internal helper field
        obj.pop("jobs_mtime", None)
        out.append({
            "id": obj["id"],
            "total_time_to_run": obj["total_time_to_run"],
            "fairness": obj["fairness"],
            "sources": obj["sources"],
        })

    out_path = base / "summary_results.json"
    out_path.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=".", help="Folder that contains results_* directories")
    args = ap.parse_args()
    main(args.base)
