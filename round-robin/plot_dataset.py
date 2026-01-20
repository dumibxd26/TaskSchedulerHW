import argparse
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt

def concurrency_sweep(df: pd.DataFrame):
    starts = df["arrival_time_ms"].to_numpy(np.int64)
    ends = (df["arrival_time_ms"] + df["service_time_ms"]).to_numpy(np.int64)

    times = np.concatenate([starts, ends])
    deltas = np.concatenate([np.ones_like(starts), -np.ones_like(ends)])

    order = np.argsort(times, kind="stable")
    times = times[order]
    deltas = deltas[order]

    uniq_times, conc = [], []
    cur = 0
    i = 0
    while i < len(times):
        t = times[i]
        while i < len(times) and times[i] == t:
            cur += deltas[i]
            i += 1
        uniq_times.append(t)
        conc.append(cur)

    return np.array(uniq_times), np.array(conc)

def concurrency_on_grid(df: pd.DataFrame, step_ms: int):
    t_changes, c_changes = concurrency_sweep(df)

    x_min = 0
    x_max = int(df["arrival_time_ms"].max())  # <-- last ARRIVAL only (what you asked)

    grid = np.arange(x_min, x_max + step_ms, step_ms, dtype=np.int64)
    idx = np.searchsorted(t_changes, grid, side="right") - 1
    conc = np.where(idx >= 0, c_changes[idx], 0)
    return grid, conc, x_min, x_max

def plot(csv_path: str, title: str, step_ms: int, bin_ms: int):
    df = pd.read_csv(csv_path).sort_values("arrival_time_ms", kind="stable")

    # Force first arrival to be the left bound (you said it will always be 0)
    x_min = 0
    x_max = int(df["arrival_time_ms"].max())

    # 1) overlap line (sampled)
    g, conc, _, _ = concurrency_on_grid(df, step_ms=step_ms)
    plt.figure()
    plt.plot(g, conc)
    plt.xlim(x_min, x_max)
    plt.xlabel("time (ms)")
    plt.ylabel(f"overlap (sampled every {step_ms} ms)")
    plt.title(f"{title} — overlap")
    plt.tight_layout()
    plt.show()

    # 2) arrival density histogram
    bins = np.arange(x_min, x_max + bin_ms, bin_ms)
    counts, edges = np.histogram(df["arrival_time_ms"].to_numpy(), bins=bins)
    centers = (edges[:-1] + edges[1:]) / 2

    plt.figure()
    plt.bar(centers, counts, width=bin_ms, align="center")
    plt.xlim(x_min, x_max)
    plt.xlabel("time (ms)")
    plt.ylabel(f"arrivals per {bin_ms} ms bin")
    plt.title(f"{title} — arrival density")
    plt.tight_layout()
    plt.show()

    print("\nMax arrival:", x_max)
    print("Max overlap (sampled):", int(conc.max()))
    print(df.head(10))

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True)
    ap.add_argument("--title", default=None)
    ap.add_argument("--step-ms", type=int, default=50, help="sampling step for overlap line")
    ap.add_argument("--bin-ms", type=int, default=200, help="bin size for arrival histogram")
    args = ap.parse_args()

    plot(args.csv, args.title or args.csv, step_ms=args.step_ms, bin_ms=args.bin_ms)

if __name__ == "__main__":
    main()
