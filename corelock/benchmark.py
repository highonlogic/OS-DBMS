# Author: Anmol Tyagi
# Phase-III: Benchmark harness (team TriCoders, CoreLock)
"""
benchmark.py -- Compare scheduling algorithms on identical workloads.

For each algorithm (FCFS, SJF, PRIORITY, ROUND_ROBIN), this harness
runs the SAME synthetic workload (same seat requests, same priorities,
same arrival order) through a fresh engine, and reports:

    * throughput             -- committed bookings per wall-second
    * avg_wait_s             -- mean time from create -> start
    * avg_lock_wait_s        -- mean time blocked on lock acquires
    * avg_turnaround_s       -- mean time from create -> finish
    * deadlocks              -- cycles observed by DeadlockManager
    * aborts                 -- transactions ending in ABORTED
    * abort_rate             -- aborts / total
    * cpu_utilization        -- engine busy time / wall capacity
    * context_switches       -- total RR preemptions (0 for others)

Run standalone:
    python -m corelock.benchmark

Also importable from the dashboard (see run_benchmark()).
"""

from __future__ import annotations

import argparse
import random
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .seat_inventory import Cinema, Screen, Show
from .transaction_manager import TransactionManager, TxnState
from .lock_manager import LockManager
from .scheduler import Scheduler, Algorithm
from .execution_engine import ExecutionEngine
from .recovery_manager import RecoveryManager


# ---------------------------------------------------------------------------
# Workload generator
# ---------------------------------------------------------------------------

@dataclass
class WorkloadRequest:
    user: str
    seats: List[str]
    priority: int


def generate_workload(num_txns: int, seed: int = 42,
                      contention: str = "medium") -> List[WorkloadRequest]:
    """
    Build a reproducible workload of booking requests.

    contention:
        "low"    -- requests spread across the full seat map; few conflicts
        "medium" -- 60% on a hot 10-seat zone, 40% random
        "high"   -- 90% on a hot 6-seat zone (lots of lock contention)
    """
    rng = random.Random(seed)
    rows, cols = 5, 6
    all_seats = [f"{chr(ord('A') + r)}{c + 1}"
                 for r in range(rows) for c in range(cols)]
    hot = {
        "low":    all_seats,
        "medium": ["A3", "A4", "A5", "B3", "B4", "B5",
                   "C3", "C4", "C5", "D3"],
        "high":   ["A3", "A4", "B3", "B4", "C3", "C4"],
    }[contention]
    hot_ratio = {"low": 0.0, "medium": 0.6, "high": 0.9}[contention]

    requests = []
    for i in range(num_txns):
        pool = hot if rng.random() < hot_ratio else all_seats
        k = rng.choice([1, 1, 2, 2, 3])
        seats = rng.sample(pool, k=min(k, len(pool)))
        requests.append(WorkloadRequest(
            user=f"User{i+1:03d}",
            seats=seats,
            priority=rng.randint(1, 8),
        ))
    return requests


# ---------------------------------------------------------------------------
# Single-algorithm run
# ---------------------------------------------------------------------------

@dataclass
class BenchmarkResult:
    algorithm: str
    num_txns: int
    contention: str
    hold_seconds: float
    max_concurrent: int

    wall_s: float = 0.0
    throughput_per_s: float = 0.0

    committed: int = 0
    aborted: int = 0
    abort_rate: float = 0.0

    avg_wait_s: float = 0.0
    avg_lock_wait_s: float = 0.0
    avg_turnaround_s: float = 0.0

    deadlocks: int = 0
    context_switches: int = 0
    cpu_utilization: float = 0.0

    per_txn: List[dict] = field(default_factory=list)

    def row(self) -> dict:
        """Flat dict for a pandas DataFrame / CSV row."""
        return {
            "algorithm":          self.algorithm,
            "num_txns":           self.num_txns,
            "contention":         self.contention,
            "wall_s":             round(self.wall_s, 3),
            "throughput_per_s":   round(self.throughput_per_s, 3),
            "committed":          self.committed,
            "aborted":            self.aborted,
            "abort_rate":         round(self.abort_rate, 3),
            "avg_wait_s":         round(self.avg_wait_s, 3),
            "avg_lock_wait_s":    round(self.avg_lock_wait_s, 3),
            "avg_turnaround_s":   round(self.avg_turnaround_s, 3),
            "deadlocks":          self.deadlocks,
            "context_switches":   self.context_switches,
            "cpu_utilization":    round(self.cpu_utilization, 3),
        }


def _fresh_engine(algorithm: Algorithm,
                  max_concurrent: int) -> Tuple[Cinema, TransactionManager,
                                                Scheduler, ExecutionEngine,
                                                RecoveryManager]:
    """Build an isolated engine (fresh WAL file in /tmp)."""
    import os, tempfile
    log_path = os.path.join(
        tempfile.gettempdir(),
        f"corelock_bench_{algorithm.value}_{int(time.time()*1000)}.jsonl",
    )
    cinema = Cinema.seed_demo_data()
    tm = TransactionManager()
    lm = LockManager(wait_timeout=2.0)
    sch = Scheduler(algorithm=algorithm, rr_quantum_s=0.3)
    rec = RecoveryManager(log_path=log_path, truncate=True)
    eng = ExecutionEngine(cinema, tm, lm, sch, rec,
                          max_concurrent=max_concurrent,
                          monitor_interval=0.2)
    eng.start_monitor()
    return cinema, tm, sch, eng, rec


def run_single(algorithm: Algorithm,
               workload: List[WorkloadRequest],
               hold_seconds: float = 0.3,
               max_concurrent: int = 6,
               show_id: str = "S101") -> BenchmarkResult:
    """Run the given workload once under one algorithm."""
    cinema, tm, sch, eng, rec = _fresh_engine(algorithm, max_concurrent)

    # Create all txns up front so SJF/PRIORITY can see the full queue
    # when they do their first _sorted_snapshot().
    for req in workload:
        txn = tm.create_transaction(req.user, show_id, req.seats, req.priority)
        sch.add(txn)

    # Dispatch everything.
    batch = sch.next_batch(len(workload))
    wall_start = time.time()
    threads = eng.run_all_threaded(batch, default_hold=hold_seconds, wait=True)
    wall = time.time() - wall_start

    # Give the deadlock monitor one more sweep so its history is accurate.
    time.sleep(0.3)
    eng.stop_monitor()

    snaps = tm.all_snapshots()
    committed = sum(1 for s in snaps if s["state"] == "COMMITTED")
    aborted   = sum(1 for s in snaps if s["state"] == "ABORTED")
    total     = max(1, len(snaps))

    avg_wait        = sum(s["wait_s"]        for s in snaps) / total
    avg_lock_wait   = sum(s["lock_wait_s"]   for s in snaps) / total
    avg_turnaround  = sum(s["turnaround_s"]  for s in snaps) / total
    ctx_switches    = sum(s["ctx_switches"]  for s in snaps)

    cpu = eng.cpu_utilization()
    deadlocks = len(eng.deadlock_manager.cycle_history())

    return BenchmarkResult(
        algorithm=algorithm.value,
        num_txns=len(workload),
        contention="(set by caller)",
        hold_seconds=hold_seconds,
        max_concurrent=max_concurrent,
        wall_s=wall,
        throughput_per_s=committed / max(1e-6, wall),
        committed=committed,
        aborted=aborted,
        abort_rate=aborted / total,
        avg_wait_s=avg_wait,
        avg_lock_wait_s=avg_lock_wait,
        avg_turnaround_s=avg_turnaround,
        deadlocks=deadlocks,
        context_switches=ctx_switches,
        cpu_utilization=cpu["utilization"],
        per_txn=snaps,
    )


# ---------------------------------------------------------------------------
# Full benchmark sweep
# ---------------------------------------------------------------------------

def run_benchmark(num_txns: int = 30,
                  contention: str = "medium",
                  hold_seconds: float = 0.3,
                  max_concurrent: int = 6,
                  seed: int = 42,
                  algorithms: Optional[List[Algorithm]] = None,
                  ) -> List[BenchmarkResult]:
    """
    Build ONE workload and run it under every algorithm. Returns one
    BenchmarkResult per algorithm, in the given order.
    """
    algos = algorithms or [
        Algorithm.FCFS, Algorithm.SJF, Algorithm.PRIORITY, Algorithm.ROUND_ROBIN,
    ]
    workload = generate_workload(num_txns, seed=seed, contention=contention)
    results = []
    for algo in algos:
        r = run_single(algo, workload,
                       hold_seconds=hold_seconds,
                       max_concurrent=max_concurrent)
        r.contention = contention
        results.append(r)
    return results


def format_table(results: List[BenchmarkResult]) -> str:
    """Pretty-print a benchmark comparison table."""
    rows = [r.row() for r in results]
    headers = ["algorithm", "committed", "aborted", "abort_rate",
               "throughput_per_s", "avg_wait_s", "avg_lock_wait_s",
               "avg_turnaround_s", "deadlocks", "ctx_sw", "cpu_util"]
    widths = {h: max(len(h), *(len(str(r.get(h, r.get("context_switches" if h == "ctx_sw" else "cpu_utilization" if h == "cpu_util" else h, "")))) for r in rows)) for h in headers}
    def fmt(v):
        return str(v) if not isinstance(v, float) else f"{v:.3f}"
    def col(r, h):
        key = {"ctx_sw": "context_switches", "cpu_util": "cpu_utilization"}.get(h, h)
        return fmt(r.get(key, "-"))
    out = []
    out.append(" | ".join(h.ljust(widths[h]) for h in headers))
    out.append("-+-".join("-" * widths[h] for h in headers))
    for r in rows:
        out.append(" | ".join(col(r, h).ljust(widths[h]) for h in headers))
    return "\n".join(out)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(
        description="CoreLock scheduling-algorithm benchmark.")
    ap.add_argument("-n", "--num-txns", type=int, default=30)
    ap.add_argument("-c", "--contention", choices=["low", "medium", "high"],
                    default="medium")
    ap.add_argument("--hold", type=float, default=0.3,
                    help="browsing-hold seconds per txn")
    ap.add_argument("--concurrent", type=int, default=6,
                    help="max concurrent runners in the engine")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--csv", type=str, default=None,
                    help="optional path to write a CSV of results")
    args = ap.parse_args()

    print(f"\nCoreLock Benchmark")
    print(f"  workload: num_txns={args.num_txns}, contention={args.contention}")
    print(f"            hold={args.hold}s, max_concurrent={args.concurrent}")
    print(f"            seed={args.seed}\n")

    results = run_benchmark(
        num_txns=args.num_txns,
        contention=args.contention,
        hold_seconds=args.hold,
        max_concurrent=args.concurrent,
        seed=args.seed,
    )

    print(format_table(results))

    if args.csv:
        import csv
        rows = [r.row() for r in results]
        with open(args.csv, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
        print(f"\nCSV written to {args.csv}")


if __name__ == "__main__":
    main()
