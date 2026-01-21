## Opinion

Round-Robin is simple on paper, but in a distributed setup the implementation matter almost as much as the policy: locks, long-polling, request rate, queue operations, and result logging all add real overhead.

I expected runtime to drop almost linearly with more machines/cores, but the plots show diminishing returns, especially with small quanta. The scheduler becomes a bottleneck because every time slice triggers coordination: a worker requests work and later reports completion. When the quantum is tiny, slices per job explode, which means HTTP round trips explode, and the system spends more time scheduling/communicating than executing. That’s why 10ms quanta lead to very large avg_slices_per_job and much longer wall time.

Fairness also has a clear trade-off. Smaller quanta usually improve fairness and responsiveness (jobs get CPU time more often), but they increase preemptions and scheduler load. Using metrics like waiting-time Gini or slowdown-based Jain’s index helped show that “fair” depends on what we measure: equal waiting time, equal slowdown, or better tail behavior.