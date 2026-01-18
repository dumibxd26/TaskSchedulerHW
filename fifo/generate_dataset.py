# generate_dataset.py - Generate CSV with overlapping jobs for FIFO testing

import argparse
import random
import csv

def generate_dataset(
    output_file: str,
    num_jobs: int = 1000,
    arrival_interval_min: int = 10,
    arrival_interval_max: int = 50,
    service_time_min: int = 100,
    service_time_max: int = 500,
    job_id_prefix: str = "F",
):
    """
    Generate a CSV dataset with overlapping jobs for FIFO scheduling.
    
    Strategy:
    - Jobs arrive with intervals between arrival_interval_min and arrival_interval_max ms
    - Each job has service_time between service_time_min and service_time_max ms
    - service_time is typically > arrival_interval, causing guaranteed overlapping
    - This creates a queue scenario where jobs wait for previous ones to complete
    
    Args:
        output_file: Path to output CSV file
        num_jobs: Number of jobs to generate
        arrival_interval_min: Minimum time between job arrivals (ms)
        arrival_interval_max: Maximum time between job arrivals (ms)
        service_time_min: Minimum service time per job (ms)
        service_time_max: Maximum service time per job (ms)
        job_id_prefix: Prefix for job IDs (e.g., "F" for FIFO)
    """
    
    # Generate jobs
    jobs = []
    current_arrival = 0
    
    for i in range(1, num_jobs + 1):
        # Generate arrival time (progressive with interval)
        if i > 1:
            interval = random.randint(arrival_interval_min, arrival_interval_max)
            current_arrival += interval
        else:
            current_arrival = 0  # First job arrives at time 0
        
        # Generate service time (longer than typical interval to ensure overlap)
        service_time = random.randint(service_time_min, service_time_max)
        
        # Generate job ID
        job_id = f"{job_id_prefix}{i:05d}"
        
        jobs.append({
            "job_id": job_id,
            "service_time_ms": service_time,
            "arrival_time_ms": current_arrival,
            "priority": 1,  # Default priority
        })
    
    # Sort by arrival_time (should already be sorted, but ensure it)
    jobs.sort(key=lambda x: x["arrival_time_ms"])
    
    # Write to CSV
    with open(output_file, 'w', newline='') as f:
        fieldnames = ["job_id", "service_time_ms", "arrival_time_ms", "priority"]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(jobs)
    
    # Print statistics
    total_time = jobs[-1]["arrival_time_ms"] + jobs[-1]["service_time_ms"]
    avg_arrival_interval = sum(jobs[i]["arrival_time_ms"] - jobs[i-1]["arrival_time_ms"] 
                               for i in range(1, len(jobs))) / (len(jobs) - 1) if len(jobs) > 1 else 0
    avg_service_time = sum(j["service_time_ms"] for j in jobs) / len(jobs)
    overlapping_ratio = avg_service_time / avg_arrival_interval if avg_arrival_interval > 0 else float('inf')
    
    print(f"Generated dataset: {output_file}")
    print(f"  Jobs: {num_jobs}")
    print(f"  Arrival time range: 0 - {jobs[-1]['arrival_time_ms']} ms")
    print(f"  Average arrival interval: {avg_arrival_interval:.1f} ms")
    print(f"  Service time range: {service_time_min} - {service_time_max} ms")
    print(f"  Average service time: {avg_service_time:.1f} ms")
    print(f"  Overlapping ratio: {overlapping_ratio:.2f}x (service_time / arrival_interval)")
    print(f"  Total simulation time (last arrival + last service): {total_time} ms")
    
    if overlapping_ratio > 1.5:
        print(f"  [OK] Good overlapping: jobs will queue up (ratio > 1.5)")
    elif overlapping_ratio > 1.0:
        print(f"  [WARN] Moderate overlapping: some queuing expected")
    else:
        print(f"  [LOW] Low overlapping: jobs may not queue much")

def main():
    ap = argparse.ArgumentParser(description="Generate CSV dataset with overlapping jobs for FIFO testing")
    ap.add_argument("--output", "-o", required=True, help="Output CSV file path")
    ap.add_argument("--num-jobs", "-n", type=int, default=1000, help="Number of jobs to generate")
    ap.add_argument("--arrival-interval-min", type=int, default=10, help="Minimum arrival interval (ms)")
    ap.add_argument("--arrival-interval-max", type=int, default=50, help="Maximum arrival interval (ms)")
    ap.add_argument("--service-time-min", type=int, default=100, help="Minimum service time (ms)")
    ap.add_argument("--service-time-max", type=int, default=500, help="Maximum service time (ms)")
    ap.add_argument("--job-prefix", default="F", help="Job ID prefix (e.g., 'F' for FIFO)")
    ap.add_argument("--seed", type=int, help="Random seed for reproducibility")
    
    args = ap.parse_args()
    
    if args.seed is not None:
        random.seed(args.seed)
    
    generate_dataset(
        output_file=args.output,
        num_jobs=args.num_jobs,
        arrival_interval_min=args.arrival_interval_min,
        arrival_interval_max=args.arrival_interval_max,
        service_time_min=args.service_time_min,
        service_time_max=args.service_time_max,
        job_id_prefix=args.job_prefix,
    )

if __name__ == "__main__":
    main()
