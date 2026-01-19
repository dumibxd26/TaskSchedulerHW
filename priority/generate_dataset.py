#!/usr/bin/env python3
"""Generate CSV datasets tailored for priority-based schedulers."""

from __future__ import annotations

import argparse
import csv
import random
from dataclasses import dataclass
from pathlib import Path
from typing import List


@dataclass(frozen=True)
class PriorityProfile:
    priority: int
    weight: float
    service_min: int
    service_max: int

    def service_range_str(self) -> str:
        return f"{self.service_min}-{self.service_max}"


def build_priority_profiles(
    priority_min: int,
    priority_max: int,
    base_service_min: int,
    base_service_max: int,
) -> List[PriorityProfile]:
    if priority_min > priority_max:
        raise ValueError("priority_min must be <= priority_max")

    levels = priority_max - priority_min + 1
    span = max(1, levels - 1)

    profiles: List[PriorityProfile] = []
    for idx, priority in enumerate(range(priority_min, priority_max + 1)):
        # Linux niceness: lower numbers represent higher priority.
        priority_rank = idx / span  # 0.0 for highest priority, 1.0 for lowest

        # Higher priority jobs are more frequent and shorter on average.
        weight = 1.0 + (1.5 * (1.0 - priority_rank))
        multiplier = 0.5 + (priority_rank * 1.2)

        svc_min = max(1, int(round(base_service_min * multiplier)))
        svc_max = max(svc_min, int(round(base_service_max * multiplier)))

        profiles.append(
            PriorityProfile(
                priority=priority,
                weight=weight,
                service_min=svc_min,
                service_max=svc_max,
            )
        )

    return profiles


def generate_dataset(
    output_file: Path,
    num_jobs: int,
    arrival_interval_min: int,
    arrival_interval_max: int,
    service_time_min: int,
    service_time_max: int,
    job_id_prefix: str,
    priority_profiles: List[PriorityProfile],
    burst_probability: float,
    burst_multiplier: float,
):
    if num_jobs <= 0:
        raise ValueError("num_jobs must be >= 1")
    if arrival_interval_min <= 0 or arrival_interval_max < arrival_interval_min:
        raise ValueError("arrival interval bounds must be positive and min <= max")
    if service_time_min <= 0 or service_time_max < service_time_min:
        raise ValueError("service time bounds must be positive and min <= max")
    if not 0 <= burst_probability <= 1:
        raise ValueError("burst_probability must be between 0 and 1")
    if not 0 < burst_multiplier <= 1:
        raise ValueError("burst_multiplier must be > 0 and <= 1")

    rng_profiles = list(priority_profiles)
    weights = [p.weight for p in rng_profiles]

    jobs = []
    arrival_time = 0
    arrival_intervals = []
    stats = {p.priority: {"count": 0, "service_sum": 0} for p in rng_profiles}

    for idx in range(1, num_jobs + 1):
        if idx == 1:
            arrival_time = 0
        else:
            interval = random.randint(arrival_interval_min, arrival_interval_max)
            if burst_probability > 0 and random.random() < burst_probability:
                interval = max(1, int(round(interval * burst_multiplier)))
            arrival_time += interval
            arrival_intervals.append(interval)

        profile = random.choices(rng_profiles, weights=weights, k=1)[0]
        service_time = random.randint(profile.service_min, profile.service_max)

        row = {
            "job_id": f"{job_id_prefix}{idx:05d}",
            "arrival_time_ms": arrival_time,
            "service_time_ms": service_time,
            "priority": profile.priority,
        }

        jobs.append(row)
        stats[profile.priority]["count"] += 1
        stats[profile.priority]["service_sum"] += service_time

    fieldnames = ["job_id", "arrival_time_ms", "service_time_ms", "priority"]

    output_file.parent.mkdir(parents=True, exist_ok=True)
    with output_file.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(jobs)

    total_duration = jobs[-1]["arrival_time_ms"] + jobs[-1]["service_time_ms"]
    avg_arrival_interval = (
        sum(arrival_intervals) / len(arrival_intervals)
        if arrival_intervals
        else arrival_interval_min
    )

    print(f"Generated dataset: {output_file}")
    print(f"  Jobs: {num_jobs}")
    print(f"  Arrival interval range: {arrival_interval_min}-{arrival_interval_max} ms")
    print(f"  Average arrival interval: {avg_arrival_interval:.1f} ms")
    print(f"  Service time range (base): {service_time_min}-{service_time_max} ms")
    print(f"  Burst probability: {burst_probability:.2f} (multiplier={burst_multiplier:.2f})")
    print(f"  Total simulation window: {total_duration} ms")
    print("  Priority breakdown:")
    total_weight = sum(weights)
    for profile in rng_profiles:
        tier_stats = stats[profile.priority]
        count = tier_stats["count"]
        fraction = count / num_jobs if num_jobs else 0
        avg_service = tier_stats["service_sum"] / count if count else 0
        weight_pct = profile.weight / total_weight if total_weight else 0
        print(
            "    "
            f"Priority {profile.priority}: {count} jobs "
            f"({fraction:.1%}), target share {weight_pct:.1%}, service {profile.service_range_str()} ms, "
            f"avg service {avg_service:.1f} ms"
        )


def main():
    parser = argparse.ArgumentParser(
        description="Generate CSV workloads that stress-test priority schedulers."
    )
    parser.add_argument("--output", "-o", required=True, help="Path for the CSV to emit")
    parser.add_argument("--num-jobs", "-n", type=int, default=1000, help="Number of jobs to synthesize")
    parser.add_argument("--arrival-interval-min", type=int, default=5, help="Minimum gap between arrivals (ms)")
    parser.add_argument("--arrival-interval-max", type=int, default=50, help="Maximum gap between arrivals (ms)")
    parser.add_argument("--service-time-min", type=int, default=20, help="Baseline minimum service time (ms)")
    parser.add_argument("--service-time-max", type=int, default=500, help="Baseline maximum service time (ms)")
    parser.add_argument("--job-prefix", default="PR", help="Prefix for generated job IDs")
    parser.add_argument(
        "--priority-min",
        type=int,
        default=-19,
        help="Minimum (highest) scheduler priority, matching Linux nice range",
    )
    parser.add_argument(
        "--priority-max",
        type=int,
        default=19,
        help="Maximum (lowest) scheduler priority, matching Linux nice range",
    )
    parser.add_argument(
        "--burst-probability",
        type=float,
        default=0.15,
        help="Chance that an arrival interval is shortened to create bursts",
    )
    parser.add_argument(
        "--burst-multiplier",
        type=float,
        default=0.35,
        help="Factor applied to arrival interval during bursts (0 < value <= 1)",
    )
    parser.add_argument("--seed", type=int, help="Optional RNG seed for reproducibility")

    args = parser.parse_args()

    if args.seed is not None:
        random.seed(args.seed)

    profiles = build_priority_profiles(
        priority_min=args.priority_min,
        priority_max=args.priority_max,
        base_service_min=args.service_time_min,
        base_service_max=args.service_time_max,
    )

    generate_dataset(
        output_file=Path(args.output),
        num_jobs=args.num_jobs,
        arrival_interval_min=args.arrival_interval_min,
        arrival_interval_max=args.arrival_interval_max,
        service_time_min=args.service_time_min,
        service_time_max=args.service_time_max,
        job_id_prefix=args.job_prefix,
        priority_profiles=profiles,
        burst_probability=args.burst_probability,
        burst_multiplier=args.burst_multiplier,
    )


if __name__ == "__main__":
    main()
